# A2 -- rendered-frame integration test of the start/end-tag referee's PnP.
#
# End-to-end on REAL PIXELS, no robot: the demo harness's renderer
# (demo/marker_loc_demo/render.py, run in demo/.venv via subprocess) draws a
# DICT_4X4_50 tag (id 49, 100 mm -- the survey/holdout tag spec) at 3 known
# camera poses; this test then runs metrics_logger.py's holdout solvePnP path
# on those frames and asserts the recovered tag_T_camera is within 1 cm /
# 1 deg of the renderer's ground truth.
#
# Why the path is REPLICATED rather than imported: metrics_logger's holdout
# helper is the bound method MetricsLogger._on_holdout_color_image -- its
# enclosing class opens live transports (make_transport / tf_backend) in
# __init__, so the helper is not importable as a pure function without
# standing up a transport stack. This test replays its exact call chain,
# call-for-call, against the same dimos functions it uses
# (dimos.perception.fiducial.marker_pose):
#     Image.to_grayscale().as_numpy()
#     -> create_aruco_detector(dict).detectMarkers(gray)
#     -> [id filter] -> estimate_marker_pose(corner_set, length, K, D,
#                                            distortion_model=...)
#     -> rvec_tvec_to_transform(...).inverse()      # = tag_T_camera
#     -> marker_reprojection_error(...)
# (metrics_logger.py lines 228-284; any drift there should be mirrored here.)
#
# Run from the dimos venv (needs dimos + a built demo/.venv for rendering):
#   cd dimos && uv run pytest ../trial/scripts/tests/test_start_end_referee_render.py
#
# Added by the verification battery (2026-07-15); tests only, touches no
# component under test (the demo's DICT retarget is runtime-only, in the
# render subprocess).

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]  # .../Dimensional
DEMO_DIR = ROOT / "demo"
DEMO_PY = DEMO_DIR / ".venv" / "bin" / "python"
RENDER_SCRIPT = TESTS_DIR / "_render_dict4x4_frames.py"
OUT_DIR = TESTS_DIR / "out" / "render_referee"

DICTIONARY = "DICT_4X4_50"
MARKER_ID = 49
MARKER_LENGTH_M = 0.10
TOL_M = 0.01
TOL_DEG = 1.0


