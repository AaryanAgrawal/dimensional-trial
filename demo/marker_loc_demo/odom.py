"""Dead-reckoned odometry: integrate the ground-truth body-frame deltas with
per-step gaussian noise plus a slow bias random walk on heading rate — heading
drift dominates real (differential-drive) robots because a small yaw-rate
bias integrates into unbounded position error, not just unbounded heading
error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .trajectory import GroundTruth

SIGMA_TRANS_PER_STEP_M = 0.006  # additive noise on each per-step body-frame translation (m)
SIGMA_YAW_PER_STEP_RAD = np.radians(0.05)  # additive white noise on each per-step yaw delta
# Per-step random-walk increment on the yaw-rate bias. Note this compounds
# doubly (bias is a random walk; heading is bias's running sum), so the
# *cumulative* heading error grows like O(steps^1.5), not O(sqrt(steps)) — a
# per-step value that looks tiny still adds up to real drift over 1000 steps.
SIGMA_YAW_BIAS_WALK_RAD = np.radians(0.004)


def _wrap(a: np.ndarray | float) -> np.ndarray | float:
    return (a + np.pi) % (2 * np.pi) - np.pi


@dataclass
class Odometry:
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    yaw: np.ndarray


def simulate_odometry_steps(
    gt: GroundTruth,
    seed: int,
    noise_scale: float = 1.0,
    disturbance_fn: Callable[[float], tuple[float, float]] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The per-step noisy body-frame measurements (`dx_body`, `dy_body`,
    `dyaw`), length `len(gt.t) - 1`, *before* integration — same noise model
    and rng draw order as `simulate_odometry`, just factored out so a caller
    can integrate with a *substituted* yaw stream (the markerless-seed spike's
    hybrid mode fuses in a vision-derived yaw delta between sparse tag
    sightings; see `marker_loc_demo/features.py` and `pipeline.run_scenario`'s
    `use_features` path) while keeping the exact same wheel translation model.
    """
    rng = np.random.default_rng(seed)
    n = len(gt.t)

    dx_world = np.diff(gt.x)
    dy_world = np.diff(gt.y)
    dyaw_true = _wrap(np.diff(gt.yaw))
    c, s = np.cos(gt.yaw[:-1]), np.sin(gt.yaw[:-1])
    dx_body = c * dx_world + s * dy_world
    dy_body = -s * dx_world + c * dy_world

    dx_meas = np.empty(n - 1)
    dy_meas = np.empty(n - 1)
    dyaw_meas = np.empty(n - 1)

    bias = 0.0
    for k in range(n - 1):
        extra_trans, extra_yaw = disturbance_fn(float(gt.t[k])) if disturbance_fn is not None else (0.0, 0.0)
        bias += rng.normal(0, SIGMA_YAW_BIAS_WALK_RAD * noise_scale)
        dyaw_meas[k] = dyaw_true[k] + bias + rng.normal(0, SIGMA_YAW_PER_STEP_RAD * noise_scale + extra_yaw)
        dx_meas[k] = dx_body[k] + rng.normal(0, SIGMA_TRANS_PER_STEP_M * noise_scale + extra_trans)
        dy_meas[k] = dy_body[k] + rng.normal(0, SIGMA_TRANS_PER_STEP_M * noise_scale + extra_trans)

    return dx_meas, dy_meas, dyaw_meas


def integrate_odometry(
    t: np.ndarray, x0: float, y0: float, yaw0: float, dx_meas: np.ndarray, dy_meas: np.ndarray, dyaw_meas: np.ndarray
) -> Odometry:
    """Dead-reckoning integration of per-step body-frame deltas — shared by
    `simulate_odometry` (wheel-only) and the markerless-seed hybrid pipeline
    (wheel translation + vision-fused yaw)."""
    n = len(dx_meas) + 1
    x = np.empty(n)
    y = np.empty(n)
    yaw = np.empty(n)
    x[0], y[0], yaw[0] = x0, y0, yaw0
    for k in range(n - 1):
        c0, s0 = np.cos(yaw[k]), np.sin(yaw[k])
        x[k + 1] = x[k] + c0 * dx_meas[k] - s0 * dy_meas[k]
        y[k + 1] = y[k] + s0 * dx_meas[k] + c0 * dy_meas[k]
        yaw[k + 1] = _wrap(yaw[k] + dyaw_meas[k])
    return Odometry(t=t.copy(), x=x, y=y, yaw=yaw)


def simulate_odometry(
    gt: GroundTruth,
    seed: int,
    noise_scale: float = 1.0,
    disturbance_fn: Callable[[float], tuple[float, float]] | None = None,
) -> Odometry:
    """`noise_scale` multiplies all per-step sigmas (used for the elevator
    scenario's "odom noise triples" zone). `disturbance_fn(t) -> (extra_trans_
    sigma_m, extra_yaw_sigma_rad)`, if given, is *added* on top of the scaled
    per-step sigma at each step's timestamp — a time-windowed burst (e.g. cab
    accel/decel bleeding into the horizontal dead-reckoning solve) layered on
    top of the steady-state noise floor, rather than another multiplier on it.
    Defaults to no-op (zero extra), so every existing caller is unaffected.
    """
    dx_meas, dy_meas, dyaw_meas = simulate_odometry_steps(gt, seed, noise_scale, disturbance_fn)
    return integrate_odometry(gt.t, gt.x[0], gt.y[0], gt.yaw[0], dx_meas, dy_meas, dyaw_meas)
