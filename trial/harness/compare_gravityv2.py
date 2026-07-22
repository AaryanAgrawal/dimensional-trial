#!/usr/bin/env python3
"""Before/after comparison for the v2 gravity-consistency gate on stairs1 fastlio.

BEFORE = archived mid360_gir_stairs1_20260601.ransac.json (map_up=None, world-z gate).
AFTER  = mid360_gir_stairs1_20260601.ransac_gravityv2.json (map_up threaded, consistency gate).
The RANSAC candidate pool is byte-identical between the two (generate_ransac_candidates
unchanged c6415c9b9->9ec95de0c, same per-frame seeds), so every delta is the gate alone.

Regime labels re-derived from the BEFORE winner's z-axis tilt arccos(R[2,2]):
  gate-hijack   = failure with est forced ~upright (1-11 deg) while truth ~47-56 deg
  scene-ambig   = failure with est tilted/flipped (29-149 deg) -- not a gate problem
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
RES = HARNESS / "out" / "results"
BEFORE = RES / "mid360_gir_stairs1_20260601.ransac.json"
AFTER = RES / "mid360_gir_stairs1_20260601.ransac_gravityv2.json"

GATE_HIJACK = {5, 1717, 3429, 3857, 4713, 5141, 5569, 6853}
SCENE_AMBIG = {861, 1289, 2145, 4285, 5997, 6425}
SUCCESS0 = {433, 2573, 3001, 7282}


def regime(fx: int) -> str:
    if fx in GATE_HIJACK:
        return "gate-hijack"
    if fx in SCENE_AMBIG:
        return "scene-ambig"
    if fx in SUCCESS0:
        return "success"
    return "?"


def zaxis_tilt_deg(T: list) -> float:
    """Old world-z gate metric: angle of the candidate body-z from world-z."""
    r22 = np.asarray(T)[2, 2]
    return float(np.degrees(np.arccos(np.clip(float(r22), -1.0, 1.0))))


def main() -> int:
    b = {r["frame_idx"]: r for r in json.loads(BEFORE.read_text())["results"]}
    bsum = json.loads(BEFORE.read_text())["summary"]
    ad = json.loads(AFTER.read_text())
    a = {r["frame_idx"]: r for r in ad["results"]}
    asum = ad["summary"]

    print(f"BEFORE {BEFORE.name}: {bsum['gravity_gate'] if 'gravity_gate' in bsum else 'world-z (map_up=None)'}"
          f"  dimos_code={bsum.get('git_rev_dimos_code', bsum['git_rev_dimos'])}")
    print(f"AFTER  {AFTER.name}: {asum.get('gravity_gate')}  map_up={asum.get('map_up')}"
          f"  dimos_code={asum.get('git_rev_dimos_code')}")
    print(f"truth: {asum.get('truth')}\n")

    n = len(b)
    b_succ = sum(b[fx].get("success", False) for fx in b)
    a_succ = sum(a[fx].get("success", False) for fx in a)
    print(f"OVERALL success: BEFORE {b_succ}/{n} = {100*b_succ/n:.1f}%   "
          f"AFTER {a_succ}/{n} = {100*a_succ/n:.1f}%   (delta {a_succ-b_succ:+d})\n")

    hdr = ("regime", "frame", "b_succ", "a_succ", "flip", "b_err_t", "a_err_t",
           "b_err_r", "a_err_r", "b_ztilt", "a_ztilt", "b_fit", "a_fit", "a_status")
    print("{:<12}{:>6}{:>7}{:>7}{:>6}{:>9}{:>9}{:>9}{:>9}{:>9}{:>9}{:>7}{:>7}{:>10}".format(*hdr))

    trans: dict[str, dict[str, int]] = {}
    for fx in sorted(b):
        reg = regime(fx)
        br, ar = b[fx], a[fx]
        bs = bool(br.get("success", False))
        as_ = bool(ar.get("success", False))
        flip = "  ->OK" if (as_ and not bs) else ("->FAIL" if (bs and not as_) else "  same")
        astatus = ar.get("status", "?")
        b_zt = zaxis_tilt_deg(br["T_est"]) if "T_est" in br else float("nan")
        a_zt = zaxis_tilt_deg(ar["T_est"]) if "T_est" in ar and astatus == "ok" else float("nan")
        print("{:<12}{:>6}{:>7}{:>7}{:>6}{:>9.2f}{:>9.2f}{:>9.2f}{:>9.2f}{:>9.1f}{:>9.1f}{:>7.3f}{:>7.3f}{:>10}".format(
            reg, fx, "Y" if bs else "n", "Y" if as_ else "n", flip.strip(),
            br.get("err_t", float("nan")), ar.get("err_t", float("nan")),
            br.get("err_r", float("nan")), ar.get("err_r", float("nan")),
            b_zt, a_zt, br.get("fitness", float("nan")), ar.get("fitness", float("nan")),
            astatus))
        key = f"{'S' if bs else 'F'}->{'S' if as_ else 'F'}"
        trans.setdefault(reg, {}).setdefault(key, 0)
        trans[reg][key] += 1

    print("\n=== per-regime transition (S=success, F=fail) ===")
    for reg in ("success", "gate-hijack", "scene-ambig"):
        d = trans.get(reg, {})
        tot = sum(d.values())
        cells = "  ".join(f"{k}:{v}" for k, v in sorted(d.items()))
        print(f"  {reg:<12} n={tot:2d}   {cells}")

    hij = sorted(GATE_HIJACK)
    flipped = [fx for fx in hij if a[fx].get("success") and not b[fx].get("success")]
    print(f"\ngate-hijack flipped to success: {len(flipped)}/8  {flipped}")
    not_flipped = [fx for fx in hij if not (a[fx].get("success") and not b[fx].get("success"))]
    if not_flipped:
        print(f"gate-hijack NOT rescued: {not_flipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
