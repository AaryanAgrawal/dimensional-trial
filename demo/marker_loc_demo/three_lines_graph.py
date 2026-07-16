"""THE 3-LINE GRAPH: how the three relocalization primitives compare on one
run, built for `trial/visual/three-lines.png` (+ a compact top-down
companion, `three-lines-trajectory.png`).

Three estimators, one simulated 100s/4-lap drive (seed 42, the standard
trajectory):

  1. odometry only (gray, dashed) -- REAL data: the same dead-reckoning
     drift `spec_visuals.simulate_nominal` and `project1_preview.py` use.
     Unbounded -- nothing ever corrects it.
  2. lidar relocalization (amber, solid) -- SIMULATED: this harness has no
     lidar, so this line is a synthesized ICP-like corrector (frequent
     small corrections every ~2s, one degraded stretch where scan matches
     get worse) standing in for Project 2's `RelocalizationModule`. Not
     pipeline output -- a stand-in curve, honestly labeled as such both in
     its direct label and the corner badge.
  3. marker relocalization (green, solid) -- REAL data: this trial's actual
     pipeline (detect -> solvePnP -> fuse -> gate -> hold), same run as (1).
     Sawtooth: held correction drifts between tag sightings, snaps back on
     each accepted one.

Position error (m) vs time (s), tag-sighting instants ticked on the time
axis, direct end-of-line labels (no legend box) -- same visual language as
`spec_visuals.py` / `project1_preview.py`.

    cd demo
    ./.venv/bin/python -m marker_loc_demo.three_lines_graph

writes ../trial/visual/three-lines.png and ../trial/visual/three-lines-trajectory.png
(flags: `--out PATH`, `--out-traj PATH`, `--seed N`, `--no-trajectory`).
"""

from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np

from . import camera as cam
from . import localization as loc
from . import odom as odom_mod
from . import render
from . import trajectory as traj
from . import world

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(DEMO_ROOT)
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "trial", "visual", "three-lines.png")
DEFAULT_OUT_TRAJ = os.path.join(PROJECT_ROOT, "trial", "visual", "three-lines-trajectory.png")

# ---- palette -- reuses the exact identity colors already established across
# spec_visuals.py (GRAY/GREEN) and start_end_demo.py (AMBER), so the same
# estimator always reads as the same color across every figure in the set.
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BASELINE = "#c3c2b7"
GRAY = "#9b9a90"  # odometry only -- real, unbounded
AMBER = "#d99a2e"  # lidar relocalization -- simulated stand-in
GREEN = "#1e8f5f"  # marker relocalization -- real pipeline output
HOLDOUT = "#7a4fb5"  # start/end tag, matches project1_preview.py
BADGE_BG = "#fdf1d6"
BADGE_FG = "#8a5710"

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]

DEGRADED_WINDOW_S = (44.0, 58.0)  # lidar's one visible degraded stretch


def _set_font() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family


def _frame_rng(seed: int, k: int) -> np.random.Generator:
    return np.random.default_rng((seed * 1_000_003 + k) % (2**63 - 1))


# ---------------------------------------------------------------------------
# real simulation -- same per-frame loop as spec_visuals.simulate_nominal,
# extended to also record each ACCEPTED tag-sighting instant (the moments
# the graph ticks), which the shared helper doesn't expose.
# ---------------------------------------------------------------------------


def simulate(seed: int = 42) -> dict:
    markers = world.build_marker_map()
    gt = traj.build_ground_truth()
    od = odom_mod.simulate_odometry(gt, seed=seed, noise_scale=1.0)
    intr = cam.Intrinsics.default()
    filt = loc.CorrectionFilter()
    marker_by_id = {m.id: m for m in markers}

    n = len(gt.t)
    true_x, true_y = gt.x.copy(), gt.y.copy()
    raw_x, raw_y = od.x.copy(), od.y.copy()
    corr_x, corr_y = np.zeros(n), np.zeros(n)
    sighting_times: list[float] = []

    for k in range(n):
        pan = cam.cam_pan_yaw(gt.t[k])
        T_base_cam = cam.base_to_camera_T(pan_yaw_rad=pan)
        T_world_base_true = cam.world_to_base_T(gt.x[k], gt.y[k], gt.yaw[k])
        T_world_camera_true = T_world_base_true @ T_base_cam
        rr = render.render_frame(T_world_camera_true, markers, intr, rng=_frame_rng(seed, k))

        filt.predict_step()
        dets = loc.detect_and_solve(rr.image, marker_by_id, intr)

        T_odom_base_raw = cam.world_to_base_T(od.x[k], od.y[k], od.yaw[k])
        fused = loc.fuse_detections(dets, T_base_cam)
        if fused is not None:
            meas = loc.to_world_odom_measurement(fused, T_odom_base_raw)
            if filt.try_update(meas):
                sighting_times.append(float(gt.t[k]))

        T_world_base_corr = filt.T_world_odom @ T_odom_base_raw
        corr_x[k], corr_y[k] = T_world_base_corr[0, 3], T_world_base_corr[1, 3]

    return {
        "markers": markers,
        "t": gt.t,
        "true_x": true_x, "true_y": true_y,
        "raw_x": raw_x, "raw_y": raw_y,
        "corr_x": corr_x, "corr_y": corr_y,
        "sighting_times": np.array(sighting_times),
        "room_w": world.ROOM_WIDTH_X,
        "room_d": world.ROOM_DEPTH_Y,
    }


