#!/usr/bin/env python3
"""Record the REAL dimos relocalization pipeline running on a recording, as a
rerun ``.rrd`` (+ two still PNGs), for PR evidence -- with the surveyed markers
drawn on the world map.

This is a PASSIVE recorder, not a runner. You launch the shipped blueprint in one
terminal (the LCM bus is EXCLUSIVE -- only one ``dimos --replay`` may run at a
time; a second Coordinator crashes it), and this script in another. It never
launches a replay and never re-implements a dimos data-path step -- it only
listens on the shared LCM bus and reads the log the pipeline already writes:

  Terminal A (the pipeline -- the exact run this evidence is OF):
    uv run --project /home/dimos/dimensional-trial/dimos \
        dimos --replay --replay-db=<rec> run unitree-go2-relocalization-fiducial \
        -o relocalizationmodule.map_file=<premap.pc2.lcm abs> \
        -o relocalizationmodule.marker_map_file=<markers.json abs> \
        -o relocalizationmodule.use_fiducial_prior=true

  Terminal B (this recorder, capturing while A runs):
    uv run --project /home/dimos/dimensional-trial/dimos \
        python ../trial/harness/record_reloc.py <rec> \
        --premap <premap.pc2.lcm abs> --marker-map <markers.json abs> \
        --out out/reloc_<rec>.rrd --max-wall-s 240

What it captures into the ``.rrd`` (scene graph rooted at the ``world`` frame; the
premap and markers hang under a ``world/map`` transform so the reloc fix is shown
PLACING the map into the world):
  1. PREMAP point cloud -- static grey, frame ``map``, loaded from the ``.pc2.lcm``
     and voxel-downsampled for size.
  2. LIVE SUBMAP over time -- the module's ``global_map`` In rides LCM channel
     ``/global_map`` (verified: the VoxelGridMapper Out publishes there, reloc's In
     subscribes there). Logged as Points3D on a timeline, coloured by height.
  3. PUBLISHED FIX -- ``/tf`` TFMessage filtered to frame_id==world &&
     child_frame_id==map; logged as a Transform3D (world_T_map) at each update.
  4. MARKERS (the ask) -- each surveyed ``map_T_tag`` as a labelled axis triad
     (three Arrows3D) plus a small Boxes3D carrying the tag id, static in ``map``.
  5. HEALTH -- the module's own ``relocalize accepted fitness=... source=...``
     lines, parsed from the run log (reloc_log.py: console or main.jsonl, no field
     order assumed) and logged as TextLog on the timeline, each aligned to the fix
     it published.

Then two PNGs next to the ``.rrd`` (matplotlib Agg, offscreen):
  (a) ``*.overlay.png`` -- top-down premap (grey) placed into world by the first
      accepted fix, overlaid with the live submap at that moment (coloured).
  (b) ``*.markers.png`` -- the ``map_T_tag`` positions with tag-id labels over a
      faint premap footprint.

Determinism: no RNG and no wall-clock inputs to any geometry -- only voxel/stride
subsampling. The rerun timeline is the capture's own arrival times (the data),
rebased to 0 at the first sample so reruns line up; git revs + the exact pipeline
command are stamped into the recording and printed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # offscreen; no display, deterministic raster output
import matplotlib.pyplot as plt
import numpy as np
import rerun as rr

from dimos.core.transport import LCMTransport
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.tf2_msgs.TFMessage import TFMessage
from dimos.perception.fiducial.fiducial_relocalization import load_marker_map

sys.path.insert(0, str(Path(__file__).resolve().parent))  # harness-local modules
from reloc_log import Accept, parse_accepts  # noqa: E402

DIMOS_ROOT = Path(__file__).resolve().parents[2] / "dimos"
TRIAL_ROOT = Path(__file__).resolve().parents[1]

BLUEPRINT = "unitree-go2-relocalization-fiducial"
CH_GLOBAL_MAP = "/global_map"  # VoxelGridMapper Out -> reloc `global_map` In (live submap)
CH_TF = "/tf"                  # TF tree; the module republishes world_T_map here
FRAME_WORLD = "world"
FRAME_MAP = "map"

PREMAP_VOXEL_M = 0.15          # premap downsample for viewer size (metres)
SUBMAP_EVERY_S = 2.0           # keep at most one live-submap frame per this (matches RELOC_INTERVAL)
MAX_SUBMAP_PTS = 60_000        # stride-cap a submap frame kept for the recording
MARKER_AXIS_LEN_M = 0.5        # drawn length of each marker's axis triad (metres)
MARKER_BOX_HALF_M = 0.08       # half-size of the tag id box (metres, ~ a 0.10 m tag)
GREY = [150, 150, 150]
TF_MATCH_TOL_M = 0.02          # published_t_m (log, 3 dp) -> captured TF translation match radius


def _git_rev(path: Path) -> str:
    """Short HEAD of the repo at ``path``; '(unknown)' if git is unavailable."""
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "(unknown)"


def _turbo_lut() -> np.ndarray:
    """256x3 uint8 turbo colormap LUT (one build; height -> colour)."""
    return (plt.get_cmap("turbo")(np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)


def _height_colors(pts_xyz: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Per-point RGB (uint8 Nx3) by z, normalised over this cloud's own extent."""
    z = pts_xyz[:, 2]
    idx = ((z - z.min()) / (np.ptp(z) + 1e-9) * 255).astype(np.uint8)
    return lut[idx]


