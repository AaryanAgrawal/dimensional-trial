"""The module under test: real ArUco/AprilTag detection -> solvePnP -> invert
against the marker map -> multi-tag fusion -> outlier gate -> a held,
smoothed `world -> odom` TF correction. Mirrors dimos's
`dimos/mapping/relocalization/module.py` pattern (there: ICP fitness-gated
`world -> map`; here: reprojection + Mahalanobis-gated `world -> odom`) —
same job, camera instead of lidar.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import camera as cam
from . import tags
from . import transforms as tf
from .world import MarkerSpec

REPROJ_MAX_PX = 3.0  # per-tag hard reject: solve too poor to trust at all
GATE_SIGMA = 5.0  # Mahalanobis outlier gate, in combined sigmas

# IPPE mirror-pose ambiguity gate: a planar square viewed at weak perspective
# (small on-sensor, near head-on) has two PnP solutions whose reprojection
# errors can be nearly identical while one is the flipped (wrong) pose —
# Collins & Bartoli's IPPE result, measured directly by the envelope sweep
# (3.15m worst-frame spike in the 8-tag density run traced to exactly this).
# `solvePnPGeneric` returns both candidates; we only trust the best solution
# when it beats the runner-up by this reprojection-error ratio. Tuned on the
# sweep's gate-off trial dump (see out/envelope/ENVELOPE.md "ambiguity gate"
# section): R chosen as the smallest tested value where accepted poses are
# >=95% correct across the distance+angle sweeps.
AMBIGUITY_RATIO_MIN = 2.0

# Per-tag measurement uncertainty model: base noise floor + a reprojection
# term + a *range-dependent* term (the spec's own mitigation for "distance
# falloff": PnP pose error from a fixed corner-detection noise grows
# roughly quadratically with range for a fixed tag size).
MEAS_SIGMA_POS_BASE_M = 0.03
MEAS_SIGMA_POS_PER_PX = 0.015
MEAS_SIGMA_POS_RANGE_COEF = 0.03  # sigma += COEF * range_m**2
MEAS_SIGMA_YAW_BASE_RAD = np.radians(1.0)
MEAS_SIGMA_YAW_PER_PX = np.radians(0.3)
MEAS_SIGMA_YAW_RANGE_COEF = np.radians(0.8)  # sigma += COEF * range_m**2

# Growth of the held correction's uncertainty between sightings. This is
# *linear* in elapsed time, not sqrt(n)-diffusion: the dominant real driver is
# a slowly-varying heading-rate bias (see odom.py), so locally the position/
# heading discrepancy that accumulates while unobserved grows roughly at a
# constant rate, not as an independent-increment random walk. Rates picked to
# roughly track this odometry model's empirical drift-per-second (see
# demo notes) with headroom.
PROCESS_RATE_POS_M_PER_STEP = 0.025  # ~0.25 m/s of unaccounted drift
PROCESS_RATE_YAW_RAD_PER_STEP = np.radians(0.06)  # ~0.6 deg/step = 6 deg/s

INIT_SIGMA_POS_M = 0.05
INIT_SIGMA_YAW_RAD = np.radians(2.0)


def _object_points(size_m: float) -> np.ndarray:
    """Corner order matches OpenCV ArUco / dimos `marker_pose.py` convention."""
    h = size_m / 2.0
    return np.array([[-h, h, 0.0], [h, h, 0.0], [h, -h, 0.0], [-h, -h, 0.0]], dtype=np.float32)


@dataclass
class TagDetection:
    marker_id: int
    reproj_err_px: float
    range_m: float
    T_world_camera: np.ndarray
    # runner-up reprojection error / best reprojection error, from
    # `solvePnPGeneric`'s two IPPE candidates. Large = the best solution is
    # unambiguous; near 1.0 = the flipped mirror pose explains the pixels
    # almost as well and the best pose cannot be trusted. `inf` when the
    # solver returns a single candidate.
    ambiguity_ratio: float = float("inf")
    # Raw solvePnP result in camera-local coordinates (camera <- marker),
    # independent of any assumed marker-map pose -- unlike `T_world_camera`,
    # which is already composed against `marker_map_by_id`'s (possibly
    # unknown/being-surveyed) world pose. `map_refinement.py`'s self-survey
    # needs this: during a self-survey there is no trusted marker map yet,
    # so it composes this raw camera-frame solve against the robot's own
    # (drifting) odometry-estimated pose instead. Always populated by
    # `detect_and_solve`; default `None` only so the dataclass stays valid
    # if ever constructed without it.
    T_camera_marker: np.ndarray | None = None


def _reproj_err_px(obj: np.ndarray, img: np.ndarray, rvec: np.ndarray, tvec: np.ndarray, intr: cam.Intrinsics) -> float:
    projected, _ = cv2.projectPoints(obj, rvec, tvec, intr.K, intr.dist)
    residual = projected.reshape(4, 2) - img.reshape(4, 2)
    return float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))


def detect_and_solve(
    frame: np.ndarray, marker_map_by_id: dict[int, MarkerSpec], intr: cam.Intrinsics
) -> list[TagDetection]:
    """Real cv2.aruco detection + solvePnPGeneric(IPPE_SQUARE) on real pixels.

    Uses `solvePnPGeneric` (both IPPE candidates) rather than `solvePnP` (best
    only) so each detection carries an `ambiguity_ratio` for the mirror-pose
    gate — the flipped candidate of a weak-perspective planar tag can have
    reprojection error as low as the correct one, which a best-solution-only
    solve silently hides.
    """
    corners, ids, _ = tags.get_detector().detectMarkers(frame)
    out: list[TagDetection] = []
    if ids is None:
        return out
    for c, mid_arr in zip(corners, ids.flatten()):
        mid = int(mid_arr)
        m = marker_map_by_id.get(mid)
        if m is None:
            continue
        obj = _object_points(m.size_m)
        img = c.reshape(4, 1, 2).astype(np.float32)
        n_sols, rvecs, tvecs, _errs = cv2.solvePnPGeneric(
            obj, img, intr.K, intr.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE
        )
        if n_sols < 1:
            continue
        # Rank candidates by our own reprojection metric (same RMSE convention
        # as the rest of the pipeline, rather than trusting the solver's
        # ordering/metric blindly). Drop non-finite candidates outright —
        # solvePnPGeneric can emit a fully-NaN solution on degenerate corner
        # input (observed ~0.1% of weak-perspective trials in the envelope
        # sweep); a NaN pose must never reach the fusion stage or the stats.
        cand = [
            (i, _reproj_err_px(obj, img, rvecs[i], tvecs[i], intr))
            for i in range(n_sols)
            if np.all(np.isfinite(rvecs[i])) and np.all(np.isfinite(tvecs[i]))
        ]
        cand = [(i, e) for i, e in cand if np.isfinite(e)]
        if not cand:
            continue
        n_sols = len(cand)
        errs = [e for _, e in cand]
        idx_map = [i for i, _ in cand]
        order = np.argsort(errs)
        best = idx_map[int(order[0])]
        reproj_err = errs[int(order[0])]
        if n_sols > 1:
            second_err = errs[int(order[1])]
            ambiguity_ratio = float(second_err / reproj_err) if reproj_err > 1e-12 else float("inf")
        else:
            ambiguity_ratio = float("inf")

        rvec, tvec = rvecs[best], tvecs[best]
        T_camera_marker = tf.rvec_tvec_to_T(rvec, tvec)
        T_world_camera = m.T_world_marker @ tf.invert(T_camera_marker)
        range_m = float(tvec.reshape(3)[2])
        out.append(
            TagDetection(
                marker_id=mid,
                reproj_err_px=reproj_err,
                range_m=range_m,
                T_world_camera=T_world_camera,
                ambiguity_ratio=ambiguity_ratio,
                T_camera_marker=T_camera_marker,
            )
        )
    return out


def _base_xyzyaw(T_world_camera: np.ndarray, T_base_camera: np.ndarray) -> tuple[float, float, float]:
    T_world_base = T_world_camera @ tf.invert(T_base_camera)
    x, y = T_world_base[0, 3], T_world_base[1, 3]
    yaw = tf.matrix_to_yaw(T_world_base[:3, :3])
    return float(x), float(y), float(yaw)


@dataclass
class FusedMeasurement:
    x: float
    y: float
    yaw: float
    sigma_pos: float
    sigma_yaw: float
    n_tags: int
    mean_reproj_px: float


def per_tag_sigma(d: TagDetection) -> tuple[float, float]:
    """(sigma_pos, sigma_yaw) for a single detection: base + reprojection +
    range-squared terms (range dominates — this is the falloff mitigation)."""
    r2 = d.range_m**2
    sigma_pos = MEAS_SIGMA_POS_BASE_M + MEAS_SIGMA_POS_PER_PX * d.reproj_err_px + MEAS_SIGMA_POS_RANGE_COEF * r2
    sigma_yaw = MEAS_SIGMA_YAW_BASE_RAD + MEAS_SIGMA_YAW_PER_PX * d.reproj_err_px + MEAS_SIGMA_YAW_RANGE_COEF * r2
    return sigma_pos, sigma_yaw


def tag_gate_ok(d: TagDetection, *, ambiguity_ratio_min: float | None = None) -> bool:
    """Per-tag trust gate: absolute reprojection quality AND the best PnP
    candidate must beat the mirror candidate by `ambiguity_ratio_min` (see
    AMBIGUITY_RATIO_MIN; resolved at call time so sweeps can tune/disable it —
    1.0 disables). Both must hold — a flipped pose passes the absolute gate
    with ease, which is exactly why the ratio gate exists."""
    if ambiguity_ratio_min is None:
        ambiguity_ratio_min = AMBIGUITY_RATIO_MIN
    return d.reproj_err_px <= REPROJ_MAX_PX and d.ambiguity_ratio >= ambiguity_ratio_min


def fuse_detections(
    detections: list[TagDetection],
    T_base_camera: np.ndarray,
    *,
    ambiguity_ratio_min: float | None = None,
) -> FusedMeasurement | None:
    good = [d for d in detections if tag_gate_ok(d, ambiguity_ratio_min=ambiguity_ratio_min)]
    if not good:
        return None

    xs, ys, yaws = [], [], []
    pos_weights, yaw_weights = [], []
    for d in good:
        x, y, yaw = _base_xyzyaw(d.T_world_camera, T_base_camera)
        sigma_pos, sigma_yaw = per_tag_sigma(d)
        xs.append(x)
        ys.append(y)
        yaws.append(yaw)
        pos_weights.append(1.0 / sigma_pos**2)
        yaw_weights.append(1.0 / sigma_yaw**2)

    pos_w = np.array(pos_weights)
    yaw_w = np.array(yaw_weights)
    pos_w_norm = pos_w / pos_w.sum()
    yaw_w_norm = yaw_w / yaw_w.sum()

    fused_x = float(np.dot(pos_w_norm, xs))
    fused_y = float(np.dot(pos_w_norm, ys))
    sin_sum = float(np.dot(yaw_w_norm, np.sin(yaws)))
    cos_sum = float(np.dot(yaw_w_norm, np.cos(yaws)))
    fused_yaw = float(np.arctan2(sin_sum, cos_sum))

    # inverse-variance fusion: combined variance = 1 / sum(1/sigma_i^2)
    sigma_pos = float(1.0 / np.sqrt(pos_w.sum()))
    sigma_yaw = float(1.0 / np.sqrt(yaw_w.sum()))
    mean_reproj = float(np.mean([d.reproj_err_px for d in good]))

    return FusedMeasurement(
        x=fused_x, y=fused_y, yaw=fused_yaw, sigma_pos=sigma_pos, sigma_yaw=sigma_yaw,
        n_tags=len(good), mean_reproj_px=mean_reproj,
    )


def _wrap(a: float) -> float:
    return (a + np.pi) % (2 * np.pi) - np.pi


def to_world_odom_measurement(fused: FusedMeasurement, T_odom_base_raw: np.ndarray) -> FusedMeasurement:
    """`fused` is a `world_T_base` estimate from the tags. Compose against the
    current raw (drifting) odom reading to get a `world_T_odom` correction
    candidate: `world_T_odom = world_T_base_meas @ inv(odom_T_base_raw)`.
    Same (x, y, yaw) reduction as everywhere else — the base moves in a plane.
    """
    T_world_base_meas = tf.T_from_xyzyaw(fused.x, fused.y, 0.0, fused.yaw)
    T_world_odom_meas = T_world_base_meas @ tf.invert(T_odom_base_raw)
    x, y = float(T_world_odom_meas[0, 3]), float(T_world_odom_meas[1, 3])
    yaw = tf.matrix_to_yaw(T_world_odom_meas[:3, :3])
    return FusedMeasurement(
        x=x, y=y, yaw=yaw, sigma_pos=fused.sigma_pos, sigma_yaw=fused.sigma_yaw,
        n_tags=fused.n_tags, mean_reproj_px=fused.mean_reproj_px,
    )


@dataclass
class CorrectionFilter:
    """Holds a smoothed `world -> odom` correction (x, y, yaw). Never touches
    raw odom; publishes/holds a correction on top, exactly like
    `RelocalizationModule._publish_tf` holding the last accepted `world -> map`
    transform between successful relocalizations.
    """

    corr_x: float = 0.0
    corr_y: float = 0.0
    corr_yaw: float = 0.0
    sigma_pos: float = INIT_SIGMA_POS_M
    sigma_yaw: float = INIT_SIGMA_YAW_RAD
    n_accepted: int = 0
    n_rejected: int = 0
    # Scales the process-noise growth rate to match the odometry it's
    # correcting (e.g. the elevator scenario's 3x-noisier odom drifts 3x
    # faster, so the held correction's uncertainty should grow 3x faster
    # too — a zone-specific tuning knob, same idea as the spec's own
    # per-zone tag-size/covariance guidance).
    process_noise_scale: float = 1.0

    def predict_step(self) -> None:
        self.sigma_pos += PROCESS_RATE_POS_M_PER_STEP * self.process_noise_scale
        self.sigma_yaw += PROCESS_RATE_YAW_RAD_PER_STEP * self.process_noise_scale

    def try_update(self, meas: FusedMeasurement) -> bool:
        """`meas` must already be a `world_T_odom` correction candidate — see
        `to_world_odom_measurement`. Returns True iff accepted."""
        pos_resid = float(np.hypot(meas.x - self.corr_x, meas.y - self.corr_y))
        yaw_resid = abs(_wrap(meas.yaw - self.corr_yaw))
        pos_sigma_combined = np.sqrt(self.sigma_pos**2 + meas.sigma_pos**2)
        yaw_sigma_combined = np.sqrt(self.sigma_yaw**2 + meas.sigma_yaw**2)

        if pos_resid / pos_sigma_combined > GATE_SIGMA or yaw_resid / yaw_sigma_combined > GATE_SIGMA:
            self.n_rejected += 1
            return False

        k_pos = self.sigma_pos**2 / (self.sigma_pos**2 + meas.sigma_pos**2)
        k_yaw = self.sigma_yaw**2 / (self.sigma_yaw**2 + meas.sigma_yaw**2)
        self.corr_x += k_pos * (meas.x - self.corr_x)
        self.corr_y += k_pos * (meas.y - self.corr_y)
        self.corr_yaw = _wrap(self.corr_yaw + k_yaw * _wrap(meas.yaw - self.corr_yaw))
        self.sigma_pos = float(np.sqrt((1 - k_pos) * self.sigma_pos**2))
        self.sigma_yaw = float(np.sqrt((1 - k_yaw) * self.sigma_yaw**2))
        self.n_accepted += 1
        return True

    @property
    def T_world_odom(self) -> np.ndarray:
        return tf.T_from_xyzyaw(self.corr_x, self.corr_y, 0.0, self.corr_yaw)
