"""AprilTag 36h11 bitmap generation, matching dimos's default dictionary
(`dimos/perception/fiducial/marker_pose.py`: `create_aruco_detector("DICT_APRILTAG_36h11")`).
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import cv2.aruco as aruco
import numpy as np

DICTIONARY_NAME = "DICT_APRILTAG_36h11"
QUIET_ZONE_FRAC = 0.30  # white margin around the tag pattern, as a fraction of tag_px


@lru_cache(maxsize=1)
def get_dictionary() -> aruco.Dictionary:
    return aruco.getPredefinedDictionary(getattr(aruco, DICTIONARY_NAME))


@lru_cache(maxsize=1)
def get_detector() -> aruco.ArucoDetector:
    params = aruco.DetectorParameters()
    return aruco.ArucoDetector(get_dictionary(), params)


@lru_cache(maxsize=None)
def tag_canvas(marker_id: int, tag_px: int = 200) -> np.ndarray:
    """Full printable tag bitmap: the dictionary pattern plus a white quiet-zone
    margin. The *pattern* (inner `tag_px` square) is what corresponds to the
    physical `size_m` used everywhere else (object points, the marker map) —
    detectors report corners at the pattern's outer edge, not the quiet zone.
    """
    pattern = aruco.generateImageMarker(get_dictionary(), marker_id, tag_px)
    pad = int(round(tag_px * QUIET_ZONE_FRAC))
    canvas = np.full((tag_px + 2 * pad, tag_px + 2 * pad), 255, dtype=np.uint8)
    canvas[pad : pad + tag_px, pad : pad + tag_px] = pattern
    return canvas, pad, tag_px


def canvas_corners_px(pad: int, tag_px: int) -> np.ndarray:
    """Pixel corners of the full padded canvas, TL/TR/BR/BL (cv2 image convention)."""
    n = tag_px + 2 * pad
    return np.array([[0, 0], [n, 0], [n, n], [0, n]], dtype=np.float64)
