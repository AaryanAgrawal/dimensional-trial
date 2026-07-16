"""Project-1 benchmark PREVIEW: a single top-down figure showing what one
real benchmark run will produce, built for `trial/visual/project1-preview.png`.

Two trajectory trails from the same drive (Project 1's combined run: odom
baseline + fiducial correction; the lidar `RelocalizationModule` trail is
Project 2, layered on later), overlaid on the demo's synthetic room:

  1. raw odometry (gray) -- REAL data, unmodified: reuses the nominal
     scenario's odom-drift simulation exactly as `spec_visuals.py` runs it
     for hero.png/demo.gif (`simulate_nominal(seed=42)` -> real dead-reckoning
     drift, same numbers behind `out/metrics.json`).
  2. marker-corrected (green) -- REAL data, the same call's corrected
     trajectory (AprilTag detect -> solvePnP -> invert -> fuse -> gate ->
     hold, exactly what `out/trajectory.png` shows).

Also marks the fence/start reference point + ChArUco board reference (see
`trial/benchmark-rig.md`'s rig design) and repurposes one real wall tag as
the "start/end tag" -- a marker excluded from the corrector's map, kept
only to referee each run (a benchmark design element illustrated here, not
yet implemented in this harness).

Bonus inset: a mock of the live green-box camera-overlay concept, composited
on top of one real rendered camera frame this same sim run captured.

    cd demo
    ./.venv/bin/python -m marker_loc_demo.project1_preview

writes ../trial/visual/project1-preview.png (flags: `--out PATH`, `--seed N`,
default 42 -- matches spec_visuals.py and out/metrics.json).
"""

from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from . import spec_visuals as sv

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(DEMO_ROOT)
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "trial", "visual", "project1-preview.png")

# ---- palette ---------------------------------------------------------------
# A dedicated gray/green pair for this 2-way preview -- distinct from
# spec_visuals' red/blue hero palette.
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BASELINE = "#c3c2b7"
GRAY = "#9b9a90"  # raw odometry -- real, drifts unbounded
GREEN = "#1e8f5f"  # marker-corrected -- real, snaps back on tag sightings
HOLDOUT = "#7a4fb5"  # start/end tag -- distinct from ordinary wall tags
BADGE_BG = "#fdf1d6"
BADGE_FG = "#8a5710"

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pick_holdout(markers):
    """Repurpose one existing wall tag (x=0 wall, mid-height) as the
    'start/end tag' -- excluded from the map, kept only to referee each
    run. Chosen for a clear, uncluttered spot on the far wall from the
    fence/board reference point."""
    candidates = [m for m in markers if m.wall == "x=0"]
    return min(candidates, key=lambda m: abs(m.translation[1] - 5.0))


def _draw_camera_inset(fig, sim: dict) -> None:
    """Bonus inset: a mock of the live green-box camera overlay concept,
    composited on one real rendered camera frame from this same sim run
    (not a stock image -- genuinely rendered, noisy pixels)."""
    captured = sim["captured"]
    frame = next((c for c in captured if c[3] is not None), captured[0])
    _, img, _, _ = frame

    iax = fig.add_axes((0.045, 0.085, 0.225, 0.225))
    iax.imshow(img, cmap="gray", vmin=0, vmax=255, aspect="auto")
    iax.set_xticks([])
    iax.set_yticks([])
    for spine in iax.spines.values():
        spine.set_color(BASELINE)
        spine.set_linewidth(1.2)

    h, w = img.shape[:2]
    # reference (expected) box -- dashed, where the map says the tag should be
    ref = mpatches.Rectangle(
        (w * 0.36, h * 0.30), w * 0.28, h * 0.34, fill=False,
        edgecolor="white", linewidth=1.6, linestyle=(0, (4, 3)), zorder=3,
    )
    # current (live) box -- solid green, the overlay snapping onto it
    cur = mpatches.Rectangle(
        (w * 0.385, h * 0.335), w * 0.28, h * 0.34, fill=False,
        edgecolor=GREEN, linewidth=2.2, zorder=4,
    )
    iax.add_patch(ref)
    iax.add_patch(cur)
    iax.text(
        w * 0.70, h * 0.28, "Δ 1.7cm", fontsize=11, fontweight="bold",
        color=GREEN, ha="left", va="bottom", zorder=5,
        bbox=dict(boxstyle="round,pad=0.18", facecolor="black", alpha=0.55, edgecolor="none"),
    )
    iax.set_title("green-box camera overlay (concept)", fontsize=8.3, color=INK_MUTED, pad=4, loc="left")


