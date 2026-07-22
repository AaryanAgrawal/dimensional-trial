#!/usr/bin/env python3
"""Visualize a premap: points + robot trajectory + fiducial tags.

A reusable, parametrized rerun/PNG view for ANY dimos premap. Point it at a
premap export and it renders, as separate independently-toggleable branches:

  1. ``world/premap`` -- the premap PGO-export as PLAIN POINTS (no voxel
     downsample), ONE steel-blue hue with brightness from robust (2-98th pctile)
     normalized height: HIGHER = LIGHTER. The VALUE range is lifted to
     [0.5..1.0] so the floor (low z) stays visible on rerun's dark canvas.
  2. ``world/path_pgo`` -- the PGO-corrected keyframe trajectory (the path that
     built the map, world_corrected frame), a START->END turbo gradient with a
     green start sphere and a red end sphere.
  3. ``world/path_odom`` -- the raw LIO odom trajectory (pre-correction,
     world_raw frame), same START->END turbo gradient + green/red endcaps.
  4. ``world/markers`` -- the surveyed fiducial tags as bold magenta spheres,
     each labelled ``tag <id>``.

Static, offline, files-in view -- NOT a runner. No ``dimos --replay``, no LCM
bus, no live capture. Read-only on all inputs. The data-path loaders (premap
decode, PGO keyframe path, odom path, marker map) and the trajectory/marker
logging are IMPORTED VERBATIM from ``premap_with_path.py`` -- nothing about the
data path is re-derived here; this module only parametrizes them behind a CLI
and supplies the dark-visible premap shading.

Determinism: no RNG, no wall-clock. Fixed files, fixed order (keyframes/odom
sorted by timestamp, tags sorted by id); everything logged ``static=True``.

Defaults target sf_office survey1. Point it at another premap with the flags:

    python premap_viz.py \
        --premap    out/robotday_build/sf_office_go2_20260718_survey3.pc2.lcm \
        --prep-pkl  out/prepared/sf_office_go2_20260718_survey3.pkl \
        --odom-db   ../../dimos/data/sf_office_go2_20260718_survey3.db \
        --marker-map out/robotday_build_gated/sf_office_go2_20260718_survey3.marker_map.json \
        --out-rrd   out/eval/survey3_premap_markers.rrd

The PNG defaults to the ``--out-rrd`` path with a ``.png`` suffix. A missing
prep-pkl / odom-db / marker-map is skipped with a note, not an error, so the
tool still renders whatever inputs a given premap has.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rerun as rr

# Data-path loaders + trajectory/marker logging come straight from
# premap_with_path -- the shared, verified source. This module never re-derives
# a premap/path/marker step; it only drives them and adds dark-visible shading.
from premap_with_path import (
    ODOM_DB,
    PGO_RADIUS_M,
    ODOM_RADIUS_M,
    PREMAP_HUE,
    PREMAP_RADIUS_M,
    PREMAP_SAT,
    PREMAP_VAL_HI,
    PREP_PKL,
    PREMAP_PC2,
    MARKER_MAP_JSON,
    _height_shade,
    _log_markers,
    _log_path,
    load_markers,
    load_odom_path,
    load_pgo_path,
    load_premap_points,
    max_xy_divergence,
)

DEFAULT_OUT_RRD = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/eval/"
    "survey1_premap_markers.rrd"
)

# This view renders on a DARK canvas (rerun) and a charcoal PNG, so the floor
# (low z) needs a lifted VALUE floor or it vanishes into the background. Same
# steel-blue hue + "higher is lighter" as premap_with_path, but VALUE spans
# [0.5..1.0] instead of [0.15..1.0]: low z stays a clearly visible mid steel-blue.
SHARE_VAL_LO = 0.50
CHARCOAL = "#16161a"      # PNG background -- dark, matches rerun's canvas
TITLE_FG = "#e8e8ec"      # light title text, reads on charcoal
LABEL_FG = "#f5f5fa"      # near-white tag labels, reads on charcoal


def _share_colors(z: np.ndarray) -> np.ndarray:
    """Nx3 uint8 steel-blue, VALUE in [SHARE_VAL_LO..PREMAP_VAL_HI] from robust
    (2-98th pctile) normalized height: low z = mid steel-blue (still visible on
    dark), high z = light. One hue -- read vertical structure as shading."""
    v = SHARE_VAL_LO + _height_shade(z) * (PREMAP_VAL_HI - SHARE_VAL_LO)
    hsv = np.column_stack(
        [np.full_like(v, PREMAP_HUE), np.full_like(v, PREMAP_SAT), v]
    )
    return (mcolors.hsv_to_rgb(hsv) * 255).astype(np.uint8)


def _share_cmap() -> mcolors.LinearSegmentedColormap:
    """Single-hue steel-blue colormap for the PNG scatter, spanning the lifted
    [SHARE_VAL_LO..PREMAP_VAL_HI] value range so it matches the rrd points."""
    dark = mcolors.hsv_to_rgb((PREMAP_HUE, PREMAP_SAT, SHARE_VAL_LO))
    light = mcolors.hsv_to_rgb((PREMAP_HUE, PREMAP_SAT, PREMAP_VAL_HI))
    return mcolors.LinearSegmentedColormap.from_list("steel_share", [dark, light])


def save_png(out_png: Path, premap: np.ndarray, pgo: np.ndarray | None,
             odom: np.ndarray | None, div: tuple[float, float] | None,
             markers: list[tuple[int, np.ndarray]]) -> None:
    """Top-down XY still on charcoal: dark-visible steel-blue premap scatter +
    both START->END turbo trajectories (green * start, red X end) + magenta
    labelled tags. Same dark-visible shading as the rrd points."""
    fig, ax = plt.subplots(figsize=(13, 11))
    fig.patch.set_facecolor(CHARCOAL)
    ax.set_facecolor(CHARCOAL)
    # Stride keeps the PNG light; full cloud is in the rrd. Single steel-blue hue,
    # brightness from normalized height (low z = mid-blue, high z = light) -- the
    # lifted [0.5..1.0] shading, so the floor stays visible on the charcoal bg.
    shade = _height_shade(premap[:, 2])
    pm, pm_c = premap[::3], shade[::3]
    ax.scatter(pm[:, 0], pm[:, 1], s=1.1, c=pm_c, cmap=_share_cmap(),
               vmin=0.0, vmax=1.0, alpha=0.7, linewidths=0, zorder=1,
               label=f"premap ({len(premap)} pts, height->shade)")
    for xyz, name, marker, sz in (
        (pgo, "PGO-corrected keyframes", "o", 10),
        (odom, "raw LIO odom", ".", 4),
    ):
        if xyz is None:
            continue
        t = np.linspace(0, 1, len(xyz))
        ax.scatter(xyz[:, 0], xyz[:, 1], s=sz, c=t, cmap="turbo", marker=marker,
                   linewidths=0, zorder=3, label=f"{name} (n={len(xyz)})")
        ax.scatter(*xyz[0, :2], s=180, c="#28c846", edgecolors="k",
                   linewidths=1.2, zorder=5, marker="*")
        ax.scatter(*xyz[-1, :2], s=180, c="#dc2828", edgecolors="k",
                   linewidths=1.2, zorder=5, marker="X")
    if markers:
        mxy = np.array([p[:2] for _id, p in markers])
        # No legend label: markerscale would balloon this s=200 marker; the tags
        # are already labelled inline ("tag <id>"), so the legend stays clean.
        ax.scatter(mxy[:, 0], mxy[:, 1], s=200, c="#ff00c8", marker="o",
                   edgecolors="w", linewidths=1.3, zorder=6)
        for (tag_id, _p), (x, y) in zip(markers, mxy):
            ax.annotate(f"tag {tag_id}", (x, y), textcoords="offset points",
                        xytext=(8, 7), fontsize=11, fontweight="bold",
                        color=LABEL_FG, zorder=7)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ttl = "premap (steel-blue, height->brightness) + trajectories + fiducial tags"
    if div is not None:
        ttl += f"   |   max PGO-vs-odom XY divergence: {div[0]:.3f} m"
    ax.set_title(ttl, fontsize=13, fontweight="bold", pad=12, color=TITLE_FG)
    leg = ax.legend(loc="best", fontsize=9, markerscale=3, framealpha=0.15)
    for txt in leg.get_texts():
        txt.set_color(TITLE_FG)
    ax.text(0.01, 0.01, "green *=start   red X=end", transform=ax.transAxes,
            fontsize=9, va="bottom", color=TITLE_FG)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor=CHARCOAL)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Visualize a premap: points + trajectory (PGO + odom) + tags.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--premap", type=Path, default=PREMAP_PC2,
                   help="premap PGO-export .pc2.lcm (rendered as plain points)")
    p.add_argument("--prep-pkl", type=Path, default=PREP_PKL,
                   help="prep pickle with pose_graph_bytes for path_pgo")
    p.add_argument("--odom-db", type=Path, default=ODOM_DB,
                   help="recording db with the odom stream for path_odom")
    p.add_argument("--marker-map", type=Path, default=MARKER_MAP_JSON,
                   help="marker_map.json (map_T_tag) for the fiducial tags")
    p.add_argument("--out-rrd", type=Path, default=DEFAULT_OUT_RRD,
                   help="output .rrd path")
    p.add_argument("--out-png", type=Path, default=None,
                   help="output .png path (default: --out-rrd with .png suffix)")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    out_png = a.out_png if a.out_png is not None else a.out_rrd.with_suffix(".png")

    premap, n_full = load_premap_points(a.premap)
    pgo = load_pgo_path(a.prep_pkl)
    odom = load_odom_path(a.odom_db)
    markers = load_markers(a.marker_map)

    print(f"[premap_viz] premap: {n_full} raw pts (points, no voxel) from {a.premap.name}")
    if pgo is None:
        print("[premap_viz] path_pgo: MISSING (prep pkl absent) -- skipped")
    else:
        print(f"[premap_viz] path_pgo: {len(pgo)} PGO-corrected keyframes "
              f"(world_corrected) from {a.prep_pkl.name}")
    if odom is None:
        print("[premap_viz] path_odom: MISSING (db absent) -- skipped")
    else:
        print(f"[premap_viz] path_odom: {len(odom)} raw LIO odom poses "
              f"(world_raw) from {a.odom_db.name}")
    if not markers:
        print("[premap_viz] markers: MISSING (marker map absent) -- skipped")
    else:
        ids = [tag_id for tag_id, _p in markers]
        print(f"[premap_viz] markers: {len(markers)} survey tags {ids} "
              f"(map_T_tag) from {a.marker_map.name}")

    div = None
    if pgo is not None and odom is not None:
        div = max_xy_divergence(pgo, odom)
        print(f"[premap_viz] max PGO-vs-odom XY divergence: {div[0]:.3f} m "
              f"(at ts {div[1]:.1f})")

    rr.init("premap_viz", spawn=False)
    a.out_rrd.parent.mkdir(parents=True, exist_ok=True)
    rr.save(str(a.out_rrd))

    rr.log("world/premap",
           rr.Points3D(premap, colors=_share_colors(premap[:, 2]),
                       radii=PREMAP_RADIUS_M),
           static=True)
    if pgo is not None:
        _log_path("world/path_pgo", pgo[:, :3], PGO_RADIUS_M)
    if odom is not None:
        _log_path("world/path_odom", odom[:, :3], ODOM_RADIUS_M)
    _log_markers(markers)

    save_png(out_png, premap, pgo, odom, div, markers)
    print(f"[premap_viz] wrote {a.out_rrd}")
    print(f"[premap_viz] wrote {out_png}")


if __name__ == "__main__":
    main()