# ---------------------------------------------------------------------------
# synthetic lidar -- this harness has no lidar, so its "correction" is a
# hand-built ICP-like sawtooth: small residual after each fix, linear drift
# between fixes, one stretch where scan matches degrade. Deterministic given
# `seed` (its own RNG stream, offset from the real sim's so it never
# consumes the same draws), independent of any real pipeline code.
# ---------------------------------------------------------------------------


def synth_lidar(
    t: np.ndarray,
    seed: int = 42,
    period_s: float = 2.0,
    phase_s: float = 1.3,
    growth_m_per_s: float = 0.045,
    residual_sigma_m: float = 0.010,
    degraded: tuple[float, float] = DEGRADED_WINDOW_S,
    degraded_growth_mult: float = 5.0,
    degraded_residual_mult: float = 4.0,
) -> dict:
    """Returns `err` (m, per sample in `t`) and `fix_times` (s) -- the
    corrector's own correction instants, distinct from the real marker
    sim's `sighting_times`."""
    rng = np.random.default_rng(seed + 7_919)  # offset stream -- never touches the real sim's draws
    err = np.empty_like(t)
    fix_times: list[float] = []

    last_fix_t = 0.0
    residual = abs(rng.normal(0.0, residual_sigma_m))
    next_fix = phase_s

    for i, ti in enumerate(t):
        while ti >= next_fix:
            last_fix_t = next_fix
            fix_times.append(last_fix_t)
            in_window = degraded[0] <= last_fix_t <= degraded[1]
            sigma = residual_sigma_m * (degraded_residual_mult if in_window else 1.0)
            residual = abs(rng.normal(0.0, sigma))
            next_fix += period_s
        in_window = degraded[0] <= ti <= degraded[1]
        rate = growth_m_per_s * (degraded_growth_mult if in_window else 1.0)
        err[i] = residual + rate * (ti - last_fix_t)

    return {"err": err, "fix_times": np.array(fix_times)}


