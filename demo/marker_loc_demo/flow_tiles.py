"""Five small flowchart-step tiles for the /dimensional page's "How it works"
run flowchart — one rendered illustration per step, replacing the single
error-over-time graph. Consistent palette/weight across all 5 (shared with
`spec_visuals.py` / `start_end_demo.py`).

Steps 1/2/4 crop a real `render.py` frame (genuine tag bitmap, homography
-warped for the camera pose — no stock imagery, no fake detector output).
Step 3 (the route) and step 5 (the readout) are clean schematic drawings —
labelled "SIMULATED" / abstract, never presented as real-hardware results.

    cd demo
    ./.venv/bin/python -m marker_loc_demo.flow_tiles

writes 5 PNGs to the portfolio's public/dimensional/flow/ dir (flag: --out DIR).
"""

from __future__ import annotations

import argparse
import os

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from . import camera as cam
from . import render
from . import transforms as tf
from .world import WALL_NORMALS, MarkerSpec

DEFAULT_OUT = (
    "/Users/aaryan/Files/Personal Assistant/07_Professional/portfolio/public/dimensional/flow"
)

# ---- shared palette (matches spec_visuals.py / start_end_demo.py) ---------
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"
GREEN = "#1e8f5f"
BADGE_BG = "#eef1ee"
BADGE_FG = "#52514e"

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
_SIZE_IN = 3.2  # inches -> 640px square @ 200dpi
_DPI = 200


def _set_font() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family


def _hex_to_bgr(h: str) -> tuple[int, int, int]:
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    return (b, g, r)


def _new_square_fig():
    fig = plt.figure(figsize=(_SIZE_IN, _SIZE_IN), dpi=_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def _badge(ax, text: str) -> None:
    """Small uppercase corner badge, top-right, marking the tile as an
    illustration (never presented as a real-hardware result). Sits in the
    plain white margin above the photo inset (see `_show_crop`), never
    overlapping it, so it stays legible instead of blending into the noise."""
    ax.text(
        0.94, 0.975, text, transform=ax.transAxes, fontsize=7.5, fontweight="bold",
        color=BADGE_FG, ha="right", va="top",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=BADGE_BG, edgecolor=BASELINE, linewidth=0.8),
    )


def _caption(ax, text: str) -> None:
    ax.text(
        0.5, 0.035, text, transform=ax.transAxes, fontsize=10.5, fontweight="bold",
        color=INK, ha="center", va="bottom",
    )


# ---------------------------------------------------------------------------
# shared render setup — one wall tag, viewed straight-on (mirrors
# start_end_demo.py's reference pose)
# ---------------------------------------------------------------------------

TAG_SIZE_M = 0.15
TAG_HEIGHT_M = 1.0
TAG_ALONG_M = 4.0
CAM_STANDOFF_M = 1.8


def _marker() -> MarkerSpec:
    return MarkerSpec(
        id=0,
        size_m=TAG_SIZE_M,
        translation=np.array([0.0, TAG_ALONG_M, TAG_HEIGHT_M]),
        R_world_marker=tf.wall_marker_rotation(WALL_NORMALS["x=0"]),
        wall="x=0",
    )


def _camera_T() -> np.ndarray:
    T_world_base = cam.world_to_base_T(CAM_STANDOFF_M, TAG_ALONG_M, np.pi)
    return T_world_base @ cam.base_to_camera_T()


def _tag_corners_px(marker: MarkerSpec, T_world_camera: np.ndarray, intr) -> np.ndarray:
    s = marker.size_m / 2.0
    obj = np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]])
    T_camera_marker = np.linalg.inv(T_world_camera) @ marker.T_world_marker
    return cam.project(T_camera_marker, obj, intr)


def _pad_box(pts: np.ndarray, scale: float = 1.9) -> np.ndarray:
    c = pts.mean(axis=0)
    return c + (pts - c) * scale


def _draw_dashed_poly(img, pts: np.ndarray, color, thickness=2, dash=7, gap=6) -> None:
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


def _render_tag_crop() -> tuple[np.ndarray, np.ndarray, object]:
    """Returns (rgb image, ref/live corner box, intr) for the shared reference pose."""
    intr = cam.Intrinsics.default()
    marker = _marker()
    T_cam = _camera_T()
    rng = np.random.default_rng(42)
    result = render.render_frame(T_cam, [marker], intr, rng=rng)
    img = cv2.cvtColor(result.image, cv2.COLOR_GRAY2BGR)
    box = _pad_box(_tag_corners_px(marker, T_cam, intr))
    return img, box, intr


