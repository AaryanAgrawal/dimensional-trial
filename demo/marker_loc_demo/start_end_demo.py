"""Start/end tag referee DEMO: a horizontal 3-frame strip showing the
return-until-green mechanic, built for `trial/visual/start-end-demo.png`.

Each frame is genuinely rendered by `render.py` (real tag bitmap,
homography-warped for the camera pose — no stock imagery): one wall tag
viewed as the robot returns toward its frozen start pose. Overlaid per frame:

  - the FROZEN reference outline (thin dashed white) — where the tag sat in
    the camera when the run started; frozen at START, never moves.
  - the LIVE detection box (solid, colored) — where the tag is now. The
    offset between the two IS the run's loop-closure residual.

Frame 1: far off  -> red,   Δ 9.2 cm
Frame 2: closing  -> amber, Δ 3.4 cm
Frame 3: aligned  -> green, Δ 1.1 cm

The camera poses are exact (offsets of literally 9.2/3.4/1.1 cm from the
reference pose), so every Δ label is the true rendered offset, not a mockup
number. Badged "RENDERED DEMO" for honesty.

    cd demo
    ./.venv/bin/python -m marker_loc_demo.start_end_demo

writes ../trial/visual/start-end-demo.png (flag: `--out PATH`).
"""

from __future__ import annotations

import argparse
import os

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np

from . import camera as cam
from . import render
from . import transforms as tf
from .world import WALL_NORMALS, MarkerSpec

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(DEMO_ROOT)
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "trial", "visual", "start-end-demo.png")

INK = "#0b0b0b"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BADGE_BG = "#fdf1d6"
BADGE_FG = "#8a5710"
RED = "#c0392b"
AMBER = "#d99a2e"
GREEN = "#1e8f5f"

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]

TAG_SIZE_M = 0.15
TAG_HEIGHT_M = 1.0
TAG_ALONG_M = 4.0
CAM_STANDOFF_M = 1.8  # reference robot pose: this far off the x=0 wall

# (label offset in meters, color) — the offset IS the printed Δ, exactly.
FRAMES = [(0.092, RED), (0.034, AMBER), (0.011, GREEN)]


def _hex_to_bgr(h: str) -> tuple[int, int, int]:
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    return (b, g, r)


def _camera_T(dy: float) -> np.ndarray:
    """Robot base at (CAM_STANDOFF, TAG_ALONG + dy), facing the x=0 wall."""
    T_world_base = cam.world_to_base_T(CAM_STANDOFF_M, TAG_ALONG_M + dy, np.pi)
    return T_world_base @ cam.base_to_camera_T()


def _tag_corners_px(marker: MarkerSpec, T_world_camera: np.ndarray, intr) -> np.ndarray:
    """Project the tag's 4 outer corners (marker-local X-right/Y-up plane,
    matching marker_pose.py's object-point convention) into pixels."""
    s = marker.size_m / 2.0
    obj = np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]])
    T_camera_marker = np.linalg.inv(T_world_camera) @ marker.T_world_marker
    return cam.project(T_camera_marker, obj, intr)


def _draw_dashed_poly(img, pts: np.ndarray, color, thickness=1, dash=8, gap=6) -> None:
    """cv2 has no dashed polylines — draw each edge as alternating segments."""
    n = len(pts)
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        length = float(np.hypot(*(p1 - p0)))
        if length < 1e-6:
            continue
        direction = (p1 - p0) / length
        pos = 0.0
        while pos < length:
            a = p0 + direction * pos
            b = p0 + direction * min(pos + dash, length)
            cv2.line(img, tuple(np.round(a).astype(int)), tuple(np.round(b).astype(int)), color, thickness, cv2.LINE_AA)
            pos += dash + gap


def _pad_box(pts: np.ndarray, scale: float = 1.6) -> np.ndarray:
    """Scale the box outward around its centroid so the overlay rings the
    tag instead of covering its quiet-zone border."""
    c = pts.mean(axis=0)
    return c + (pts - c) * scale


def render_strip_frames(marker: MarkerSpec, intr) -> tuple[list, np.ndarray]:
    rng = np.random.default_rng(42)
    T_ref = _camera_T(0.0)
    ref_px = _pad_box(_tag_corners_px(marker, T_ref, intr))

    frames = []
    for dy, color in FRAMES:
        T_cur = _camera_T(dy)
        result = render.render_frame(T_cur, [marker], intr, rng=rng)
        img = cv2.cvtColor(result.image, cv2.COLOR_GRAY2BGR)
        live_px = _pad_box(_tag_corners_px(marker, T_cur, intr))
        # live detection box — solid, colored by residual (under the reference,
        # so frame 3's "aligned" reads as white dashes sitting on the color)
        cv2.polylines(
            img, [np.round(live_px).astype(int).reshape(-1, 1, 2)], True,
            _hex_to_bgr(color), 2, cv2.LINE_AA,
        )
        # frozen reference outline — thin, dashed, white, on top
        _draw_dashed_poly(img, ref_px, (255, 255, 255), thickness=2, dash=6, gap=5)
        frames.append((cv2.cvtColor(img, cv2.COLOR_BGR2RGB), dy, color))
    return frames, ref_px


def make_strip(frames, ref_px: np.ndarray, out_path: str) -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family

    fig, axes = plt.subplots(1, 3, figsize=(12.9, 3.85), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(left=0.008, right=0.992, top=0.865, bottom=0.075, wspace=0.03)

    # zoom window (4:3, fixed across frames) centered between the frozen
    # outline and the widest live box, so the offset reads at a glance
    cx0, cy0 = ref_px.mean(axis=0)
    win_w, win_h = 300.0, 225.0
    h, w = frames[0][0].shape[:2]
    x_lo = float(np.clip(cx0 - win_w * 0.58, 0, w - win_w))
    y_lo = float(np.clip(cy0 - win_h / 2.0, 0, h - win_h))

    sublabels = ["1 · far — keep driving", "2 · closing", "3 · within tolerance — run scored"]
    for ax, (img, dy, color), sub in zip(axes, frames, sublabels):
        ax.imshow(img)
        ax.set_xlim(x_lo, x_lo + win_w)
        ax.set_ylim(y_lo + win_h, y_lo)  # imshow's y runs downward
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#c3c2b7")
            spine.set_linewidth(1.0)
        ax.text(
            0.965, 0.935, f"Δ {dy * 100:.1f}cm", fontsize=15, fontweight="bold",
            color=color, ha="right", va="top", transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.55, edgecolor="none"),
        )
        ax.set_xlabel(sub, fontsize=10.5, color=INK, labelpad=6)

    fig.text(
        0.008, 0.955,
        "Start/end tag referee — dashed = frozen start outline · solid = live detection",
        ha="left", va="center", fontsize=12, fontweight="bold", color=INK,
    )
    fig.text(
        0.992, 0.955, "RENDERED DEMO — real footage replaces this",
        ha="right", va="center", fontsize=9, fontweight="bold", color=BADGE_FG,
        bbox=dict(boxstyle="round,pad=0.4", facecolor=BADGE_BG, edgecolor=BADGE_FG, linewidth=1.0),
    )

    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    intr = cam.Intrinsics.default()
    marker = MarkerSpec(
        id=0,
        size_m=TAG_SIZE_M,
        translation=np.array([0.0, TAG_ALONG_M, TAG_HEIGHT_M]),
        R_world_marker=tf.wall_marker_rotation(WALL_NORMALS["x=0"]),
        wall="x=0",
    )
    frames, ref_px = render_strip_frames(marker, intr)
    make_strip(frames, ref_px, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
