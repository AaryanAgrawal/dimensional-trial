"""ONE-RUN TWO-PANEL: what a single benchmark run produces, remade per
`trial/PAGE-REFINEMENTS.md` ("THE 'what one benchmark run produces' GRAPH" +
"LATEST BATCH" -> two-panel). Replaces the old repeated-loop version
(three-lines.png / project1-preview.png read as many runs) with ONE ~75s
lap, side by side:

  (a) top-down / coordinate view -- ground truth (neutral gray-ink), the
      odom estimate visibly peeling away from it, and the corrected
      ("visual") estimate hugging truth except for one bulge during the
      stretch where the board isn't in view, snapping back onto truth near
      the end. Board + start/end-tag glyphs mark the rig.
  (b) error vs. time -- three lines, same run:
        odom  (gray)  -- monotonic, UNBOUNDED, super-linear, never returns.
        lidar (amber) -- bounded, low, small sawtooth throughout (~2s fixes).
        visual (green) -- low while the board's in view, a HUMP while blind
                          (drifting on odom with nothing to correct it),
                          a hard SNAP back the instant the board reappears.
      The point of the whole figure: a correction goes UP THEN BACK DOWN;
      uncorrected drift goes up and STAYS up. Made explicit with direct
      callouts, not just color.

Entirely a simulated illustration (no real pipeline run backing these
numbers) -- honestly badged as such, matching the "simulated -- expected
output" instruction. Same palette as three_lines_graph.py / project1_preview.py
so odom/lidar/visual read as the same color everywhere on the page.

    cd demo
    ./.venv/bin/python -m marker_loc_demo.one_run_figure

writes the portfolio's public/dimensional/one-run.png directly (flag: `--out PATH`).
"""

from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np

from . import world

DEFAULT_OUT = (
    "/Users/aaryan/Files/Personal Assistant/07_Professional/portfolio/public/dimensional/one-run.png"
)

# ---- palette -- identical hex values to three_lines_graph.py / project1_preview.py
# so the same estimator reads as the same color everywhere on the page.
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BASELINE = "#c3c2b7"
GRAY = "#9b9a90"  # odom -- real character, unbounded
AMBER = "#d99a2e"  # lidar (RelocalizationModule) -- bounded small sawtooth
GREEN = "#1e8f5f"  # visual (this work) -- low / hump / hard snap
HOLDOUT = "#7a4fb5"  # start/end tag glyph
BADGE_BG = "#fdf1d6"
BADGE_FG = "#8a5710"

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]

T_DURATION = 75.0
HZ = 10.0
BOARD_LOST_T = 8.0  # board leaves view
BOARD_SEEN_T = 68.0  # board reacquired -- the hard snap instant


def _set_font() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family


# ---------------------------------------------------------------------------
# simulation -- one ~75s single lap, all three error curves built directly
# to the honest shapes PAGE-REFINEMENTS.md specifies (no real pipeline run
# behind this -- an illustration, badged SIMULATED throughout).
# ---------------------------------------------------------------------------


