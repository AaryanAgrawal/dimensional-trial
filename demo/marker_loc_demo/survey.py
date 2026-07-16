"""SURVEY pass for the markerless-v2 ORB/PnP approach (`trial/markerless-
seed.md` v2): drive the standard trajectory once with the 3 sparse anchor
tags visible and build a persistent 3D ORB feature map from real rendered
pixels — "tags supply scale, features fill the gaps," at build time.

Reuses `pipeline.run_scenario` unmodified for the actual render/detect/fuse/
gate/hold loop (a plain tags-sparse run) via the `frame_observer_fn` hook —
this module only harvests per-frame poses + ORB keypoints from that hook and
does the triangulation bookkeeping; it does not duplicate any of the
odometry/tag/filter logic.

Camera pose at each survey frame is the tag-corrected `world -> odom` hold
(NOT ground truth — the map inherits whatever pose error the sparse-tag
filter has accumulated at that instant, an honest, measured effect, see
`trial/markerless-seed.md`). Map points come from two-view triangulation
between the first and last frame of a short window (ORB descriptor
matching, no LK, no frame-to-frame incremental rotation estimation — the
mechanism the v1 spike diagnosed as the failure point plays no part here),
kept only if cheirality + reprojection + parallax gates pass.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import camera as cam
from . import feature_map as fm
from . import pipeline
from . import transforms as tf
from . import world

WINDOW_FRAMES = 4  # ~0.4s @ 10Hz -- camera pans ~24deg in that span (PAN_PERIOD_S=6s):
# enough two-view parallax for stable triangulation without the viewpoint
# change getting so wide ORB/BRIEF matching itself starts failing.
MIN_PARALLAX_DEG = 2.0  # below this the two rays are ~collinear -- triangulation is numerically degenerate
MAX_TRIANGULATION_REPROJ_PX = 3.0  # symmetric two-view reprojection gate, same units/spirit as loc.REPROJ_MAX_PX
MIN_DEPTH_M = 0.1
MAX_DEPTH_M = 6.0  # discard absurdly-far/behind-camera degenerate solves
MIN_RAW_MATCHES = 4


@dataclass
class _Buffered:
    k: int
    pts: np.ndarray
    desc: np.ndarray | None
    T_world_camera: np.ndarray


def _triangulate_pair(
    a: _Buffered, b: _Buffered, intr: cam.Intrinsics, matcher: cv2.BFMatcher
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Match `a` <-> `b` by ORB descriptor (ratio test), triangulate each
    matched pair via `cv2.triangulatePoints` using the two known(-ish)
    survey camera poses, and gate on cheirality + reprojection + parallax.
    Returns `(points_world (P,3), descriptors (P,32), reproj_px (P,))`."""
    empty = (np.zeros((0, 3)), np.zeros((0, 32), dtype=np.uint8), np.zeros((0,)))
    if a.desc is None or b.desc is None or len(a.pts) == 0 or len(b.pts) == 0:
        return empty

    knn = matcher.knnMatch(a.desc, b.desc, k=2)
    good = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < fm.MATCH_RATIO * n.distance:
            good.append(m)
    if len(good) < MIN_RAW_MATCHES:
        return empty

    T_cam_a_world = tf.invert(a.T_world_camera)
    T_cam_b_world = tf.invert(b.T_world_camera)
    P_a = intr.K @ T_cam_a_world[:3, :]
    P_b = intr.K @ T_cam_b_world[:3, :]

    pts_a = np.array([a.pts[m.queryIdx] for m in good], dtype=np.float64).T  # (2,N)
    pts_b = np.array([b.pts[m.trainIdx] for m in good], dtype=np.float64).T

    X_h = cv2.triangulatePoints(P_a, P_b, pts_a, pts_b)  # (4,N)
    X = (X_h[:3] / X_h[3]).T  # (N,3) world

    Xa_cam = (T_cam_a_world[:3, :3] @ X.T).T + T_cam_a_world[:3, 3]
    Xb_cam = (T_cam_b_world[:3, :3] @ X.T).T + T_cam_b_world[:3, 3]
    depth_ok = (
        (Xa_cam[:, 2] > MIN_DEPTH_M) & (Xa_cam[:, 2] < MAX_DEPTH_M)
        & (Xb_cam[:, 2] > MIN_DEPTH_M) & (Xb_cam[:, 2] < MAX_DEPTH_M)
    )

    proj_a = cam.project(T_cam_a_world, X, intr)
    proj_b = cam.project(T_cam_b_world, X, intr)
    err_a = np.linalg.norm(proj_a - pts_a.T, axis=1)
    err_b = np.linalg.norm(proj_b - pts_b.T, axis=1)
    mean_err = (err_a + err_b) / 2.0
    reproj_ok = mean_err < MAX_TRIANGULATION_REPROJ_PX

    cam_a_pos = a.T_world_camera[:3, 3]
    cam_b_pos = b.T_world_camera[:3, 3]
    ray_a = X - cam_a_pos
    ray_b = X - cam_b_pos
    cos_par = np.sum(ray_a * ray_b, axis=1) / (
        np.linalg.norm(ray_a, axis=1) * np.linalg.norm(ray_b, axis=1) + 1e-12
    )
    parallax_deg = np.degrees(np.arccos(np.clip(cos_par, -1.0, 1.0)))
    parallax_ok = parallax_deg > MIN_PARALLAX_DEG

    keep = depth_ok & reproj_ok & parallax_ok
    if not np.any(keep):
        return empty

    descs = np.array([b.desc[m.trainIdx] for m in good], dtype=np.uint8)
    return X[keep], descs[keep], mean_err[keep]


