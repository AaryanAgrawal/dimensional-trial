"""Minimal SE(3) helpers.

Convention (matches dimos): a `Transform` is `frame_id <- child_frame_id`,
i.e. a 4x4 homogeneous matrix T such that `point_in_frame_id = T @ point_in_child_frame_id`.
Composition: T_a_c = T_a_b @ T_b_c. Inversion: T_b_a = inv(T_a_b).

We use plain 4x4 numpy arrays everywhere instead of a Transform class — this
is a standalone demo, not a dimos module, so we keep the math undressed.
"""

from __future__ import annotations

import numpy as np


def rot_x(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Extrinsic X-Y-Z (roll about world X, then pitch about world Y, then yaw about world Z)."""
    return rot_z(yaw) @ rot_y(pitch) @ rot_x(roll)


def matrix_to_yaw(R: np.ndarray) -> float:
    """Yaw (rotation about Z) of a rotation matrix, ignoring roll/pitch."""
    return float(np.arctan2(R[1, 0], R[0, 0]))


def make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


def T_from_xyzyaw(x: float, y: float, z: float, yaw: float) -> np.ndarray:
    return make_T(rot_z(yaw), np.array([x, y, z]))


def invert(T: np.ndarray) -> np.ndarray:
    R = T[:3, :3]
    t = T[:3, 3]
    Ti = np.eye(4, dtype=np.float64)
    Ti[:3, :3] = R.T
    Ti[:3, 3] = -R.T @ t
    return Ti


def rvec_tvec_to_T(rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    import cv2

    R, _ = cv2.Rodrigues(rvec)
    return make_T(R, tvec.reshape(3))


def wall_marker_rotation(normal_world: np.ndarray, up_world: np.ndarray = np.array([0.0, 0.0, 1.0])) -> np.ndarray:
    """Rotation matrix for a marker mounted flat on a wall.

    Marker-local convention (matches dimos `marker_pose.py` object points):
    X right, Y up, Z is the marker normal (points away from the wall, toward
    the room). Returns R_world_marker (columns = marker axes expressed in world).
    """
    z_m = normal_world / np.linalg.norm(normal_world)
    y_m = up_world - np.dot(up_world, z_m) * z_m
    y_m = y_m / np.linalg.norm(y_m)
    x_m = np.cross(y_m, z_m)
    x_m = x_m / np.linalg.norm(x_m)
    return np.stack([x_m, y_m, z_m], axis=1)


def quat_from_matrix(R: np.ndarray) -> np.ndarray:
    """Return (w, x, y, z)."""
    tr = np.trace(R)
    if tr > 0:
        S = np.sqrt(tr + 1.0) * 2
        w = 0.25 * S
        x = (R[2, 1] - R[1, 2]) / S
        y = (R[0, 2] - R[2, 0]) / S
        z = (R[1, 0] - R[0, 1]) / S
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / S
        x = 0.25 * S
        y = (R[0, 1] + R[1, 0]) / S
        z = (R[0, 2] + R[2, 0]) / S
    elif R[1, 1] > R[2, 2]:
        S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / S
        x = (R[0, 1] + R[1, 0]) / S
        y = 0.25 * S
        z = (R[1, 2] + R[2, 1]) / S
    else:
        S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / S
        x = (R[0, 2] + R[2, 0]) / S
        y = (R[1, 2] + R[2, 1]) / S
        z = 0.25 * S
    return np.array([w, x, y, z], dtype=np.float64)


def matrix_from_quat(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def weighted_quat_mean(quats: list[np.ndarray], weights: list[float]) -> np.ndarray:
    """Weighted average of unit quaternions (sign-aligned to the first)."""
    ref = quats[0]
    acc = np.zeros(4, dtype=np.float64)
    for q, w in zip(quats, weights):
        if np.dot(q, ref) < 0:
            q = -q
        acc += w * q
    return acc / np.linalg.norm(acc)