def _zoom_window(box: np.ndarray, img_shape: tuple[int, int], win: float = 185.0):
    cx, cy = box.mean(axis=0)
    h, w = img_shape
    x_lo = float(np.clip(cx - win / 2.0, 0, w - win))
    y_lo = float(np.clip(cy - win / 2.0, 0, h - win))
    return x_lo, y_lo, win


def _show_crop(ax, img_rgb: np.ndarray, box: np.ndarray) -> None:
    x_lo, y_lo, win = _zoom_window(box, img_rgb.shape[:2])
    # Draw the crop in its own sub-axes so it stays square regardless of the
    # outer figure's aspect. Leaves a plain white margin above (for the
    # corner badge) and below (for the caption) so neither overlaps the photo.
    inset = ax.inset_axes((0.07, 0.15, 0.86, 0.68))
    inset.imshow(img_rgb)
    inset.set_xlim(x_lo, x_lo + win)
    inset.set_ylim(y_lo + win, y_lo)
    inset.set_xticks([])
    inset.set_yticks([])
    for spine in inset.spines.values():
        spine.set_color(BASELINE)
        spine.set_linewidth(1.1)


# ---------------------------------------------------------------------------
# tile 1 — face-tag.png
# ---------------------------------------------------------------------------


def make_face_tag(out_path: str) -> None:
    img, box, _ = _render_tag_crop()
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    fig, ax = _new_square_fig()
    _show_crop(ax, img_rgb, box)
    _badge(ax, "RENDERED")
    _caption(ax, "start/end tag")
    fig.savefig(out_path, dpi=_DPI, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tile 2 — start-frozen.png
# ---------------------------------------------------------------------------


def make_start_frozen(out_path: str) -> None:
    img, box, _ = _render_tag_crop()
    _draw_dashed_poly(img, box, _hex_to_bgr(INK), thickness=2, dash=7, gap=6)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    fig, ax = _new_square_fig()
    _show_crop(ax, img_rgb, box)
    _badge(ax, "RENDERED")
    _caption(ax, "reference frozen")
    fig.savefig(out_path, dpi=_DPI, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tile 4 — return-green.png
# ---------------------------------------------------------------------------


def make_return_green(out_path: str) -> None:
    img, box, _ = _render_tag_crop()
    # frozen reference — thin dashed, neutral ink
    _draw_dashed_poly(img, box, _hex_to_bgr(INK), thickness=2, dash=7, gap=6)
    # live box back inside it, aligned — solid green
    live_box = box + (box - box.mean(axis=0)) * -0.12  # slightly inset, reads as "inside"
    cv2.polylines(img, [np.round(live_box).astype(int).reshape(-1, 1, 2)], True, _hex_to_bgr(GREEN), 2, cv2.LINE_AA)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    fig, ax = _new_square_fig()
    _show_crop(ax, img_rgb, box)
    _badge(ax, "RENDERED")
    _caption(ax, "aligned")
    fig.savefig(out_path, dpi=_DPI, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tile 3 — drive-route.png (the important one)
# ---------------------------------------------------------------------------


def make_drive_route(out_path: str) -> None:
    _set_font()
    fig, ax = _new_square_fig()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")

    # a clean rounded-rectangle loop, start=end at the bottom-center tag
    cx, cy = 0.5, 0.56
    rx, ry = 0.30, 0.26
    theta = np.linspace(0, 2 * np.pi, 200)
    # superellipse-ish rounding for a soft "loop" shape, not a hard rectangle
    loop_x = cx + rx * np.sign(np.cos(theta)) * np.abs(np.cos(theta)) ** 0.7
    loop_y = cy + ry * np.sign(np.sin(theta)) * np.abs(np.sin(theta)) ** 0.7

    def loop_point(deg: float) -> tuple[float, float]:
        r = np.radians(deg)
        x = cx + rx * np.sign(np.cos(r)) * np.abs(np.cos(r)) ** 0.7
        y = cy + ry * np.sign(np.sin(r)) * np.abs(np.sin(r)) ** 0.7
        return float(x), float(y)

    ax.plot(loop_x, loop_y, color=BLUE, lw=2.4, solid_capstyle="round", zorder=3)
    # direction arrow partway around the loop
    ai = 40
    ax.annotate(
        "", xy=(loop_x[ai + 3], loop_y[ai + 3]), xytext=(loop_x[ai], loop_y[ai]),
        arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=2.0, mutation_scale=14), zorder=4,
    )

    # start=end glyph — small square tag icon at the bottom of the loop
    start_xy = loop_point(270)
    start_xy = (start_xy[0], start_xy[1] - 0.012)
    ax.scatter(*start_xy, marker="s", s=90, color=INK, zorder=6, linewidths=0)
    ax.text(start_xy[0], start_xy[1] - 0.075, "start = end", fontsize=9.5, color=INK, ha="center", va="top", fontweight="bold")

    # board glyph — small rectangle near the tag, representing the marker board
    board_xy = (start_xy[0] + 0.10, start_xy[1])
    ax.add_patch(mpatches.Rectangle((board_xy[0] - 0.018, board_xy[1] - 0.012), 0.036, 0.024, facecolor=INK_MUTED, edgecolor="none", zorder=5))

    # pick-up -> place-down: a short dashed jump from the loop's upper-left
    # side into open interior space, clear of the start/end labels below —
    # the discontinuity odometry can't see but a tag fixes on first sight.
    pick_xy = loop_point(135)
    place_xy = (0.40, 0.565)
    ax.plot(*pick_xy, marker="o", markersize=6.5, color=INK, zorder=6)
    ax.plot(*place_xy, marker="o", markersize=6.5, color=INK, zorder=6)
    ax.annotate(
        "", xy=place_xy, xytext=pick_xy,
        arrowprops=dict(arrowstyle="-|>", color=INK_SECONDARY, lw=1.8, linestyle=(0, (5, 4)), mutation_scale=14),
        zorder=5,
    )
    ax.text(pick_xy[0] - 0.022, pick_xy[1] + 0.018, "pick up", fontsize=9.5, fontweight="bold", color=INK_SECONDARY, ha="right", va="bottom")
    ax.text(place_xy[0] - 0.022, place_xy[1] - 0.005, "place down", fontsize=9.5, fontweight="bold", color=INK_SECONDARY, ha="right", va="top")

    _badge(ax, "SIMULATED ROUTE")
    _caption(ax, "loop + pick-up / place-down")
    ax.axis("off")
    fig.savefig(out_path, dpi=_DPI, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tile 5 — stop-recorded.png
# ---------------------------------------------------------------------------


def make_stop_recorded(out_path: str) -> None:
    _set_font()
    fig, ax = _new_square_fig()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # start=end glyph, aligned (green ring around it — the run closed cleanly)
    cx, cy = 0.5, 0.62
    ax.scatter(cx, cy, marker="s", s=110, color=INK, zorder=4, linewidths=0)
    ring = mpatches.Circle((cx, cy), 0.085, facecolor="none", edgecolor=GREEN, linewidth=2.2, zorder=3)
    ax.add_patch(ring)

    # a compact readout chip below it — abstract "Δ" placeholder, no numbers
    chip = mpatches.FancyBboxPatch(
        (cx - 0.135, cy - 0.235), 0.27, 0.115,
        boxstyle="round,pad=0.012,rounding_size=0.03",
        facecolor=SURFACE, edgecolor=BASELINE, linewidth=1.2, zorder=4,
    )
    ax.add_patch(chip)
    ax.text(cx, cy - 0.178, "Δ measured", fontsize=13, fontweight="bold", color=INK, ha="center", va="center", zorder=5)

    ax.text(cx, cy + 0.135, "error recorded", fontsize=10.5, fontweight="bold", color=INK, ha="center", va="bottom")

    _badge(ax, "SIMULATED")
    ax.axis("off")
    fig.savefig(out_path, dpi=_DPI, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    _set_font()

    jobs = [
        ("face-tag.png", make_face_tag),
        ("start-frozen.png", make_start_frozen),
        ("drive-route.png", make_drive_route),
        ("return-green.png", make_return_green),
        ("stop-recorded.png", make_stop_recorded),
    ]
    for name, fn in jobs:
        path = os.path.join(args.out, name)
        fn(path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
