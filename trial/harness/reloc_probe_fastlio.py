#!/usr/bin/env python3
"""Offline diagnostic: call the REAL dimos relocalize() on a REAL dimos-VoxelGrid
world-frame submap, and report the returned transform's error vs PGO truth --
including for REJECTS (fitness < gate), which the live replay never publishes.

Why offline: the shipped RelocalizationModule publishes world->map ONLY on
accept (fitness >= 0.45); a rejected solve emits no TF, so `dimos --replay`
capture can't tell us WHERE a low-fitness fastlio solve actually landed. And on
this large outdoor scene each live reloc call runs many minutes, so a full
per-2s denominator isn't reachable in a replay window. This probe uses the SAME
two dimos objects the live pipeline does -- VoxelGrid (frame_id='world', the
gravity-preserving accumulator) to build the submap, and
mapping.relocalization.relocalize.relocalize() to solve -- just driven directly
so we can read the transform + time it, across premap densities. It re-implements
NOTHING: the submap is dimos's world-frame voxel accumulation (never a body-frame
re-anchor), the solve is dimos's shipped RANSAC+ICP.

Truth: fastlio-lane PGO graph (prepared pkl, re-posed from fastlio_odometry).
relocalize() returns T = map_T_world_raw (scan_in_map = T @ scan_raw); truth =
correction_at(t) = map_T_world_raw. err_t/err_r compare them directly. The
success gate matches score_replay.py: err_t<1m AND err_r<15deg.

Run: uv run --project <dimos> python reloc_probe_fastlio.py
"""

from __future__ import annotations

import argparse
import pickle
import time
from pathlib import Path

import numpy as np

from dimos.mapping.relocalization.relocalize import relocalize
from dimos.mapping.voxels import VoxelGrid
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

TRIAL = Path(__file__).resolve().parents[1]
DIMOS = TRIAL.parent / "dimos"
REC = "recording_go2_mid360_2026-05-29_4-45pm-PST"  # score/truth key = the base (fastlio pkl)
FASTLIO_DB = "recording_go2_mid360_2026-05-29_4-45pm-PST.fastlio"  # lidar==fastlio_lidar
MIN_LOCAL_POINTS = 50_000  # module.py live gate
SUCCESS_T_M, SUCCESS_R_DEG = 1.0, 15.0
PREMAP_DIR = TRIAL / "harness" / "out" / "rehearsal_mid360"


def _geodesic_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    cos = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def build_cumulative_submap(store: SqliteStore, target_pts: int, device: str,
                            start_after: int = 0):
    """Accumulate fastlio_lidar clouds from `start_after` into ONE persistent
    VoxelGrid (0.05 m, carve ON, world frame) -- exactly VoxelGridMapper's live
    global_map -- until it reaches target_pts. Returns (o3d cloud, last_ts, n_scans)."""
    grid = VoxelGrid(voxel_size=0.05, carve_columns=True, frame_id="world",
                     device=device, show_startup_log=False)
    last_ts, n = 0.0, 0
    try:
        for i, obs in enumerate(store.stream("lidar", PointCloud2)):
            if i < start_after:
                continue
            grid.add_frame(obs.data)
            last_ts, n = float(obs.ts), n + 1
            if grid.size() >= target_pts:
                break
        pc2 = grid.get_global_pointcloud2()
        cloud = pc2.pointcloud
        # copy points out before disposing the grid-backed cloud
        pts = np.asarray(cloud.points).copy()
        import open3d as o3d
        out = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
        return out, last_ts, n, len(pts)
    finally:
        grid.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--premaps", nargs="+",
                    default=[f"{FASTLIO_DB}.v30.pc2.lcm", f"{FASTLIO_DB}.v10.pc2.lcm",
                             f"{FASTLIO_DB}.pc2.lcm"],
                    help="premap filenames under rehearsal_mid360, coarse->dense")
    ap.add_argument("--target-pts", type=int, default=MIN_LOCAL_POINTS)
    ap.add_argument("--device", default="CPU:0")
    a = ap.parse_args()

    graph = pickle.loads(pickle.loads(
        (TRIAL / "harness" / "out" / "prepared" / f"{REC}.fastlio.pkl").read_bytes()
    )["pose_graph_bytes"])

    store = SqliteStore(path=str(DIMOS / "data" / f"{FASTLIO_DB}.db"), must_exist=True)
    with store:
        t0 = time.perf_counter()
        submap, last_ts, n_scans, n_pts = build_cumulative_submap(
            store, a.target_pts, a.device)
        print(f"[submap] dimos VoxelGrid world-frame cumulative: {n_pts} pts from "
              f"{n_scans} fastlio scans, last_ts={last_ts:.2f}, "
              f"build={time.perf_counter()-t0:.1f}s", flush=True)

    truth = graph.correction_at(last_ts).to_matrix()  # map_T_world_raw at query pos
    print(f"[truth] correction_at({last_ts:.2f}) trans={truth[:3,3].round(3).tolist()} "
          f"(PGO-silver; early-drive correction ~identity)", flush=True)

    for name in a.premaps:
        path = PREMAP_DIR / name
        if not path.exists():
            print(f"[premap {name}] MISSING", flush=True)
            continue
        premap = PointCloud2.lcm_decode(path.read_bytes())
        npm = len(premap.pointcloud.points)
        t0 = time.perf_counter()
        try:
            T, fitness = relocalize(premap.pointcloud, submap)
            dt = time.perf_counter() - t0
            err_t = float(np.linalg.norm(T[:3, 3] - truth[:3, 3]))
            err_r = _geodesic_deg(T[:3, :3], truth[:3, :3])
            gate = "ACCEPT" if fitness >= 0.45 else "reject"
            ok = err_t < SUCCESS_T_M and err_r < SUCCESS_R_DEG
            print(f"[premap {name}] pts={npm} time={dt:.1f}s fitness={fitness:.3f}({gate}) "
                  f"err_t={err_t:.3f}m err_r={err_r:.2f}deg place_correct={ok} "
                  f"reloc_t={T[:3,3].round(3).tolist()}", flush=True)
        except Exception as e:  # noqa: BLE001 -- diagnostic boundary, log & continue
            print(f"[premap {name}] pts={npm} FAILED after "
                  f"{time.perf_counter()-t0:.1f}s: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
