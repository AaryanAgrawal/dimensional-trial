#!/usr/bin/env python3
"""Render the survey1 premap as a RAW point cloud with BOTH robot trajectories
overlaid, each as its own START->END gradient path, so PGO vs raw-LIO can be
compared side by side.

Static, offline, files-in view -- NOT a runner. No ``dimos --replay``, no LCM
bus, no live capture. Read-only on existing files. It loads:
  1. the premap ``.pc2.lcm`` PGO export (dimos' own ``PointCloud2.lcm_decode``,
     rendered as PLAIN POINTS -- no voxel downsample, no re-implementation), and
  2. two trajectories, both logged into the premap's ``world`` frame:
       path_pgo  -- PGO-corrected keyframe poses (``Keyframe.optimized``, the
                    ``world_corrected -> body`` transform) from the prep pickle's
                    ``pose_graph_bytes``. This is the path that built the map, so
                    it lands ON the premap.
       path_odom -- raw LIO odom (``PoseStamped`` ``world_raw`` position) from the
                    recording db ``odom`` stream, in timestamp order. This is the
                    pre-correction path; its drift away from path_pgo is what PGO
                    removed.

Each path is coloured by a START->END gradient (order-index [0..1] through turbo)
so travel direction reads the same on both; each carries an explicit green start
sphere and red end sphere. Entities are separate branches (``world/path_pgo`` vs
``world/path_odom``) so each toggles independently in rerun.

Determinism: no RNG, no wall-clock -- fixed data, fixed order (keyframes/odom
sorted by timestamp). Everything logged ``static=True``.
"""

from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path

from matplotlib import colormaps
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rerun as rr

from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.perception.fiducial.fiducial_relocalization import load_marker_map

PREMAP_PC2 = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/robotday_build/"
    "sf_office_go2_20260718_survey1.pc2.lcm"
)
PREP_PKL = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/prepared/"
    "sf_office_go2_20260718_survey1.pkl"
)
ODOM_DB = Path(
    "/home/dimos/dimensional-trial/dimos/data/sf_office_go2_20260718_survey1.db"
)
# Marker survey the reloc benchmark actually loads as marker_map_file: eval.py
# converts robotday_build_gated's gated YAML to this sibling .json (map_T_tag,
# translation+quaternion) and passes it as relocalizationmodule.marker_map_file.
# Same PGO world frame as the robotday_build premap above, so tags land on it.
MARKER_MAP_JSON = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/robotday_build_gated/"
    "sf_office_go2_20260718_survey1.marker_map.json"
)
OUT_RRD = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/eval/survey1_premap_path.rrd"
)
OUT_PNG = Path(
    "/home/dimos/dimensional-trial/trial/harness/out/eval/"
    "survey1_premap_path_topview.png"
)

# Premap is ONE steel-blue hue; height sets brightness (low z dark, high z light)
# so vertical structure reads as light<->dark shading, not a multi-hue colormap.
PREMAP_HUE = 0.58               # HSV hue ~210 deg (clean steel/blue)
PREMAP_SAT = 0.55               # constant saturation; only VALUE varies with z
PREMAP_VAL_LO = 0.15            # darkest -> low z (floor)
PREMAP_VAL_HI = 1.00            # lightest -> high z (ceiling)
PREMAP_Z_PCTL = (2.0, 98.0)     # robust z clip so a few outliers don't wash it out
PREMAP_RADIUS_M = 0.012         # tiny -- premap is context, not the star
PGO_RADIUS_M = 0.05             # PGO path dots, larger than premap
ODOM_RADIUS_M = 0.035           # odom path dots, visibly thinner than PGO
ENDCAP_RADIUS_M = 0.20          # start/end spheres
GREEN = (40, 200, 70)           # start marker
RED = (220, 40, 40)             # end marker
MARKER_RADIUS_M = 0.15          # tag spheres, bigger than any path dot so they pop
MARKER_RGB = (255, 0, 200)      # magenta -- absent from turbo + the steel premap


def _gradient_colors(n: int) -> np.ndarray:
    """Nx3 uint8 START->END gradient (order index [0..1] through turbo)."""
    if n == 0:
        return np.zeros((0, 3), np.uint8)
    t = np.linspace(0.0, 1.0, n)
    return (colormaps["turbo"](t)[:, :3] * 255).astype(np.uint8)