def simulate_one_run(seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)

    n = int(round(T_DURATION * HZ)) + 1
    t = np.linspace(0.0, T_DURATION, n)

    room_w, room_d = world.ROOM_WIDTH_X, world.ROOM_DEPTH_Y
    cx, cy = room_w / 2.0, room_d / 2.0
    semi_x, semi_y = 3.2, 2.2

    # one full lap, starting near the y=0 wall (where the rig sits) and
    # closing exactly back onto the start point at t=T.
    theta = -np.pi / 2.0 + 2.0 * np.pi * t / T_DURATION
    true_x = cx + semi_x * np.cos(theta)
    true_y = cy + semi_y * np.sin(theta)

    # ---- odom: monotonic, unbounded, super-linear rise, no oscillation ----
    ODOM_END_M = 2.35
    err_odom = ODOM_END_M * (t / T_DURATION) ** 1.7
    phi_odom = 0.5 + 0.045 * t
    odom_x = true_x + err_odom * np.cos(phi_odom)
    odom_y = true_y + err_odom * np.sin(phi_odom)

    # ---- lidar: bounded, low, small sawtooth throughout (~2s fixes) ----
    period_s, phase_s = 2.0, 1.1
    growth_m_per_s, residual_sigma_m = 0.018, 0.006
    err_lidar = np.empty_like(t)
    last_fix_t = 0.0
    residual = abs(rng.normal(0.0, residual_sigma_m))
    next_fix = phase_s
    for i, ti in enumerate(t):
        while ti >= next_fix:
            last_fix_t = next_fix
            residual = abs(rng.normal(0.0, residual_sigma_m))
            next_fix += period_s
        err_lidar[i] = residual + growth_m_per_s * (ti - last_fix_t)

    # ---- visual: low (board seen) -> hump (blind, drifting on odom) ----
    # -> hard snap back the instant the board reappears -> low to the end.
    BASELINE_LOW = 0.035
    HUMP_MAX = 1.55
    SNAP_TAU = 0.22  # seconds -- how fast the snap collapses (near-vertical)

    err_visual = np.empty_like(t)
    for i, ti in enumerate(t):
        if ti <= BOARD_LOST_T:
            # board in view: quick settle to a small tracked residual
            err_visual[i] = BASELINE_LOW * min(1.0, ti / 2.0)
        elif ti <= BOARD_SEEN_T:
            # blind: drifting on odom alone, growing the further it goes
            frac = (ti - BOARD_LOST_T) / (BOARD_SEEN_T - BOARD_LOST_T)
            err_visual[i] = BASELINE_LOW + (HUMP_MAX - BASELINE_LOW) * frac**1.5
        else:
            # snap: board back in view -- near-instant correction
            err_visual[i] = BASELINE_LOW + (HUMP_MAX - BASELINE_LOW) * np.exp(
                -(ti - BOARD_SEEN_T) / SNAP_TAU
            )

    # constant outward direction (into the room, away from the wall the rig
    # sits on) -- keeps the blind-stretch bulge a clean single sweep instead
    # of a spiral, and keeps the post-snap collapse a straight pull-in.
    phi_vis = 1.75
    corr_x = true_x + err_visual * np.cos(phi_vis)
    corr_y = true_y + err_visual * np.sin(phi_vis)

    return {
        "t": t,
        "true_x": true_x, "true_y": true_y,
        "odom_x": odom_x, "odom_y": odom_y,
        "corr_x": corr_x, "corr_y": corr_y,
        "err_odom": err_odom, "err_lidar": err_lidar, "err_visual": err_visual,
        "room_w": room_w, "room_d": room_d,
        "start_x": float(true_x[0]), "start_y": float(true_y[0]),
    }


# ---------------------------------------------------------------------------
# panel (a) -- top-down coordinate view
# ---------------------------------------------------------------------------


def _draw_panel_a(fig, ax, sim: dict) -> None:
    room_w, room_d = sim["room_w"], sim["room_d"]
    true_x, true_y = sim["true_x"], sim["true_y"]
    odom_x, odom_y = sim["odom_x"], sim["odom_y"]
    corr_x, corr_y = sim["corr_x"], sim["corr_y"]

    ax.set_facecolor(SURFACE)
    pad_x, pad_y = 1.5, 1.3
    ax.set_xlim(-pad_x, room_w + pad_x)
    ax.set_ylim(-pad_y, room_d + pad_y)
    ax.set_aspect("equal")
    ax.axis("off")

    ax.plot(
        [0, room_w, room_w, 0, 0], [0, 0, room_d, room_d, 0],
        color=BASELINE, lw=1.2, zorder=1, solid_capstyle="round",
    )

    ax.plot(true_x, true_y, color=INK_SECONDARY, lw=1.4, zorder=2, alpha=0.55)
    ax.plot(odom_x, odom_y, color=GRAY, lw=1.7, ls=(0, (4, 2.5)), dash_capstyle="round", zorder=3, alpha=0.95)
    ax.plot(corr_x, corr_y, color=GREEN, lw=1.7, zorder=5, solid_capstyle="round", alpha=0.97)

    # ---- board + start/end-tag glyphs, at the rig near the loop's close ----
    sx, sy = sim["start_x"], sim["start_y"]
    tag_x, tag_y = sx - 0.75, sy - 0.15
    board_x, board_y = sx + 0.35, sy - 0.55

    ax.scatter([tag_x], [tag_y], marker="*", s=210, color=HOLDOUT, edgecolors="white", linewidths=0.7, zorder=8)
    ax.text(tag_x - 0.28, tag_y, "start / end\ntag", fontsize=8.5, fontweight="bold", color=HOLDOUT,
            ha="right", va="center", zorder=7)

    ax.scatter([board_x], [board_y], marker="D", s=52, facecolors=SURFACE, edgecolors=INK_SECONDARY,
               linewidths=1.2, zorder=8)
    ax.text(board_x + 0.28, board_y, "board", fontsize=8.5, color=INK_SECONDARY, ha="left", va="center", zorder=7)

    # ---- callout: odom visibly peeling away -- anchored on the far/top arc
    # (well clear of the tag/board cluster), short local connector only ----
    k_far = int(np.argmin(np.abs(sim["t"] - 0.60 * T_DURATION)))
    fx, fy = float(odom_x[k_far]), float(odom_y[k_far])
    ax.annotate(
        "odom drifts away\nfrom ground truth",
        xy=(fx, fy), xytext=(fx - 0.2, fy + 0.85),
        fontsize=8.5, color=INK, fontweight="bold", ha="center", va="bottom",
        arrowprops=dict(arrowstyle="-", color=GRAY, lw=1.0, shrinkA=2, shrinkB=4), zorder=7,
    )

    # ---- callout: the visual bulge while the board's out of view -- anchor
    # on the outer edge of the bulge, short local connector only ----
    blind_mask = (sim["t"] > BOARD_LOST_T) & (sim["t"] < BOARD_SEEN_T)
    dist_from_truth = np.hypot(corr_x - true_x, corr_y - true_y)
    k_bulge = int(np.where(blind_mask)[0][np.argmax(dist_from_truth[blind_mask])])
    bx, by = float(corr_x[k_bulge]), float(corr_y[k_bulge])
    ax.annotate(
        "board out of view —\ndrifts on odom, snaps\nback on re-sighting",
        xy=(bx, by), xytext=(bx + 0.55, by + 0.55),
        fontsize=8.5, color=INK, ha="left", va="bottom",
        arrowprops=dict(arrowstyle="-", color=GREEN, lw=1.0, shrinkA=2, shrinkB=4), zorder=7,
    )

    ax.set_title("(a)  top-down — one run", loc="left", fontsize=11.5, fontweight="bold", color=INK, pad=8)


