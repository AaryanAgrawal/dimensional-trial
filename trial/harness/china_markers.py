#!/usr/bin/env python3
"""china_office marker-agreement experiment — PGO accuracy evidence at scale.

The 20-minute china_office drive carries 3,959 pre-recorded AprilTag
detections (9 ids) — the only dataset here with enough revisit structure to
judge PGO the way lesh prescribes (observe marker, drive a loop, observe
again; locations must match), stratified by time gap.

Chain (every frame named, per repo rule):
  odom_T_tag(det) = odom_T_optical(ts_det) o optical_T_tag(det)
    - optical_T_tag: the recorded april_tags_raw PoseStamped (frame verified
      'camera_optical'; external recorder — scale consistent with 0.1 m tags)
    - odom_T_optical: recorded tf stream, BFS-composed
      odom->mid360_link->base_link->front_camera->camera_optical
  corrected = graph.correct(odom_T_tag), graph = PGO over gt_pointlio_lidar
      (the ONLY stream satisfying PGO's contract here; it is PointLIO's own
      output — labeled, not trusted as ground truth)

Run: cd dimos && uv run python ../trial/harness/china_markers.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.mapping.loop_closure.pgo import PGO  # noqa: E402
from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.memory2.tf import StreamTF  # noqa: E402
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped  # noqa: E402
from dimos.msgs.geometry_msgs.Transform import Transform  # noqa: E402
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2  # noqa: E402

REVISIT_BUCKETS_S = [(0, 10), (10, 60), (60, 300), (300, float("inf"))]
REPROJ_GATE_PX = 3.0
TF_TOL_S = 0.15


def main() -> int:
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / "china_office.db"), must_exist=True)
    out: dict = {"meta": {
        "recording": "china_office", "lidar_stream": "gt_pointlio_lidar",
        "caveats": [
            "april_tags_raw written by an external recorder (no in-repo writer); "
            "frame verified camera_optical; tag size assumed 0.1 m",
            "PGO input is PointLIO's own output (gt_ prefix unearned) — this measures "
            "PGO-over-PointLIO consistency, labeled, not absolute ground truth",
        ],
        "unix": time.time(),
    }}
    with store:
        t0 = time.perf_counter()
        graph = store.stream("gt_pointlio_lidar", PointCloud2).transform(PGO()).last().data
        pgo_s = time.perf_counter() - t0
        out["meta"]["pgo"] = {"keyframes": len(graph.keyframes), "loops": len(graph.loops),
                              "seconds": pgo_s}
        print(f"PGO: {len(graph.keyframes)} kf, {len(graph.loops)} loops, {pgo_s:.0f}s", flush=True)

        tf = StreamTF.from_store(store)
        rows = []
        n_no_tf, n_reproj = 0, 0
        for obs in store.stream("april_tags_raw", PoseStamped):
            tags = obs.tags or {}
            if float(tags.get("reproj_px", 0.0)) > REPROJ_GATE_PX:
                n_reproj += 1
                continue
            odom_T_opt = tf.get("odom", "camera_optical", time_point=obs.ts,
                                time_tolerance=TF_TOL_S)
            if odom_T_opt is None:
                n_no_tf += 1
                continue
            p = obs.data
            opt_T_tag = pose7_to_mat((p.position.x, p.position.y, p.position.z,
                                      p.orientation.x, p.orientation.y,
                                      p.orientation.z, p.orientation.w))
            odom_T_tag = transform_to_mat(odom_T_opt) @ opt_T_tag
            rows.append({"ts": float(obs.ts), "id": int(tags.get("marker_id", -1)),
                         "raw": odom_T_tag[:3, 3], "T_raw": odom_T_tag})
        print(f"detections kept: {len(rows)} (reproj-gated {n_reproj}, no-tf {n_no_tf})",
              flush=True)

        # Correct in bulk (graph.correct on a Transform built field-wise —
        # from_matrix may not exist; do it via explicit fields).
        from dimos.msgs.geometry_msgs.Quaternion import Quaternion
        from dimos.msgs.geometry_msgs.Vector3 import Vector3
        from scipy.spatial.transform import Rotation
        for r in rows:
            T = r["T_raw"]
            q = Rotation.from_matrix(T[:3, :3]).as_quat()
            raw_tf = Transform(translation=Vector3(*T[:3, 3]),
                               rotation=Quaternion(*q),
                               frame_id="odom", child_frame_id=f"marker_{r['id']}",
                               ts=r["ts"])
            r["corr"] = transform_to_mat(graph.correct(raw_tf))[:3, 3]

    per_marker = {}
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["id"]].append(r)
    for mid, rs in sorted(by_id.items()):
        rs.sort(key=lambda r: r["ts"])
        ts = np.array([r["ts"] for r in rs])
        raw = np.array([r["raw"] for r in rs])
        cor = np.array([r["corr"] for r in rs])
        entry = {"n_sightings": len(rs), "span_s": float(ts[-1] - ts[0]),
                 "revisit_by_gap": {}}
        for lo, hi in REVISIT_BUCKETS_S:
            dr, dc = [], []
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    if lo <= ts[j] - ts[i] < hi:
                        dr.append(np.linalg.norm(raw[i] - raw[j]))
                        dc.append(np.linalg.norm(cor[i] - cor[j]))
            key = f"{lo}-{'inf' if hi == float('inf') else int(hi)}s"
            entry["revisit_by_gap"][key] = (
                {"n_pairs": len(dr),
                 "median_raw_m": float(np.median(dr)), "median_pgo_m": float(np.median(dc)),
                 "p90_raw_m": float(np.percentile(dr, 90)), "p90_pgo_m": float(np.percentile(dc, 90))}
                if dr else {"n_pairs": 0})
        per_marker[str(mid)] = entry
        g60 = entry["revisit_by_gap"].get("60-300s", {})
        ginf = entry["revisit_by_gap"].get("300-infs", {})
        print(f"id {mid}: n={len(rs)} span={ts[-1]-ts[0]:.0f}s "
              f"60-300s: {g60.get('median_raw_m', float('nan')):.3f}->{g60.get('median_pgo_m', float('nan')):.3f} "
              f"300s+: {ginf.get('median_raw_m', float('nan')):.3f}->{ginf.get('median_pgo_m', float('nan')):.3f}",
              flush=True)

    out["per_marker"] = per_marker
    out_dir = HARNESS / "out" / "markers"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "china_office.scatter.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {out_dir}/china_office.scatter.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
