#!/usr/bin/env python3
"""Score a dimos-native replay benchmark run against PGO silver truth.

Consumes out/results_dimos/<rec>.replay.json (from replay_bench.py) -- the REAL
pipeline's published world->map fixes -- and grades each against the recording's
PGO correction at that drive-moment.

Truth model (silver, ~6 cm floor):
  correction_at(t) = world_corrected("map") <- world_raw = map_T_world, from the
  recording's own PGO graph (PoseGraph.correction_at, real dimos). Replay's world
  frame IS the recording's world_raw (odom origin), so a published world->map fix
  is directly comparable. The graph is reused from the prepared pickle when
  present (the same graph prior truth work used); its "map" frame and the replay
  premap's "map" frame are INDEPENDENT PGO runs, so a perfect fix still shows the
  inter-run wobble (~6 cm village-scale) -- the floor every error inherits.

Per fix (map_T_world both sides):
  est = inv(world_map_fix)   [world_map_fix = world_T_map captured off /tf]
  err_t = || est[:3,3] - truth[:3,3] ||                       (meters)
  err_r = geodesic angle( est[:3,:3], truth[:3,:3] )          (degrees)
  success = err_t < 1.0 m AND err_r < 15 deg
Fixes with no captured rotation (rare) are scored on translation only (err_t via
the logged reloc_t = map_T_world translation); their success is left undetermined.

Run: uv run --project /home/dimos/dimensional-trial/dimos \
        python ../trial/harness/score_replay.py hk_village3
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np

from dimos.mapping.loop_closure.pgo import PGO
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

DIMOS_ROOT = Path(__file__).resolve().parents[2] / "dimos"
RESULTS_DIR = Path(__file__).parent / "out" / "results_dimos"
PREPARED_DIR = Path(__file__).parent / "out" / "prepared"

SUCCESS_T_M = 1.0
SUCCESS_R_DEG = 15.0
NPTS_BINS = [0, 60_000, 80_000, 100_000, np.inf]  # submap-size strata (voxel count)


def _load_graph(recording: str, lidar_stream: str):
    """Reuse the prepared pickle's PGO graph if present (same graph prior truth
    work used); else build it fresh the way prep.py does -- real dimos PGO over
    the recording's lidar stream. correction_at() is what defines truth."""
    pkl = PREPARED_DIR / f"{recording}.pkl"
    if pkl.exists():
        d = pickle.loads(pkl.read_bytes())
        if "pose_graph_bytes" in d:
            return pickle.loads(d["pose_graph_bytes"]), f"prepared/{recording}.pkl"
    db = DIMOS_ROOT / "data" / f"{recording}.db"
    store = SqliteStore(path=str(db), must_exist=True)
    with store:
        graph = store.stream(lidar_stream, PointCloud2).transform(PGO()).last().data
        pickle.dumps(graph)  # force the correction_at closure before the store closes
    return graph, f"fresh PGO({recording}.{lidar_stream})"


def _geodesic_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    """Angle of Ra^T Rb about its axis, in degrees. clip guards acos domain."""
    cos = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def _score_fix(fix: dict, truth: np.ndarray) -> dict:
    truth_t = truth[:3, 3]
    if fix["world_map_fix"] is not None:
        est = np.linalg.inv(np.asarray(fix["world_map_fix"]))  # map_T_world
        err_t = float(np.linalg.norm(est[:3, 3] - truth_t))
        err_r = _geodesic_deg(est[:3, :3], truth[:3, :3])
        success = bool(err_t < SUCCESS_T_M and err_r < SUCCESS_R_DEG)
    else:
        err_t = float(np.linalg.norm(np.asarray(fix["reloc_t"]) - truth_t))
        err_r, success = None, None  # rotation not captured -> undetermined
    return {"err_t_m": err_t, "err_r_deg": err_r, "success": success}


