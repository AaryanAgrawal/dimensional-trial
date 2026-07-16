"""Bonus scenario: a small mirrored-box elevator cab. Odom noise triples
(cab vibration + a non-inertial, moving reference frame degrade dead-
reckoning) and only 2 tags exist in the whole room, so at most 1-2 are ever
visible. Shows the same correction filter holding a stable pose anyway —
same code path as the nominal scenario, just a smaller world.
"""

from __future__ import annotations

import numpy as np

from . import render
from . import transforms as tf
from .trajectory import GroundTruth
from .world import MarkerSpec

ROOM_W = 2.0  # cab interior, x
ROOM_D = 2.0  # cab interior, y (door at y=0, back wall at y=ROOM_D)
TAG_SIZE_M = 0.15
ODOM_NOISE_SCALE = 3.0
HZ = 10.0

# --- Harness upgrade: cab-motion odom disturbance + door-visibility windows ---
#
# Laid on top of the existing 4 keyframes (0=enter start, 4=reached dwell,
# 10=start backing out, 16=exit end) without touching the ground-truth
# geometry itself -- real elevators interlock the door shut before the cab
# moves, so the schedule below nests cleanly inside those boundaries:
#   [0.0, 4.0)  door OPEN,  entering        -- hallway backlight/exposure hunt
#   [4.0, 5.0)  door CLOSED, cab ACCELERATES -- vertical accel bleed + jerk blur
#   [5.0, 8.0)  door CLOSED, steady transit  -- baseline 3x vibration only
#   [8.0, 9.0)  door CLOSED, cab DECELERATES -- vertical accel bleed + jerk blur
#   [9.0, 16.0) door OPEN,  arrived/exiting  -- hallway backlight/exposure hunt
DOOR_OPEN_WINDOWS = [(0.0, 4.0), (9.0, 16.0)]
ACCEL_WINDOW = (4.0, 5.0)
DECEL_WINDOW = (8.0, 9.0)

# Door-open: corridor light behind the robot washes into the shot / the
# camera's auto-exposure is still settling from the brighter hallway --
# modeled as elevated frame noise (see `render_overrides`).
DOOR_OPEN_NOISE_MULT = 1.8

# Accel/decel: cab jerk transmitted through the mount blurs the frame.
MOTION_BLUR_MULT = 2.2

# Accel/decel: vertical acceleration leaking into the horizontal
# dead-reckoning solve (pitch/roll misestimate -> spurious xy translation),
# plus a heading-rate burst from the same jerk. Added on top of the steady
# 3x-noise floor (`ODOM_NOISE_SCALE`), not multiplied into it -- these are a
# transient burst, not a new steady state.
EXTRA_TRANS_SIGMA_M = 0.05
EXTRA_YAW_SIGMA_RAD = np.radians(0.6)


def _in_window(t: float, window: tuple[float, float]) -> bool:
    return window[0] <= t < window[1]


def door_open(t: float) -> bool:
    return any(_in_window(t, w) for w in DOOR_OPEN_WINDOWS)


def in_motion_transient(t: float) -> bool:
    return _in_window(t, ACCEL_WINDOW) or _in_window(t, DECEL_WINDOW)


def cab_motion_disturbance(t: float) -> tuple[float, float]:
    """`odom.simulate_odometry`'s `disturbance_fn`: extra (trans_sigma_m,
    yaw_sigma_rad) during accel/decel, zero elsewhere."""
    if in_motion_transient(t):
        return EXTRA_TRANS_SIGMA_M, EXTRA_YAW_SIGMA_RAD
    return 0.0, 0.0


def render_overrides(t: float) -> tuple[float, float]:
    """`pipeline.run_scenario`'s `render_overrides_fn`: (blur_sigma,
    noise_std) for the frame at time `t` -- door-open windows get a noise
    bump (lighting/exposure), accel/decel windows get a blur bump (motion
    blur); baseline (render module defaults) the rest of the transit."""
    blur = render.GAUSSIAN_BLUR_SIGMA
    noise = render.NOISE_STD
    if door_open(t):
        noise *= DOOR_OPEN_NOISE_MULT
    if in_motion_transient(t):
        blur *= MOTION_BLUR_MULT
    return blur, noise


def build_elevator_scenario() -> tuple[list[MarkerSpec], GroundTruth, float, float]:
    normal_into_cab = np.array([0.0, -1.0, 0.0])  # back wall (y=ROOM_D) faces -Y into the cab
    R = tf.wall_marker_rotation(normal_into_cab)
    # Mounted lower than the nominal room's wall tags (0.9-1.0m, near camera
    # height): a cab forces close viewing range, and a wall tag mounted at
    # normal corridor height (~1.4m+) foreshortens out of vertical FOV up
    # close — the same falloff/placement lesson the spec calls out, just at
    # the near end instead of the far end.
    markers = [
        MarkerSpec(id=100, size_m=TAG_SIZE_M, translation=np.array([0.6, ROOM_D, 0.9]), R_world_marker=R, wall="elevator back-left"),
        MarkerSpec(id=101, size_m=TAG_SIZE_M, translation=np.array([1.4, ROOM_D, 1.0]), R_world_marker=R, wall="elevator back-right"),
    ]

    # keyframes: (t, x, y, yaw_rad) — enter through the door, dwell just
    # inside (not hard against the back wall — keeps tags in workable range),
    # back out, facing the back wall the whole time (watch the anchor tags
    # through the whole transit, the exact case the spec calls out).
    keyframes = [
        (0.0, 1.0, -1.2, np.pi / 2),
        (4.0, 1.0, 0.6, np.pi / 2),
        (10.0, 1.0, 0.6, np.pi / 2),
        (16.0, 1.0, -1.2, np.pi / 2),
    ]
    n = int(round(keyframes[-1][0] * HZ)) + 1
    t = np.arange(n) / HZ
    kt = np.array([k[0] for k in keyframes])
    kx = np.array([k[1] for k in keyframes])
    ky = np.array([k[2] for k in keyframes])
    kyaw = np.array([k[3] for k in keyframes])
    x = np.interp(t, kt, kx)
    y = np.interp(t, kt, ky)
    yaw = np.interp(t, kt, kyaw)

    return markers, GroundTruth(t=t, x=x, y=y, yaw=yaw), ROOM_W, ROOM_D
