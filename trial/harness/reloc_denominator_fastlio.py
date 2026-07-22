#!/usr/bin/env python3
"""Denominator sweep: real dimos relocalize() on real dimos-VoxelGrid world-frame
CUMULATIVE submaps at N drive positions, scored vs PGO truth. Same faithfulness
contract as reloc_probe_fastlio.py (dimos VoxelGrid submap + dimos relocalize;
zero re-implementation) -- this just walks several query positions to get a
success RATE, the number the live replay is too slow to accumulate.

The live RelocalizationModule reads VoxelGridMapper.global_map, which is the FULL
cumulative voxel map. So each query's local_map is the cumulative map from drive
start up to that query's time -- we snapshot the SAME grid as it grows past a set
of scan-count checkpoints (one persistent grid, add_frame forward, snapshot when
crossing each checkpoint), exactly the live cumulative semantics.

Run: uv run --project <dimos> python reloc_denominator_fastlio.py \
        --premap <name>.pc2.lcm --checkpoints 50000 100000 150000 200000 300000
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
import open3d as o3d

from dimos.mapping.relocalization.relocalize import relocalize
from dimos.mapping.voxels import VoxelGrid
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

TRIAL = Path(__file__).resolve().parents[1]
DIMOS = TRIAL.parent / "dimos"
REC = "recording_go2_mid360_2026-05-29_4-45pm-PST"
FASTLIO_DB = "recording_go2_mid360_2026-05-29_4-45pm-PST.fastlio"
SUCCESS_T_M, SUCCESS_R_DEG = 1.0, 15.0
PREMAP_DIR = TRIAL / "harness" / "out" / "rehearsal_mid360"
OUT = TRIAL / "harness" / "out" / "results_dimos"


def _geodesic_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    cos = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def snapshots(store: SqliteStore, checkpoints: list[int], device: str):
    """One persistent VoxelGrid (0.05 m carve ON world) grown forward; snapshot
    its cloud + latest ts each time size first crosses a checkpoint."""
    grid = VoxelGrid(voxel_size=0.05, carve_columns=True, frame_id="world",
                     device=device, show_startup_log=False)
    cps = sorted(checkpoints)
    out = []
    try:
        ci = 0
        last_ts = 0.0
        for obs in store.stream("lidar", PointCloud2):
            grid.add_frame(obs.data)
            last_ts = float(obs.ts)
            while ci < len(cps) and grid.size() >= cps[ci]:
                pts = np.asarray(grid.get_global_pointcloud2().pointcloud.points).copy()
                cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
                out.append((cps[ci], cloud, last_ts, len(pts)))
                ci += 1
            if ci >= len(cps):
                break
        return out
    finally:
        grid.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--premap", required=True, help="premap filename under rehearsal_mid360")
    ap.add_argument("--checkpoints", type=int, nargs="+",
                    default=[50_000, 100_000, 150_000, 200_000, 300_000])
    ap.add_argument("--device", default="CPU:0")
    a = ap.parse_args()

    graph = pickle.loads(pickle.loads(
        (TRIAL / "harness" / "out" / "prepared" / f"{REC}.fastlio.pkl").read_bytes()
    )["pose_graph_bytes"])
    premap = PointCloud2.lcm_decode((PREMAP_DIR / a.premap).read_bytes())
    npm = len(premap.pointcloud.points)
    print(f"[premap] {a.premap} pts={npm}", flush=True)

    store = SqliteStore(path=str(DIMOS / "data" / f"{FASTLIO_DB}.db"), must_exist=True)
    with store:
        snaps = snapshots(store, a.checkpoints, a.device)
    print(f"[submaps] {len(snaps)} cumulative snapshots built", flush=True)

    rows = []
    for cp, cloud, ts, n_pts in snaps:
        truth = graph.correction_at(ts).to_matrix()
        t0 = time.perf_counter()
        try:
            T, fitness = relocalize(premap.pointcloud, cloud)
            dt = time.perf_counter() - t0
            err_t = float(np.linalg.norm(T[:3, 3] - truth[:3, 3]))
            err_r = _geodesic_deg(T[:3, :3], truth[:3, :3])
            ok = bool(err_t < SUCCESS_T_M and err_r < SUCCESS_R_DEG)
            row = {"checkpoint": cp, "n_pts": n_pts, "ts": ts, "time_s": dt,
                   "fitness": float(fitness), "err_t_m": err_t, "err_r_deg": err_r,
                   "place_correct": ok, "accept": bool(fitness >= 0.45),
                   "reloc_t": T[:3, 3].round(3).tolist(),
                   "truth_t": truth[:3, 3].round(3).tolist()}
        except Exception as e:  # noqa: BLE001 -- diagnostic boundary
            row = {"checkpoint": cp, "n_pts": n_pts, "ts": ts,
                   "time_s": time.perf_counter() - t0, "error": f"{type(e).__name__}: {e}"}
        rows.append(row)
        print(f"[cp {cp}] n_pts={n_pts} " + (
            f"time={row['time_s']:.1f}s fit={row['fitness']:.3f} "
            f"err_t={row['err_t_m']:.3f}m err_r={row['err_r_deg']:.2f}deg "
            f"place_correct={row['place_correct']} accept={row['accept']}"
            if "error" not in row else f"ERROR {row['error']}"), flush=True)

    judged = [r for r in rows if "err_t_m" in r]
    ok_place = [r for r in judged if r["place_correct"]]
    accepts = [r for r in judged if r["accept"]]
    summary = {
        "recording": FASTLIO_DB, "premap": a.premap, "premap_pts": npm,
        "n_queries": len(rows), "n_judged": len(judged),
        "n_place_correct": len(ok_place), "n_accept_gate": len(accepts),
        "place_correct_rate": (len(ok_place) / len(judged)) if judged else None,
        "median_err_t_m": float(np.median([r["err_t_m"] for r in judged])) if judged else None,
        "median_time_s": float(np.median([r["time_s"] for r in judged])) if judged else None,
        "success_gate": {"err_t_m": SUCCESS_T_M, "err_r_deg": SUCCESS_R_DEG},
        "rows": rows,
    }
    outp = OUT / f"{FASTLIO_DB}.reloc_denominator.json"
    outp.write_text(json.dumps(summary, indent=2))
    print(f"[summary] place_correct={len(ok_place)}/{len(judged)} "
          f"accepts={len(accepts)}/{len(judged)} median_err_t="
          f"{summary['median_err_t_m']}m -> {outp}", flush=True)


if __name__ == "__main__":
    main()