def _height_shade(z: np.ndarray) -> np.ndarray:
    """Normalized height in [0..1] with z robustly clipped to the 2nd..98th
    percentile so a few outliers don't wash out the shading (0 = low z, 1 = high)."""
    zlo, zhi = np.percentile(z, PREMAP_Z_PCTL)
    if zhi <= zlo:
        return np.zeros_like(z, dtype=np.float64)
    return np.clip((z - zlo) / (zhi - zlo), 0.0, 1.0)


def _premap_colors(z: np.ndarray) -> np.ndarray:
    """Nx3 uint8, single steel-blue hue with VALUE from normalized height:
    low z = dark, high z = light. One hue -- read vertical structure as shading."""
    v = PREMAP_VAL_LO + _height_shade(z) * (PREMAP_VAL_HI - PREMAP_VAL_LO)
    hsv = np.column_stack(
        [np.full_like(v, PREMAP_HUE), np.full_like(v, PREMAP_SAT), v]
    )
    return (mcolors.hsv_to_rgb(hsv) * 255).astype(np.uint8)


def _premap_cmap() -> mcolors.LinearSegmentedColormap:
    """Single-hue dark->light colormap (same steel-blue) for the PNG scatter."""
    dark = mcolors.hsv_to_rgb((PREMAP_HUE, PREMAP_SAT, PREMAP_VAL_LO))
    light = mcolors.hsv_to_rgb((PREMAP_HUE, PREMAP_SAT, PREMAP_VAL_HI))
    return mcolors.LinearSegmentedColormap.from_list("steel_height", [dark, light])


def load_premap_points(pc2_path: Path = PREMAP_PC2) -> tuple[np.ndarray, int]:
    """Raw premap points in the ``world`` frame (Nx3 float32) -- NO downsample."""
    pc = PointCloud2.lcm_decode(pc2_path.read_bytes())
    pts = np.asarray(pc.points_f32(), dtype=np.float32)
    return pts, len(pc)


def load_pgo_path(prep_pkl: Path = PREP_PKL) -> np.ndarray | None:
    """Ordered PGO-corrected keyframe positions (Nx3, ``world_corrected`` frame).

    ``Keyframe.optimized`` is the ``world_corrected -> body`` transform; its
    translation is the body origin in the corrected map frame -- the path that
    actually built the premap. Sorted by keyframe timestamp for travel order.
    """
    if not prep_pkl.exists():
        return None
    d = pickle.loads(prep_pkl.read_bytes())
    graph = pickle.loads(d["pose_graph_bytes"])
    kfs = sorted(graph.keyframes, key=lambda k: k.ts)
    xyz = np.array(
        [k.optimized.to_matrix()[:3, 3] for k in kfs], dtype=np.float64
    ).reshape(-1, 3)
    ts = np.array([k.ts for k in kfs], dtype=np.float64)
    return np.column_stack([xyz, ts])  # (N,4): x,y,z,ts


def load_odom_path(odom_db: Path = ODOM_DB) -> np.ndarray | None:
    """Raw LIO odom positions (Nx3, ``world_raw`` frame) in timestamp order.

    Read-only connection; ``odom`` is a ``PoseStamped`` stream, the pose_x/y/z
    columns are the world-registered body position.
    """
    if not odom_db.exists():
        return None
    # Read-only URI so we never touch a db a recording might be writing.
    con = sqlite3.connect(f"file:{odom_db}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, pose_x, pose_y, pose_z FROM odom ORDER BY ts"
        ).fetchall()
    finally:
        con.close()
    a = np.asarray(rows, dtype=np.float64)
    return np.column_stack([a[:, 1], a[:, 2], a[:, 3], a[:, 0]])  # x,y,z,ts


