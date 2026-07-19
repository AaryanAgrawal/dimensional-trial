#!/usr/bin/env python3
"""markers.py outputs for the mid360_fastlio LANE of the two-lane recording.

markers.py composes marker sightings into world frame via DetectMarkers, which
uses the image obs pose — the go2 odometry world. This recording's second lane
(fastlio_lidar + fastlio_odometry) lives in a DIFFERENT world frame, so its
marker map / fixes / referee must be rebuilt there:

  world_B_T_tag(det) = world_B_T_body(ts) o body_T_optical_B o optical_T_tag
    - optical_T_tag: markers.py's own solver (estimate_marker_pose, single
      IPPE_SQUARE solution — identical to DetectMarkers) on the SAME gated
      image stream (QualityWindow sharpness 0.1 s -> SpeedLimit 0.5 m/s /
      50 deg/s), so the detection set matches the go2-lane fixes.
    - world_B_T_body: fastlio_odometry payload pose, nearest-ts join 0.15 s
      (storage obs poses are placeholders on this recording — probed live).
    - body_T_optical_B = inv(front_T_mid360) o front_T_optical from the rig
      statics; FAST-LIO body == mid360_link ASSUMED (two_lane.json caveat:
      a constant mount error biases raw and PGO equally within the lane).
  corrected with the PoseGraph pickled in the FASTLIO pkl — the exact graph
  that defined this lane's benchmark truth (prep.py contract).

Aggregation (scatter/marker map/fixes/referee) is imported from markers.py
unchanged; outputs use the '<rec>.fastlio' name so run_bench.py and
referee_verdict.py work on the lane without modification. Referee tag comes
from benchmark_setup.yaml under the BASE recording name (id 4).

Run: cd dimos && uv run python ../trial/harness/fastlio_lane_markers.py
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from markers import (  # noqa: E402
    AGE_MAX_S,
    AGE_TAU_S,
    CONF_MAX,
    MARKER_LENGTH_M,
    REPROJ_GATE_PX,
    derive_marker_map,
    fiducial_fixes,
    print_referee_table,
    referee_from_setup,
    scatter_stats,
)
from prep import pose7_to_mat, transform_to_mat  # noqa: E402
from two_lane_markers import _static_map  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.memory2.transform import QualityWindow, SpeedLimit  # noqa: E402
from dimos.msgs.geometry_msgs.Quaternion import Quaternion  # noqa: E402
from dimos.msgs.geometry_msgs.Transform import Transform  # noqa: E402
from dimos.msgs.geometry_msgs.Vector3 import Vector3  # noqa: E402
from dimos.msgs.nav_msgs.Odometry import Odometry  # noqa: E402
from dimos.msgs.sensor_msgs.Image import Image  # noqa: E402
from dimos.perception.fiducial.marker_pose import (  # noqa: E402
    camera_info_to_cv_matrices,
    create_aruco_detector,
    estimate_marker_pose,
    marker_reprojection_error,
)
from dimos.robot.unitree.go2.connection import _camera_info_static  # noqa: E402

RECORDING = "recording_go2_mid360_2026-05-29_4-45pm-PST"
OUT_NAME = RECORDING + ".fastlio"  # pkl + json + results all keyed on this
POSE_TOL_S = 0.15  # nearest-ts join, same as prep.reposed_lidar_obs


def detect_gated_optical(store: SqliteStore) -> list[dict]:
    """markers.py's gate chain, emitting optical_T_tag instead of world poses."""
    info = _camera_info_static()
    k, d = camera_info_to_cv_matrices(info)
    model = info.distortion_model
    det = create_aruco_detector("DICT_APRILTAG_36h11")  # DetectMarkers default
    gated = (
        store.stream("color_image", Image)
        .transform(QualityWindow(lambda img: img.sharpness, window=0.1))
        .transform(SpeedLimit(max_mps=0.5, max_dps=50.0))
    )
    rows, n_img = [], 0
    for obs in gated:
        n_img += 1
        img = obs.data
        if img.width != info.width or img.height != info.height:
            continue
        corners, ids, _ = det.detectMarkers(img.to_grayscale().as_numpy())
        if ids is None:
            continue
        for c, mid in zip(corners, ids):
            cp = c.reshape(4, 2)
            pose = estimate_marker_pose(cp, MARKER_LENGTH_M, k, d, distortion_model=model)
            if pose is None:
                continue
            rvec, tvec = pose
            err = marker_reprojection_error(cp, MARKER_LENGTH_M, k, d, rvec, tvec,
                                            distortion_model=model)
            R, _ = cv2.Rodrigues(rvec)
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = tvec.reshape(3)
            rows.append({"ts": float(obs.ts), "marker_id": int(mid[0]),
                         "reproj_px": float(err), "opt_T_tag": T})
    print(f"detections: {len(rows)} tag solves from {n_img} gated images", flush=True)
    return rows