# ---------------------------------------------------------------------------
# main figure
# ---------------------------------------------------------------------------


def make_preview(sim: dict, out_path: str) -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family

    markers = sim["markers"]
    t = sim["t"]
    true_x, true_y = sim["true_x"], sim["true_y"]
    raw_x, raw_y = sim["raw_x"], sim["raw_y"]
    corr_x, corr_y = sim["corr_x"], sim["corr_y"]
    room_w, room_d = sim["room_w"], sim["room_d"]
    cx, cy = room_w / 2.0, room_d / 2.0

    fig = plt.figure(figsize=(9.4, 8.5), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes((0.045, 0.05, 0.91, 0.74))
    ax.set_facecolor(SURFACE)

    pad_x, pad_y = 2.0, 1.6
    ax.set_xlim(-pad_x, room_w + pad_x)
    ax.set_ylim(-pad_y, room_d + pad_y)
    ax.set_aspect("equal")
    ax.axis("off")

    # room walls
    ax.plot(
        [0, room_w, room_w, 0, 0], [0, 0, room_d, room_d, 0],
        color=BASELINE, lw=1.4, zorder=1, solid_capstyle="round",
    )

    # --- the two trails ------------------------------------------------------
    ax.plot(raw_x, raw_y, color=GRAY, lw=1.7, ls=(0, (4, 2.5)), zorder=3, dash_capstyle="round", alpha=0.95)
    ax.plot(corr_x, corr_y, color=GREEN, lw=1.7, zorder=5, solid_capstyle="round", alpha=0.97)

    # wall tags -- ordinary (in the map) vs the start/end tag (excluded, referee-only)
    holdout = _pick_holdout(markers)
    mx = np.array([m.translation[0] for m in markers])
    my = np.array([m.translation[1] for m in markers])
    normal = np.array([m.id != holdout.id for m in markers])
    ax.scatter(mx[normal], my[normal], marker="s", s=36, color=INK, zorder=6, linewidths=0)
    ax.scatter(
        [holdout.translation[0]], [holdout.translation[1]], marker="*", s=230,
        color=HOLDOUT, edgecolors="white", linewidths=0.7, zorder=8,
    )
    ax.text(
        holdout.translation[0] - 0.3, holdout.translation[1], "start/end tag\n(not in map)",
        fontsize=9, fontweight="bold", color=HOLDOUT, ha="right", va="center", zorder=7,
    )

    # fence/start (small square) + ChArUco board reference, co-located near
    # the trajectory's start point (trial/benchmark-rig.md's rig design)
    fence_x, fence_y = float(true_x[0]), float(true_y[0])
    ax.scatter(
        [fence_x], [fence_y], marker="s", s=75, facecolors="none",
        edgecolors=INK, linewidths=1.7, zorder=8,
    )
    board_x, board_y = fence_x + 0.55, fence_y - 0.4
    ax.scatter(
        [board_x], [board_y], marker="D", s=48, facecolors=SURFACE,
        edgecolors=INK_SECONDARY, linewidths=1.3, zorder=8,
    )
    ax.annotate(
        "fence / start", xy=(fence_x, fence_y), xytext=(fence_x + 0.35, fence_y + 0.95),
        fontsize=9.5, color=INK, ha="left", va="center",
        arrowprops=dict(arrowstyle="-", color=INK, lw=1.0, shrinkA=3, shrinkB=3), zorder=7,
    )
    ax.annotate(
        "ChArUco board (reference)", xy=(board_x, board_y), xytext=(board_x + 0.3, board_y - 0.85),
        fontsize=9.5, color=INK_SECONDARY, ha="left", va="center",
        arrowprops=dict(arrowstyle="-", color=INK_SECONDARY, lw=1.0, shrinkA=3, shrinkB=3), zorder=7,
    )

    # --- direct labels for the 3 trails --------------------------------
    # top callout -- raw odometry (mirrors spec_visuals.make_hero's style:
    # vertical connector straight up past the top wall, bold INK text)
    k_top = int(np.argmax(raw_y))
    top_x, top_y = float(raw_x[k_top]), float(raw_y[k_top])
    max_drift = float(np.max(np.hypot(raw_x - true_x, raw_y - true_y)))
    ax.annotate(
        "", xy=(top_x, top_y), xytext=(top_x, room_d + 0.32),
        arrowprops=dict(arrowstyle="-", color=GRAY, lw=1.1, shrinkA=0, shrinkB=4), zorder=7,
    )
    ax.text(
        top_x, room_d + 0.44, f"raw odometry — drifts {max_drift:.1f} m (real)",
        fontsize=12, fontweight="bold", color=INK, ha="center", va="bottom", zorder=7,
    )

    # bottom callout -- marker-corrected
    tail = max(1, len(true_x) // 10)
    k_bot = tail + int(np.argmin(corr_y[tail:]))
    bot_x, bot_y = float(corr_x[k_bot]), float(corr_y[k_bot])
    label_x = min(bot_x + 1.2, room_w - 1.2)
    ax.annotate(
        "", xy=(bot_x, bot_y), xytext=(label_x, -0.55),
        arrowprops=dict(arrowstyle="-", color=GREEN, lw=1.1, shrinkA=0, shrinkB=4), zorder=7,
    )
    ax.text(
        label_x, -0.68, "marker-corrected — snaps back (real)",
        fontsize=12, fontweight="bold", color=INK, ha="center", va="top", zorder=7,
    )

    # scale bar
    sb_x0, sb_y = -pad_x + 0.35, -pad_y + 0.55
    ax.plot([sb_x0, sb_x0 + 2.0], [sb_y, sb_y], color=INK_MUTED, lw=1.4, solid_capstyle="butt", zorder=6)
    for xt in (sb_x0, sb_x0 + 2.0):
        ax.plot([xt, xt], [sb_y - 0.08, sb_y + 0.08], color=INK_MUTED, lw=1.4, zorder=6)
    ax.text(sb_x0 + 1.0, sb_y - 0.22, "2 m", fontsize=9, color=INK_MUTED, ha="center", va="top")

    # headline + subhead (figure-level, full width, clear of the badge below)
    fig.text(
        0.5, 0.978, "What one benchmark run will produce", ha="center", va="top",
        fontsize=19, fontweight="bold", color=INK,
    )
    fig.text(
        0.5, 0.940,
        "Same drive, both pose estimates measured at once",
        ha="center", va="top", fontsize=10.5, color=INK_MUTED,
    )

    # corner badge -- SIMULATED -- top-right of the axes pad, clear
    # of the headline (figure-level, well above) and every in-plot label
    ax.text(
        room_w + pad_x - 0.2, room_d + pad_y - 0.15,
        "SIMULATED\nillustrative — real data replaces this",
        ha="right", va="top", fontsize=9, color=BADGE_FG, fontweight="bold", zorder=9,
        bbox=dict(boxstyle="round,pad=0.5", facecolor=BADGE_BG, edgecolor=BADGE_FG, linewidth=1.1),
    )

    # bonus inset -- green-box camera overlay mock, from one real captured frame
    _draw_camera_inset(fig, sim)

    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    sim = sv.simulate_nominal(seed=args.seed, frame_stride=7)
    make_preview(sim, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
