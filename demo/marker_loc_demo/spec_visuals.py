"""Generate the two visual deliverables for `spec/visual/` (hero.png,
demo.gif) from the *exact same* synthetic pipeline `main.py` runs for the
nominal scenario: same world, same trajectory, same odometry, same camera,
same detector -> solvePnP -> fuse -> gate -> hold loop as `pipeline.py`.

This file adds only two things on top of the existing package: (1) capture —
the full per-frame trajectory arrays plus a subsampled set of rendered
camera frames (pipeline.py already samples 4 for `sample_frame_*.png`; this
takes more, for animation), and (2) two renderers (a static hero figure, an
animated split-view GIF). It does not change `pipeline.py` or any numbers —
run with the default seed (42) and the on-screen metrics match
`out/metrics.json` (1.75m raw / 0.33m corrected / 5.3x).

    cd demo
    ./.venv/bin/python -m marker_loc_demo.spec_visuals

writes ../spec/visual/hero.png and ../spec/visual/demo.gif. Flags:
`--out DIR`, `--seed N` (default 42), `--frame-stride N` (default 7, ~143
GIF frames covering all 4 laps), `--fps N` (default 10), `--no-gif` (hero
only, fast iteration).
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
from PIL import Image, ImageDraw, ImageFont

from . import camera as cam
from . import localization as loc
from . import odom as odom_mod
from . import render
from . import trajectory as traj
from . import world

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(DEMO_ROOT)
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "spec", "visual")

# ---- palette: the dataviz skill's reference instance (validated) ----------
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#ffffff"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"  # categorical slot 1 / diverging pole -- tag-corrected (good)
RED = "#e34948"  # categorical slot 6 / diverging pole -- raw odom (drifted)
GT_GRAY = INK_SECONDARY  # neutral diverging midpoint -- ground truth

_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
_ARIAL_TTF = "/System/Library/Fonts/Supplemental/Arial.ttf"
_ARIAL_BOLD_TTF = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _frame_rng(seed: int, k: int) -> np.random.Generator:
    return np.random.default_rng((seed * 1_000_003 + k) % (2**63 - 1))


# ---------------------------------------------------------------------------
# Simulation (mirrors pipeline.run_scenario's nominal path exactly, plus
# capture)
# ---------------------------------------------------------------------------


def simulate_nominal(seed: int = 42, frame_stride: int = 7):
    markers = world.build_marker_map()
    gt = traj.build_ground_truth()
    od = odom_mod.simulate_odometry(gt, seed=seed, noise_scale=1.0)
    intr = cam.Intrinsics.default()
    filt = loc.CorrectionFilter()
    marker_by_id = {m.id: m for m in markers}

    n = len(gt.t)
    true_x, true_y = gt.x.copy(), gt.y.copy()
    raw_x, raw_y, raw_yaw = od.x.copy(), od.y.copy(), od.yaw.copy()
    corr_x, corr_y = np.zeros(n), np.zeros(n)
    # Per-frame copy of the filter's held world<-odom offset (its `corr_x`/
    # `corr_y`/`corr_yaw` state) -- kept alongside the composed `corr_x`/
    # `corr_y` above purely so `_gif_smoothed_correction` below can re-derive
    # a playback-only smoothed trajectory without touching `corr_x`/`corr_y`
    # themselves (those still feed `ate_corr`, hero.png, and every other
    # consumer of this function, bit-for-bit unchanged).
    held_x, held_y, held_yaw = np.zeros(n), np.zeros(n), np.zeros(n)
    captured: list[tuple[int, np.ndarray, list, np.ndarray | None]] = []

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
            filt.try_update(meas)

        T_world_base_corr = filt.T_world_odom @ T_odom_base_raw
        corr_x[k], corr_y[k] = T_world_base_corr[0, 3], T_world_base_corr[1, 3]
        held_x[k], held_y[k], held_yaw[k] = filt.corr_x, filt.corr_y, filt.corr_yaw

        if k % frame_stride == 0:
            corners, ids, _ = loc.tags.get_detector().detectMarkers(rr.image)
            captured.append((k, rr.image.copy(), corners, ids))

    ate_raw = float(np.sqrt(np.mean((true_x - raw_x) ** 2 + (true_y - raw_y) ** 2)))
    ate_corr = float(np.sqrt(np.mean((true_x - corr_x) ** 2 + (true_y - corr_y) ** 2)))
    gif_corr_x, gif_corr_y = _gif_smoothed_correction(held_x, held_y, held_yaw, raw_x, raw_y, raw_yaw)

    return {
        "markers": markers,
        "t": gt.t,
        "true_x": true_x, "true_y": true_y,
        "raw_x": raw_x, "raw_y": raw_y,
        "corr_x": corr_x, "corr_y": corr_y,
        "gif_corr_x": gif_corr_x, "gif_corr_y": gif_corr_y,
        "captured": captured,
        "ate_raw": ate_raw,
        "ate_corr": ate_corr,
        "room_w": world.ROOM_WIDTH_X,
        "room_d": world.ROOM_DEPTH_Y,
    }


# ---------------------------------------------------------------------------
# demo.gif-only smoothing of the corrected trajectory
# ---------------------------------------------------------------------------
#
# `CorrectionFilter.try_update` (localization.py) applies each accepted tag
# correction as a Kalman-gain step to the *held* world<-odom offset in one
# frame -- correct for the benchmark (it's what makes `ate_corr` an honest
# number: the filter's actual behavior, not a cosmetic replay of it) but
# visually a hard snap when played back frame-by-frame at 10fps: measured
# directly on this seed, the composed `corr_x`/`corr_y` trajectory has
# per-step jumps up to ~1.09m in a single 0.1s tick against a ~0.07m median
# step -- i.e. the correction, not the robot, moving. Smoothing is applied
# ONLY to the held offset (`held_x`/`held_y`/`held_yaw`, the filter's slowly-
# corrected bias term), then recomposed against the *unsmoothed* raw odom
# pose every frame -- so the real per-frame motion (same signal driving the
# raw/gray trail and the ground truth) keeps full fidelity, and only the
# correction's convergence gets blended in over a handful of frames instead
# of applied instantaneously. `corr_x`/`corr_y` (hero.png, `ate_corr`,
# metrics) are never touched by this.
GIF_CORR_EWMA_ALPHA = 0.07  # time constant ~1/alpha = 14 steps = 1.4s at HZ=10


def _ewma(values: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = out[i - 1] + alpha * (values[i] - out[i - 1])
    return out


def _gif_smoothed_correction(
    held_x: np.ndarray, held_y: np.ndarray, held_yaw: np.ndarray,
    raw_x: np.ndarray, raw_y: np.ndarray, raw_yaw: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    smooth_x = _ewma(held_x, GIF_CORR_EWMA_ALPHA)
    smooth_y = _ewma(held_y, GIF_CORR_EWMA_ALPHA)
    smooth_yaw = _ewma(np.unwrap(held_yaw), GIF_CORR_EWMA_ALPHA)

    n = len(raw_x)
    out_x, out_y = np.zeros(n), np.zeros(n)
    for k in range(n):
        T_world_odom_smooth = cam.world_to_base_T(smooth_x[k], smooth_y[k], smooth_yaw[k])
        T_odom_base_raw = cam.world_to_base_T(raw_x[k], raw_y[k], raw_yaw[k])
        T_world_base_smooth = T_world_odom_smooth @ T_odom_base_raw
        out_x[k], out_y[k] = T_world_base_smooth[0, 3], T_world_base_smooth[1, 3]
    return out_x, out_y


# ---------------------------------------------------------------------------
# hero.png -- one static, self-explanatory figure
# ---------------------------------------------------------------------------


def make_hero(sim: dict, out_path: str) -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    family = next((f for f in _SANS_CANDIDATES if f in available), "DejaVu Sans")
    plt.rcParams["font.family"] = family

    markers = sim["markers"]
    true_x, true_y = sim["true_x"], sim["true_y"]
    raw_x, raw_y = sim["raw_x"], sim["raw_y"]
    corr_x, corr_y = sim["corr_x"], sim["corr_y"]
    room_w, room_d = sim["room_w"], sim["room_d"]
    n_tags = len(markers)

    fig = plt.figure(figsize=(9.4, 7.9), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes((0.045, 0.05, 0.91, 0.80))
    ax.set_facecolor(SURFACE)

    pad_x, pad_y = 1.5, 1.4
    ax.set_xlim(-pad_x, room_w + pad_x)
    ax.set_ylim(-pad_y, room_d + pad_y)
    ax.set_aspect("equal")
    ax.axis("off")

    # room walls -- the frame itself, no axis chrome needed
    ax.plot(
        [0, room_w, room_w, 0, 0], [0, 0, room_d, room_d, 0],
        color=BASELINE, lw=1.4, zorder=1, solid_capstyle="round",
    )

    # the three paths
    ax.plot(raw_x, raw_y, color=RED, lw=1.8, ls=(0, (5, 3)), zorder=3, dash_capstyle="round", alpha=0.9)
    ax.plot(true_x, true_y, color=GT_GRAY, lw=2.0, zorder=4, solid_capstyle="round")
    ax.plot(corr_x, corr_y, color=BLUE, lw=1.7, zorder=5, solid_capstyle="round", alpha=0.95)

    # wall tags
    mx = np.array([m.translation[0] for m in markers])
    my = np.array([m.translation[1] for m in markers])
    ax.scatter(mx, my, marker="s", s=38, color=INK, zorder=6, linewidths=0)

    # --- direct labels (no boxed legend) ---------------------------------
    # ground truth -- open space on the left wall
    ax.annotate(
        "ground truth", xy=(true_x[np.argmin(true_x)], true_y[np.argmin(true_x)]),
        xytext=(0.15, 4.65), fontsize=11, color=GT_GRAY, ha="left", va="center",
        arrowprops=dict(arrowstyle="-", color=GT_GRAY, lw=1.1, shrinkA=0, shrinkB=3),
    )

    # raw-odom callout -- anchored just above the top of the drifted loops.
    # single line (matches the headline register), connector runs vertically
    # between the two nearest wall tags so it never touches one.
    k_bad = int(np.argmax(raw_y))
    bad_x, bad_y = float(raw_x[k_bad]), float(raw_y[k_bad])
    ax.annotate(
        "", xy=(bad_x, bad_y), xytext=(bad_x, room_d + 0.30),
        arrowprops=dict(arrowstyle="-", color=RED, lw=1.1, shrinkA=0, shrinkB=4),
        zorder=7,
    )
    ax.text(
        bad_x, room_d + 0.42, f"odometry only — {sim['ate_raw']:.2f} m error",
        fontsize=12.5, fontweight="bold", color=INK, ha="center", va="bottom",
    )

    # corrected callout -- anchored just below the bottom of the corrected
    # path; the label sits off to the side so its connector clears the
    # bottom-wall tags instead of dropping straight through one.
    k_good = int(np.argmin(corr_y))
    good_x, good_y = float(corr_x[k_good]), float(corr_y[k_good])
    label_x = good_x + 1.4
    ax.annotate(
        "", xy=(good_x, good_y), xytext=(label_x, -0.55),
        arrowprops=dict(arrowstyle="-", color=BLUE, lw=1.1, shrinkA=0, shrinkB=4),
        zorder=7,
    )
    ax.text(
        label_x, -0.68, f"with {n_tags} stickers — {sim['ate_corr']:.2f} m error",
        fontsize=12.5, fontweight="bold", color=INK, ha="center", va="top",
    )

    # wall-tag caption -- a short label beside one open marker, no leader line
    tag_i = int(np.argmin(np.hypot(mx - (room_w), my - 1.0)))
    ax.text(
        mx[tag_i] - 0.35, my[tag_i], "wall tag", fontsize=9.5, color=INK_SECONDARY, ha="right", va="center",
    )

    # scale bar, bottom-left, inside the pad margin
    sb_x0, sb_y = -1.3, -1.0
    ax.plot([sb_x0, sb_x0 + 2.0], [sb_y, sb_y], color=INK_MUTED, lw=1.4, solid_capstyle="butt", zorder=6)
    for xt in (sb_x0, sb_x0 + 2.0):
        ax.plot([xt, xt], [sb_y - 0.08, sb_y + 0.08], color=INK_MUTED, lw=1.4, zorder=6)
    ax.text(sb_x0 + 1.0, sb_y - 0.22, "2 m", fontsize=9, color=INK_MUTED, ha="center", va="top")

    # headline + subhead, figure-level so they never collide with the plot
    factor = sim["ate_raw"] / sim["ate_corr"]
    fig.text(
        0.5, 0.968, f"Wall tags cut position drift {factor:.1f}×", ha="center", va="top",
        fontsize=20, fontweight="bold", color=INK,
    )
    fig.text(
        0.5, 0.930,
        f"Synthetic 10×8 m room · 100 s loop · {n_tags} wall tags · real ArUco detection on rendered frames",
        ha="center", va="top", fontsize=10.5, color=INK_MUTED,
    )

    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# demo.gif -- split view: left = camera + live detections, right = growing
# top-down map
# ---------------------------------------------------------------------------

_CAM_W, _CAM_H = 384, 288  # resized from the 640x480 render
_MAP_W, _MAP_H = 384, 288
_MARGIN = 16
_HEADER_H = 30
_FOOTER_H = 26
_GIF_W = _MARGIN * 3 + _CAM_W + _MAP_W
_GIF_H = _HEADER_H + max(_CAM_H, _MAP_H) + _FOOTER_H + _MARGIN


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _ARIAL_BOLD_TTF if bold else _ARIAL_TTF
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _map_transform(room_w: float, room_d: float, w: int, h: int, margin: int = 26):
    scale = min((w - 2 * margin) / room_w, (h - 2 * margin) / room_d)
    ox = (w - room_w * scale) / 2.0
    oy = (h - room_d * scale) / 2.0

    def to_px(x: float, y: float) -> tuple[float, float]:
        return ox + x * scale, h - (oy + y * scale)  # flip y: image origin top-left

    return to_px


def _draw_map_base(room_w: float, room_d: float, markers, w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), _hex_rgb(SURFACE))
    d = ImageDraw.Draw(img)
    to_px = _map_transform(room_w, room_d, w, h)
    wall = [to_px(0, 0), to_px(room_w, 0), to_px(room_w, room_d), to_px(0, room_d), to_px(0, 0)]
    d.line(wall, fill=_hex_rgb(BASELINE), width=2)
    for m in markers:
        px, py = to_px(m.translation[0], m.translation[1])
        r = 3.5
        d.rectangle([px - r, py - r, px + r, py + r], fill=_hex_rgb(INK))
    return img


def _draw_polyline(d: ImageDraw.ImageDraw, pts, color, width):
    if len(pts) < 2:
        return
    d.line(pts, fill=color, width=width, joint="curve")


def _compose_frame(
    cam_img_gray: np.ndarray,
    corners,
    ids,
    base_map: Image.Image,
    to_px,
    true_pts, raw_pts, corr_pts,
    t_now: float,
    lap_now: float,
    f_small, f_small_bold,
) -> Image.Image:
    canvas = Image.new("RGB", (_GIF_W, _GIF_H), _hex_rgb(SURFACE))
    d = ImageDraw.Draw(canvas)

    top = _HEADER_H
    # header: time / lap counter, right-aligned; pane captions left-aligned
    d.text((_MARGIN, 6), "camera — live tag detection", font=f_small, fill=_hex_rgb(INK_MUTED))
    d.text(
        (_MARGIN * 2 + _CAM_W, 6), "top-down map — odometry vs tag-corrected",
        font=f_small, fill=_hex_rgb(INK_MUTED),
    )
    d.text(
        (_GIF_W - _MARGIN, 6), f"t = {t_now:4.1f}s   lap {lap_now:.1f}",
        font=f_small, fill=_hex_rgb(INK_MUTED), anchor="ra",
    )

    # --- left: camera frame -------------------------------------------
    # A light blur after the resize costs no legibility (tag edges are high
    # -contrast, they survive it) but collapses the per-pixel sensor-noise
    # texture into far fewer distinct GIF-palette runs -- the single biggest
    # lever on output file size.
    cam_rgb = cv2.cvtColor(cam_img_gray, cv2.COLOR_GRAY2RGB)
    cam_rgb = cv2.resize(cam_rgb, (_CAM_W, _CAM_H), interpolation=cv2.INTER_AREA)
    cam_rgb = cv2.GaussianBlur(cam_rgb, (0, 0), 1.1)
    cam_pil = Image.fromarray(cam_rgb)
    cd = ImageDraw.Draw(cam_pil)
    sx, sy = _CAM_W / cam.IMG_W, _CAM_H / cam.IMG_H
    if ids is not None:
        for c, mid_arr in zip(corners, ids.flatten()):
            pts = [(float(px * sx), float(py * sy)) for px, py in c.reshape(4, 2)]
            cd.line(pts + [pts[0]], fill=_hex_rgb(BLUE), width=2, joint="curve")
            cx = sum(p[0] for p in pts) / 4.0
            cy = sum(p[1] for p in pts) / 4.0
            cd.text((cx, cy - 16), str(int(mid_arr)), font=f_small_bold, fill=_hex_rgb(BLUE), anchor="mm")
    canvas.paste(cam_pil, (_MARGIN, top))
    d.rectangle(
        [_MARGIN, top, _MARGIN + _CAM_W, top + _CAM_H], outline=_hex_rgb(BASELINE), width=1,
    )

    # --- right: growing top-down map ------------------------------------
    map_img = base_map.copy()
    md = ImageDraw.Draw(map_img)
    _draw_polyline(md, true_pts, _hex_rgb(GT_GRAY), 2)
    _draw_polyline(md, raw_pts, _hex_rgb(RED), 2)
    _draw_polyline(md, corr_pts, _hex_rgb(BLUE), 2)
    if raw_pts:
        px, py = raw_pts[-1]
        md.ellipse([px - 4, py - 4, px + 4, py + 4], fill=_hex_rgb(RED))
    if corr_pts:
        px, py = corr_pts[-1]
        md.ellipse([px - 4, py - 4, px + 4, py + 4], fill=_hex_rgb(BLUE))
    map_x0 = _MARGIN * 2 + _CAM_W
    canvas.paste(map_img, (map_x0, top))
    d.rectangle([map_x0, top, map_x0 + _MAP_W, top + _CAM_H], outline=_hex_rgb(BASELINE), width=1)

    # footer legend (small, unboxed, same on every frame) -- solid swatches,
    # matching the solid lines actually drawn on the map (no dash at this
    # scale: it would only add GIF-compression entropy for no legibility
    # gain)
    fy = top + max(_CAM_H, _MAP_H) + 6
    lx = _MARGIN
    for label, color in (
        ("ground truth", GT_GRAY),
        ("odometry only", RED),
        ("tag-corrected", BLUE),
    ):
        d.line([(lx, fy + 6), (lx + 16, fy + 6)], fill=_hex_rgb(color), width=2)
        d.text((lx + 22, fy), label, font=f_small, fill=_hex_rgb(INK))
        lx += 22 + len(label) * 6 + 22

    return canvas


def _palette_seed(frame: Image.Image) -> Image.Image:
    """`frame` plus a strip of solid blocks in every identity color, so a
    global adaptive quantize (see `make_gif`) always reserves a slot for
    each -- see the comment at the call site for why this is necessary."""
    base = frame.convert("RGB")
    w, h = base.size
    swatch_colors = [SURFACE, BASELINE, INK, INK_SECONDARY, INK_MUTED, RED, BLUE, GT_GRAY]
    strip_h = 28
    strip = Image.new("RGB", (w, strip_h), _hex_rgb(SURFACE))
    sd = ImageDraw.Draw(strip)
    block_w = max(1, w // len(swatch_colors))
    for i, c in enumerate(swatch_colors):
        sd.rectangle([i * block_w, 0, (i + 1) * block_w, strip_h], fill=_hex_rgb(c))
    seed = Image.new("RGB", (w, h + strip_h), _hex_rgb(SURFACE))
    seed.paste(base, (0, 0))
    seed.paste(strip, (0, h))
    return seed


def make_gif(sim: dict, out_path: str, fps: int = 10) -> None:
    markers = sim["markers"]
    t = sim["t"]
    room_w, room_d = sim["room_w"], sim["room_d"]
    to_px = _map_transform(room_w, room_d, _MAP_W, _MAP_H)
    base_map = _draw_map_base(room_w, room_d, markers, _MAP_W, _MAP_H)

    f_small = _font(12)
    f_small_bold = _font(11, bold=True)

    lap_period_s = traj.DURATION_S / traj.NUM_LAPS

    frames: list[Image.Image] = []
    true_pts, raw_pts, corr_pts = [], [], []
    prev_k = 0
    for k, img, corners, ids in sim["captured"]:
        for kk in range(prev_k, k + 1):
            true_pts.append(to_px(sim["true_x"][kk], sim["true_y"][kk]))
            raw_pts.append(to_px(sim["raw_x"][kk], sim["raw_y"][kk]))
            # GIF playback uses the EWMA-smoothed correction (see
            # `_gif_smoothed_correction`) -- not `sim["corr_x"]`/`corr_y"`,
            # which are the unmodified filter output used everywhere else.
            corr_pts.append(to_px(sim["gif_corr_x"][kk], sim["gif_corr_y"][kk]))
        prev_k = k + 1

        frame = _compose_frame(
            img, corners, ids, base_map, to_px,
            true_pts, raw_pts, corr_pts,
            t_now=float(t[k]), lap_now=float(t[k]) / lap_period_s,
            f_small=f_small, f_small_bold=f_small_bold,
        )
        frames.append(frame)

    # Shared adaptive palette across all frames for small, consistent output.
    # A plain MEDIANCUT over one frame starves the identity colors: they're
    # a couple of thin 2px lines against a huge gray camera-noise field, so
    # the histogram can merge red/black/gray into one muddy bucket (seen in
    # an earlier pass -- odometry-red and ground-truth-gray both quantized
    # to the same maroon). Seed the quantizer with a solid block of every
    # identity color so each earns its own palette slot regardless of how
    # little frame area it covers.
    pal_source = _palette_seed(frames[len(frames) // 2])
    pal_img = pal_source.quantize(colors=96, method=Image.MEDIANCUT)
    # No dithering: the source is mostly flat fills + a softened noise wash,
    # so dithering would only inject high-frequency speckle that LZW can't
    # compress -- a plain nearest-color quantize looks the same at this size
    # and is dramatically smaller.
    frames_p = [f.convert("RGB").quantize(palette=pal_img, dither=Image.NONE) for f in frames]

    duration_ms = int(round(1000 / fps))
    frames_p[0].save(
        out_path, save_all=True, append_images=frames_p[1:], duration=duration_ms, loop=0, optimize=True, disposal=2,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--frame-stride", type=int, default=7)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--no-gif", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    sim = simulate_nominal(seed=args.seed, frame_stride=args.frame_stride)

    hero_path = os.path.join(args.out, "hero.png")
    make_hero(sim, hero_path)
    print(f"wrote {hero_path}")

    if not args.no_gif:
        gif_path = os.path.join(args.out, "demo.gif")
        make_gif(sim, gif_path, fps=args.fps)
        size_mb = os.path.getsize(gif_path) / 1e6
        n_frames = len(sim["captured"])
        duration_s = n_frames / args.fps
        print(f"wrote {gif_path} ({size_mb:.2f} MB, {n_frames} frames, {duration_s:.1f}s @ {args.fps}fps)")


if __name__ == "__main__":
    main()
