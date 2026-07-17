#!/usr/bin/env python3
"""Two-lane marker-revisit verification on lesh's purpose-built recording.

recording_go2_mid360_2026-05-29_4-45pm-PST.db carries BOTH lidar chains over
one 13-minute, >100 m walk with 6 AprilTags revisited at gaps up to ~12 min:
  lane A (go2):     `lidar` (WebRTC, world-frame clouds+poses) + `odom`
  lane B (mid360):  `fastlio_lidar` (world-frame clouds+poses) + `fastlio_odometry`
Same walk, same tags, same pixels — so lesh's PGO verification (observe
marker -> large walk -> observe again; locations must overlap) runs on the
Go2 lane and the production mid360 lane against identical physical anchors.

Per lane L:  world_L_T_tag(det) = world_L_T_body(ts) o body_T_optical_L o optical_T_tag
  - optical_T_tag: solved from pixels (IPPE, fisheye-correct, best-of-2 by
    reprojection, gate <= 3 px) — shared by both lanes.
  - mounts from go2_mid360_static_transforms (the rig's own constants).
    CAVEAT: FAST-LIO's `body` is assumed = mid360_link; a constant mount
    error biases each sighting equally in raw and PGO, so the raw-vs-PGO
    comparison within a lane survives it. Absolute positions do not.
  - corrected = graph_L.correct(...), graph_L = PGO over that lane's stream.

Run: cd dimos && uv run python ../trial/harness/two_lane_markers.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.mapping.loop_closure.pgo import PGO  # noqa: E402
from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped  # noqa: E402
from dimos.msgs.geometry_msgs.Transform import Transform  # noqa: E402
from dimos.msgs.nav_msgs.Odometry import Odometry  # noqa: E402
from dimos.msgs.sensor_msgs.Image import Image  # noqa: E402
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2  # noqa: E402
from dimos.perception.fiducial.marker_pose import (  # noqa: E402
    camera_info_to_cv_matrices,
    create_aruco_detector,
    estimate_marker_pose_candidates,
    marker_reprojection_error,
)
from dimos.robot.unitree.go2.connection import _camera_info_static  # noqa: E402
from dimos.robot.unitree.go2 import go2_mid360_static_transforms as statics  # noqa: E402

RECORDING = "recording_go2_mid360_2026-05-29_4-45pm-PST"
MARKER_LENGTH_M = 0.1
REPROJ_GATE_PX = 3.0
POSE_TOL_S = 0.15
REVISIT_BUCKETS_S = [(0, 10), (10, 60), (60, 300), (300, float("inf"))]


def _static_map() -> dict[tuple[str, str], np.ndarray]:
    out = {}
    for spec in statics.FRAMES:
        tf = spec if isinstance(spec, Transform) else getattr(spec, "transform", None)
        if tf is None:
            # FrameSpec-like: build from attributes
            t = spec.translation
            q = spec.rotation
            T = pose7_to_mat((t[0], t[1], t[2], q[0], q[1], q[2], q[3])) \
                if not hasattr(t, "x") else pose7_to_mat((t.x, t.y, t.z, q.x, q.y, q.z, q.w))
            out[(spec.frame_id, spec.child_frame_id)] = T
        else:
            out[(tf.frame_id, tf.child_frame_id)] = transform_to_mat(tf)
    return out


def detect_optical_T_tag(store: SqliteStore) -> list[dict]:
    """optical_T_tag per detection from pixels — lane-independent."""
    info = _camera_info_static()
    k, d = camera_info_to_cv_matrices(info)
    model = info.distortion_model
    det = create_aruco_detector("DICT_APRILTAG_36h11")
    rows = []
    n_img = 0
    for obs in store.stream("color_image", Image):
        n_img += 1
        img = obs.data
        if img.width != info.width or img.height != info.height:
            continue
        corners, ids, _ = det.detectMarkers(img.to_grayscale().as_numpy())
        if ids is None:
            continue
        for c, mid in zip(corners, ids):
            cp = c.reshape(4, 2)
            cands = estimate_marker_pose_candidates(cp, MARKER_LENGTH_M, k, d,
                                                    distortion_model=model)
            if not cands:
                continue
            scored = sorted(
                (marker_reprojection_error(cp, MARKER_LENGTH_M, k, d, rv, tv,
                                           distortion_model=model), rv, tv)
                for rv, tv in cands)
            err, rvec, tvec = scored[0]
            if err > REPROJ_GATE_PX:
                continue
            R, _ = cv2.Rodrigues(rvec)
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = tvec.reshape(3)
            rows.append({"ts": float(obs.ts), "id": int(mid[0]),
                         "opt_T_tag": T, "reproj_px": float(err)})
    print(f"detections: {len(rows)} tag solves from {n_img} images", flush=True)
    return rows


def lane_positions(store, dets, pose_stream, pose_type, body_T_opt, graph):
    """Per detection: (raw world position, PGO-corrected position) in lane frame."""
    ps = store.stream(pose_stream, pose_type)
    out = []
    for r in dets:
        obs = ps.at(r["ts"], tolerance=POSE_TOL_S).first()
        if obs is None:
            continue
        p = obs.data
        if pose_type is Odometry:
            pos, q = p.position, p.orientation
        else:
            pos, q = p.position, p.orientation
        W = pose7_to_mat((pos.x, pos.y, pos.z, q.x, q.y, q.z, q.w))
        T_world_tag = W @ body_T_opt @ r["opt_T_tag"]
        qq = None
        from scipy.spatial.transform import Rotation
        qv = Rotation.from_matrix(T_world_tag[:3, :3]).as_quat()
        from dimos.msgs.geometry_msgs.Quaternion import Quaternion
        from dimos.msgs.geometry_msgs.Vector3 import Vector3
        raw_tf = Transform(translation=Vector3(*T_world_tag[:3, 3]),
                           rotation=Quaternion(*qv), frame_id="world",
                           child_frame_id=f"marker_{r['id']}", ts=r["ts"])
        corr = transform_to_mat(graph.correct(raw_tf))[:3, 3]
        out.append({"ts": r["ts"], "id": r["id"], "raw": T_world_tag[:3, 3],
                    "corr": corr, "reproj_px": r["reproj_px"]})
    return out


def buckets(rows) -> dict:
    per = {}
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["id"]].append(r)
    for mid, rs in sorted(by_id.items()):
        rs.sort(key=lambda r: r["ts"])
        ts = np.array([r["ts"] for r in rs])
        raw = np.array([r["raw"] for r in rs])
        cor = np.array([r["corr"] for r in rs])
        entry = {"n": len(rs), "span_s": float(ts[-1] - ts[0]), "gaps": {}}
        for lo, hi in REVISIT_BUCKETS_S:
            dr, dc = [], []
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    if lo <= ts[j] - ts[i] < hi:
                        dr.append(float(np.linalg.norm(raw[i] - raw[j])))
                        dc.append(float(np.linalg.norm(cor[i] - cor[j])))
            key = f"{lo}-{'inf' if hi == float('inf') else int(hi)}s"
            entry["gaps"][key] = (
                {"n_pairs": len(dr), "median_raw_m": float(np.median(dr)),
                 "median_pgo_m": float(np.median(dc)),
                 "p90_raw_m": float(np.percentile(dr, 90)),
                 "p90_pgo_m": float(np.percentile(dc, 90))}
                if dr else {"n_pairs": 0})
        per[str(mid)] = entry
    return per


def main() -> int:
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{RECORDING}.db"), must_exist=True)
    S = _static_map()
    print("static edges:", list(S), flush=True)
    base_T_front = S[("base_link", "front_camera")]
    front_T_opt = S[("front_camera", "camera_optical")]
    front_T_mid = S[("front_camera", "mid360_link")]
    base_T_opt = base_T_front @ front_T_opt  # lane A mount
    mid_T_opt = np.linalg.inv(front_T_mid) @ front_T_opt  # lane B mount (body=mid360 assumed)

    result = {"meta": {"recording": RECORDING, "unix": time.time(),
                       "caveats": [
                           "lane B assumes FAST-LIO body == mid360_link; constant mount error "
                           "biases raw and PGO equally within the lane",
                           "camera intrinsics = go2 front_camera_720.yaml (fisheye handled)"]},
              "lanes": {}}
    with store:
        dets = detect_optical_T_tag(store)
        for lane, (lidar_stream, pose_stream, pose_type, mount) in {
            "go2": ("lidar", "odom", PoseStamped, base_T_opt),
            "mid360_fastlio": ("fastlio_lidar", "fastlio_odometry", Odometry, mid_T_opt),
        }.items():
            t0 = time.perf_counter()
            graph = store.stream(lidar_stream, PointCloud2).transform(PGO()).last().data
            print(f"[{lane}] PGO: {len(graph.keyframes)} kf, {len(graph.loops)} loops, "
                  f"{time.perf_counter()-t0:.0f}s", flush=True)
            rows = lane_positions(store, dets, pose_stream, pose_type, mount, graph)
            per = buckets(rows)
            result["lanes"][lane] = {
                "pgo": {"keyframes": len(graph.keyframes), "loops": len(graph.loops)},
                "n_positioned": len(rows), "per_marker": per}
            for mid, e in per.items():
                g = e["gaps"].get("300-infs", {})
                g2 = e["gaps"].get("60-300s", {})
                print(f"[{lane}] id {mid}: n={e['n']} span={e['span_s']:.0f}s "
                      f"60-300s: {g2.get('median_raw_m', float('nan')):.3f}->{g2.get('median_pgo_m', float('nan')):.3f} "
                      f"(n={g2.get('n_pairs',0)})  300s+: {g.get('median_raw_m', float('nan')):.3f}->"
                      f"{g.get('median_pgo_m', float('nan')):.3f} (n={g.get('n_pairs',0)})", flush=True)

    out = HARNESS / "out" / "markers" / f"{RECORDING}.two_lane.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=1)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
