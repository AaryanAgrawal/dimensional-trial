#!/usr/bin/env python3
"""Spawn a rerun viewer of surveyed markers ON TOP of one-or-more GLOBAL MAPS.

Static, offline, files-in view -- NOT a runner. No ``dimos --replay``, no LCM
bus, no live capture. It only:
  1. loads each premap ``.pc2.lcm`` point cloud (dimos' own ``PointCloud2.lcm_decode``
     + ``voxel_downsample`` -- never a re-implementation), and
  2. loads a marker-map JSON (auto-detecting the two on-disk schemas, below),
then logs them into rerun. By default it SPAWNS the native viewer directly (no
file written); pass ``--out <rrd>`` to also save a recording.

Multiple premaps go in as SEPARATE toggleable entities -- ``--premap NAME=PATH``
repeatable -- each under its own path ``world/<name>`` (height-coloured, voxel-
downsampled). Toggle e.g. carved vs no-carve on/off in rerun to see the diff.
There is no native rerun loader for ``.pc2.lcm`` (a dimos LCM blob), which is
why we decode + spawn here instead of ``rerun file.pc2.lcm``.

A tag IS a flat square, so it is drawn as one: a translucent pink quad (fill), a
crisp full-opacity pink outline, a short +z facing arrow, and the id in the
middle -- all four under the SAME per-tag entity path ``markers/tag_<id>`` so
hovering shows the id and clicking selects that one tag.

Marker-map schema (two variants, auto-detected):
  A. ``map global --markers`` / eval.py survey schema (the default gated file) --
     dimos' own ``load_marker_map`` reads exactly this
     (dimos/perception/fiducial/fiducial_relocalization.py:62-76):
       {"meta": {...}, "markers": {"<id>": {"translation": [x,y,z],
                                            "rotation": [qx,qy,qz,qw]}}}  # map_T_tag
     translation is [x,y,z] m; rotation is a [qx,qy,qz,qw] quaternion.
  B. harness ``markers.py`` schema (out/markers/*.marker_map.json) -- a row-major
     4x4 ``map_T_tag`` per id (trial/harness/markers.py:298):
       {"meta": {...}, "markers": {"<id>": [[...4x4...]]}}

Scene graph (root frame = ``world``):
  - ``world/<name>``        : one premap's Points3D, voxel-downsampled, coloured
                              by height. One per ``--premap`` -- toggleable.
  - ``markers/tag_<id>``    : fill (Mesh3D quad) + outline (LineStrips3D) + facing
                              arrow (Arrows3D) + centre id (Points3D), one tag only.
  - ``note``                : a TextDocument stamping paths, schema, tag ids, legend.

Determinism: no RNG, no wall-clock -- only voxel/stride subsampling of fixed data.
Everything logged ``static=True`` (a single map snapshot, no timeline).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from matplotlib import colormaps
import numpy as np
import rerun as rr

from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.perception.fiducial.fiducial_relocalization import load_marker_map

DEF_PREMAP = (
    "/home/dimos/dimensional-trial/trial/harness/out/robotday_build/"
    "sf_office_go2_20260718_survey1.pc2.lcm"
)
DEF_MARKER_MAP = (
    "/home/dimos/dimensional-trial/trial/harness/out/robotday_build_gated/"
    "sf_office_go2_20260718_survey1.marker_map.json"
)

PREMAP_VOXEL_M = 0.10          # premap downsample for viewer size (metres)
DEFAULT_MARKER_LEN_M = 0.10    # square side if the marker map carries no length (metres)
FACING_ARROW_LEN_M = 0.15      # drawn length of the +z facing arrow (metres)

PINK_FILL = (255, 45, 149, 55)    # RGBA translucent tint for the quad interior
PINK_EDGE = (255, 45, 149, 255)   # RGBA full-opacity for outline + facing arrow
LABEL_WHITE = (255, 255, 255, 255)  # centre id glyph colour


def _turbo_lut() -> np.ndarray:
    """256x3 uint8 turbo colormap LUT (height -> colour)."""
    return (colormaps["turbo"](np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)


def _height_colors(pts_xyz: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Per-point RGB (uint8 Nx3) by z, normalised over this cloud's own extent."""
    z = pts_xyz[:, 2]
    idx = ((z - z.min()) / (np.ptp(z) + 1e-9) * 255).astype(np.uint8)
    return lut[idx]


