"""Feature-rich static wall texture ("posters") for the markerless-seed spike
— see `trial/markerless-seed.md`. Physically the same idea as the AprilTags
in `world.py`/`tags.py` (a flat rectangle mounted on a wall at a known world
pose, homography-warped into the frame with correct perspective in
`render.py`), except the pattern carries no ID/decode payload — it exists
only to give `cv2.goodFeaturesToTrack`/LK real, trackable corners on real
rendered pixels. Reuses `world.MarkerSpec` (translation + rotation + size)
and `transforms.wall_marker_rotation` rather than inventing a parallel type.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from . import transforms as tf
from . import world
from .world import MarkerSpec

POSTER_SIZE_M = 0.7  # bigger than a 15cm tag on purpose — texture, not a decodable code
POSTER_CANVAS_PX = 240
CHECKER_CELLS = 6  # 6x6 alternating cells -> strong Shi-Tomasi corners at every internal vertex


@lru_cache(maxsize=1)
def poster_canvas(px: int = POSTER_CANVAS_PX, cells: int = CHECKER_CELLS) -> np.ndarray:
    """A single cached checkerboard canvas, reused at every poster placement
    (posters are tracked frame-to-frame by LK, not re-identified against each
    other, so a shared pattern is fine — see `features.py`). Checkerboard
    corners are the textbook strong-corner case for `goodFeaturesToTrack`;
    plain smooth noise (cf. `render._background`) starves LK of anything to
    lock onto.
    """
    cell = px // cells
    n = cell * cells
    canvas = np.full((n, n), 128, dtype=np.uint8)
    for r in range(cells):
        for c in range(cells):
            if (r + c) % 2 == 0:
                canvas[r * cell : (r + 1) * cell, c * cell : (c + 1) * cell] = 235
            else:
                canvas[r * cell : (r + 1) * cell, c * cell : (c + 1) * cell] = 20
    return canvas


@lru_cache(maxsize=None)
def poster_canvas_unique(poster_id: int, px: int = POSTER_CANVAS_PX, n_blobs: int = 220) -> np.ndarray:
    """A per-poster-unique procedural texture, deterministic (seeded by
    `poster_id`, no wall-clock nondeterminism). `poster_canvas()`'s single
    shared checkerboard is fine for LK's frame-to-frame *tracking* (never
    needs to tell one poster's corners apart from another's -- continuous
    optical flow, not re-identification) but breaks ORB *descriptor
    matching* outright: a uniform checkerboard's every corner has the same
    tiny local neighborhood, so is indistinguishable in a 31px BRIEF patch
    regardless of *which* poster or corner it's actually on -- matches land
    on the wrong poster clear across the room (measured directly: median
    ORB/PnP pose error before this fix was multiple meters, dominated by
    exactly this). A version tiled at a coarser cell size than a BRIEF
    patch (first fix attempted) doesn't help either -- the patch still only
    samples one cell edge, i.e. one of a handful of repeating local codes.
    What actually gives BRIEF patch-scale uniqueness: a random mosaic of
    overlapping rectangles at random gray levels and positions -- every
    ~31x31 neighborhood sees a effectively-unique combination of edges, and
    per-poster-seeded placement means it never repeats across posters
    either. Also a more honest stand-in for real wall/poster clutter
    (signage, wear, uneven paint) than a bathroom-tile pattern.
    """
    rng = np.random.default_rng(90_000 + poster_id)
    canvas = np.full((px, px), 128, dtype=np.uint8)
    for _ in range(n_blobs):
        w = int(rng.integers(6, 22))
        h = int(rng.integers(6, 22))
        x = int(rng.integers(0, px - w))
        y = int(rng.integers(0, px - h))
        canvas[y : y + h, x : x + w] = int(rng.integers(10, 245))
    return canvas


def _wall_point(wall: str, along: float, height: float) -> np.ndarray:
    """Local copy of `world._wall_point` (leading-underscore helper, kept
    local rather than importing across modules — same convention
    `envelope_sweep.py` already uses for the same reason)."""
    if wall == "x=0":
        return np.array([0.0, along, height])
    if wall == "x=10":
        return np.array([world.ROOM_WIDTH_X, along, height])
    if wall == "y=0":
        return np.array([along, 0.0, height])
    if wall == "y=8":
        return np.array([along, world.ROOM_DEPTH_Y, height])
    raise ValueError(wall)


def build_poster_map(
    per_wall: int = 12,
    heights_m: tuple[float, ...] = (0.6, 1.4, 2.2),
    room_w: float = world.ROOM_WIDTH_X,
    room_d: float = world.ROOM_DEPTH_Y,
) -> list[MarkerSpec]:
    """Evenly-spaced poster grid across all 4 walls, 3 heights (near floor,
    eye level, near ceiling) so texture is visible regardless of the
    panning camera's current tilt/range — dense on purpose (this is
    background texture, not a sparse landmark set; tag *sparsity* is the
    variable under test, not poster sparsity, see markerless-seed.md).
    """
    walls = ["x=0", "x=10", "y=0", "y=8"]
    lengths = {"x=0": room_d, "x=10": room_d, "y=0": room_w, "y=8": room_w}
    margin = 0.8
    posters: list[MarkerSpec] = []
    next_id = 0
    for wall in walls:
        length = lengths[wall]
        alongs = np.linspace(margin, length - margin, per_wall)
        R = tf.wall_marker_rotation(world.WALL_NORMALS[wall])
        for i, along in enumerate(alongs):
            h = heights_m[i % len(heights_m)]
            t = _wall_point(wall, float(along), h)
            posters.append(MarkerSpec(id=next_id, size_m=POSTER_SIZE_M, translation=t, R_world_marker=R, wall=wall))
            next_id += 1
    return posters