def load_markers(
    marker_map_json: Path = MARKER_MAP_JSON,
) -> list[tuple[int, np.ndarray]]:
    """Surveyed tags as ``(tag_id, xyz_map)`` in the premap ``world`` frame, sorted
    by id. Positions are the ``map_T_tag`` translations via dimos' own validated
    ``load_marker_map`` (no bespoke quaternion/JSON parsing). Empty if the file is absent.
    """
    if not marker_map_json.exists():
        return []
    tags = load_marker_map(marker_map_json)  # marker_id -> map_T_tag Transform
    return [
        (tag_id, tags[tag_id].to_matrix()[:3, 3].astype(np.float64))
        for tag_id in sorted(tags)
    ]


def max_xy_divergence(pgo: np.ndarray, odom: np.ndarray) -> tuple[float, float]:
    """Max XY gap between the two paths, matching each PGO keyframe to the
    nearest-in-time odom sample. Returns (max_gap_m, ts_of_max).

    Restricted to keyframes inside the odom time window with a tight match
    (dt < DT_TOL_S): the odom stream starts ~1.8 s after the first keyframe, so
    an out-of-window keyframe would match across a real-motion gap and report a
    phantom drift, not a PGO correction. This gap equals the per-keyframe
    ``local`` (world_raw) vs ``optimized`` (world_corrected) displacement --
    odom and ``Keyframe.local`` are the same LIO frame -- so it is the honest
    "how much PGO moved it" number.
    """
    DT_TOL_S = 0.15
    o_ts = odom[:, 3]
    order = np.argsort(o_ts)
    o_ts_s, o_xy_s = o_ts[order], odom[order, :2]
    idx = np.clip(np.searchsorted(o_ts_s, pgo[:, 3]), 1, len(o_ts_s) - 1)
    lo = np.abs(pgo[:, 3] - o_ts_s[idx - 1]) < np.abs(pgo[:, 3] - o_ts_s[idx])
    nn = np.where(lo, idx - 1, idx)
    dt = np.abs(pgo[:, 3] - o_ts_s[nn])
    keep = (pgo[:, 3] >= o_ts_s[0]) & (pgo[:, 3] <= o_ts_s[-1]) & (dt < DT_TOL_S)
    gaps = np.where(keep, np.linalg.norm(pgo[:, :2] - o_xy_s[nn], axis=1), -1.0)
    k = int(np.argmax(gaps))
    return float(gaps[k]), float(pgo[k, 3])


def _log_path(base: str, xyz: np.ndarray, radius: float) -> None:
    """Log one trajectory: gradient Points3D + connected LineStrips3D + green
    start sphere + red end sphere, all under ``base`` so they toggle together
    and independently of the other path."""
    colors = _gradient_colors(len(xyz))
    rr.log(f"{base}/points", rr.Points3D(xyz, colors=colors, radii=radius),
           static=True)
    rr.log(f"{base}/line", rr.LineStrips3D([xyz], colors=[(200, 200, 200, 120)],
           radii=radius * 0.35), static=True)
    rr.log(f"{base}/start", rr.Points3D([xyz[0]], colors=[GREEN],
           radii=ENDCAP_RADIUS_M, labels=["start"]), static=True)
    rr.log(f"{base}/end", rr.Points3D([xyz[-1]], colors=[RED],
           radii=ENDCAP_RADIUS_M, labels=["end"]), static=True)


def _log_markers(markers: list[tuple[int, np.ndarray]]) -> None:
    """Log all surveyed tags as ONE ``world/markers`` entity: magenta spheres,
    bigger than the path dots, each labelled with its tag id. A separate branch so
    it toggles on/off independently of the premap and both trajectories."""
    if not markers:
        return
    xyz = np.array([p for _id, p in markers], dtype=np.float64)
    labels = [f"tag {tag_id}" for tag_id, _p in markers]
    rr.log("world/markers",
           rr.Points3D(xyz, colors=[MARKER_RGB] * len(markers),
                       radii=MARKER_RADIUS_M, labels=labels),
           static=True)


