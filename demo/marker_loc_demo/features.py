"""Frame-to-frame natural-feature tracking ("visual gyro") for the
markerless-seed spike — see `trial/markerless-seed.md`. Real corners
(`cv2.goodFeaturesToTrack` on the poster checkerboard texture, `posters.py`)
tracked frame-to-frame with real LK optical flow (`cv2.calcOpticalFlowPyrLK`)
on real rendered pixels. No injected correspondence, no ground truth read.

Math: for a camera undergoing a small frame-to-frame motion where scene
depth is large relative to the translation step (true here — centimeter-
scale odometry steps at 10 Hz vs. >=1m to the wall texture), the homography
that best explains matched-point motion approximates the pure-rotation
homography

    x2_px ~ K @ R_cam2_cam1 @ inv(K) @ x1_px

(exact for zero translation regardless of scene depth; the standard
"rotation-only" / visual-gyro identity used in panorama stitching and
rotation-only VO — an approximation here only insofar as the camera also
translates a little between frames). We RANSAC-fit H from LK-tracked point
pairs, recover the nearest true rotation via SVD (orthogonal Procrustes),
then convert the camera-frame rotation estimate into a BASE-frame yaw delta
using the KNOWN base->camera extrinsic (mount pitch + commanded pan angle)
at both timestamps — the same "known, commanded/telemetered" treatment
`camera.py` already gives the pan joint, not a fudge.

Convention check: this module's sign/transpose convention was verified
against `marker_loc_demo`'s own render+camera stack with a pure-yaw,
zero-translation two-frame test before being wired into the pipeline (see
markerless-seed.md's "feature-tracking math check" note) — recovered base
yaw delta matched the injected ground-truth delta to <0.5 deg.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import transforms as tf

MAX_CORNERS = 300
MIN_CORNERS_TO_TRACK = 40  # below this, refresh (re-detect) rather than keep tracking survivors
QUALITY_LEVEL = 0.01
MIN_DISTANCE_PX = 8
LK_WIN = (21, 21)
LK_MAX_LEVEL = 3

MIN_INLIER_MATCHES = 20  # below this, the homography fit isn't trusted at all
RANSAC_REPROJ_THRESH_PX = 2.5
MIN_INLIER_RATIO = 0.5
MAX_TRUSTED_YAW_DELTA_RAD = np.radians(15.0)  # per-step sanity gate against a degenerate fit


@dataclass
class FeatureState:
    """Carried frame-to-frame by the caller (one instance per scenario run)."""

    gray: np.ndarray | None = None
    pts: np.ndarray | None = None  # (N,1,2) float32, pixel coords in `gray`


_SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 30, 0.01)


def _detect(gray: np.ndarray) -> np.ndarray | None:
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=MAX_CORNERS, qualityLevel=QUALITY_LEVEL, minDistance=MIN_DISTANCE_PX)
    if pts is not None:
        cv2.cornerSubPix(gray, pts, (7, 7), (-1, -1), _SUBPIX_CRITERIA)
    return pts


def estimate_camera_rotation(pts1: np.ndarray, pts2: np.ndarray, K: np.ndarray) -> tuple[np.ndarray, int] | None:
    """`pts1`/`pts2`: (N,2) matched pixel coordinates, frame1 -> frame2.
    Returns `(R_cam1_cam2_est, n_inliers)` or None if the fit isn't
    trustworthy (too few points / too few RANSAC inliers)."""
    if len(pts1) < MIN_INLIER_MATCHES:
        return None
    H, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, RANSAC_REPROJ_THRESH_PX)
    if H is None or inlier_mask is None:
        return None
    n_inliers = int(inlier_mask.sum())
    if n_inliers < MIN_INLIER_MATCHES or n_inliers / len(pts1) < MIN_INLIER_RATIO:
        return None
    Kinv = np.linalg.inv(K)
    M = Kinv @ H @ K  # ~= R_cam2_cam1, up to a positive scale factor
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt
    if np.linalg.det(R) < 0:  # reflection, not a rotation -- flip the smallest-singular-value axis
        U[:, -1] *= -1
        R = U @ Vt
    R_cam1_cam2 = R.T  # invert cam2_cam1 -> cam1_cam2
    return R_cam1_cam2, n_inliers


def _predict_points(pts: np.ndarray, R_cam2_cam1_known: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Where `pts` (N,1,2) should land in frame2 if the ONLY camera rotation
    between frames were the known component (here: the commanded pan delta —
    see `track_step`'s docstring). `R_cam2_cam1_known` maps a frame1 ray into
    frame2 (the forward pixel-prediction direction, `x2 ~ K @ R_cam2_cam1 @
    inv(K) @ x1` — see the module docstring). Seeds LK's search near the
    right answer instead of at zero-motion, so LK only has to resolve the
    small RESIDUAL motion (the unknown base-yaw delta we actually want)
    rather than the pan's own several-deg/step sweep — the standard
    gyro/odometry-aided-KLT trick, not a fudge (pan is genuinely known, see
    `camera.cam_pan_yaw`)."""
    M = K @ R_cam2_cam1_known @ np.linalg.inv(K)
    homog = np.concatenate([pts.reshape(-1, 2), np.ones((len(pts), 1))], axis=1)
    proj = homog @ M.T
    proj = proj[:, :2] / proj[:, 2:3]
    return proj.reshape(-1, 1, 2).astype(np.float32)