def _load_premap_points_map(premap_path: Path) -> tuple[np.ndarray, int]:
    """Premap points in the ``world`` frame, voxel-downsampled. Returns
    (points_world Nx3 float32, n_points_full). dimos' own decode + downsample."""
    premap = PointCloud2.lcm_decode(premap_path.read_bytes())
    n_full = len(premap)
    pts_world = premap.voxel_downsample(PREMAP_VOXEL_M).points_f32()
    return np.asarray(pts_world), n_full


def _load_markers_map_T_tag(
    marker_map_path: Path,
) -> tuple[dict[int, np.ndarray], str, float]:
    """``tag_id -> map_T_tag`` (4x4), the detected schema name, and the square side.

    Auto-detects the two on-disk variants (see module docstring). Schema A goes
    through dimos' own ``load_marker_map`` (validated translation+quaternion, no
    bespoke quaternion math); schema B is a plain row-major 4x4 per id.
    """
    data = json.loads(marker_map_path.read_text()) or {}
    markers = data.get("markers", {}) or {}
    meta = data.get("meta", {}) or {}
    # Length lives in meta for a survey that recorded it; default otherwise.
    side_m = float(
        meta.get("marker_length_m")
        or meta.get("marker_size")
        or meta.get("marker_length")
        or DEFAULT_MARKER_LEN_M
    )

    first = next(iter(markers.values()), None)
    if isinstance(first, dict):  # schema A: {"translation": [...], "rotation": [...]}
        mats = {
            tag_id: transform.to_matrix()
            for tag_id, transform in load_marker_map(marker_map_path).items()
        }
        return mats, "A: map global --markers (translation+quaternion)", side_m

    # schema B: row-major 4x4 map_T_tag per id (trial/harness/markers.py:298).
    mats = {int(k): np.asarray(v, dtype=np.float64).reshape(4, 4) for k, v in markers.items()}
    return mats, "B: harness 4x4 matrix", side_m


def _square_corners_map(map_T_tag: np.ndarray, side_m: float) -> np.ndarray:
    """4 corners (map frame, Nx3) of the flat tag square in its own XY plane at z=0,
    CCW viewed from +z: corners_map = R @ corners_local + t."""
    h = side_m / 2.0
    corners_local = np.array(
        [[-h, -h, 0.0], [h, -h, 0.0], [h, h, 0.0], [-h, h, 0.0]], dtype=np.float64
    )
    R, t = map_T_tag[:3, :3], map_T_tag[:3, 3]
    return (corners_local @ R.T) + t  # (R @ c) per row


def _log_tag(tag_id: int, map_T_tag: np.ndarray, side_m: float) -> None:
    """Fill + outline + facing arrow + centre id for ONE tag, all under
    ``markers/tag_<id>`` so hover shows the id and click selects just this tag."""
    path = f"markers/tag_{tag_id}"
    label = f"tag {tag_id}"
    corners = _square_corners_map(map_T_tag, side_m)
    origin = map_T_tag[:3, 3]
    z_axis = map_T_tag[:3, 2]  # tag +z normal in map frame

    # Fill: translucent pink quad (two triangles), id as class label on the mesh.
    rr.log(
        path,
        rr.Mesh3D(
            vertex_positions=corners,
            triangle_indices=[[0, 1, 2], [0, 2, 3]],
            vertex_colors=[PINK_FILL] * 4,
        ),
        static=True,
    )
    # Outline: crisp full-opacity pink closed loop; label rides the geometry.
    rr.log(
        path,
        rr.LineStrips3D(
            [np.vstack([corners, corners[0]])],
            colors=[PINK_EDGE],
            labels=[label],
            radii=[0.004],
        ),
        static=True,
    )
    # Facing arrow: +z normal, ~0.15 m, so orientation/flip is visible.
    rr.log(
        path,
        rr.Arrows3D(
            origins=[origin],
            vectors=[z_axis * FACING_ARROW_LEN_M],
            colors=[PINK_EDGE],
            labels=[label],
        ),
        static=True,
    )
    # Centre id: one point at the tag centre carrying the id, so the number sits
    # in the middle of the square. Tiny radius so the glyph reads, not the dot.
    rr.log(
        path,
        rr.Points3D(
            [origin],
            colors=[LABEL_WHITE],
            labels=[str(tag_id)],
            radii=[0.01],
        ),
        static=True,
    )