def lane_rows(store: SqliteStore, dets: list[dict], graph) -> list[dict]:
    """Compose each detection into the fastlio lane's raw + corrected world."""
    S = _static_map()
    front_T_opt = S[("front_camera", "camera_optical")]
    front_T_mid = S[("front_camera", "mid360_link")]
    mid_T_opt = np.linalg.inv(front_T_mid) @ front_T_opt  # body=mid360 assumed

    odom = [(o.ts, o.data) for o in store.stream("fastlio_odometry", Odometry)]
    ots = np.array([t for t, _ in odom])
    rows, n_unmatched = [], 0
    for r in dets:
        i = int(np.searchsorted(ots, r["ts"]))
        best = min((j for j in (i - 1, i) if 0 <= j < len(odom)),
                   key=lambda j: abs(ots[j] - r["ts"]))
        if abs(ots[best] - r["ts"]) > POSE_TOL_S:
            n_unmatched += 1
            continue
        p = odom[best][1]
        W = pose7_to_mat((p.position.x, p.position.y, p.position.z,
                          p.orientation.x, p.orientation.y, p.orientation.z,
                          p.orientation.w))
        T_world_tag = W @ mid_T_opt @ r["opt_T_tag"]
        q = Rotation.from_matrix(T_world_tag[:3, :3]).as_quat()
        raw_tf = Transform(translation=Vector3(*T_world_tag[:3, 3]),
                           rotation=Quaternion(*q), frame_id="world",
                           child_frame_id=f"marker_{r['marker_id']}", ts=r["ts"])
        rows.append({
            "ts": r["ts"], "marker_id": r["marker_id"], "reproj_px": r["reproj_px"],
            "T_world_tag_raw": T_world_tag,
            "T_map_tag_corr": transform_to_mat(graph.correct(raw_tf)),
        })
    print(f"lane-positioned: {len(rows)} ({n_unmatched} dropped, no fastlio pose "
          f"within {POSE_TOL_S}s)", flush=True)
    return rows


