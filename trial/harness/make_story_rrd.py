#!/usr/bin/env python3
"""Village3 step-by-step evidence story for rerun.

Toggle the step_* groups in order (left panel). Each step carries its own
text. Frames: everything under map/ is the PGO 'map' frame; step_1 raw
items live in the recording's raw world frame (they diverge — that IS the
drift). Run: cd dimos && uv run python ../trial/harness/make_story_rrd.py
"""
import json, pickle, sys
from pathlib import Path
import numpy as np
import rerun as rr
from matplotlib import cm

H = Path(__file__).parent
sys.path.insert(0, str(H))
from markers import detect_all
from prep import pose7_to_mat, transform_to_mat
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

REC = "hk_village3"
prep = pickle.load(open(H / "out" / "prepared" / f"{REC}.pkl", "rb"))
graph = pickle.loads(prep["pose_graph_bytes"])
res = json.load(open(H / "out" / "results" / f"{REC}.ransac.json"))["results"]
droot = Path(__file__).resolve().parents[2] / "dimos"
store = SqliteStore(path=str(droot / "data" / f"{REC}.db"), must_exist=True)
with store:
    rows = detect_all(store, graph)
    P_raw, P_cor = [], []
    for o in store.stream("lidar", PointCloud2):
        if o.pose_tuple is None: continue
        P = pose7_to_mat(o.pose_tuple)
        C = transform_to_mat(graph.correction_at(o.ts))
        P_raw.append(P[:3, 3]); P_cor.append((C @ P)[:3, 3])

rows.sort(key=lambda r: r["ts"])
t = np.array([r["ts"] for r in rows]); t -= t.min()
tagraw = np.array([r["T_world_tag_raw"][:3, 3] for r in rows])
tagcor = np.array([r["T_map_tag_corr"][:3, 3] for r in rows])
colors = (np.array([cm.viridis(x)[:3] for x in t / t.max()]) * 255).astype(np.uint8)

rr.init("village3_story")
# backdrop: the premap (subsampled)
pm = prep["premap_pts"][:: max(1, len(prep["premap_pts"]) // 120000)]
rr.log("backdrop_premap", rr.Points3D(pm, colors=[[90, 90, 100]], radii=0.012), static=True)

rr.log("step_1_odometry_drift/text", rr.TextDocument(
    "STEP 1 — ODOMETRY DRIFTS\nGray = raw odometry trajectory (the robot's own belief).\n"
    "Blue (step 2) = where PGO says it actually went. They start together and split: that gap is drift."), static=True)
rr.log("step_1_odometry_drift/trajectory_raw", rr.LineStrips3D([np.array(P_raw)], colors=[[160, 160, 160]]), static=True)

rr.log("step_2_pgo_correction/text", rr.TextDocument(
    "STEP 2 — PGO CORRECTS THE TRAJECTORY\n12 loop closures pull the path consistent.\n"
    "But correction is spread along the whole path — remember this for step 4."), static=True)
rr.log("step_2_pgo_correction/trajectory_pgo", rr.LineStrips3D([np.array(P_cor)], colors=[[70, 130, 255]]), static=True)

rr.log("step_3_tag_raw/text", rr.TextDocument(
    "STEP 3 — THE REFEREE TAG, RAW\nOne physical tag, 156 sightings (color = time).\n"
    "Under raw odometry its placements smear ~0.9 m across revisits: drift, made visible."), static=True)
rr.log("step_3_tag_raw/sightings", rr.Points3D(tagraw, colors=colors, radii=0.05), static=True)

mask = (t < 40) | (t > 85)
c0 = tagcor[mask].mean(0)
bad = tagcor[(t >= 55) & (t <= 70)].mean(0)
rr.log("step_4_tag_corrected/text", rr.TextDocument(
    "STEP 4 — THE SAME TAG, PGO-CORRECTED\nLoop-return passes collapse: 0.93 -> 0.28 m (green globe = the agreement, drawn to scale).\n"
    "But the ~60 s pass lands 1.4 m away (red) — the spread correction bent the middle of the drive."), static=True)
rr.log("step_4_tag_corrected/sightings", rr.Points3D(tagcor, colors=colors, radii=0.05), static=True)
rr.log("step_4_tag_corrected/consensus", rr.Points3D([c0], colors=[[0, 200, 0]], radii=0.28, labels=["3 passes agree ±0.28 m"]), static=True)
rr.log("step_4_tag_corrected/displaced", rr.Points3D([bad], colors=[[255, 60, 60]], radii=0.12, labels=["~60 s pass: bent 1.4 m off"]), static=True)

ok_pts, bust_pts, bust_lines, labels = [], [], [], []
for r in res:
    if r["status"] != "ok" or "T_est" not in r: continue
    s = next(x for x in prep["sections"] if x["frame_idx"] == r["frame_idx"])
    est = np.asarray(r["T_est"])[:3, 3]; tru = np.asarray(s["T_true"])[:3, 3]
    if r["success"]: ok_pts.append(est)
    else:
        bust_pts.append(est); bust_lines.append([tru, est])
        labels.append(f"f{r['frame_idx']} fit={r['fitness']:.2f} err={r['err_t']:.1f}m")
rr.log("step_5_module_answers/text", rr.TextDocument(
    "STEP 5 — THE RELOCALIZATION MODULE'S ANSWERS\nGreen dots = correct solves (within 1 m/15°).\n"
    "Red dots = busts; red lines run truth -> claimed pose. Hover a red dot: its FITNESS is high\n"
    "(0.62-0.995) — every bust would be accepted at the 0.45 gate. That is the confidence problem."), static=True)
rr.log("step_5_module_answers/correct", rr.Points3D(np.array(ok_pts), colors=[[40, 200, 90]], radii=0.10), static=True)
rr.log("step_5_module_answers/busts", rr.Points3D(np.array(bust_pts), colors=[[255, 60, 60]], radii=0.14, labels=labels), static=True)
rr.log("step_5_module_answers/error_vectors", rr.LineStrips3D(bust_lines, colors=[[255, 60, 60]]), static=True)

out = H.parent / "results" / "village3_story.rrd"
rr.save(str(out))
print("wrote", out)
