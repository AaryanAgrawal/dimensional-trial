#!/usr/bin/env python3
"""Clean, shareable view of the survey1 premap + fiducial tags -- NO trajectories.

A stripped-down sibling of ``premap_with_path.py`` for dropping into a team chat:
just the two things a reader cares about, each its own toggleable branch.
  1. ``world/premap`` -- the survey1 PGO-export premap as PLAIN POINTS (no voxel),
     ONE steel-blue hue, brightness from robust (2-98th pctile) normalized height
     (low z dark, high z light). Exact same shading as ``premap_with_path.py``.
  2. ``world/markers`` -- the 5 surveyed fiducial tags (ids 2,3,5,6,7), each a bold
     magenta sphere labelled ``tag <id>``.

Loaders are REUSED verbatim from ``premap_with_path.py`` (dimos' own
``PointCloud2.lcm_decode`` premap + validated ``load_marker_map`` tags) -- nothing
re-derived here. Read-only on data. Determinism: no RNG, no wall-clock; fixed
files, fixed order (tags sorted by id), everything logged ``static=True``.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rerun as rr

from premap_with_path import (
    _height_shade,
    MARKER_RGB,
    PREMAP_HUE,
    PREMAP_SAT,
    PREMAP_VAL_HI,
    PREMAP_RADIUS_M,
    load_markers,
    load_premap_points,
)

MARKER_RADIUS_M = 0.20  # bold tag spheres for the shared view, per request

# This view renders on a DARK bg (charcoal PNG + rerun's dark canvas), so the
# floor (low z) needs a lifted VALUE floor or it vanishes into the background.
# Same steel-blue hue + "higher is lighter" as premap_with_path, but VALUE spans
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

OUT_RRD = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/eval/"
    "survey1_premap_markers.rrd"
)
OUT_PNG = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/eval/"
    "survey1_premap_markers.png"
)


def save_png(premap: np.ndarray,
             markers: list[tuple[int, np.ndarray]]) -> None:
    """Polished top-down XY still: single steel-blue height-shaded premap scatter
    + magenta labelled tags, clean title, no axis clutter -- inline-ready."""
    fig, ax = plt.subplots(figsize=(12, 11))
    fig.patch.set_facecolor(CHARCOAL)
    ax.set_facecolor(CHARCOAL)
    # Stride keeps the PNG light; full cloud is in the rrd. Single steel-blue hue,
    # brightness from normalized height (low z = mid-blue, high z = light) -- same
    # lifted shading as the rrd points, so vertical structure reads as shading and
    # the floor stays visible on the charcoal bg. s bumped so density reads on dark.
    shade = _height_shade(premap[:, 2])
    pm, pm_c = premap[::3], shade[::3]
    ax.scatter(pm[:, 0], pm[:, 1], s=1.1, c=pm_c, cmap=_share_cmap(),
               vmin=0.0, vmax=1.0, alpha=0.7, linewidths=0, zorder=1)
    if markers:
        mxy = np.array([p[:2] for _id, p in markers])
        ax.scatter(mxy[:, 0], mxy[:, 1], s=260, c="#ff00c8", marker="o",
                   edgecolors="w", linewidths=1.4, zorder=6)
        for (tag_id, _p), (x, y) in zip(markers, mxy):
            ax.annotate(f"tag {tag_id}", (x, y), textcoords="offset points",
                        xytext=(10, 8), fontsize=12, fontweight="bold",
                        color=LABEL_FG, zorder=7)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("sf_office survey1 premap + fiducial tags",
                 fontsize=16, fontweight="bold", pad=14, color=TITLE_FG)
    fig.tight_layout()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=150, facecolor=CHARCOAL)
    plt.close(fig)


def main() -> None:
    premap, n_full = load_premap_points()
    markers = load_markers()
    ids = [tag_id for tag_id, _p in markers]

    print(f"[premap_markers_share] premap: {n_full} raw pts (points, no voxel)")
    print(f"[premap_markers_share] markers: {len(markers)} tags {ids}")

    rr.init("survey1_premap_markers", spawn=False)
    OUT_RRD.parent.mkdir(parents=True, exist_ok=True)
    rr.save(str(OUT_RRD))

    rr.log("world/premap",
           rr.Points3D(premap, colors=_share_colors(premap[:, 2]),
                       radii=PREMAP_RADIUS_M),
           static=True)
    if markers:
        xyz = np.array([p for _id, p in markers], dtype=np.float64)
        labels = [f"tag {tag_id}" for tag_id, _p in markers]
        rr.log("world/markers",
               rr.Points3D(xyz, colors=[MARKER_RGB] * len(markers),
                           radii=MARKER_RADIUS_M, labels=labels),
               static=True)

    save_png(premap, markers)
    print(f"[premap_markers_share] wrote {OUT_RRD}")
    print(f"[premap_markers_share] wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
