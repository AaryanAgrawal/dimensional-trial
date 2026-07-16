"""Persistent 3D ORB feature map for the markerless-v2 ORB/PnP approach
(`trial/markerless-seed.md` v2 — the spike's own "use ORB/PnP instead"
recommendation). Plays the same role as `world.MarkerSpec` / the tag map
(known 3D points + something to match against a frame), except every point's
world position comes from two-view triangulation during a tag-anchored
SURVEY pass (`survey.py`) rather than a hand-placed spec, and "match" means
ORB descriptor matching (`orb_localize.py`) rather than a decoded tag ID.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

ORB_N_FEATURES = 500
# Lowe's ratio test threshold for accepting a descriptor match as
# unambiguous (standard SfM/SLAM convention, not tuned per-scene).
MATCH_RATIO = 0.75


def get_orb() -> cv2.ORB:
    return cv2.ORB_create(nfeatures=ORB_N_FEATURES, scaleFactor=1.2, nlevels=8)


def get_matcher() -> cv2.BFMatcher:
    return cv2.BFMatcher(cv2.NORM_HAMMING)


def detect(orb: cv2.ORB, image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Real `cv2.ORB` keypoint detection + BRIEF-style descriptor extraction
    on real rendered pixels. Returns `(pts (N,2) float32, descriptors (N,32)
    uint8 | None)` — `descriptors` is None iff no keypoints were found."""
    kps, desc = orb.detectAndCompute(image, None)
    if not kps:
        return np.zeros((0, 2), dtype=np.float32), None
    pts = np.array([kp.pt for kp in kps], dtype=np.float32)
    return pts, desc


@dataclass
class FeatureMap:
    points: np.ndarray  # (M, 3) float64, world xyz
    descriptors: np.ndarray  # (M, 32) uint8, ORB/BRIEF descriptors
    n_obs: np.ndarray  # (M,) int -- number of survey observations that produced each point
    mean_reproj_px: np.ndarray  # (M,) float -- triangulation reprojection residual

    def __len__(self) -> int:
        return int(len(self.points))

    def save(self, path: str) -> None:
        np.savez(
            path,
            points=self.points,
            descriptors=self.descriptors,
            n_obs=self.n_obs,
            mean_reproj_px=self.mean_reproj_px,
        )

    @staticmethod
    def load(path: str) -> "FeatureMap":
        d = np.load(path)
        return FeatureMap(
            points=d["points"],
            descriptors=d["descriptors"],
            n_obs=d["n_obs"],
            mean_reproj_px=d["mean_reproj_px"],
        )
