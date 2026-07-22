#!/usr/bin/env python3
"""Pre-flight for the v2 gravity-consistency gate on stairs1 fastlio.

Cheap (no RANSAC): for every prepared section, estimate the premap up-axis and
the per-section submap up-axis exactly as refine_candidates(map_up=...) would,
then measure whether the CORRECT transform T_true passes the new consistency
gate (residual between T_true@submap_up and map_up). If T_true's residual is
~0 the fix's precondition holds (the gate would accept the correct answer); if
it is large the submap-up estimate is failing and the fix cannot rescue that
section. Also reports the OLD world-z gate tilt of T_true (what got it rejected
before) and of the baseline winning estimate (why the hijack won).
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import open3d as o3d

from dimos.mapping.relocalization.relocalize import (
    WORLD_UP,
    _gravity_tilt_deg,
    estimate_map_up,
)

HARNESS = Path(__file__).parent
PKL = HARNESS / "out" / "prepared" / "mid360_gir_stairs1_20260601.pkl"
BASELINE = HARNESS / "out" / "results" / "mid360_gir_stairs1_20260601.ransac.json"

# regime labels re-derived from baseline T_est R[2,2] tilt (see task diagnosis)
GATE_HIJACK = {5, 1717, 3429, 3857, 4713, 5141, 5569, 6853}
SCENE_AMBIG = {861, 1289, 2145, 4285, 5997, 6425}
SUCCESS = {433, 2573, 3001, 7282}


def cloud(pts: np.ndarray) -> o3d.geometry.PointCloud:
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(np.asarray(pts, dtype=np.float64))
    return pc


def tilt_from_worldz(v: np.ndarray) -> float:
    v = v / np.linalg.norm(v)
    return float(np.degrees(np.arccos(np.clip(abs(float(v @ WORLD_UP)), -1.0, 1.0))))


def main() -> int:
    with open(PKL, "rb") as f:
        prep = pickle.load(f)
    sections = {s["frame_idx"]: s for s in prep["sections"]}
    baseline = {r["frame_idx"]: r for r in json.loads(BASELINE.read_text())["results"]}

    premap = cloud(prep["premap_pts"])
    map_up = estimate_map_up(premap)
    print(f"premap: {len(prep['premap_pts'])} pts  map_up={map_up.round(4).tolist()}  "
          f"tilt_off_worldz={tilt_from_worldz(map_up):.2f} deg")
    print(f"dimos_rev={prep.get('git_rev_dimos')}  n_sections={len(sections)}")
    print()
    hdr = ("regime", "frame", "n_pts", "submap_up_tilt", "truth_tilt", "Ttrue_resid",
           "Ttrue_oldgate", "est_oldgate", "est_resid", "base_err_t")
    print("{:<13}{:>6}{:>8}{:>16}{:>11}{:>13}{:>15}{:>13}{:>11}{:>11}".format(*hdr))

    for fx in sorted(sections):
        s = sections[fx]
        regime = ("hijack" if fx in GATE_HIJACK else "scene-amb" if fx in SCENE_AMBIG
                  else "success" if fx in SUCCESS else "?")
        body = cloud(s["body_pts"])
        # submap_up estimated on the SAME fine-voxel downsample refine_candidates uses
        src_fine = body.voxel_down_sample(0.1)
        submap_up = estimate_map_up(src_fine)
        T_true = np.asarray(s["T_true"])
        # truth tilt = body z-axis in map frame vs world-z (the mount angle)
        truth_tilt = float(np.degrees(np.arccos(np.clip(float(T_true[2, 2]), -1.0, 1.0))))
        # NEW consistency residual of the correct answer (want ~0)
        ttrue_resid = _gravity_tilt_deg(T_true, submap_up, map_up)
        # OLD world-z gate tilt of the correct answer (what rejected it): arccos(T_true[2,2])
        ttrue_oldgate = truth_tilt
        # baseline winning estimate
        T_est = np.asarray(baseline[fx]["T_est"])
        est_oldgate = float(np.degrees(np.arccos(np.clip(float(T_est[2, 2]), -1.0, 1.0))))
        est_resid = _gravity_tilt_deg(T_est, submap_up, map_up)
        base_err_t = baseline[fx]["err_t"]
        print("{:<13}{:>6}{:>8}{:>16.2f}{:>11.2f}{:>13.2f}{:>15.2f}{:>13.2f}{:>11.2f}{:>11.2f}".format(
            regime, fx, len(s["body_pts"]), tilt_from_worldz(submap_up), truth_tilt,
            ttrue_resid, ttrue_oldgate, est_oldgate, est_resid, base_err_t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
