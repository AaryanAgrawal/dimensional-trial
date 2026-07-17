#!/usr/bin/env python3
"""Prepare benchmark sections from a recorded drive (offline, dimos-as-library).

Turns one recording (data/<name>.db) into:
  - a PGO premap (what the robot would carry as its map file), and
  - N query sections: body-frame accumulated local submaps + silver-truth poses,
saved under out/prepared/<name>.pkl for run_bench.py.

Faithful to the live stack (verified against source, WORKSPACE.md recon digest):
  - premap  = per-frame PGO-corrected clouds -> VoxelGrid(0.05 m, carve OFF)  [CLI default]
  - localmap = trailing scans -> VoxelGrid(0.05 m, carve ON)                  [live mapper]
  - query gate: accumulate until >= 50_000 voxels (module.py MIN_LOCAL_POINTS)
  - truth: T_true = correction_at(ts) o world_raw_T_body(ts) = map_T_body, where
    "map" is the PGO world_corrected frame the premap is built in.
    TRUTH IS SILVER: PGO is not bit-deterministic (~6 cm across runs, measured);
    every downstream number inherits that floor. This module records the floor.

Run: cd dimos && uv run python ../trial/harness/prep.py hk_village3 --n-queries 24
"""

from __future__ import annotations

import argparse
import pickle
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from dimos.mapping.loop_closure.pgo import PGO, PoseGraph
from dimos.mapping.voxels import VoxelGrid
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

OUT_DIR = Path(__file__).parent / "out" / "prepared"

MIN_LOCAL_POINTS = 50_000  # module.py:47 — the live gate
VOXEL = 0.05  # premap + live mapper voxel size
PREMAP_DEDUP_TOL_M = 0.3  # dimos map global --pgo-tol default


def pose7_to_mat(p: tuple[float, ...]) -> np.ndarray:
    """(x,y,z,qx,qy,qz,qw) -> 4x4 homogeneous."""
    T = np.eye(4)
    T[:3, :3] = Rotation.from_quat(p[3:7]).as_matrix()
    T[:3, 3] = p[0:3]
    return T


def transform_to_mat(tf) -> np.ndarray:
    """dimos Transform -> 4x4."""
    t, q = tf.translation, tf.rotation
    T = np.eye(4)
    T[:3, :3] = Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix()
    T[:3, 3] = [t.x, t.y, t.z]
    return T


@dataclass
class Section:
    frame_idx: int  # lidar scan index in the stream (also the RNG seed downstream)
    ts: float  # epoch seconds of the query scan
    body_pts: np.ndarray  # (N,3) float32, local submap re-anchored to query body frame
    T_true: np.ndarray  # (4,4) map_T_body — SILVER truth (PGO, ~6 cm floor)
    n_scans: int  # scans accumulated into the submap
    window_s: float  # submap time span
    reached_gate: bool  # accumulated >= MIN_LOCAL_POINTS (live gate) before scan cap


@dataclass
class Prepared:
    recording: str
    lidar_stream: str
    premap_pts: np.ndarray  # (M,3) float32, world_corrected ("map") frame
    sections: list[Section]
    n_keyframes: int
    n_loops: int
    pgo_seconds: float
    git_rev_dimos: str
    git_rev_trial: str
    created_unix: float
    notes: list[str] = field(default_factory=list)


