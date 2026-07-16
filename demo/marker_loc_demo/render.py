"""Render a synthetic camera frame: real tag bitmaps homography-warped into
the image with correct perspective for the current camera pose, then mild
sensor noise + blur. No detection is ever injected — the detector in
`localization.py` runs on these pixels exactly like it would on a real frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from . import camera as cam
from . import posters
from . import tags
from .world import MarkerSpec

# Posters are texture, not a decodable code -- looser than the tag envelope
# (bigger physical size buys range; we don't need corner-level decode
# precision, just visible checkerboard structure for LK to grab onto).
POSTER_MIN_DEPTH_M = 0.3
POSTER_MAX_COS_OBLIQUITY = 0.35  # cos(~69.5deg): a bit more permissive than the tag gate
POSTER_MIN_BBOX_PX = 24.0
POSTER_MAX_RANGE_PER_SIZE = 20.0  # ~14m for a 0.7m poster -- basically "in the room"

GAUSSIAN_BLUR_SIGMA = 0.6
NOISE_STD = 6.0


@lru_cache(maxsize=1)
def _background(w: int = cam.IMG_W, h: int = cam.IMG_H) -> np.ndarray:
    """A flat-ish procedural background (floor/wall gradient + fixed low-freq
    texture) so tags aren't warped onto a perfectly blank field."""
    rng = np.random.default_rng(1234)
    grad = np.linspace(150, 205, h, dtype=np.float64).reshape(h, 1) * np.ones((1, w))
    low_res = rng.normal(0, 6.0, (h // 20 + 2, w // 20 + 2))
    tex = cv2.resize(low_res, (w, h), interpolation=cv2.INTER_CUBIC)
    bg = np.clip(grad + tex, 0, 255).astype(np.uint8)
    return bg


@dataclass
class RenderResult:
    image: np.ndarray  # (H, W) uint8 grayscale
    visible_ids: list[int]


def render_frame(
    T_world_camera: np.ndarray,
    marker_map: list[MarkerSpec],
    intr: cam.Intrinsics,
    *,
    rng: np.random.Generator,
    blur_sigma: float = GAUSSIAN_BLUR_SIGMA,
    noise_std: float = NOISE_STD,
    bypass_envelope_gate: bool = False,
    poster_map: list[MarkerSpec] | None = None,
    unique_poster_texture: bool = False,
) -> RenderResult:
    """`blur_sigma`/`noise_std` default to the module constants (unchanged
    behavior for every existing caller). `bypass_envelope_gate=True` skips
    `camera.visibility_check`'s asserted placement envelope (min/max range,
    max obliquity, min on-screen size) and keeps only the one check that's
    physically mandatory (marker in front of the camera) -- used by
    `envelope_sweep.py` so it can genuinely measure detector behavior *outside*
    the envelope the spec's placement guidance assumes, instead of having
    those frames pre-filtered to an automatic non-detection. `poster_map`
    (default None -- every existing caller unaffected) draws the
    markerless-seed spike's checkerboard wall texture (see `posters.py`)
    *underneath* the tags, same homography-warp compositing, so real
    `cv2.goodFeaturesToTrack`/LK features exist in the rendered pixels.
    `unique_poster_texture` (default False -- v1 spike callers unaffected,
    bit-identical) swaps the single shared checkerboard for a per-poster
    -unique procedural pattern (`posters.poster_canvas_unique`): required
    for markerless-v2's ORB *descriptor matching* (unlike v1's LK frame-to-
    frame tracking, descriptor matching needs each poster to look locally
    distinct or matches alias across posters -- see `trial/markerless-
    seed.md` v2).
    """
    frame = _background(intr.width, intr.height).copy().astype(np.float64)
    T_camera_world = np.linalg.inv(T_world_camera)
    camera_pos_world = T_world_camera[:3, 3]

    if poster_map:
        visible_posters: list[tuple[float, MarkerSpec, np.ndarray]] = []
        for p in poster_map:
            T_camera_poster = T_camera_world @ p.T_world_marker
            normal_world = p.R_world_marker[:, 2]
            if not cam.visibility_check(
                T_camera_poster, normal_world, camera_pos_world, p.translation, p.size_m / 2.0, intr,
                min_depth_m=POSTER_MIN_DEPTH_M, max_cos_obliquity=POSTER_MAX_COS_OBLIQUITY,
                min_bbox_px=POSTER_MIN_BBOX_PX, max_range_per_tag_size=POSTER_MAX_RANGE_PER_SIZE,
            ):
                continue
            visible_posters.append((T_camera_poster[2, 3], p, T_camera_poster))
        visible_posters.sort(key=lambda v: -v[0])
        shared_canvas = posters.poster_canvas()
        n = shared_canvas.shape[0]
        h = posters.POSTER_SIZE_M / 2.0
        obj_poster = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])
        src = np.array([[0, 0], [n, 0], [n, n], [0, n]], dtype=np.float32)
        for _, p, T_camera_poster in visible_posters:
            canvas = posters.poster_canvas_unique(p.id) if unique_poster_texture else shared_canvas
            dst = cam.project(T_camera_poster, obj_poster, intr).astype(np.float32)
            Hmat = cv2.getPerspectiveTransform(src, dst)
            warped = cv2.warpPerspective(
                canvas.astype(np.float64), Hmat, (intr.width, intr.height), flags=cv2.INTER_LINEAR, borderValue=-1.0
            )
            mask = warped >= 0
            frame[mask] = warped[mask]

    visible: list[tuple[float, MarkerSpec, np.ndarray]] = []
    for m in marker_map:
        T_camera_marker = T_camera_world @ m.T_world_marker
        normal_world = m.R_world_marker[:, 2]
        if bypass_envelope_gate:
            if T_camera_marker[2, 3] <= 0.01:  # behind (or at) the camera: geometrically degenerate
                continue
        elif not cam.visibility_check(
            T_camera_marker, normal_world, camera_pos_world, m.translation, m.size_m / 2.0, intr
        ):
            continue
        visible.append((T_camera_marker[2, 3], m, T_camera_marker))

    # draw far-to-near so nearer tags win on any (rare) image-space overlap
    visible.sort(key=lambda v: -v[0])

    for _, m, T_camera_marker in visible:
        canvas, pad, tag_px = tags.tag_canvas(m.id)
        n = tag_px + 2 * pad
        H_full = m.size_m * (1.0 + 2.0 * tags.QUIET_ZONE_FRAC) / 2.0
        obj_canvas = np.array([[-H_full, H_full, 0], [H_full, H_full, 0], [H_full, -H_full, 0], [-H_full, -H_full, 0]])
        dst = cam.project(T_camera_marker, obj_canvas, intr).astype(np.float32)
        src = np.array([[0, 0], [n, 0], [n, n], [0, n]], dtype=np.float32)
        Hmat = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(
            canvas.astype(np.float64), Hmat, (intr.width, intr.height), flags=cv2.INTER_LINEAR, borderValue=-1.0
        )
        mask = warped >= 0
        frame[mask] = warped[mask]

    if blur_sigma > 0:
        frame = cv2.GaussianBlur(frame, (0, 0), blur_sigma)
    frame = frame + rng.normal(0, noise_std, frame.shape)
    frame = np.clip(frame, 0, 255).astype(np.uint8)

    return RenderResult(image=frame, visible_ids=[m.id for _, m, _ in visible])