def run_survey(
    gt,
    anchor_markers: list[world.MarkerSpec],
    poster_map: list[world.MarkerSpec],
    out_dir: str,
    *,
    seed: int,
    window: int = WINDOW_FRAMES,
) -> tuple[fm.FeatureMap, dict]:
    orb = fm.get_orb()
    matcher = fm.get_matcher()
    intr = cam.Intrinsics.default()

    buffer: list[_Buffered] = []
    all_points: list[np.ndarray] = []
    all_descs: list[np.ndarray] = []
    all_errs: list[np.ndarray] = []
    n_windows = 0

    def observe(k: int, image: np.ndarray, T_base_cam: np.ndarray, T_world_base_corr: np.ndarray) -> None:
        nonlocal buffer, n_windows
        T_world_camera = T_world_base_corr @ T_base_cam
        pts, desc = fm.detect(orb, image)
        buffer.append(_Buffered(k=k, pts=pts, desc=desc, T_world_camera=T_world_camera))
        if len(buffer) >= window + 1:
            X, D, E = _triangulate_pair(buffer[0], buffer[-1], intr, matcher)
            if len(X):
                all_points.append(X)
                all_descs.append(D)
                all_errs.append(E)
            n_windows += 1
            buffer = []

    survey_result = pipeline.run_scenario(
        "orb-survey", gt, anchor_markers, out_dir, seed=seed, poster_map=poster_map, frame_observer_fn=observe,
        unique_poster_texture=True,
    )

    if all_points:
        points = np.concatenate(all_points, axis=0)
        descs = np.concatenate(all_descs, axis=0)
        errs = np.concatenate(all_errs, axis=0)
    else:
        points = np.zeros((0, 3))
        descs = np.zeros((0, 32), dtype=np.uint8)
        errs = np.zeros((0,))

    fmap = fm.FeatureMap(points=points, descriptors=descs, n_obs=np.full(len(points), 2, dtype=int), mean_reproj_px=errs)
    diag = {
        "survey_ate_corrected_m": survey_result["ate_rmse_corrected_m"],
        "survey_ate_raw_m": survey_result["ate_rmse_raw_odom_m"],
        "survey_detection_rate": survey_result["detection_rate_frames_with_ge1_tag"],
        "n_windows": n_windows,
        "n_map_points": len(fmap),
        "mean_triangulation_reproj_px": float(np.mean(errs)) if len(errs) else None,
    }
    return fmap, diag
