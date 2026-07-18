#!/usr/bin/env python3
"""Did _wall_subset's <100-point fallback (floors back in, rotation-blind)
ever fire on our benchmark sections? Per section: wall-point count at the
judge's own scale, fallback verdict, and the section's bench outcome.

Run: cd dimos && uv run python ../trial/harness/probe_wall_fallback.py hk_village3 [more]
"""
import json, pickle, sys
from pathlib import Path
import numpy as np
import open3d as o3d

H = Path(__file__).parent
FINE = 0.1  # relocalize.FINE_VOXEL — judge scores at this scale

for rec in sys.argv[1:] or ["hk_village3"]:
    prep = pickle.load(open(H / "out" / "prepared" / f"{rec}.pkl", "rb"))
    try:
        res = {r["frame_idx"]: r for r in json.load(open(H / "out" / "results" / f"{rec}.ransac.json"))["results"]}
    except FileNotFoundError:
        res = {}
    fired, rows = 0, []
    for s in prep["sections"]:
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(s["body_pts"].astype(np.float64))
        down = pc.voxel_down_sample(FINE)
        down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=FINE * 2, max_nn=30))
        nrm = np.asarray(down.normals)
        walls = int((np.abs(nrm[:, 2]) < 0.7).sum())
        fb = walls < 100
        fired += fb
        r = res.get(s["frame_idx"], {})
        rows.append((s["frame_idx"], len(down.points), walls, fb,
                     r.get("success"), round(r.get("fitness", -1), 3), round(r.get("err_t", -1), 2)))
    print(f"=== {rec}: fallback fired on {fired}/{len(prep['sections'])} sections ===")
    print(f"{'frame':>6} {'pts@0.1':>8} {'walls':>6} {'FB':>3} {'ok':>5} {'fit':>6} {'err':>6}")
    for fr, n, w, fb, ok, fit, err in rows:
        if fb or (ok is False):
            print(f"{fr:>6} {n:>8} {w:>6} {'YES' if fb else '':>3} {str(ok):>5} {fit:>6} {err:>6}")