def track_step(
    state: FeatureState,
    gray: np.ndarray,
    K: np.ndarray,
    T_base_camera_prev: np.ndarray,
    T_base_camera_curr: np.ndarray,
) -> tuple[FeatureState, float | None, dict]:
    """One frame step. `T_base_camera_{prev,curr}` are the KNOWN base->camera
    extrinsics at the previous/current timestep (mount pitch + commanded
    pan — see `camera.base_to_camera_T`); used both to seed LK's search (see
    `_predict_points` — the camera's own continuous pan can move several
    deg/frame, well outside LK's default zero-motion search window, but it's
    a KNOWN quantity so we compensate for it rather than asking LK to find
    it blind) and to convert the vision-estimated RESIDUAL camera-frame
    rotation into a base-frame yaw delta, never to inject the answer.
    Returns `(new_state, base_delta_yaw_rad | None, info)` — None when
    tracking/the fit isn't trusted this step (first frame, too few
    survivors, degenerate homography, or an implausible jump)."""
    info = {"n_tracked": 0, "n_inliers": 0, "trusted": False}
    if state.gray is None or state.pts is None or len(state.pts) < MIN_CORNERS_TO_TRACK:
        return FeatureState(gray=gray, pts=_detect(gray)), None, info

    # forward direction (cam1 -> cam2), matching `_predict_points`'s convention
    R_pred_fwd = T_base_camera_curr[:3, :3].T @ T_base_camera_prev[:3, :3]
    pred_pts = _predict_points(state.pts, R_pred_fwd, K)
    new_pts, status, _err = cv2.calcOpticalFlowPyrLK(
        state.gray, gray, state.pts, pred_pts, winSize=LK_WIN, maxLevel=LK_MAX_LEVEL,
        flags=cv2.OPTFLOW_USE_INITIAL_FLOW,
    )
    if new_pts is None or status is None:
        return FeatureState(gray=gray, pts=_detect(gray)), None, info

    status = status.reshape(-1).astype(bool)
    h, w = gray.shape[:2]
    in_bounds = (new_pts[:, 0, 0] >= 0) & (new_pts[:, 0, 0] < w) & (new_pts[:, 0, 1] >= 0) & (new_pts[:, 0, 1] < h)
    keep = status & in_bounds
    prev_pts = state.pts[keep].reshape(-1, 2)
    curr_pts = new_pts[keep].reshape(-1, 2)
    info["n_tracked"] = int(keep.sum())

    base_delta_yaw = None
    est = estimate_camera_rotation(prev_pts, curr_pts, K)
    if est is not None:
        R_cam1_cam2, n_inliers = est
        info["n_inliers"] = n_inliers
        R_base1_base2 = T_base_camera_prev[:3, :3] @ R_cam1_cam2 @ T_base_camera_curr[:3, :3].T
        dyaw = tf.matrix_to_yaw(R_base1_base2)
        if abs(dyaw) <= MAX_TRUSTED_YAW_DELTA_RAD:
            base_delta_yaw = float(dyaw)
            info["trusted"] = True

    next_pts = _detect(gray) if len(curr_pts) < MIN_CORNERS_TO_TRACK else curr_pts.reshape(-1, 1, 2)
    return FeatureState(gray=gray, pts=next_pts), base_delta_yaw, info
