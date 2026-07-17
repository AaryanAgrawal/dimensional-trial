#!/usr/bin/env python3
"""Verification .rrd per benchmark recording — see the evidence in rerun.

Layers (toggle in the viewer):
  world/trajectory/raw, world/trajectory/pgo
  world/tag_sightings/raw, world/tag_sightings/pgo   (color = time into drive)
  world/tag_instances    labeled centroid per SPATIAL CLUSTER per id — one
                         globe per physical tag; >1 globe for one id = the
                         duplicate-id exclusion evidence (hk_village2/4)

Run: cd dimos && uv run python ../trial/harness/make_rrd.py hk_village2 [more...]
"""

from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import rerun as rr
from matplotlib import cm
from scipy.cluster.hierarchy import fcluster, linkage

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from markers import detect_all  # noqa: E402
from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2  # noqa: E402

OUT = HARNESS.parent / "results"


def one(recording: str) -> Path:
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    with open(HARNESS / "out" / "prepared" / f"{recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])
    store = SqliteStore(path=str(dimos_root / "data" / f"{recording}.db"), must_exist=True)
    with store:
        rows = detect_all(store, graph)
        P_raw, P_cor = [], []
        for o in store.stream(prep["lidar_stream"], PointCloud2):
            if o.pose_tuple is None:
                continue
            P = pose7_to_mat(o.pose_tuple)
            C = transform_to_mat(graph.correction_at(o.ts))
            P_raw.append(P[:3, 3])
            P_cor.append((C @ P)[:3, 3])

    rr.init(f"benchmark_{recording}")
    rr.log("world/trajectory/raw", rr.LineStrips3D([np.array(P_raw)],
           colors=[[150, 150, 150]]), static=True)
    rr.log("world/trajectory/pgo", rr.LineStrips3D([np.array(P_cor)],
           colors=[[60, 120, 255]]), static=True)

    if rows:
        rows.sort(key=lambda r: r["ts"])
        t = np.array([r["ts"] for r in rows])
        t = t - t.min()
        raw = np.array([r["T_world_tag_raw"][:3, 3] for r in rows])
        cor = np.array([r["T_map_tag_corr"][:3, 3] for r in rows])
        colors = (np.array([cm.viridis(x)[:3] for x in t / max(t.max(), 1e-9)]) * 255
                  ).astype(np.uint8)
        rr.log("world/tag_sightings/raw", rr.Points3D(raw, colors=colors, radii=0.05),
               static=True)
        rr.log("world/tag_sightings/pgo", rr.Points3D(cor, colors=colors, radii=0.05),
               static=True)

        # Per-id spatial clusters on RAW positions — the validity evidence.
        by_id = defaultdict(list)
        for i, r in enumerate(rows):
            by_id[r["marker_id"]].append(i)
        centers, labels = [], []
        note_lines = []
        for mid, idxs in sorted(by_id.items()):
            pts = raw[idxs]
            cl = (fcluster(linkage(pts[:, :2], "single"), 1.0, criterion="distance")
                  if len(idxs) > 1 else np.array([1]))
            ncl = len(set(cl))
            for c in sorted(set(cl)):
                m = cl == c
                centers.append(pts[m].mean(0))
                labels.append(f"id {mid}" + (f" · instance {c}/{ncl}" if ncl > 1 else ""))
            note_lines.append(f"id {mid}: {len(idxs)} sightings, {ncl} physical instance(s)"
                              + ("  <-- DUPLICATE-ID, excluded from stats" if ncl > 1 else ""))
        rr.log("world/tag_instances", rr.Points3D(np.array(centers),
               colors=[[255, 140, 0]], radii=0.15, labels=labels), static=True)
        rr.log("explainer", rr.TextDocument(
            f"{recording} — relocalization benchmark verification (replay)\n\n"
            "Toggle tag_sightings/raw vs /pgo (color = time). Orange globes =\n"
            "one per PHYSICAL tag instance (spatial cluster):\n\n"
            + "\n".join(note_lines)), static=True)
    else:
        rr.log("explainer", rr.TextDocument(
            f"{recording}: no tag detections — not a marker-benchmark run."), static=True)

    out = OUT / f"benchmark_{recording}.rrd"
    rr.save(str(out))
    print(f"wrote {out}")
    return out


if __name__ == "__main__":
    for rec in sys.argv[1:] or ["hk_village1", "hk_village2", "hk_village3",
                                 "hk_village4", "hk_village5", "hk_village6"]:
        one(rec)
