#!/usr/bin/env python3
"""Deterministic relocalization benchmark over prepared sections.

Scores a relocalization config on every section prep.py produced, with the
published fitness captured per attempt — the raw material for the confidence
analysis (analyze.py). Determinism recipe is #2137's, replicated exactly:
OMP single-thread BEFORE open3d import, per-frame seeds = frame_idx, sorted
order, fork workers. Two #2137 defects are deliberately NOT replicated:
  - no frame is excluded, ever ("hard" frames are the interesting ones);
  - the denominator is ALL sections — crashed or over-budget attempts count
    as failures, so a slow-but-lucky config can't inflate its success rate.

Configs:
  ransac           today's stack: relocalize() (RANSAC pool -> shared judge)
  ransac+lastpose  Phase 1 flag: + LastPosePrior carrying the last ACCEPTED
                   answer (sequential by nature — tracking, not kidnap)
  ransac+fiducial  Phase 2: + FiducialPrior (needs --fiducial-fixes from
                   markers.py; age-gated, judged like everything else)
  fiducial+judge   fiducial candidates alone through the judge (no RANSAC):
                   what markers buy when the search is skipped entirely

Run: cd dimos && uv run python ../trial/harness/run_bench.py hk_village3 --config ransac
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")  # BEFORE open3d import (determinism)

import argparse
import json
import pickle
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial.transform import Rotation

HARNESS = Path(__file__).parent
OUT_DIR = HARNESS / "out" / "results"

SUCCESS_T_M = 1.0  # #2137-comparable success bar
SUCCESS_R_DEG = 15.0
FITNESS_GATE = 0.45  # module.py accept threshold (code truth, not the docs' 0.6)

# Fork-inherited worker state (set in main before pool creation).
_PREMAP_PTS: np.ndarray | None = None
_SECTIONS: list | None = None
_CONFIG: str = "ransac"
_FIDUCIAL_FIXES: dict | None = None  # frame_idx -> list of candidate dicts

_worker_premap = None  # per-process o3d cloud cache


def _premap_cloud() -> o3d.geometry.PointCloud:
    global _worker_premap
    if _worker_premap is None:
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(_PREMAP_PTS.astype(np.float64))
        _worker_premap = pc
    return _worker_premap


def _to_cloud(pts: np.ndarray) -> o3d.geometry.PointCloud:
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(pts.astype(np.float64))
    return pc


def _errors(T: np.ndarray, T_true: np.ndarray) -> tuple[float, float]:
    err_t = float(np.linalg.norm(T[:3, 3] - T_true[:3, 3]))
    err_r = float(
        Rotation.from_matrix(T[:3, :3] @ T_true[:3, :3].T).magnitude() * 180.0 / np.pi
    )
    return err_t, err_r


def _seed(frame_idx: int) -> None:
    o3d.utility.random.seed(frame_idx)
    np.random.seed(frame_idx)
    random.seed(frame_idx)


def _fiducial_candidates(frame_idx: int):
    """Recorded marker fixes for this section -> Candidate list (may be [])."""
    from dimos.mapping.relocalization.priors import Candidate

    out = []
    for fx in (_FIDUCIAL_FIXES or {}).get(frame_idx, []):
        out.append(Candidate(T=np.asarray(fx["T"]), source="fiducial",
                             confidence=float(fx["confidence"])))
    return out


def _eval_one(i: int) -> dict:
    """Worker: one section, one attempt. Returns a flat, JSON-able dict."""
    from dimos.mapping.relocalization.priors import RansacPrior
    from dimos.mapping.relocalization.relocalize import refine_candidates, relocalize

    s = _SECTIONS[i]
    _seed(s.frame_idx)
    gm, lm = _premap_cloud(), _to_cloud(s.body_pts)

    t0 = time.perf_counter()
    try:
        source = "ransac"
        if _CONFIG == "ransac":
            T, fitness = relocalize(gm, lm)
        elif _CONFIG == "ransac+fiducial":
            from dimos.mapping.relocalization.relocalize import generate_ransac_candidates

            cands = _fiducial_candidates(s.frame_idx)
            pool = [c.T for c in cands] + generate_ransac_candidates(gm, lm)
            sources = ["fiducial"] * len(cands) + ["ransac"] * (len(pool) - len(cands))
            T, fitness, idx = refine_candidates(gm, lm, pool)
            source = sources[idx]
        elif _CONFIG == "fiducial+judge":
            cands = _fiducial_candidates(s.frame_idx)
            if not cands:
                return {"frame_idx": s.frame_idx, "status": "no_candidates",
                        "dt": time.perf_counter() - t0}
            T, fitness, idx = refine_candidates(gm, lm, [c.T for c in cands])
            source = "fiducial"
        else:
            raise ValueError(f"unknown config {_CONFIG}")
    except Exception as e:  # full accounting: crashes are results, not gaps
        return {"frame_idx": s.frame_idx, "status": "crashed", "error": repr(e),
                "dt": time.perf_counter() - t0}
    dt = time.perf_counter() - t0

    err_t, err_r = _errors(np.asarray(T), s.T_true)
    return {
        "frame_idx": s.frame_idx, "status": "ok", "err_t": err_t, "err_r": err_r,
        "fitness": float(fitness), "source": source, "dt": dt,
        "n_pts": int(len(s.body_pts)), "reached_gate": bool(s.reached_gate),
        "success": bool(err_t < SUCCESS_T_M and err_r < SUCCESS_R_DEG),
        "accepted_at_gate": bool(fitness >= FITNESS_GATE),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--config", default="ransac",
                    choices=["ransac", "ransac+lastpose", "ransac+fiducial", "fiducial+judge"])
    ap.add_argument("--fiducial-fixes", default=None,
                    help="JSON from markers.py: per-frame fiducial candidates")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()

    global _PREMAP_PTS, _SECTIONS, _CONFIG, _FIDUCIAL_FIXES
    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        prep_d = pickle.load(f)
    from types import SimpleNamespace
    prep = SimpleNamespace(**prep_d)
    _PREMAP_PTS = prep.premap_pts
    _SECTIONS = sorted((SimpleNamespace(**s) for s in prep.sections),
                       key=lambda s: s.frame_idx)
    _CONFIG = a.config
    if a.fiducial_fixes:
        with open(a.fiducial_fixes) as f:
            _FIDUCIAL_FIXES = {int(k): v for k, v in json.load(f).items()}

    print(f"bench: {a.recording} config={a.config} sections={len(_SECTIONS)} "
          f"premap={len(_PREMAP_PTS)} pts workers={a.workers} "
          f"dimos={prep.git_rev_dimos} trial={prep.git_rev_trial} "
          f"seeds=frame_idx OMP=1", flush=True)

    t0 = time.perf_counter()
    if a.config == "ransac+lastpose":
        # Sequential by design: the last-pose seed is temporal state.
        from dimos.mapping.relocalization.priors import (
            LastPosePrior, RansacPrior, relocalize_with_priors)
        results = []
        lp = LastPosePrior()
        gm = _to_cloud(_PREMAP_PTS)
        for s in _SECTIONS:
            _seed(s.frame_idx)
            t1 = time.perf_counter()
            try:
                T, fitness, source = relocalize_with_priors(
                    gm, _to_cloud(s.body_pts), [RansacPrior(), lp])
            except Exception as e:
                results.append({"frame_idx": s.frame_idx, "status": "crashed",
                                "error": repr(e), "dt": time.perf_counter() - t1})
                continue
            dt = time.perf_counter() - t1
            if fitness >= FITNESS_GATE:
                lp.update(np.asarray(T))  # module.py updates only on accept
            err_t, err_r = _errors(np.asarray(T), s.T_true)
            results.append({
                "frame_idx": s.frame_idx, "status": "ok", "err_t": err_t,
                "err_r": err_r, "fitness": float(fitness), "source": source,
                "dt": dt, "n_pts": int(len(s.body_pts)),
                "reached_gate": bool(s.reached_gate),
                "success": bool(err_t < SUCCESS_T_M and err_r < SUCCESS_R_DEG),
                "accepted_at_gate": bool(fitness >= FITNESS_GATE),
            })
            print(f"  frame {s.frame_idx}: {results[-1].get('err_t', -1):.3f}m "
                  f"fit={results[-1].get('fitness', -1):.3f} src={source} {dt:.1f}s",
                  flush=True)
    else:
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            results = list(pool.map(_eval_one, range(len(_SECTIONS))))

    wall = time.perf_counter() - t0
    ok = [r for r in results if r["status"] == "ok"]
    n_all = len(results)
    n_succ = sum(r.get("success", False) for r in ok)
    summary = {
        "recording": a.recording, "config": a.config,
        "n_sections": n_all, "n_ok": len(ok),
        "n_crashed": sum(r["status"] == "crashed" for r in results),
        "n_no_candidates": sum(r["status"] == "no_candidates" for r in results),
        # FULL denominator: failures of any kind stay in.
        "success_rate_all": n_succ / n_all if n_all else float("nan"),
        "median_err_t_ok": float(np.median([r["err_t"] for r in ok])) if ok else None,
        "median_dt": float(np.median([r["dt"] for r in results])),
        "wall_seconds": wall,
        "truth": "PGO silver (~6 cm run-to-run floor, see WORKSPACE §7)",
        "rung": "replay (real recorded sensor data, offline)",
        "git_rev_dimos": prep.git_rev_dimos, "git_rev_trial": prep.git_rev_trial,
        "command": " ".join(sys.argv), "seeds": "per-frame frame_idx; OMP_NUM_THREADS=1",
        "unix": time.time(),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{a.recording}.{a.config.replace('+', '_')}.json"
    with open(out, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1)
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
