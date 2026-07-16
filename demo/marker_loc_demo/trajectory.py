"""Ground-truth robot trajectory: a smooth multi-lap elliptical loop around
the room, sampled at 10 Hz. Heading follows the path tangent, like a real
differential-drive robot with a forward-facing camera — an ellipse (unlike a
racetrack with long straights) keeps the heading continuously turning, so
over a lap it sweeps through every direction and regularly points at each
wall in turn.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .world import ROOM_DEPTH_Y, ROOM_WIDTH_X

HZ = 10.0
DURATION_S = 100.0
CENTER = (ROOM_WIDTH_X / 2.0, ROOM_DEPTH_Y / 2.0)
SEMI_AXIS_X = 3.2
SEMI_AXIS_Y = 2.2
NUM_LAPS = 4.0


@dataclass
class GroundTruth:
    t: np.ndarray  # (N,) seconds
    x: np.ndarray
    y: np.ndarray
    yaw: np.ndarray  # camera/body heading, radians


def build_ground_truth(duration_s: float = DURATION_S, hz: float = HZ) -> GroundTruth:
    n = int(round(duration_s * hz)) + 1
    t = np.arange(n) / hz
    theta = 2 * np.pi * NUM_LAPS * t / duration_s

    cx, cy = CENTER
    x = cx + SEMI_AXIS_X * np.cos(theta)
    y = cy + SEMI_AXIS_Y * np.sin(theta)

    dtheta = 2 * np.pi * NUM_LAPS / duration_s
    dx = -SEMI_AXIS_X * np.sin(theta) * dtheta
    dy = SEMI_AXIS_Y * np.cos(theta) * dtheta
    yaw = np.arctan2(dy, dx)

    return GroundTruth(t=t, x=x, y=y, yaw=yaw)
