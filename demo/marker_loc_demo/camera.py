"""Pinhole camera model + a fixed robot->camera mount, in OpenCV optical
convention (X-right, Y-down, Z-forward) — the same convention `cv2.solvePnP`
and dimos's `marker_pose.py` operate in.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import transforms as tf

IMG_W = 640
IMG_H = 480
HFOV_DEG = 110.0  # wide-angle sensor head (needed so a single continuously-panning camera sweeps every wall often enough)

# Body (base_link) -> optical, standard ROS camera-frame convention:
# body X=forward, Y=left, Z=up  ->  optical X=right, Y=down, Z=forward.
_R_BODY_TO_OPTICAL = np.array(
    [
        [0.0, 0.0, 1.0],
        [-1.0, 0.0, 0.0],
        [0.0, -1.0, 0.0],
    ]
)

MOUNT_HEIGHT_M = 0.35
MOUNT_FORWARD_M = 0.05
MOUNT_PITCH_DEG = 8.0  # small upward tilt so wall-mounted tags sit mid-frame

PAN_PERIOD_S = 6.0  # sensor head's continuous-pan period, independent of drive heading


def cam_pan_yaw(t: float | np.ndarray) -> float | np.ndarray:
    """Continuous camera pan angle relative to the chassis — a rotating
    sensor head, not locked to drive direction, so every wall gets swept
    regularly regardless of which way the robot happens to be driving.
    A known, commanded/telemetered angle (like a pan-tilt joint reading),
    not a fudge — the rest of the pipeline treats it as "currently known",
    same as any other fixed extrinsic."""
    return 2.0 * np.pi * t / PAN_PERIOD_S


@dataclass
class Intrinsics:
    K: np.ndarray
    dist: np.ndarray
    width: int = IMG_W
    height: int = IMG_H

    @staticmethod
    def default() -> "Intrinsics":
        fx = (IMG_W / 2.0) / np.tan(np.radians(HFOV_DEG / 2.0))
        K = np.array([[fx, 0, IMG_W / 2.0], [0, fx, IMG_H / 2.0], [0, 0, 1]], dtype=np.float64)
        dist = np.zeros(5, dtype=np.float64)  # ideal pinhole, no lens distortion
        return Intrinsics(K=K, dist=dist)


def base_to_camera_T(pitch_deg: float = MOUNT_PITCH_DEG, pan_yaw_rad: float = 0.0) -> np.ndarray:
    """Extrinsic `base_link -> camera_optical` (mirrors the fixed static TF
    chain `GO2Connection`/`DeskStaticTfModule` already publish for a fixed
    camera; `pan_yaw_rad` adds a known turret rotation on top, see
    `cam_pan_yaw`)."""
    R_tilt = tf.rot_y(np.radians(pitch_deg))  # tilt the forward axis upward
    R_pan = tf.rot_z(pan_yaw_rad)  # turret rotation about the mast's vertical axis
    R = R_pan @ R_tilt @ _R_BODY_TO_OPTICAL
    t = np.array([MOUNT_FORWARD_M, 0.0, MOUNT_HEIGHT_M])
    return tf.make_T(R, t)


def world_to_base_T(x: float, y: float, yaw: float) -> np.ndarray:
    """Ground robot: z=0, roll=pitch=0, only (x, y, yaw) vary."""
    return tf.T_from_xyzyaw(x, y, 0.0, yaw)


def project(T_camera_marker: np.ndarray, object_points: np.ndarray, intr: Intrinsics) -> np.ndarray:
    """Project marker-local 3D points (N,3) through a known camera pose into pixels (N,2)."""
    rvec, _ = cv2.Rodrigues(T_camera_marker[:3, :3])
    tvec = T_camera_marker[:3, 3].reshape(3, 1)
    pts, _ = cv2.projectPoints(object_points.astype(np.float64), rvec, tvec, intr.K, intr.dist)
    return pts.reshape(-1, 2)


def visibility_check(
    T_camera_marker: np.ndarray,
    marker_normal_world: np.ndarray,
    camera_pos_world: np.ndarray,
    marker_pos_world: np.ndarray,
    tag_half_size_m: float,
    intr: Intrinsics,
    *,
    min_depth_m: float = 1.3,  # empirically, too-close wall tags foreshorten out of vertical FOV
    max_cos_obliquity: float = 0.45,  # cos(~63 deg): the spec's own placement guideline
    min_bbox_px: float = 8.0,
    max_range_per_tag_size: float = 28.0,  # ~4.2m for a 15cm tag: the reliable-detection sweet spot
) -> bool:
    """Cheap physical pre-filter: behind camera / too oblique / too small / too
    far to plausibly detect well. Real pass/fail is still decided by the
    detector on the rendered pixels — this only skips geometrically-degenerate
    or known-unreliable render attempts (mirrors the spec's own placement
    guidance: >~60 deg off-axis kills detection, pose error grows past a few
    meters for a tag this size).
    """
    depth = T_camera_marker[2, 3]
    if depth <= min_depth_m:
        return False
    if depth > max_range_per_tag_size * (2 * tag_half_size_m):
        return False

    view_dir = camera_pos_world - marker_pos_world
    view_dir = view_dir / np.linalg.norm(view_dir)
    cos_angle = float(np.dot(view_dir, marker_normal_world))
    if cos_angle <= max_cos_obliquity:
        return False

    h = tag_half_size_m
    corners_marker = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])
    px = project(T_camera_marker, corners_marker, intr)
    x_min, y_min = px.min(axis=0)
    x_max, y_max = px.max(axis=0)
    if x_max < 0 or y_max < 0 or x_min > intr.width or y_min > intr.height:
        return False
    if max(x_max - x_min, y_max - y_min) < min_bbox_px:
        return False
    return True