# ---------------------------------------------------------------------------
# panel (b) -- error vs. time
# ---------------------------------------------------------------------------


def _end_labels(ax, x_end: float, entries: list[tuple[float, str, str]], min_gap_px: float) -> None:
    trans = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
    order = sorted(range(len(entries)), key=lambda i: entries[i][0])
    placed: list[float] = []
    for i in order:
        y, _, _ = entries[i]
        y_disp = ax.transData.transform((0, y))[1]
        if placed and y_disp - placed[-1] < min_gap_px:
            y_disp = placed[-1] + min_gap_px
        placed.append(y_disp)

    for slot, i in enumerate(order):
        y_val, text, color = entries[i]
        y_plot = ax.transData.inverted().transform((0, placed[slot]))[1]
        ax.annotate(
            "", xy=(x_end, y_val), xycoords="data", xytext=(1.02, y_plot), textcoords=trans,
            arrowprops=dict(arrowstyle="-", color=color, lw=1.0, shrinkA=0, shrinkB=0),
            annotation_clip=False, zorder=6,
        )
        ax.text(
            1.035, y_plot, text, transform=trans, fontsize=10.5, fontweight="bold", color=color,
            ha="left", va="center", clip_on=False, zorder=6,
        )


def _draw_panel_b(fig, ax, sim: dict) -> None:
    t = sim["t"]
    err_odom, err_lidar, err_visual = sim["err_odom"], sim["err_lidar"], sim["err_visual"]
    t_max = float(t[-1])

    ax.set_facecolor(SURFACE)
    y_max = float(max(err_odom.max(), err_lidar.max(), err_visual.max())) * 1.18
    ax.set_xlim(0, t_max)
    ax.set_ylim(-0.04 * y_max, y_max)

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
        ax.spines[spine].set_linewidth(1.1)

    ax.yaxis.grid(True, color=BASELINE, linewidth=0.7, alpha=0.55, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", colors=INK_MUTED, labelsize=9, length=3.5, width=0.9)
    ax.set_xticks(np.arange(0, t_max + 1, 15))
    ax.set_xlabel("time (s)", fontsize=10, color=INK_SECONDARY, labelpad=6)
    ax.set_ylabel("position error (m)", fontsize=10, color=INK_SECONDARY, labelpad=8)

    ax.plot(t, err_odom, color=GRAY, lw=1.8, ls=(0, (5, 3)), dash_capstyle="round", zorder=3, alpha=0.95)
    ax.plot(t, err_lidar, color=AMBER, lw=1.6, solid_capstyle="round", zorder=4)
    ax.plot(t, err_visual, color=GREEN, lw=1.9, solid_capstyle="round", zorder=5)

    # start marker -- all begin at (0, ~0)
    ax.annotate(
        "all start ≈ 0", xy=(0.0, 0.0), xytext=(6.5, 0.10 * y_max),
        fontsize=8.5, color=INK_MUTED, ha="left", va="bottom", style="italic",
        arrowprops=dict(arrowstyle="-", color=INK_MUTED, lw=0.8, shrinkA=2, shrinkB=2), zorder=6,
    )

    # THE key contrast callouts -- up-then-down vs. up-and-stays-up
    k_peak = int(np.argmax(err_visual))
    ax.annotate(
        "correction: error rises,\nthen SNAPS back down",
        xy=(float(t[k_peak]), float(err_visual[k_peak])),
        xytext=(float(t[k_peak]) - 20.0, float(err_visual[k_peak]) + 0.26 * y_max),
        fontsize=9, color=GREEN, fontweight="bold", ha="left", va="bottom",
        arrowprops=dict(arrowstyle="-", color=GREEN, lw=1.0, shrinkA=2, shrinkB=4), zorder=7,
    )
    k_late = int(np.argmin(np.abs(t - t_max * 0.72)))
    ax.annotate(
        "drift: error rises and\nNEVER comes back down",
        xy=(float(t[k_late]), float(err_odom[k_late])),
        xytext=(float(t[k_late]) - 32.0, float(err_odom[k_late]) - 0.05 * y_max),
        fontsize=9, color=INK, fontweight="bold", ha="left", va="top",
        arrowprops=dict(arrowstyle="-", color=GRAY, lw=1.0, shrinkA=2, shrinkB=4), zorder=7,
    )

    # end-gap bracket: odom big vs. visual small, same instant
    x_bk = t_max + 0.6
    ax.plot([x_bk, x_bk], [float(err_visual[-1]), float(err_odom[-1])], color=INK_MUTED, lw=1.0, zorder=6,
            transform=ax.transData, clip_on=False)
    for yv in (float(err_visual[-1]), float(err_odom[-1])):
        ax.plot([x_bk - 0.4, x_bk], [yv, yv], color=INK_MUTED, lw=1.0, zorder=6, clip_on=False)

    _end_labels(
        ax, t_max,
        [
            (float(err_odom[-1]), "odom — unbounded drift", GRAY),
            (float(err_lidar[-1]), "lidar — bounded, small", AMBER),
            (float(err_visual[-1]), "visual (this work) — back near zero", GREEN),
        ],
        min_gap_px=34.0,
    )

    ax.set_title("(b)  error vs. time — same run", loc="left", fontsize=11.5, fontweight="bold", color=INK, pad=8)


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


def make_one_run(sim: dict, out_path: str) -> dict:
    _set_font()

    fig = plt.figure(figsize=(15.6, 6.6), dpi=190)
    fig.patch.set_facecolor(SURFACE)

    ax_a = fig.add_axes((0.028, 0.08, 0.30, 0.72))
    ax_b = fig.add_axes((0.40, 0.115, 0.375, 0.655))

    _draw_panel_a(fig, ax_a, sim)
    _draw_panel_b(fig, ax_b, sim)

    fig.text(0.5, 0.965, "What one benchmark run produces", ha="center", va="top",
              fontsize=20, fontweight="bold", color=INK)
    fig.text(
        0.5, 0.925,
        "One ~75s run, not repeated loops — a correction always comes back down; uncorrected drift never does",
        ha="center", va="top", fontsize=10.5, color=INK_MUTED,
    )

    # shared color key -- same three colors mean the same thing in both
    # panels, so state it once, above both, rather than repeating a legend.
    legend_handles = [
        mlines.Line2D([], [], color=GRAY, lw=1.8, ls=(0, (5, 3)), label="odom"),
        mlines.Line2D([], [], color=AMBER, lw=1.8, label="lidar"),
        mlines.Line2D([], [], color=GREEN, lw=1.9, label="visual (this work)"),
    ]
    fig.legend(
        handles=legend_handles, loc="upper center", bbox_to_anchor=(0.402, 0.885),
        ncol=3, frameon=False, fontsize=10.5, handlelength=2.2, columnspacing=1.6,
        labelcolor=[GRAY, AMBER, GREEN],
    )

    fig.text(
        0.965, 0.045, "SIMULATED\nexpected output — illustrative, not measured data",
        ha="right", va="bottom", fontsize=8.5, color=BADGE_FG, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.45", facecolor=BADGE_BG, edgecolor=BADGE_FG, linewidth=1.0),
    )

    fig.savefig(out_path, dpi=190, facecolor=SURFACE)
    plt.close(fig)

    return {
        "final_odom_m": float(sim["err_odom"][-1]),
        "final_lidar_m": float(sim["err_lidar"][-1]),
        "final_visual_m": float(sim["err_visual"][-1]),
        "hump_peak_m": float(sim["err_visual"].max()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    sim = simulate_one_run(seed=args.seed)
    finals = make_one_run(sim, args.out)
    print(f"wrote {args.out}")
    print(
        f"final @ t={T_DURATION:.0f}s -- odom: {finals['final_odom_m']:.3f} m | "
        f"lidar: {finals['final_lidar_m']:.3f} m | visual: {finals['final_visual_m']:.3f} m "
        f"(hump peak {finals['hump_peak_m']:.3f} m @ ~{BOARD_SEEN_T:.0f}s)"
    )


if __name__ == "__main__":
    main()