@pytest.fixture(scope="module")
def rendered() -> dict:
    if not DEMO_PY.exists():
        pytest.skip(f"demo venv python not found at {DEMO_PY}")
    subprocess.run(
        [str(DEMO_PY), str(RENDER_SCRIPT), str(OUT_DIR)],
        cwd=DEMO_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads((OUT_DIR / "truth.json").read_text())


def _rotation_angle_deg(R_a: np.ndarray, R_b: np.ndarray) -> float:
    R_err = R_a.T @ R_b
    c = (np.trace(R_err) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _recover_tag_T_camera(png_path: Path, truth: dict):
    """metrics_logger.MetricsLogger._on_holdout_color_image, replicated
    call-for-call (see module docstring for why it can't be imported)."""
    from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
    from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
    from dimos.perception.fiducial.marker_pose import (
        camera_info_to_cv_matrices,
        camera_optical_frame_id,
        create_aruco_detector,
        estimate_marker_pose,
        marker_reprojection_error,
        rvec_tvec_to_transform,
    )

    K = np.array(truth["K"], dtype=np.float64)
    info = CameraInfo(
        K=K.flatten().tolist(),
        D=list(truth["D"]),
        width=truth["width"],
        height=truth["height"],
        distortion_model="plumb_bob",
        frame_id="camera_optical",
    )
    cam_mtx, dist = camera_info_to_cv_matrices(info)
    detector = create_aruco_detector(DICTIONARY)

    gray_px = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    assert gray_px is not None, png_path
    # Same message type the color_image transport delivers (3-channel BGR).
    image = Image.from_numpy(
        cv2.cvtColor(gray_px, cv2.COLOR_GRAY2BGR),
        format=ImageFormat.BGR,
        frame_id="camera_optical",
        ts=1.0,
    )

    # -- the replicated path ------------------------------------------------
    gray = image.to_grayscale().as_numpy()
    corners, ids, _ = detector.detectMarkers(gray)
    assert ids is not None and len(ids) > 0, f"{png_path.name}: no markers detected"

    optical_frame = camera_optical_frame_id(image, info)
    for corner_set, mid_arr in zip(corners, ids):
        mid = int(mid_arr[0])
        if mid != MARKER_ID:
            continue
        pose = estimate_marker_pose(
            corner_set,
            MARKER_LENGTH_M,
            cam_mtx,
            dist,
            distortion_model=info.distortion_model,
        )
        assert pose is not None, f"{png_path.name}: solvePnP failed"
        rvec, tvec = pose
        optical_t_marker = rvec_tvec_to_transform(
            rvec, tvec, frame_id=optical_frame, child_frame_id=f"marker_{mid}", ts=image.ts
        )
        marker_t_camera = optical_t_marker.inverse()  # tag's fixed frame -> camera
        corners_2d = corner_set.reshape(4, 2).astype("float32")
        reproj = marker_reprojection_error(
            corners_2d,
            MARKER_LENGTH_M,
            cam_mtx,
            dist,
            rvec,
            tvec,
            distortion_model=info.distortion_model,
        )
        return marker_t_camera, float(reproj)
    raise AssertionError(f"{png_path.name}: tag {MARKER_ID} not among detected ids {ids.ravel().tolist()}")


def _as_matrix(transform) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = transform.rotation.to_rotation_matrix()
    T[:3, 3] = [transform.translation.x, transform.translation.y, transform.translation.z]
    return T


def test_recovered_tag_T_camera_within_1cm_1deg(rendered: dict) -> None:
    assert rendered["dictionary"] == DICTIONARY and rendered["marker_id"] == MARKER_ID
    assert len(rendered["frames"]) == 3
    for frame in rendered["frames"]:
        recovered_tf, reproj = _recover_tag_T_camera(OUT_DIR / frame["png"], rendered)
        T_rec = _as_matrix(recovered_tf)
        T_truth = np.array(frame["tag_T_camera"])

        err_m = float(np.linalg.norm(T_rec[:3, 3] - T_truth[:3, 3]))
        err_deg = _rotation_angle_deg(T_rec[:3, :3], T_truth[:3, :3])
        print(
            f"{frame['png']}: |t| err {err_m * 1000:.2f} mm, rot err {err_deg:.3f} deg, "
            f"reproj {reproj:.3f} px"
        )
        assert err_m < TOL_M, f"{frame['png']}: translation error {err_m:.4f} m >= {TOL_M} m"
        assert err_deg < TOL_DEG, f"{frame['png']}: rotation error {err_deg:.3f} deg >= {TOL_DEG} deg"
        assert recovered_tf.frame_id == f"marker_{MARKER_ID}"
        assert recovered_tf.child_frame_id == "camera_optical"


def test_start_end_displacement_matches_truth(rendered: dict) -> None:
    """The referee's actual job: the camera displacement between the first
    and last pose, measured purely in the tag's fixed frame, must match the
    known ground-truth displacement within 1 cm."""
    frames = rendered["frames"]
    rec_first, _ = _recover_tag_T_camera(OUT_DIR / frames[0]["png"], rendered)
    rec_last, _ = _recover_tag_T_camera(OUT_DIR / frames[-1]["png"], rendered)
    d_rec = float(
        np.linalg.norm(_as_matrix(rec_last)[:3, 3] - _as_matrix(rec_first)[:3, 3])
    )
    d_truth = float(
        np.linalg.norm(
            np.array(frames[-1]["tag_T_camera"])[:3, 3]
            - np.array(frames[0]["tag_T_camera"])[:3, 3]
        )
    )
    print(f"start->end displacement: recovered {d_rec:.4f} m vs truth {d_truth:.4f} m")
    assert abs(d_rec - d_truth) < TOL_M


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