def _log_premap(name: str, pts_world: np.ndarray, lut: np.ndarray) -> None:
    """Log one premap's cloud under ``world/<name>`` -- its own toggleable entity."""
    colors = _height_colors(pts_world, lut) if len(pts_world) else None
    rr.log(
        f"world/{name}",
        rr.Points3D(pts_world, colors=colors, radii=PREMAP_VOXEL_M / 2),
        static=True,
    )


def _parse_premap_arg(spec: str) -> tuple[str, Path]:
    """Parse one ``NAME=PATH`` premap spec into (name, resolved abs path)."""
    name, sep, path = spec.partition("=")
    if not sep or not name or not path:
        raise argparse.ArgumentTypeError(
            f"--premap expects NAME=PATH (e.g. carved=/abs/x.pc2.lcm), got {spec!r}"
        )
    return name, Path(path).expanduser().resolve()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Spawn a rerun view of surveyed markers on one-or-more global maps"
    )
    ap.add_argument(
        "--premap",
        action="append",
        type=_parse_premap_arg,
        metavar="NAME=PATH",
        help="repeatable: a named .pc2.lcm premap logged under world/NAME "
        "(e.g. --premap nocarve=... --premap carved=...). Default: one 'premap'.",
    )
    ap.add_argument("--marker-map", type=Path, default=Path(DEF_MARKER_MAP),
                    help="ABS path to the marker-map JSON (map_T_tag per tag)")
    ap.add_argument("--spawn", action="store_true",
                    help="spawn the native viewer directly (default if no --out)")
    ap.add_argument("--out", type=Path, default=None,
                    help="optional: also save a .rrd recording to this path")
    a = ap.parse_args()

    premaps = a.premap or [("premap", Path(DEF_PREMAP).resolve())]
    marker_path = a.marker_map.resolve()
    for name, path in premaps:
        if not path.exists():
            raise FileNotFoundError(f"premap {name!r} not found: {path}")
    if not marker_path.exists():
        raise FileNotFoundError(f"marker map not found: {marker_path}")

    # Default action is spawn; --out additionally (or instead) saves a recording.
    out_path = a.out.resolve() if a.out else None
    do_spawn = a.spawn or out_path is None

    loaded = [
        (name, *_load_premap_points_map(path), path) for name, path in premaps
    ]
    markers_map_T_tag, schema, side_m = _load_markers_map_T_tag(marker_path)

    premap_lines = [
        f"  world/{name}: {n_full} pts full -> {len(pts)} shown  ({path})"
        for name, pts, n_full, path in loaded
    ]
    tag_lines = [
        f"  tag {tag_id}: map_pos=[{T[0, 3]:.3f}, {T[1, 3]:.3f}, {T[2, 3]:.3f}] m"
        for tag_id, T in sorted(markers_map_T_tag.items())
    ]
    note = (
        "Surveyed markers on the global map(s) (static, offline -- no replay/LCM).\n"
        f"premaps (voxel {PREMAP_VOXEL_M} m, coloured by height):\n"
        + "\n".join(premap_lines) + "\n"
        f"marker-map: {marker_path}\n"
        f"  schema detected: {schema}\n"
        f"  square side: {side_m:.3f} m\n"
        f"tag ids: {sorted(markers_map_T_tag)}\n" + "\n".join(tag_lines) + "\n"
        "legend: pink outline square = tag (id shown inside), "
        "pink arrow = tag +z facing. Toggle world/<name> to compare premaps."
    )

    rr.init("markers_on_map", spawn=False)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rr.save(str(out_path))
    if do_spawn:
        rr.spawn()

    lut = _turbo_lut()
    rr.log("note", rr.TextDocument(note), static=True)
    for name, pts, _n_full, _path in loaded:
        _log_premap(name, pts, lut)
    for tag_id, map_T_tag in sorted(markers_map_T_tag.items()):
        _log_tag(tag_id, map_T_tag, side_m)

    for line in premap_lines:
        print(f"[show_markers_on_map]{line}")
    print(f"[show_markers_on_map] {len(markers_map_T_tag)} markers loaded; "
          f"schema {schema}; side {side_m:.3f} m")
    for line in tag_lines:
        print(f"[show_markers_on_map]{line}")
    if out_path is not None:
        print(f"[show_markers_on_map] wrote {out_path}")
    if do_spawn:
        print("[show_markers_on_map] spawned native viewer")


if __name__ == "__main__":
    main()
