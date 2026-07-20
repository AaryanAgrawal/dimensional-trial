#!/usr/bin/env python3
"""Stratified scorecard numbers for the flashdrive bench runs.

Reads out/prepared/<rec>.pkl (submap sizes, T_true) + out/results/<rec>.<cfg>.json
and prints, per config: full-denominator success, median err on successes,
solve-time, success STRATIFIED by the 50k live gate (reached_gate flag), and
fitness-gate (0.45) pass count. For the ransac config it also computes the tilt
smoking-gun: tilt(T_true) vs tilt(T_est) — on the mid360 tilted lane the correct
answer is ~50 deg tilted, every accepted answer is forced <=10 deg by
GRAVITY_TILT_MAX_DEG.

Determinism/truth labels come from the JSONs themselves (seeds=frame_idx, PGO
silver ~6 cm floor, decimetre+ on long accumulation). No recompute of poses.

Run: cd dimos && uv run python ../trial/harness/fd_analyze.py <rec> <cfg> [<cfg> ...]
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

H = Path(__file__).resolve().parent
GATE = 50_000  # MIN_LOCAL_POINTS
FIT_GATE = 0.45


def tilt_deg(T) -> float:
    T = np.asarray(T)
    z = T[:3, :3] @ np.array([0.0, 0.0, 1.0])
    return float(np.degrees(np.arccos(np.clip(z[2], -1.0, 1.0))))


def main() -> int:
    rec = sys.argv[1]
    cfgs = sys.argv[2:]
    with open(H / "out" / "prepared" / f"{rec}.pkl", "rb") as f:
        prep = pickle.load(f)
    secs = {s["frame_idx"]: s for s in prep["sections"]}
    npts = np.array([len(s["body_pts"]) for s in prep["sections"]])
    reached = np.array([bool(s["reached_gate"]) for s in prep["sections"]])
    print(f"=== {rec} ===")
    print(f"sections={len(secs)}  reached_gate(>=50k)={int(reached.sum())}/{len(reached)}  "
          f"submap pts: min={npts.min()} p25={int(np.percentile(npts,25))} "
          f"med={int(np.median(npts))} p75={int(np.percentile(npts,75))} max={npts.max()}")
    print(f"PGO: keyframes={prep['n_keyframes']} loops={prep['n_loops']} "
          f"pgo_s={prep['pgo_seconds']:.1f}  premap={len(prep['premap_pts'])} pts  "
          f"dimos={prep['git_rev_dimos']} trial={prep['git_rev_trial']}")

    for cfg in cfgs:
        p = H / "out" / "results" / f"{rec}.{cfg}.json"
        if not p.exists():
            print(f"\n-- {cfg}: NO RESULTS FILE --")
            continue
        d = json.load(open(p))
        rs = d["results"]
        n = len(rs)
        ok = [r for r in rs if r["status"] == "ok"]
        succ = [r for r in ok if r.get("success")]
        crashed = sum(r["status"] == "crashed" for r in rs)
        noc = sum(r["status"] == "no_candidates" for r in rs)
        acc = sum(r.get("accepted_at_gate", False) for r in ok)
        acc_wrong = sum(r.get("accepted_at_gate", False) and not r.get("success") for r in ok)
        print(f"\n-- {cfg}: success {len(succ)}/{n} = {100*len(succ)/n:.1f}% (full denom) "
              f"| ok={len(ok)} crashed={crashed} no_cand={noc}")
        if succ:
            et = np.array([r["err_t"] for r in succ])
            print(f"   median err_t (successes) = {np.median(et):.3f} m  (n={len(succ)})")
        if ok:
            dt = np.array([r["dt"] for r in rs])
            print(f"   solve dt: med={np.median(dt):.2f}s p90={np.percentile(dt,90):.2f}s")
            print(f"   fitness>=0.45 (would-accept): {acc}/{len(ok)}  "
                  f"of which WRONG (accept but fail): {acc_wrong}")
        for label, want in [("reached>=50k", True), ("below-gate", False)]:
            sel = [r for r in rs if secs[r["frame_idx"]]["reached_gate"] == want]
            sn = sum(r.get("success", False) for r in sel)
            if sel:
                print(f"   [{label:>12}] {sn}/{len(sel)} succeed")
        if cfg == "ransac":
            tt = np.array([tilt_deg(secs[r["frame_idx"]]["T_true"]) for r in ok])
            te = np.array([tilt_deg(r["T_est"]) for r in ok if "T_est" in r])
            if len(tt) and len(te):
                print(f"   TILT: truth(T_true) med={np.median(tt):.1f} deg "
                      f"[{tt.min():.1f}-{tt.max():.1f}]  |  "
                      f"est(T_est) med={np.median(te):.1f} deg [{te.min():.1f}-{te.max():.1f}]  "
                      f"<=10deg est: {int((te<=10).sum())}/{len(te)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
