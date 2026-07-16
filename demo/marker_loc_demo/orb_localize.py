"""LOCALIZE pass for the markerless-v2 ORB/PnP approach (`trial/markerless-
seed.md` v2): match this frame's real ORB descriptors against the persistent
map (`feature_map.py`, built once by `survey.py`), solve PnP against the
matched 3D points, and return a measurement in the exact same
`loc.FusedMeasurement` shape the tag path already produces -- one-shot
absolute pose from matched map points, never an accumulated delta. This is
the design doc's whole point (`trial/markerless-seed.md` v1's "Verdict"
section): no incremental estimate to lose in noise, same as a tag sighting.
`make_localizer`'s return value plugs directly into
`pipeline.run_scenario`'s `extra_localizer` hook.
"""

from __future__ import annotations

import cv2
import numpy as np

from . import camera as cam
from . import feature_map as fm
from . import localization as loc
from . import transforms as tf

MIN_MATCHES = 22  # below this, PnP RANSAC isn't trustworthy at all
MIN_INLIERS = 20  # measured directly (see calibration note below): pos error median
# keeps falling as the inlier-count floor rises (0.49m at >=6, 0.32m at
# >=10, 0.27m at >=15, 0.23m at >=20) -- yield vs. accuracy tradeoff
# resolved hard in favor of accuracy: a wrong-but-confident correction is
# worse than no correction, same lesson the v1 spike's own hybrid mode
# learned. At this floor, full-pipeline corrected ATE with the filter's
# Mahalanobis gate on top lands within ~7% of sparse-tags-alone on a 40s
# calibration window (see markerless-seed.md v2) -- low recall (a fraction
# of frames get a trusted ORB fix), high precision.
MIN_INLIER_RATIO = 0.5
PNP_RANSAC_REPROJ_PX = 4.0  # RANSAC inlier threshold
MAX_REPROJ_MEAN_PX = 3.0  # post-solve mean-inlier-reprojection hard gate, same spirit as loc.REPROJ_MAX_PX

# Per-solve measurement uncertainty model for the ORB/PnP pose -- same shape
# as `localization.py`'s `_per_tag_sigma` (base + reprojection term, shrinks
# with more independent points) but its own scale: an ORB map point
# inherits BOTH the tag-anchored survey pose's own error AND its own
# triangulation error, so a single match is noisier than a single tag
# corner. Calibrated by direct measurement: ran the localizer against
# ground truth over a batch of frames (never done inside the algorithm
# itself, only for this offline calibration) and fit sigma to the observed
# error distribution at the MIN_INLIERS operating point (median pos error
# ~0.3-0.5m, shrinking with inlier count) -- see
# `trial/markerless-seed.md` v2's calibration note. Not guessed, and
# deliberately looser than the tag model's tight ~3-15cm base: an honest
# reflection of ORB/PnP-against-a-map being structurally noisier here.
ORB_SIGMA_POS_BASE_M = 0.35
ORB_SIGMA_POS_PER_PX = 0.03
ORB_SIGMA_POS_PER_INLIER = -0.010  # shrinks with more inliers, floored below
ORB_SIGMA_POS_MIN_M = 0.12
ORB_SIGMA_YAW_BASE_RAD = np.radians(6.0)
ORB_SIGMA_YAW_PER_PX = np.radians(0.5)


def make_localizer(fmap: fm.FeatureMap, intr: cam.Intrinsics, *, min_matches: int = MIN_MATCHES):
    """Returns `localize(image, T_base_camera) -> loc.FusedMeasurement | None`
    matching `pipeline.run_scenario`'s `extra_localizer` contract. `fmap`'s
    descriptors are matched once per call (fresh ORB detection on `image`
    every frame -- no tracking, no state carried between frames, by design:
    each call is an independent absolute relocalization attempt)."""
    orb = fm.get_orb()
    matcher = fm.get_matcher()
    map_desc = fmap.descriptors
    map_pts = fmap.points

    def localize(image: np.ndarray, T_base_camera: np.ndarray) -> loc.FusedMeasurement | None:
        if len(fmap) < min_matches:
            return None
        pts, desc = fm.detect(orb, image)
        if desc is None or len(pts) < 4:
            return None

        knn = matcher.knnMatch(desc, map_desc, k=2)
        obj_pts, img_pts = [], []
        for pair in knn:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < fm.MATCH_RATIO * n.distance:
                obj_pts.append(map_pts[m.trainIdx])
                img_pts.append(pts[m.queryIdx])
        if len(obj_pts) < min_matches:
            return None
        obj_pts = np.array(obj_pts, dtype=np.float64)
        img_pts = np.array(img_pts, dtype=np.float64)

        ok, rvec, tvec, inliers = cv2.solvePnPRansac(
            obj_pts, img_pts, intr.K, intr.dist,
            reprojectionError=PNP_RANSAC_REPROJ_PX, confidence=0.999, iterationsCount=200,
            flags=cv2.SOLVEPNP_EPNP,
        )
        if not ok or inliers is None or len(inliers) < MIN_INLIERS:
            return None
        inlier_ratio = len(inliers) / len(obj_pts)
        if inlier_ratio < MIN_INLIER_RATIO:
            return None

        idx = inliers.reshape(-1)
        ok2, rvec, tvec = cv2.solvePnP(
            obj_pts[idx], img_pts[idx], intr.K, intr.dist,
            rvec=rvec, tvec=tvec, useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok2:
            return None

        T_camera_world = tf.rvec_tvec_to_T(rvec, tvec)
        proj = cam.project(T_camera_world, obj_pts[idx], intr)
        reproj_err = float(np.mean(np.linalg.norm(proj - img_pts[idx], axis=1)))
        if reproj_err > MAX_REPROJ_MEAN_PX:
            return None

        # obj_pts are already WORLD coordinates (not marker-local like the
        # tag path), so solvePnP directly gives world->camera -- invert once.
        T_world_camera = tf.invert(T_camera_world)
        T_world_base = T_world_camera @ tf.invert(T_base_camera)
        x, y = float(T_world_base[0, 3]), float(T_world_base[1, 3])
        yaw = tf.matrix_to_yaw(T_world_base[:3, :3])

        n_in = int(len(idx))
        sigma_pos = max(
            ORB_SIGMA_POS_MIN_M,
            ORB_SIGMA_POS_BASE_M + ORB_SIGMA_POS_PER_PX * reproj_err + ORB_SIGMA_POS_PER_INLIER * n_in,
        )
        sigma_yaw = ORB_SIGMA_YAW_BASE_RAD + ORB_SIGMA_YAW_PER_PX * reproj_err

        return loc.FusedMeasurement(
            x=x, y=y, yaw=yaw, sigma_pos=sigma_pos, sigma_yaw=sigma_yaw,
            n_tags=n_in,  # reused field: here means "n_inliers", see loc.FusedMeasurement
            mean_reproj_px=reproj_err,
        )

    return localize