def synth_lidar_xy(true_x: np.ndarray, true_y: np.ndarray, t: np.ndarray, err: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Turns the scalar error magnitude into a 2D offset from ground truth,
    for the top-down companion only -- a slowly-rotating direction so the
    path reads as a gentle wobble around the true trajectory rather than a
    single straight bias, at the same magnitude the main chart plots."""
    theta = 0.15 * t + 0.6
    return true_x + err * np.cos(theta), true_y + err * np.sin(theta)


def _pick_holdout(markers):
    """Same repurposed wall tag as project1_preview.py, for continuity
    across the deliverable set."""
    candidates = [m for m in markers if m.wall == "x=0"]
    return min(candidates, key=lambda m: abs(m.translation[1] - 5.0))


# ---------------------------------------------------------------------------
# three-lines.png -- the main error-over-time chart
# ---------------------------------------------------------------------------


def _end_labels(ax, x_end: float, entries: list[tuple[float, str, str]], min_gap_px: float) -> None:
    """`entries` = (y_value, text, color). Labels live just past the right
    spine in a blended transform (x = axes fraction, y = data) so their
    horizontal position never depends on the data's time range; nudged
    apart in display space so close-together line-ends never collide --
    no legend box anywhere in this figure."""
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
            1.035, y_plot, text, transform=trans, fontsize=11.5, fontweight="bold", color=color,
            ha="left", va="center", clip_on=False, zorder=6,
        )


def make_three_lines(sim: dict, lidar: dict, out_path: str) -> dict:
    _set_font()

    t = sim["t"]
    err_odom = np.hypot(sim["raw_x"] - sim["true_x"], sim["raw_y"] - sim["true_y"])
    err_marker = np.hypot(sim["corr_x"] - sim["true_x"], sim["corr_y"] - sim["true_y"])
    err_lidar = lidar["err"]
    t_max = float(t[-1])

    fig, ax = plt.subplots(figsize=(12.2, 5.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(left=0.06, right=0.685, top=0.80, bottom=0.135)
    ax.set_facecolor(SURFACE)

    y_max = float(max(err_odom.max(), err_lidar.max(), err_marker.max())) * 1.08
    ax.set_xlim(0, t_max)
    ax.set_ylim(-0.05 * y_max, y_max)

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
        ax.spines[spine].set_linewidth(1.1)

    ax.yaxis.grid(True, color=BASELINE, linewidth=0.7, alpha=0.55, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", colors=INK_MUTED, labelsize=9.5, length=3.5, width=0.9)
    ax.set_xticks(np.arange(0, t_max + 1, 20))
    ax.set_xlabel("time (s)", fontsize=10.5, color=INK_SECONDARY, labelpad=6)
    ax.set_ylabel("position error (m)", fontsize=10.5, color=INK_SECONDARY, labelpad=8)

    # the three lines
    ax.plot(t, err_odom, color=GRAY, lw=1.7, ls=(0, (5, 3)), dash_capstyle="round", zorder=3, alpha=0.95)
    ax.plot(t, err_lidar, color=AMBER, lw=1.7, solid_capstyle="round", zorder=4)
    ax.plot(t, err_marker, color=GREEN, lw=1.7, solid_capstyle="round", zorder=5)

    # tag-sighting ticks -- accepted marker corrections, real timestamps
    # (green, matching the marker line they belong to; no separate caption
    # needed -- the color ties them to the "marker relocalization" label)
    tick_y0, tick_y1 = -0.045 * y_max, -0.008 * y_max
    for st in sim["sighting_times"]:
        ax.plot([st, st], [tick_y0, tick_y1], color=GREEN, lw=1.0, alpha=0.55, solid_capstyle="butt", zorder=2)

    # subtle callout on the lidar line's one degraded stretch
    d0, d1 = DEGRADED_WINDOW_S
    win = (t >= d0) & (t <= d1)
    k_peak = int(np.argmax(err_lidar * win))
    ax.annotate(
        "weak scan matches here",
        xy=(float(t[k_peak]), float(err_lidar[k_peak])),
        xytext=(float(t[k_peak]) - 1.5, float(err_lidar[k_peak]) + 0.10 * y_max),
        fontsize=8.5, color=INK_MUTED, style="italic", ha="right", va="bottom",
        arrowprops=dict(arrowstyle="-", color=INK_MUTED, lw=0.8, alpha=0.7, shrinkA=0, shrinkB=2),
        zorder=6,
    )

    # direct end-of-line labels -- no legend box
    _end_labels(
        ax, t_max,
        [
            (float(err_odom[-1]), "odometry only", GRAY),
            (float(err_lidar[-1]), "lidar relocalization (simulated)", AMBER),
            (float(err_marker[-1]), "marker relocalization", GREEN),
        ],
        min_gap_px=40.0,
    )

    # headline + subhead
    fig.text(
        0.5, 0.965, "Three ways to close the loop", ha="center", va="top",
        fontsize=19, fontweight="bold", color=INK,
    )
    fig.text(
        0.5, 0.925,
        "Same 100s / 4-lap drive, one run each — odometry (real) vs. marker relocalization (real pipeline) vs. lidar (simulated stand-in)",
        ha="center", va="top", fontsize=10, color=INK_MUTED,
    )

    # corner badge -- top-left: every line ends up-and-to-the-right over
    # time, so top-left of the axes is the one corner guaranteed clear
    ax.text(
        0.015, 0.97, "SIMULATED\nlidar line is a stand-in — odom + marker are this trial's real pipeline",
        transform=ax.transAxes, ha="left", va="top", fontsize=8, color=BADGE_FG, fontweight="bold", zorder=9,
        bbox=dict(boxstyle="round,pad=0.4", facecolor=BADGE_BG, edgecolor=BADGE_FG, linewidth=1.0),
    )

    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)

    return {
        "final_odom_m": float(err_odom[-1]),
        "final_lidar_m": float(err_lidar[-1]),
        "final_marker_m": float(err_marker[-1]),
    }


# ---------------------------------------------------------------------------
# three-lines-trajectory.png -- compact top-down companion
# ---------------------------------------------------------------------------


def make_trajectory(sim: dict, lidar_x: np.ndarray, lidar_y: np.ndarray, out_path: str) -> None:
    _set_font()

    markers = sim["markers"]
    true_x, true_y = sim["true_x"], sim["true_y"]
    raw_x, raw_y = sim["raw_x"], sim["raw_y"]
    corr_x, corr_y = sim["corr_x"], sim["corr_y"]
    room_w, room_d = sim["room_w"], sim["room_d"]

    fig = plt.figure(figsize=(6.6, 5.7), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes((0.045, 0.045, 0.91, 0.78))
    ax.set_facecolor(SURFACE)

    pad_x, pad_y = 1.4, 1.2
    ax.set_xlim(-pad_x, room_w + pad_x)
    ax.set_ylim(-pad_y, room_d + pad_y)
    ax.set_aspect("equal")
    ax.axis("off")

    ax.plot(
        [0, room_w, room_w, 0, 0], [0, 0, room_d, room_d, 0],
        color=BASELINE, lw=1.2, zorder=1, solid_capstyle="round",
    )

    ax.plot(raw_x, raw_y, color=GRAY, lw=1.4, ls=(0, (4, 2.5)), zorder=3, dash_capstyle="round", alpha=0.9)
    ax.plot(true_x, true_y, color=INK_SECONDARY, lw=1.3, zorder=2, alpha=0.5)
    ax.plot(lidar_x, lidar_y, color=AMBER, lw=1.4, zorder=4, alpha=0.95)
    ax.plot(corr_x, corr_y, color=GREEN, lw=1.4, zorder=5, alpha=0.97)

    holdout = _pick_holdout(markers)
    mx = np.array([m.translation[0] for m in markers])
    my = np.array([m.translation[1] for m in markers])
    normal = np.array([m.id != holdout.id for m in markers])
    ax.scatter(mx[normal], my[normal], marker="s", s=24, color=INK, zorder=6, linewidths=0)
    ax.scatter(
        [holdout.translation[0]], [holdout.translation[1]], marker="*", s=160,
        color=HOLDOUT, edgecolors="white", linewidths=0.6, zorder=8,
    )
    ax.text(
        holdout.translation[0] - 0.3, holdout.translation[1], "start/end tag",
        fontsize=7.5, fontweight="bold", color=HOLDOUT, ha="right", va="center", zorder=7,
    )

    # fence/start + ChArUco board reference (same rig elements as project1_preview.py)
    fence_x, fence_y = float(true_x[0]), float(true_y[0])
    ax.scatter([fence_x], [fence_y], marker="s", s=55, facecolors="none", edgecolors=INK, linewidths=1.4, zorder=8)
    board_x, board_y = fence_x + 0.5, fence_y - 0.4
    ax.scatter([board_x], [board_y], marker="D", s=34, facecolors=SURFACE, edgecolors=INK_SECONDARY, linewidths=1.1, zorder=8)
    ax.text(fence_x + 0.25, fence_y + 0.35, "fence/start", fontsize=7.5, color=INK, ha="left", va="center", zorder=7)
    ax.text(board_x + 0.2, board_y - 0.15, "board", fontsize=7.5, color=INK_SECONDARY, ha="left", va="center", zorder=7)

    fig.text(0.5, 0.965, "Same run, top-down", ha="center", va="top", fontsize=14, fontweight="bold", color=INK)
    fig.text(
        0.5, 0.925, "odometry (real) · lidar (simulated) · marker (real) · ground truth in thin gray",
        ha="center", va="top", fontsize=8.5, color=INK_MUTED,
    )

    ax.text(
        room_w + pad_x - 0.15, room_d + pad_y - 0.1, "SIMULATED",
        ha="right", va="top", fontsize=8, color=BADGE_FG, fontweight="bold", zorder=9,
        bbox=dict(boxstyle="round,pad=0.35", facecolor=BADGE_BG, edgecolor=BADGE_FG, linewidth=1.0),
    )

    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--out-traj", default=DEFAULT_OUT_TRAJ)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-trajectory", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    sim = simulate(seed=args.seed)
    lidar = synth_lidar(sim["t"], seed=args.seed)

    finals = make_three_lines(sim, lidar, args.out)
    print(f"wrote {args.out}")
    print(
        f"final errors @ t={sim['t'][-1]:.1f}s -- "
        f"odometry only: {finals['final_odom_m']:.3f} m | "
        f"lidar relocalization (simulated): {finals['final_lidar_m']:.3f} m | "
        f"marker relocalization: {finals['final_marker_m']:.3f} m"
    )

    if not args.no_trajectory:
        lidar_x, lidar_y = synth_lidar_xy(sim["true_x"], sim["true_y"], sim["t"], lidar["err"])
        make_trajectory(sim, lidar_x, lidar_y, args.out_traj)
        print(f"wrote {args.out_traj}")


if __name__ == "__main__":
    main()