def _summarize(rows: list[dict]) -> dict:
    """Medians over successes; None-safe. Every number keeps its unit in the key."""
    def med(xs: list[float]) -> float | None:
        return float(np.median(xs)) if xs else None

    judged = [r for r in rows if r["success"] is not None]
    succ = [r for r in judged if r["success"]]
    return {
        "n": len(rows),
        "n_judged": len(judged),  # both err_t and err_r available
        "n_success": len(succ),
        "success_rate": (len(succ) / len(judged)) if judged else None,
        "median_err_t_m_on_success": med([r["err_t_m"] for r in succ]),
        "median_err_r_deg_on_success": med([r["err_r_deg"] for r in succ if r["err_r_deg"] is not None]),
        "median_err_t_m_all": med([r["err_t_m"] for r in rows]),
        "min_err_t_m": (min(r["err_t_m"] for r in rows) if rows else None),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    a = ap.parse_args()

    in_path = RESULTS_DIR / f"{a.recording}.replay.json"
    data = json.loads(in_path.read_text())
    meta, fixes = data["meta"], data["fixes"]
    graph, graph_src = _load_graph(a.recording, "lidar")
    rec_first = meta["recording_first_ts"]

    scored: list[dict] = []
    for f in fixes:
        truth = graph.correction_at(f["ts"]).to_matrix()  # map_T_world at drive-moment
        s = _score_fix(f, truth)
        scored.append({
            "ts": f["ts"],
            "t_since_drive_start_s": f["ts"] - rec_first,
            "n_pts": f["n_pts"],
            "fitness": f["fitness"],
            "source": f["source"],
            "rot_source": f["rot_source"],
            "truth_t_m": truth[:3, 3].round(4).tolist(),
            **s,
        })

    strata = []
    for lo, hi in zip(NPTS_BINS[:-1], NPTS_BINS[1:]):
        band = [r for r in scored if lo <= r["n_pts"] < hi]
        summ = _summarize(band)
        summ["n_pts_band"] = f"[{lo:,}..{'inf' if hi == np.inf else format(int(hi), ',')})"
        strata.append(summ)

    first_success = next((r for r in scored if r["success"]), None)
    report = {
        "recording": a.recording,
        "graph_source": graph_src,
        "premap": meta.get("premap"),
        "n_accepts": len(scored),
        "n_rejects": meta.get("n_rejects"),
        "success_gate": {"err_t_m": SUCCESS_T_M, "err_r_deg": SUCCESS_R_DEG},
        "overall": _summarize(scored),
        "first_accept_t_since_drive_start_s": scored[0]["t_since_drive_start_s"] if scored else None,
        "first_success_t_since_drive_start_s": (
            first_success["t_since_drive_start_s"] if first_success else None),
        "n_with_rotation": sum(1 for r in scored if r["err_r_deg"] is not None),
        "by_submap_size": strata,
        "truth_floor_note": (
            "PGO silver truth wobbles run-to-run and the wobble GROWS along the "
            "drive: independent village3 PGO (prepared vs fresh, both 220kf/12loops) "
            "agree ~0.04 m early but diverge to ~0.44 m by drive-end. So late "
            "absolute err_t carries ~+-0.4 m truth uncertainty; the >1 m late "
            "divergence here exceeds it. NOT a flat ~6 cm floor."),
        "fixes": scored,
    }
    out_path = RESULTS_DIR / f"{a.recording}.replay_score.json"
    out_path.write_text(json.dumps(report, indent=2))

    o = report["overall"]
    print(f"[score_replay] {a.recording}: graph={graph_src}")
    print(f"  accepts={report['n_accepts']} rejects={report['n_rejects']} "
          f"with_rotation={report['n_with_rotation']}")
    print(f"  success={o['n_success']}/{o['n_judged']} "
          f"rate={o['success_rate']} median_err_t={o['median_err_t_m_on_success']} m "
          f"median_err_r={o['median_err_r_deg_on_success']} deg  min_err_t={o['min_err_t_m']} m")
    print(f"  first_accept={report['first_accept_t_since_drive_start_s']:.1f}s "
          f"first_success={report['first_success_t_since_drive_start_s']}s (since drive start)")
    for s in strata:
        print(f"  n_pts {s['n_pts_band']:>22}: n={s['n']:2d} judged={s['n_judged']:2d} "
              f"success={s['n_success']:2d} rate={s['success_rate']} "
              f"med_err_t={s['median_err_t_m_on_success']} m")
    print(f"[score_replay] wrote {out_path}")


if __name__ == "__main__":
    main()