def main() -> int:
    referee = referee_from_setup(RECORDING)  # base name — yaml has no .fastlio key
    print(f"referee tag: {referee} (benchmark_setup.yaml[{RECORDING}]) — BENCHMARK "
          f"ONLY; excluded from marker map + fiducial fixes")

    with open(HARNESS / "out" / "prepared" / f"{OUT_NAME}.pkl", "rb") as f:
        prep = pickle.load(f)
    if prep["lidar_stream"] != "fastlio_lidar":
        raise ValueError(f"pkl {OUT_NAME} is lane '{prep['lidar_stream']}', "
                         f"want 'fastlio_lidar' — wrong pkl under the lane name")
    graph = pickle.loads(prep["pose_graph_bytes"])

    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{RECORDING}.db"), must_exist=True)
    with store:
        t0 = time.perf_counter()
        dets = detect_gated_optical(store)
        rows = lane_rows(store, dets, graph)
        print(f"detect+compose: {time.perf_counter() - t0:.1f}s", flush=True)

    # world_raw_T_body at each query — markers.py's recipe (graph-derived; the
    # raw lidar stream carries identity placeholders on this recording).
    lidar_poses = {
        s["frame_idx"]:
            np.linalg.inv(transform_to_mat(graph.correction_at(s["ts"]))) @ s["T_true"]
        for s in prep["sections"]}

    stats = scatter_stats(rows)
    ref_rows = [r for r in rows if r["marker_id"] == referee]
    fid_rows = [r for r in rows if r["marker_id"] != referee]
    print_referee_table(referee, stats)

    mm = derive_marker_map(fid_rows)
    print(f"marker map (fiducial set): {sorted(mm)} ids")
    for mid, T in sorted(mm.items()):
        d = float(np.linalg.norm(T[:3, 3]))
        print(f"  tag {mid}: lever {d:.2f} m from lane map origin "
              f"({d * np.pi / 180:.3f} m/deg)")
    fixes = fiducial_fixes(fid_rows, mm, prep["sections"], lidar_poses)
    n_fix = sum(len(v) for v in fixes.values())
    print(f"fiducial fixes: {len(fixes)}/{len(prep['sections'])} sections covered, {n_fix} fixes")

    out_dir = HARNESS / "out" / "markers"
    meta = {"recording": OUT_NAME, "lane": "mid360_fastlio",
            "truth_note": (
                "marker map derived from the SAME PoseGraph as the fastlio-lane "
                "benchmark truth — deployment-realistic, truth-correlated; the "
                "judge's fitness remains the independent check. FAST-LIO body == "
                "mid360_link assumed (constant mount error biases raw and PGO "
                "equally within the lane)"),
            "conf_model": f"{CONF_MAX}*exp(-age/{AGE_TAU_S}s), cutoff {AGE_MAX_S}s (unverified defaults)",
            "referee_tag": referee, "referee_source": "benchmark_setup.yaml (base name)",
            "git_rev_dimos": prep["git_rev_dimos"], "git_rev_trial": prep["git_rev_trial"]}
    with open(out_dir / f"{OUT_NAME}.scatter.json", "w") as f:
        json.dump({"meta": meta, "per_marker": stats}, f, indent=1)
    with open(out_dir / f"{OUT_NAME}.marker_map.json", "w") as f:
        json.dump({"meta": meta, "markers": {str(k): v.tolist() for k, v in mm.items()}}, f, indent=1)
    with open(out_dir / f"{OUT_NAME}.fixes.json", "w") as f:
        json.dump({str(k): v for k, v in fixes.items()}, f, indent=1)
    gated = [r for r in ref_rows if r["reproj_px"] <= REPROJ_GATE_PX]
    pos = np.array([r["T_map_tag_corr"][:3, 3] for r in gated])
    referee_out = {
        "meta": {**meta, "policy": (
            "BENCHMARK ONLY — never enters the marker map, never produces "
            "fiducial fixes (benchmark_setup.yaml decorrelation rule)")},
        "n_sightings": len(ref_rows),
        "revisit_stats": stats.get(str(referee)),
        "consensus_map_position_m": pos.mean(0).tolist() if len(gated) else None,
        "consensus_n_used": len(gated),
        "consensus_rms_m": (float(np.sqrt(((pos - pos.mean(0)) ** 2).sum(1).mean()))
                            if len(gated) else None),
        "detections": [{"ts": r["ts"], "reproj_px": r["reproj_px"],
                        "pos_world_raw_m": r["T_world_tag_raw"][:3, 3].tolist(),
                        "pos_map_pgo_m": r["T_map_tag_corr"][:3, 3].tolist()}
                       for r in ref_rows],
    }
    with open(out_dir / f"{OUT_NAME}.referee.json", "w") as f:
        json.dump(referee_out, f, indent=1)
    print(f"wrote {out_dir}/{OUT_NAME}.{{scatter,marker_map,fixes,referee}}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