def _load_premap_points_map(premap_path: Path) -> tuple[np.ndarray, int]:
    """Premap points in the ``map`` frame, voxel-downsampled. Returns
    (points_map Nx3 float32, n_points_full). Reuses dimos' own decode +
    voxel_downsample -- never a re-implementation of either."""
    premap = PointCloud2.lcm_decode(premap_path.read_bytes())
    n_full = len(premap)
    pts_map = premap.voxel_downsample(PREMAP_VOXEL_M).points_f32()
    return np.asarray(pts_map), n_full


def _load_markers_map_T_tag(marker_map_path: Path) -> dict[int, np.ndarray]:
    """``tag_id -> map_T_tag`` (4x4) from the survey JSON/YAML, via dimos'
    ``load_marker_map`` (yaml.safe_load parses JSON; the ``meta`` block is ignored,
    each entry validated). No bespoke quaternion math here."""
    return {
        tag_id: transform.to_matrix()
        for tag_id, transform in load_marker_map(marker_map_path).items()
    }


class _Capture:
    """In-process LCM listeners for the live submap and the published fix.
    Callbacks fire on the LCM handle thread; ``list.append`` is atomic under the
    GIL so no lock is needed. Every sample carries the wall time it was seen --
    the single clock the rerun timeline is built on (mirrors replay_bench.py)."""

    def __init__(self) -> None:
        # (wall_recv_s, points_map_or_world Nx3 float32) -- submap is in `world`.
        self.submap: list[tuple[float, np.ndarray]] = []
        # (wall_recv_s, world_T_map 4x4, translation (x,y,z))
        self.fixes: list[tuple[float, np.ndarray, tuple[float, float, float]]] = []
        self._submap_t = LCMTransport(CH_GLOBAL_MAP, PointCloud2)
        self._tf_t = LCMTransport(CH_TF, TFMessage)
        self._unsub: list = []
        self._last_kept_submap_s = 0.0

    def start(self) -> None:
        self._unsub.append(self._submap_t.subscribe(self._on_submap))
        self._unsub.append(self._tf_t.subscribe(self._on_tf))

    def _on_submap(self, msg: PointCloud2) -> None:
        w = time.time()
        if w - self._last_kept_submap_s < SUBMAP_EVERY_S:
            return  # throttle: bound the frame count over a multi-minute capture
        self._last_kept_submap_s = w
        pts = msg.points_f32()  # float32 Nx3, no legacy conversion on the hot path
        if len(pts) > MAX_SUBMAP_PTS:  # deterministic stride cap
            pts = pts[:: (len(pts) // MAX_SUBMAP_PTS) + 1]
        self.submap.append((w, np.asarray(pts)))

    def _on_tf(self, msg: TFMessage) -> None:
        w = time.time()
        for tf in msg.transforms:
            if tf.frame_id == FRAME_WORLD and tf.child_frame_id == FRAME_MAP:
                world_T_map = tf.to_matrix()
                self.fixes.append(
                    (w, world_T_map, tuple(float(x) for x in world_T_map[:3, 3]))
                )

    def stop(self) -> None:
        for u in self._unsub:
            u()
        self._submap_t.stop()
        self._tf_t.stop()


def _discover_log(explicit: Path | None) -> Path | None:
    """The run log to mine for health lines: the explicit ``--log`` if given, else
    the newest ``dimos/logs/*relocalization*/main.jsonl`` (the concurrent run's own
    dir, created when the pipeline started). None if nothing is found."""
    if explicit is not None:
        return explicit if explicit.exists() else None
    candidates = sorted(
        (DIMOS_ROOT / "logs").glob("*relocalization*/main.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _parse_health(log_path: Path | None) -> list[Accept]:
    """Accept records from the run log (fitness, source, n_pts, time_cost_s,
    published_t_m) via reloc_log -- console and main.jsonl alike, no field order
    assumed. Empty list if the log is missing."""
    if log_path is None:
        return []
    return parse_accepts(log_path.read_text(errors="replace"))


def _match_fix_wall(
    published_t: list[float] | None,
    fixes: list[tuple[float, np.ndarray, tuple[float, float, float]]],
) -> float | None:
    """Wall time of the captured fix whose translation matches the log's
    ``published_t`` (3 dp), within ``TF_MATCH_TOL_M``. None if no fix matches --
    ties each health line to the exact TF republish it produced."""
    if published_t is None or not fixes:
        return None
    pt = np.asarray(published_t)
    w, _T, t = min(fixes, key=lambda r: float(np.linalg.norm(np.asarray(r[2]) - pt)))
    return w if float(np.linalg.norm(np.asarray(t) - pt)) < TF_MATCH_TOL_M else None


def _build_rerun(
    out_path: Path,
    pts_premap_map: np.ndarray,
    markers_map_T_tag: dict[int, np.ndarray],
    cap: _Capture,
    health: list[Accept],
    meta: dict,
) -> None:
    """Write the ``.rrd``. Scene graph: root = world frame; premap + markers hang
    under a time-varying ``world/map`` transform (the published fix) so a flip in
    the fix visibly teleports the whole map -- the evidence we want to show."""
    lut = _turbo_lut()
    all_walls = [w for w, _ in cap.submap] + [w for w, _, _ in cap.fixes]
    t0 = min(all_walls) if all_walls else time.time()

    rr.init("reloc_evidence", spawn=False)
    rr.save(str(out_path))

    rr.log("note", rr.TextDocument(meta["summary"]), static=True)

    # (1) PREMAP -- static grey, in `map` frame (a child of world/map).
    rr.log(
        "world/map/premap",
        rr.Points3D(pts_premap_map, colors=GREY, radii=PREMAP_VOXEL_M / 2),
        static=True,
    )

    # (4) MARKERS -- labelled axis triad + id box per tag, static in `map`.
    for tag_id, map_T_tag in sorted(markers_map_T_tag.items()):
        origin = map_T_tag[:3, 3]
        axes = map_T_tag[:3, :3] * MARKER_AXIS_LEN_M  # columns = x,y,z axes in map
        rr.log(
            f"world/map/markers/tag_{tag_id}/triad",
            rr.Arrows3D(
                origins=[origin, origin, origin],
                vectors=[axes[:, 0], axes[:, 1], axes[:, 2]],
                colors=[[230, 60, 60], [60, 200, 90], [70, 130, 235]],  # x r, y g, z b
                labels=[f"tag {tag_id}", "", ""],
            ),
            static=True,
        )
        rr.log(
            f"world/map/markers/tag_{tag_id}/box",
            rr.Boxes3D(
                centers=[origin],
                half_sizes=[[MARKER_BOX_HALF_M] * 3],
                colors=[[245, 210, 60]],
                labels=[f"tag {tag_id}"],
            ),
            static=True,
        )

    # (2) LIVE SUBMAP over time -- Points3D per kept frame, coloured by height.
    for wall, pts in cap.submap:
        rr.set_time("capture", duration=float(wall - t0))
        colors = _height_colors(pts, lut) if len(pts) else None
        rr.log("world/live_submap", rr.Points3D(pts, colors=colors, radii=0.03))

    # (3) PUBLISHED FIX -- world_T_map (ParentFromChild: parent=world, child=map).
    for wall, world_T_map, _t in cap.fixes:
        rr.set_time("capture", duration=float(wall - t0))
        rr.log(
            "world/map",
            rr.Transform3D(translation=world_T_map[:3, 3], mat3x3=world_T_map[:3, :3]),
        )

    # (5) HEALTH -- TextLog aligned to the fix each line published.
    for h in health:
        wall = _match_fix_wall(h.published_t_m, cap.fixes)
        rr.set_time("capture", duration=float((wall if wall is not None else max(all_walls, default=t0)) - t0))
        tc = f"{h.time_cost_s:.1f}s" if h.time_cost_s is not None else "?"
        # source absent = the single-source path ran, i.e. ransac with no judge.
        rr.log(
            "health",
            rr.TextLog(
                f"fix fitness={h.fitness:.3f} source={h.source or 'ransac'} "
                f"n_pts={h.n_pts} time_cost={tc}",
                level="INFO",
            ),
        )


def _first_accepted_fix(
    cap: _Capture,
) -> tuple[float, np.ndarray] | None:
    """(wall, world_T_map) of the earliest captured fix -- the module only
    republishes world_T_map after an accept, so the first republish IS the first
    accepted fix. None if no fix was captured."""
    if not cap.fixes:
        return None
    wall, world_T_map, _t = min(cap.fixes, key=lambda r: r[0])
    return wall, world_T_map


def _nearest_submap(cap: _Capture, wall: float) -> np.ndarray | None:
    """The live-submap frame captured nearest ``wall`` (world frame). None if
    none were captured."""
    if not cap.submap:
        return None
    return min(cap.submap, key=lambda r: abs(r[0] - wall))[1]


def _render_pngs(
    out_path: Path,
    pts_premap_map: np.ndarray,
    markers_map_T_tag: dict[int, np.ndarray],
    cap: _Capture,
    meta: dict,
) -> list[Path]:
    """Two offscreen top-down PNGs beside the ``.rrd``. Returns their paths."""
    stem = out_path.with_suffix("")  # drop .rrd
    lut = _turbo_lut()
    written: list[Path] = []
    rev = f"dimos@{meta['git_rev_dimos']} trial@{meta['git_rev_trial']}"

    # (a) premap (grey) placed into world by the first accepted fix + submap there.
    fig, ax = plt.subplots(figsize=(8, 8))
    fix = _first_accepted_fix(cap)
    if fix is not None:
        _wall, world_T_map = fix
        ph = np.hstack([pts_premap_map, np.ones((len(pts_premap_map), 1))])
        premap_world = (world_T_map @ ph.T).T[:, :3]
        note = "premap placed into world by the first accepted world_T_map fix"
    else:
        premap_world = pts_premap_map
        note = "NO accepted fix captured -- premap shown at identity (map==world)"
    ax.scatter(premap_world[:, 0], premap_world[:, 1], s=0.4, c="0.6", label="premap (map->world)")
    submap = _nearest_submap(cap, fix[0]) if fix is not None else _nearest_submap(cap, time.time())
    if submap is not None and len(submap):
        cols = _height_colors(submap, lut) / 255.0
        ax.scatter(submap[:, 0], submap[:, 1], s=0.6, c=cols, label="live submap (by height)")
    ax.set_aspect("equal")
    ax.set_xlabel("world x (m)")
    ax.set_ylabel("world y (m)")
    ax.legend(loc="upper right", markerscale=8, fontsize=8)
    ax.set_title(f"{meta['recording']}: premap + live submap at first fix (top-down)\n{note}\n{rev}", fontsize=9)
    pa = Path(f"{stem}.overlay.png")
    fig.savefig(pa, dpi=130, bbox_inches="tight")
    plt.close(fig)
    written.append(pa)

    # (b) marker map_T_tag positions with tag-id labels over a faint premap footprint.
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(pts_premap_map[:, 0], pts_premap_map[:, 1], s=0.3, c="0.82", label="premap footprint")
    if markers_map_T_tag:
        mx = [T[0, 3] for T in markers_map_T_tag.values()]
        my = [T[1, 3] for T in markers_map_T_tag.values()]
        ax.scatter(mx, my, s=90, marker="*", c="#d43", edgecolors="k", linewidths=0.5, zorder=5, label="markers")
        for tag_id, T in sorted(markers_map_T_tag.items()):
            ax.annotate(f"tag {tag_id}", (T[0, 3], T[1, 3]), textcoords="offset points",
                        xytext=(6, 4), fontsize=9, fontweight="bold")
        mnote = f"{len(markers_map_T_tag)} surveyed markers (map_T_tag)"
    else:
        mnote = "NO markers in the survey file"
    ax.set_aspect("equal")
    ax.set_xlabel("map x (m)")
    ax.set_ylabel("map y (m)")
    ax.legend(loc="upper right", markerscale=1.5, fontsize=8)
    ax.set_title(f"{meta['recording']}: {mnote} over premap footprint\n{rev}", fontsize=9)
    pb = Path(f"{stem}.markers.png")
    fig.savefig(pb, dpi=130, bbox_inches="tight")
    plt.close(fig)
    written.append(pb)
    return written


def _run_command(recording: str, premap: Path, marker_map: Path, overrides: list[str]) -> str:
    """The exact pipeline command this recording is evidence OF (for the meta and
    stdout) -- the interface-contract run line, plus any extra ``-o`` passed."""
    has_fid = any(o.startswith("relocalizationmodule.use_fiducial_prior") for o in overrides)
    parts = [
        "uv run --project", str(DIMOS_ROOT), "dimos --replay",
        f"--replay-db={recording}", "run", BLUEPRINT,
        "-o", f"relocalizationmodule.map_file={premap}",
        "-o", f"relocalizationmodule.marker_map_file={marker_map}",
    ]
    if not has_fid:
        parts += ["-o", "relocalizationmodule.use_fiducial_prior=true"]
    for o in overrides:
        parts += ["-o", o]
    return " ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Record dimos reloc pipeline -> rerun .rrd + PNGs")
    ap.add_argument("recording", nargs="?", help="recording name (for the run command + labels)")
    ap.add_argument("--premap", type=Path, help="ABS path to the .pc2.lcm premap")
    ap.add_argument("--marker-map", type=Path, help="ABS path to the marker-map JSON (map_T_tag per tag)")
    ap.add_argument("--out", type=Path, help="output .rrd path (PNGs written beside it)")
    ap.add_argument("--max-wall-s", type=float, default=240.0, help="capture window seconds")
    ap.add_argument("--log", type=Path, default=None,
                    help="run log to mine for health lines (default: newest dimos/logs/*relocalization*/main.jsonl)")
    ap.add_argument("-o", dest="overrides", action="append", default=[], metavar="k=v",
                    help="extra dimos -o override, recorded in the printed run command (repeatable)")
    ap.add_argument("--dry-import", action="store_true",
                    help="import everything and exit 0 (smoke test; no LCM, no files)")
    a = ap.parse_args()

    if a.dry_import:
        print("record_reloc: dry-import OK "
              f"(rerun {rr.__version__}, matplotlib {matplotlib.__version__}, "
              "LCMTransport/PointCloud2/TFMessage/load_marker_map imported)")
        return

    for name, val in (("recording", a.recording), ("--premap", a.premap),
                      ("--marker-map", a.marker_map), ("--out", a.out)):
        if val is None:
            ap.error(f"{name} is required unless --dry-import")
    premap_path = a.premap.resolve()
    marker_path = a.marker_map.resolve()
    out_path = a.out.resolve()
    if not premap_path.exists():
        raise FileNotFoundError(f"premap not found: {premap_path}")
    if not marker_path.exists():
        raise FileNotFoundError(f"marker map not found: {marker_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pts_premap_map, n_premap_full = _load_premap_points_map(premap_path)
    markers_map_T_tag = _load_markers_map_T_tag(marker_path)
    run_cmd = _run_command(a.recording, premap_path, marker_path, a.overrides)
    print(f"[record_reloc] premap {n_premap_full} pts -> {len(pts_premap_map)} shown; "
          f"{len(markers_map_T_tag)} markers loaded")
    print(f"[record_reloc] run the pipeline in another terminal:\n  {run_cmd}")

    cap = _Capture()
    cap.start()
    print(f"[record_reloc] capturing {CH_GLOBAL_MAP} + {CH_TF} for {a.max_wall_s:.0f}s...")
    deadline = time.time() + a.max_wall_s
    try:
        while time.time() < deadline:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("[record_reloc] interrupted; finishing with what was captured")
    finally:
        time.sleep(0.5)  # let a last republished TF land
        cap.stop()

    log_path = _discover_log(a.log)
    health = _parse_health(log_path)
    print(f"[record_reloc] captured submap_frames={len(cap.submap)} fixes={len(cap.fixes)} "
          f"health_lines={len(health)} (log={log_path})")

    meta = {
        "recording": a.recording,
        "premap": str(premap_path),
        "marker_map": str(marker_path),
        "blueprint": BLUEPRINT,
        "run_command": run_cmd,
        "n_premap_full": n_premap_full,
        "n_markers": len(markers_map_T_tag),
        "git_rev_dimos": _git_rev(DIMOS_ROOT),
        "git_rev_trial": _git_rev(TRIAL_ROOT),
        "log": str(log_path) if log_path else None,
        "label": "replay",  # real recorded sensor data, not SIMULATED
    }
    meta["summary"] = (
        f"dimos relocalization on {a.recording} (label=replay, NOT simulated).\n"
        f"premap grey ({n_premap_full} pts) + {len(markers_map_T_tag)} markers under a world/map "
        f"transform = the published reloc fix; live submap coloured by height.\n"
        f"submap_frames={len(cap.submap)} fixes={len(cap.fixes)} health={len(health)}.\n"
        f"dimos@{meta['git_rev_dimos']} trial@{meta['git_rev_trial']}\n{run_cmd}"
    )

    _build_rerun(out_path, pts_premap_map, markers_map_T_tag, cap, health, meta)
    pngs = _render_pngs(out_path, pts_premap_map, markers_map_T_tag, cap, meta)
    print(f"[record_reloc] wrote {out_path}")
    for p in pngs:
        print(f"[record_reloc] wrote {p}")


if __name__ == "__main__":
    main()