def save_png(premap: np.ndarray, pgo: np.ndarray | None,
             odom: np.ndarray | None, div: tuple[float, float] | None,
             markers: list[tuple[int, np.ndarray]]) -> None:
    """Top-down XY: light-gray premap scatter + both gradient trajectories +
    green starts / red ends + magenta tag squares, PGO vs odom labelled."""
    fig, ax = plt.subplots(figsize=(13, 11))
    # Premap raster stride keeps the PNG small; full cloud is in the rrd. Single
    # steel-blue hue, brightness from normalized height (low z dark, high z light),
    # same shading as the rrd points -- read vertical structure as light<->dark.
    shade = _height_shade(premap[:, 2])
    pm, pm_c = premap[::3], shade[::3]
    ax.scatter(pm[:, 0], pm[:, 1], s=0.5, c=pm_c, cmap=_premap_cmap(),
               vmin=0.0, vmax=1.0, alpha=0.5, linewidths=0,
               label=f"premap ({len(premap)} pts, height->shade)", zorder=1)
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
        ax.scatter(mxy[:, 0], mxy[:, 1], s=140, c="#ff00c8", marker="s",
                   edgecolors="k", linewidths=1.0, zorder=6,
                   label=f"survey tags (n={len(markers)})")
        for (tag_id, _p), (x, y) in zip(markers, mxy):
            ax.annotate(str(tag_id), (x, y), textcoords="offset points",
                        xytext=(6, 6), fontsize=9, fontweight="bold",
                        color="#ff00c8", zorder=7)
    ax.set_aspect("equal")
    ax.set_xlabel("world X (m)")
    ax.set_ylabel("world Y (m)")
    ttl = "survey1 premap (steel-blue, height->darkness) + trajectories -- turbo gradient = start->end\n"
    ttl += "PGO-corrected keyframes (o, world_corrected) vs raw LIO odom (., world_raw); magenta squares = survey tags (id labelled)"
    if div is not None:
        ttl += f"   |   max PGO-vs-odom XY divergence: {div[0]:.3f} m"
    ax.set_title(ttl, fontsize=11)
    ax.legend(loc="best", fontsize=9, markerscale=3)
    ax.text(0.01, 0.01, "green *=start   red X=end", transform=ax.transAxes,
            fontsize=9, va="bottom")
    fig.tight_layout()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


def main() -> None:
    premap, n_full = load_premap_points()
    pgo = load_pgo_path()
    odom = load_odom_path()
    markers = load_markers()

    print(f"[premap_with_path] premap: {n_full} raw pts (rendered as points, "
          f"no voxel) from {PREMAP_PC2.name}")
    if pgo is None:
        print("[premap_with_path] path_pgo: MISSING (prep pkl absent) -- skipped")
    else:
        print(f"[premap_with_path] path_pgo: {len(pgo)} PGO-corrected keyframes "
              f"(Keyframe.optimized, world_corrected) from {PREP_PKL.name}")
    if odom is None:
        print("[premap_with_path] path_odom: MISSING (db absent) -- skipped")
    else:
        print(f"[premap_with_path] path_odom: {len(odom)} raw LIO odom poses "
              f"(PoseStamped world_raw) from {ODOM_DB.name}")
    if not markers:
        print("[premap_with_path] markers: MISSING (marker map absent) -- skipped")
    else:
        ids = [tag_id for tag_id, _p in markers]
        print(f"[premap_with_path] markers: {len(markers)} survey tags {ids} "
              f"(map_T_tag) from {MARKER_MAP_JSON.name}")

    div = None
    if pgo is not None and odom is not None:
        div = max_xy_divergence(pgo, odom)
        print(f"[premap_with_path] max PGO-vs-odom XY divergence: {div[0]:.3f} m "
              f"(at ts {div[1]:.1f})")

    rr.init("survey1_premap_path", spawn=False)
    OUT_RRD.parent.mkdir(parents=True, exist_ok=True)
    rr.save(str(OUT_RRD))

    rr.log("world/premap", rr.Points3D(premap, colors=_premap_colors(premap[:, 2]),
           radii=PREMAP_RADIUS_M), static=True)
    if pgo is not None:
        _log_path("world/path_pgo", pgo[:, :3], PGO_RADIUS_M)
    if odom is not None:
        _log_path("world/path_odom", odom[:, :3], ODOM_RADIUS_M)
    _log_markers(markers)

    save_png(premap, pgo, odom, div, markers)
    print(f"[premap_with_path] wrote {OUT_RRD}")
    print(f"[premap_with_path] wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
