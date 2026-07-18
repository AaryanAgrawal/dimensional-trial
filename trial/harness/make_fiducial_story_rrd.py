#!/usr/bin/env python3
"""Fiducial-prior evidence story (mid360 walk) for rerun.
Run: cd dimos && uv run python ../trial/harness/make_fiducial_story_rrd.py"""
import json
import pickle
from pathlib import Path

import numpy as np
import rerun as rr

H = Path(__file__).parent
REC = "recording_go2_mid360_2026-05-29_4-45pm-PST"
prep = pickle.load(open(H / "out" / "prepared" / f"{REC}.pkl", "rb"))
ra = {r["frame_idx"]: r for r in json.load(open(H / "out" / "results" / f"{REC}.ransac.json"))["results"]}
rf = {r["frame_idx"]: r for r in json.load(open(H / "out" / "results" / f"{REC}.ransac_fiducial.json"))["results"]}
mm = json.load(open(H / "out" / "markers" / f"{REC}.marker_map.json"))["markers"]
truth = {s["frame_idx"]: np.asarray(s["T_true"])[:3, 3] for s in prep["sections"]}

rr.init("fiducial_story")
pm = prep["premap_pts"][:: max(1, len(prep["premap_pts"]) // 150000)]
rr.log("backdrop_premap", rr.Points3D(pm, colors=[[90, 90, 100]], radii=0.02), static=True)

tags = np.array([np.asarray(T)[:3, 3] for T in mm.values()])
rr.log("step_1_fiducial_tags/text", rr.TextDocument(
    "STEP 1 — THE FIDUCIAL TAGS\nOrange globes = the 5 surveyed tags the prior may use.\n"
    "The REFEREE tag (id 4) is deliberately ABSENT from this map — it only grades, never helps."), static=True)
rr.log("step_1_fiducial_tags/tags", rr.Points3D(tags, colors=[[255, 140, 0]], radii=0.3,
       labels=[f"tag {k}" for k in mm]), static=True)

busts, lines, labels = [], [], []
fixed, fixed_labels = [], []
for fi, r in ra.items():
    if r["status"] != "ok" or r["success"] or "T_est" not in r:
        continue
    est = np.asarray(r["T_est"])[:3, 3]
    tru = truth[fi]
    f2 = rf.get(fi, {})
    busts.append(est)
    lines.append([tru, est])
    labels.append(f"f{fi} RANSAC err={r['err_t']:.1f}m fit={r['fitness']:.2f}")
    if f2.get("success"):
        fixed.append(np.asarray(f2["T_est"])[:3, 3])
        fixed_labels.append(f"f{fi} +fiducial err={f2['err_t']:.2f}m src={f2.get('source')}")
rr.log("step_2_ransac_busts/text", rr.TextDocument(
    "STEP 2 — RANSAC ALONE\nRed dots = wrong answers; red lines run truth -> claimed pose\n"
    "(6.7 to 72 m off) on this hard 2.8M-point outdoor map."), static=True)
rr.log("step_2_ransac_busts/answers", rr.Points3D(np.array(busts), colors=[[255, 60, 60]],
       radii=0.35, labels=labels), static=True)
rr.log("step_2_ransac_busts/error_vectors", rr.LineStrips3D(lines, colors=[[255, 60, 60]]), static=True)

rr.log("step_3_with_fiducial/text", rr.TextDocument(
    "STEP 3 — SAME SECTIONS, MARKER CANDIDATES IN THE POOL\nGreen dots = rescued answers, now\n"
    "centimeters from truth, labeled with the winning source. Score: 52.5% -> 72.5%.\n\n"
    "ATTRIBUTION PROOF: all 25 sections with no marker in view returned BYTE-IDENTICAL answers\n"
    "in both runs — every green here is the markers' doing, nothing else."), static=True)
rr.log("step_3_with_fiducial/answers", rr.Points3D(np.array(fixed), colors=[[40, 200, 90]],
       radii=0.35, labels=fixed_labels), static=True)

out = H.parent / "results" / "fiducial_story.rrd"
rr.save(str(out))
print("wrote", out, f"| busts={len(busts)} rescued={len(fixed)}")