def _git_rev(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def build_graph(store: SqliteStore, lidar_stream: str) -> tuple[PoseGraph, float]:
    t0 = time.perf_counter()
    graph = store.stream(lidar_stream, PointCloud2).transform(PGO()).last().data
    return graph, time.perf_counter() - t0


def build_premap(
    store: SqliteStore, lidar_stream: str, graph: PoseGraph, device: str
) -> np.ndarray:
    """PGO-corrected premap, mimicking `dimos map global --pgo` pass 2:
    spatially dedup frames (keep latest per 0.3 m cell of trajectory), apply
    the interpolated correction per frame, accumulate carve-OFF voxels."""
    kept: dict[tuple[int, int, int], object] = {}
    for obs in store.stream(lidar_stream, PointCloud2):
        p = obs.pose_tuple
        if p is None or (p[0] == 0 and p[1] == 0 and p[2] == 0):
            continue  # placeholder/unposed frames, same skip as PGO + CLI
        cell = tuple(int(np.floor(c / PREMAP_DEDUP_TOL_M)) for c in p[:3])
        kept[cell] = obs  # keep latest per cell (insertion order = time order)

    grid = VoxelGrid(voxel_size=VOXEL, carve_columns=False, frame_id="world",
                     device=device, show_startup_log=False)
    try:
        for obs in kept.values():
            corr = transform_to_mat(graph.correction_at(obs.ts))
            pts = obs.data.points_f32().astype(np.float64)
            pts = pts @ corr[:3, :3].T + corr[:3, 3]
            grid.add_frame(PointCloud2.from_numpy(pts, frame_id="world", timestamp=obs.ts))
        return grid.get_global_pointcloud2().points_f32().copy()
    finally:
        grid.dispose()


def build_sections(
    store: SqliteStore,
    lidar_stream: str,
    graph: PoseGraph,
    n_queries: int,
    device: str,
    max_window_scans: int = 200,
) -> tuple[list[Section], list[str]]:
    obs_all = [o for o in store.stream(lidar_stream, PointCloud2)]
    notes: list[str] = []
    n_total = len(obs_all)
    # Query frames evenly spaced (like #2137's 60-center pick), but NO frame is
    # excluded for being hard — full accounting is the point. Start high enough
    # that a trailing window exists at all (>= 5 scans of history).
    q_idx = np.unique(np.linspace(5, n_total - 1, n_queries).astype(int))

    sections: list[Section] = []
    for qi in q_idx:
        q_obs = obs_all[qi]
        if q_obs.pose_tuple is None:
            notes.append(f"frame {qi}: no pose — section skipped (recorded, not hidden)")
            continue
        grid = VoxelGrid(voxel_size=VOXEL, carve_columns=True, frame_id="world",
                         device=device, show_startup_log=False)
        try:
            n_scans, reached = 0, False
            for j in range(qi, max(-1, qi - max_window_scans), -1):
                grid.add_frame(obs_all[j].data)
                n_scans += 1
                if grid.size() >= MIN_LOCAL_POINTS:
                    reached = True
                    break
            world_pts = grid.get_global_pointcloud2().points_f32().astype(np.float64)
        finally:
            grid.dispose()

        # Re-anchor to the query frame's body frame: a kidnapped robot knows its
        # cloud only relative to itself, never in the recording's world frame.
        P = pose7_to_mat(q_obs.pose_tuple)  # world_raw_T_body
        Pinv = np.linalg.inv(P)
        body_pts = (world_pts @ Pinv[:3, :3].T + Pinv[:3, 3]).astype(np.float32)

        C = transform_to_mat(graph.correction_at(q_obs.ts))  # world_corrected <- world_raw
        T_true = C @ P  # map_T_body
        sections.append(Section(
            frame_idx=int(qi), ts=float(q_obs.ts), body_pts=body_pts,
            T_true=T_true.astype(np.float64), n_scans=n_scans,
            window_s=float(q_obs.ts - obs_all[max(0, j)].ts), reached_gate=reached,
        ))
        if not reached:
            notes.append(
                f"frame {qi}: only {len(body_pts)} pts after {n_scans} scans "
                f"(< live gate {MIN_LOCAL_POINTS}) — kept, flagged"
            )
    return sections, notes


def prepare(recording: str, lidar_stream: str, n_queries: int, device: str) -> Path:
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    trial_root = Path(__file__).resolve().parents[1]
    db = dimos_root / "data" / f"{recording}.db"
    store = SqliteStore(path=str(db), must_exist=True)
    with store:
        graph, pgo_s = build_graph(store, lidar_stream)
        # Serialize NOW: the first correction_at() call caches an unpicklable
        # closure on the (frozen) instance. Learned live.
        graph_bytes = pickle.dumps(graph)
        print(f"PGO: {len(graph.keyframes)} keyframes, {len(graph.loops)} loops, {pgo_s:.1f}s")
        premap = build_premap(store, lidar_stream, graph, device)
        print(f"premap: {len(premap)} voxel pts")
        sections, notes = build_sections(store, lidar_stream, graph, n_queries, device)
        print(f"sections: {len(sections)} (gate reached: {sum(s.reached_gate for s in sections)})")
        for n in notes:
            print(f"  note: {n}")

    # Plain dicts on purpose: pickled __main__-defined classes can't be read
    # by any other script (learned live). Arrays stay numpy.
    prepared = dict(
        recording=recording, lidar_stream=lidar_stream, premap_pts=premap,
        # The exact graph that defined truth + premap — markers.py MUST reuse
        # it (PGO wobbles ~6 cm run-to-run; two runs = two different frames).
        pose_graph_bytes=graph_bytes,
        sections=[asdict(s) for s in sections],
        n_keyframes=len(graph.keyframes), n_loops=len(graph.loops),
        pgo_seconds=pgo_s, git_rev_dimos=_git_rev(dimos_root),
        git_rev_trial=_git_rev(trial_root), created_unix=time.time(), notes=notes,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{recording}.pkl"
    with open(out, "wb") as f:
        pickle.dump(prepared, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"wrote {out} (dimos {prepared['git_rev_dimos']}, trial {prepared['git_rev_trial']})")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--lidar-stream", default="lidar")
    ap.add_argument("--n-queries", type=int, default=24)
    ap.add_argument("--device", default="CUDA:0", help="VoxelGrid accumulation device only")
    a = ap.parse_args()
    prepare(a.recording, a.lidar_stream, a.n_queries, a.device)
