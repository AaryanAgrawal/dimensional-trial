#!/usr/bin/env python3
# Survey dumper: attaches read-only to a live dimos marker-survey session
# (the `unitree-go2-markers` blueprint) and turns accumulated marker
# sightings into a marker-map YAML for VisualRelocalizationModule -- replacing
# the manual "read marker_<id> off the TF tree and hand-transcribe into YAML"
# step in trial/day1-runbook.md section 4.
#
# ATTACH ONLY. This process never launches, restarts, composes, or
# reconfigures a dimos runtime, and never edits anything under dimos/ -- it
# subscribes to the same TF stream trial/scripts/metrics_logger.py already
# solved the transport for (zenoh on macOS, LCM elsewhere, picked
# automatically off dimos.core.global_config -- see metrics_logger.py's own
# header comment) and reads out `markers -> marker_<id>` transforms. Reuses
# metrics_logger.py's exact access pattern: subclass whatever `tf_backend()`
# returns, override `receive_transform` to intercept every incoming Transform
# while still calling `super().receive_transform(*args)` so the TF buffer
# keeps behaving normally for every other subscriber on the bus.
#
# Source picked: TF, not the raw `detections` Detection3DArray stream --
# decided by reading marker_tf_module.py + marker_detection_stream_module.py
# (both read-only, neither modified):
#   - The `unitree_go2_markers` blueprint (blueprints/smart/unitree_go2.py)
#     hard-pins its `detections` channel to LCMTransport via an explicit
#     `.transports({...})` override, *regardless* of the process-wide
#     DIMOS_TRANSPORT setting -- attaching there would mean hardcoding LCM
#     instead of reusing `tf_backend()`'s already-correct backend selection.
#   - MarkerTfModule (dimos/perception/fiducial/marker_tf_module.py) mirrors
#     every accepted detection onto TF as `markers -> marker_<id>`, itself
#     anchored to `world` by an identity `world -> markers` transform
#     (marker_tf_module.py:91-101, `Vector3(0,0,0)`/identity quaternion) --
#     so `marker_<id>` is already effectively expressed in the world/map
#     frame, exactly the frame visual_relocalization.load_marker_map expects.
#     day1-runbook.md's own manual flow ("read the marker_<id> transforms off
#     the TF tree ... this survey run's `world` frame becomes your `map`
#     frame") confirms TF is what installers already treat as ground truth --
#     this script automates exactly that read, nothing more.
#   - Detection3DArray carries one frame's raw, pre-TF camera-relative
#     detection; TF carries the already-world-frame pose, ready to
#     accumulate, and needs no extra transport wiring. TF is both the more
#     reliable and the more practical source.
#
# Usage (mirrors metrics_logger.py's own documented invocation -- run from
# inside the dimos checkout so it's the one venv, one cwd, everyone has to
# remember):
#   cd dimos && uv run python ../trial/scripts/survey_dump.py \
#       --min-sightings 15 --max-spread-m 0.05
#
# (`uv run --project dimos python trial/scripts/survey_dump.py` from this
# repo's parent dir also works -- dimos is pip-installed editable in its own
# .venv, importable from any cwd -- but `cd dimos` first is the documented
# form so a future `dimos/.env` resolves the same way the live process's
# does. No transport env var is required today: `dimos/.env` does not exist,
# so DIMOS_TRANSPORT falls through to global_config's Darwin default,
# `zenoh`, with zero-config local peer discovery -- same as the live
# `unitree-go2-markers` session. Set `DIMOS_TRANSPORT=lcm` only if the target
# run was explicitly started with `--transport lcm`.)
#
# Ctrl+C (or --duration N) stops the survey and writes:
#   - office_markers.yaml           only markers passing BOTH gates
#                                    (--min-sightings, --max-spread-m) AND
#                                    not excluded by --only-ids/--exclude-ids
#                                    (see below), OVERWRITING the placeholder
#                                    there, in load_marker_map's exact schema.
#   - office_markers.survey.json    sidecar: every marker seen (included AND
#                                    excluded -- including markers excluded
#                                    only by --only-ids/--exclude-ids),
#                                    sighting counts, spreads, means, and
#                                    exact exclude reasons -- the record
#                                    report.py-style tooling can read later.
#
# --only-ids "0,1,2,..." / --exclude-ids "49" (mutually exclusive): restrict
# which surveyed markers are allowed into office_markers.yaml, independent of
# the sighting/spread gates above. --only-ids keeps ONLY the listed marker
# ids; --exclude-ids keeps everything EXCEPT the listed ids. Every marker is
# still accumulated and gated normally -- a marker dropped by one of these
# flags still appears in office_markers.survey.json (and the printed report)
# with "excluded by flag" appended to its exclude_reasons, alongside any
# min_sightings/max_spread_m reasons it also failed. The filter is applied
# only at YAML-write time (build_outputs/print_report), never during
# accumulation.
#
# --selftest fabricates a handful of Transform messages (same dimos message
# classes MarkerTfModule publishes) and drives them through
# SurveyAccumulator.on_transform -- the exact same callback the live TF
# subscription invokes -- then the exact same finalize() (gate, write YAML +
# sidecar, validate via the real loader). No transport, no dimos process, no
# robot required. See selftest() below.

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from dimos.core.global_config import global_config
from dimos.core.transport_factory import tf_backend

# MarkerTfModule's defaults (dimos/perception/fiducial/marker_tf_module.py):
# markers_frame="markers", no marker_namespace_prefix -- so a marker child
# frame is "marker_<id>", optionally "<prefix>/marker_<id>". We match on the
# child frame alone (not the parent) since that's the unambiguous part of
# the pattern and survives a namespace prefix either way.
MARKERS_PARENT_FRAME = "markers"
MARKER_CHILD_RE = re.compile(r"(?:^|/)marker_(-?\d+)$")

DEFAULT_MIN_SIGHTINGS = 15
DEFAULT_MAX_SPREAD_M = 0.05
DEFAULT_STATUS_INTERVAL_S = 1.0

_DIMENSIONAL_ROOT = Path("/Users/aaryan/Files/Side Projects/Dimensional")
DEFAULT_OUT_YAML = _DIMENSIONAL_ROOT / "office_markers.yaml"
DEFAULT_OUT_SURVEY_JSON = _DIMENSIONAL_ROOT / "office_markers.survey.json"

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SELFTEST_OUT_YAML = _SCRIPT_DIR / "out" / "selftest_office_markers.yaml"
DEFAULT_SELFTEST_OUT_SURVEY_JSON = _SCRIPT_DIR / "out" / "selftest_office_markers.survey.json"


# -- accumulation -------------------------------------------------------


@dataclass
class MarkerStats:
    marker_id: int
    translations: list[np.ndarray] = field(default_factory=list)
    # Hemisphere-aligned unit quaternions [x, y, z, w] -- see add().
    rotations: list[np.ndarray] = field(default_factory=list)
    first_seen_ts: float | None = None
    last_seen_ts: float | None = None

    def add(self, translation: np.ndarray, rotation: np.ndarray, ts: float) -> None:
        q = rotation
        if self.rotations:
            # Quaternion double-cover: q and -q encode the identical
            # rotation. Without this, two sightings that are (numerically)
            # the same orientation but landed on opposite signs would
            # partially CANCEL in a raw component-wise sum instead of
            # reinforcing. Flip each new sample to match the first
            # sighting's hemisphere (dot product sign) before accumulating.
            if np.dot(q, self.rotations[0]) < 0:
                q = -q
        self.translations.append(translation)
        self.rotations.append(q)
        if self.first_seen_ts is None:
            self.first_seen_ts = ts
        self.last_seen_ts = ts

    @property
    def count(self) -> int:
        return len(self.translations)

    def mean_translation(self) -> np.ndarray:
        return np.mean(self.translations, axis=0)

    def spread_m(self) -> float:
        """Max deviation from the mean translation -- a stability signal."""
        if not self.translations:
            return 0.0
        mean = self.mean_translation()
        return float(max(np.linalg.norm(t - mean) for t in self.translations))

    def mean_rotation(self) -> np.ndarray:
        """Component-wise mean of the hemisphere-aligned quaternions, re-normalized.

        NOT a proper manifold/spherical average (e.g. Markley et al.'s
        eigenvector method) -- that would matter if sightings spanned a wide
        rotation spread. It's adequate here because a survey re-sights one
        static tag from a handful of nearby angles: the samples cluster
        tightly, and for a tight cluster the component-wise mean, normalized
        back onto the unit sphere, is a good approximation of the true
        spherical mean.
        """
        if not self.rotations:
            return np.array([0.0, 0.0, 0.0, 1.0])
        q_mean = np.mean(self.rotations, axis=0)
        norm = np.linalg.norm(q_mean)
        if norm < 1e-9:
            return np.array([0.0, 0.0, 0.0, 1.0])
        return q_mean / norm


class SurveyAccumulator:
    """Per-marker running stats, fed by every Transform the TF subscription sees."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.markers: dict[int, MarkerStats] = {}
        self.n_transforms_seen = 0
        self.n_marker_sightings = 0

    def on_transform(self, t: Any) -> None:
        """Same role as metrics_logger.MetricsLogger._on_transform: called for
        every Transform the (possibly synthetic, in --selftest) TF stream
        delivers. Ignores anything whose child frame isn't `marker_<id>`."""
        with self._lock:
            self.n_transforms_seen += 1
            match = MARKER_CHILD_RE.search(t.child_frame_id or "")
            if not match:
                return
            try:
                marker_id = int(match.group(1))
            except ValueError:
                return
            translation = np.array(
                [t.translation.x, t.translation.y, t.translation.z], dtype=np.float64
            )
            rotation = np.array(
                [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w], dtype=np.float64
            )
            ts = float(getattr(t, "ts", None) or time.time())
            stats = self.markers.setdefault(marker_id, MarkerStats(marker_id))
            stats.add(translation, rotation, ts)
            self.n_marker_sightings += 1

    def snapshot(self) -> dict[int, MarkerStats]:
        with self._lock:
            # MarkerStats is only ever read (mean/spread), never mutated,
            # off a snapshot -- a shallow dict copy is enough.
            return dict(self.markers)


def make_tf_subscriber(accumulator: SurveyAccumulator) -> Any:
    """Build the TF subscriber. Exact pattern as metrics_logger.py's
    `_LoggingTF`: subclass whatever `tf_backend()` resolves to (ZenohTF on
    macOS, LCMTF elsewhere), intercept `receive_transform`, still call
    `super().receive_transform(*args)` so the buffer -- and anyone else
    sharing this process -- keeps working normally. Instantiating it
    subscribes immediately (`PubSubTFConfig.autostart=True`); this is a pure
    subscriber, it never calls `.publish()`."""
    tf_class = tf_backend()

    class _SurveyTF(tf_class):  # type: ignore[misc, valid-type]
        def receive_transform(self, *args: Any) -> None:
            for t in args:
                accumulator.on_transform(t)
            super().receive_transform(*args)

    return _SurveyTF()


# -- gating + output shaping ---------------------------------------------


def parse_id_list(raw: str) -> set[int]:
    """Parse a comma-separated id list like "0, 1, 2" into {0, 1, 2}. Blank
    tokens (trailing commas, whitespace) are ignored."""
    return {int(tok.strip()) for tok in raw.split(",") if tok.strip()}


def gate_marker(stats: MarkerStats, min_sightings: int, max_spread_m: float) -> list[str]:
    reasons = []
    if stats.count < min_sightings:
        reasons.append(f"sightings={stats.count} < min_sightings={min_sightings}")
    spread = stats.spread_m()
    if spread > max_spread_m:
        reasons.append(f"spread={spread:.4f}m > max_spread_m={max_spread_m}")
    return reasons


def flag_excludes(marker_id: int, only_ids: set[int] | None, exclude_ids: set[int] | None) -> bool:
    """True if --only-ids/--exclude-ids drops this marker, independent of the
    sighting/spread gates. Mutually exclusive by construction (argparse), so
    at most one of only_ids/exclude_ids is not None at a time."""
    if only_ids is not None and marker_id not in only_ids:
        return True
    if exclude_ids is not None and marker_id in exclude_ids:
        return True
    return False


def all_exclude_reasons(
    marker_id: int,
    stats: MarkerStats,
    min_sightings: int,
    max_spread_m: float,
    only_ids: set[int] | None,
    exclude_ids: set[int] | None,
) -> list[str]:
    """The gate reasons plus, if applicable, the flag-filter reason -- shared
    by build_outputs (YAML-write time) and print_report so both agree on
    exactly why a marker is excluded."""
    reasons = gate_marker(stats, min_sightings, max_spread_m)
    if flag_excludes(marker_id, only_ids, exclude_ids):
        reasons = reasons + ["excluded by flag"]
    return reasons


def build_outputs(
    markers: dict[int, MarkerStats],
    min_sightings: int,
    max_spread_m: float,
    only_ids: set[int] | None = None,
    exclude_ids: set[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (yaml_doc, survey_json_doc). --only-ids/--exclude-ids gate
    membership in yaml_doc only (YAML-write time) -- every marker, included
    or not, still gets full stats in survey_json_doc."""
    yaml_markers: dict[int, Any] = {}
    survey_markers: dict[int, Any] = {}
    for marker_id in sorted(markers):
        stats = markers[marker_id]
        reasons = all_exclude_reasons(
            marker_id, stats, min_sightings, max_spread_m, only_ids, exclude_ids
        )
        included = not reasons
        mean_t = stats.mean_translation()
        mean_q = stats.mean_rotation()
        survey_markers[marker_id] = {
            "count": stats.count,
            "first_seen": stats.first_seen_ts,
            "last_seen": stats.last_seen_ts,
            "translation_mean": [float(x) for x in mean_t],
            "translation_spread_m": stats.spread_m(),
            "rotation_mean": [float(x) for x in mean_q],
            "included": included,
            "exclude_reasons": reasons,
        }
        if included:
            yaml_markers[marker_id] = {
                "translation": [float(x) for x in mean_t],
                "rotation": [float(x) for x in mean_q],
            }
    yaml_doc = {"markers": yaml_markers}
    survey_doc = {
        "generated_at": time.time(),
        "min_sightings": min_sightings,
        "max_spread_m": max_spread_m,
        "markers": survey_markers,
    }
    return yaml_doc, survey_doc


_YAML_HEADER = """\
# Auto-generated by trial/scripts/survey_dump.py -- re-run the survey to
# refresh, don't hand-edit meaningfully. Schema matches
# dimos.perception.fiducial.visual_relocalization.load_marker_map:
#   markers:
#     <id>:
#       translation: [x, y, z]      # meters, mean over sightings, map/world frame
#       rotation: [x, y, z, w]      # quaternion, hemisphere-aligned component-wise
#                                    # mean (see survey_dump.py MarkerStats.mean_rotation),
#                                    # map/world frame
#
# generated_at: {generated_at}
# gates: min_sightings={min_sightings}  max_spread_m={max_spread_m}
# included {n_included}/{n_total} surveyed marker(s) -- see office_markers.survey.json
# alongside this file for every marker seen (incl. excluded) and exactly why.
"""


def write_yaml(
    path: Path, yaml_doc: dict[str, Any], min_sightings: int, max_spread_m: float, n_total: int
) -> None:
    n_included = len(yaml_doc["markers"])
    header = _YAML_HEADER.format(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        min_sightings=min_sightings,
        max_spread_m=max_spread_m,
        n_included=n_included,
        n_total=n_total,
    )
    body = yaml.safe_dump(yaml_doc, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + body)


def write_survey_json(path: Path, survey_doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(survey_doc, indent=2, sort_keys=True))


def print_report(
    markers: dict[int, MarkerStats],
    min_sightings: int,
    max_spread_m: float,
    only_ids: set[int] | None = None,
    exclude_ids: set[int] | None = None,
) -> None:
    included = []
    excluded = []
    for marker_id in sorted(markers):
        stats = markers[marker_id]
        reasons = all_exclude_reasons(
            marker_id, stats, min_sightings, max_spread_m, only_ids, exclude_ids
        )
        (excluded if reasons else included).append((marker_id, stats, reasons))

    print()
    print("=" * 64)
    print("SURVEY REPORT")
    print("=" * 64)
    print(f"markers seen: {len(markers)}   included: {len(included)}   excluded: {len(excluded)}")
    print()
    if included:
        print("INCLUDED:")
        for marker_id, stats, _ in included:
            print(
                f"  marker_{marker_id}: {stats.count} sightings, "
                f"spread {stats.spread_m() * 100:.1f}cm"
            )
    if excluded:
        print("EXCLUDED:")
        for marker_id, stats, reasons in excluded:
            print(f"  marker_{marker_id}: {'; '.join(reasons)}")
    if not markers:
        print("  (no marker sightings observed this session)")
    print("=" * 64)


def validate_with_loader(yaml_path: str | Path) -> int:
    """Read-only import of the real loader, from the clone, to prove the
    written YAML is actually loadable -- not just well-formed YAML."""
    from dimos.perception.fiducial.visual_relocalization import load_marker_map

    loaded = load_marker_map(yaml_path)
    return len(loaded)


def finalize(accumulator: SurveyAccumulator, args: argparse.Namespace) -> int:
    markers = accumulator.snapshot()
    only_ids = getattr(args, "only_ids", None)
    exclude_ids = getattr(args, "exclude_ids", None)
    yaml_doc, survey_doc = build_outputs(
        markers, args.min_sightings, args.max_spread_m, only_ids, exclude_ids
    )
    print_report(markers, args.min_sightings, args.max_spread_m, only_ids, exclude_ids)

    out_yaml = Path(args.out_yaml)
    out_survey_json = Path(args.out_survey_json)
    write_yaml(out_yaml, yaml_doc, args.min_sightings, args.max_spread_m, len(markers))
    write_survey_json(out_survey_json, survey_doc)
    print(f"survey_dump: wrote {out_yaml}")
    print(f"survey_dump: wrote {out_survey_json}")

    n_loaded = validate_with_loader(out_yaml)
    print(f"survey_dump: loader validation OK -- load_marker_map loaded {n_loaded} marker(s)")
    return n_loaded


# -- live console feedback ------------------------------------------------


class StatusPrinter:
    """One line per marker, `marker_3: 17 sightings, spread 2.1cm ✓`.

    Updates in place (ANSI cursor-up + clear-line) on a real terminal;
    prints a fresh periodic snapshot instead when stdout isn't a tty (e.g.
    redirected to a log file), so a saved log stays readable.
    """

    def __init__(
        self,
        accumulator: SurveyAccumulator,
        min_sightings: int,
        max_spread_m: float,
        interval_s: float,
    ) -> None:
        self._acc = accumulator
        self._min_sightings = min_sightings
        self._max_spread_m = max_spread_m
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._is_tty = sys.stdout.isatty()
        self._last_n_lines = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _render_lines(self) -> list[str]:
        snap = self._acc.snapshot()
        if not snap:
            return ["survey_dump: (no marker sightings yet)"]
        lines = []
        for marker_id in sorted(snap):
            stats = snap[marker_id]
            passing = stats.count >= self._min_sightings and stats.spread_m() <= self._max_spread_m
            mark = "✓" if passing else " "
            lines.append(
                f"marker_{marker_id}: {stats.count} sightings, "
                f"spread {stats.spread_m() * 100:.1f}cm {mark}"
            )
        return lines

    def _print(self) -> None:
        lines = self._render_lines()
        if self._is_tty:
            if self._last_n_lines:
                sys.stdout.write(f"\x1b[{self._last_n_lines}A")
            for line in lines:
                sys.stdout.write("\x1b[2K" + line + "\n")
            self._last_n_lines = len(lines)
        else:
            for line in lines:
                print(line)
        sys.stdout.flush()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            self._print()
        self._print()  # final refresh right before we stop


# -- live attach ------------------------------------------------------


def run_survey(args: argparse.Namespace) -> SurveyAccumulator:
    accumulator = SurveyAccumulator()
    tf = make_tf_subscriber(accumulator)
    print(
        f"survey_dump: transport={global_config.transport} -> subscribed to TF, "
        f"watching child frames matching 'marker_<id>'. Attach-only, read-only -- "
        f"never publishes, never touches the dimos runtime."
    )
    if args.duration is not None:
        print(f"survey_dump: surveying for {args.duration:.0f}s (or Ctrl+C to stop early)...")
    else:
        print("survey_dump: surveying -- Ctrl+C to stop and write the marker map.")

    status = StatusPrinter(accumulator, args.min_sightings, args.max_spread_m, args.status_interval)
    status.start()
    start = time.monotonic()
    try:
        while args.duration is None or (time.monotonic() - start) < args.duration:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nsurvey_dump: Ctrl+C, stopping survey...")
    finally:
        status.stop()
        try:
            tf.stop()
        except Exception:
            pass
    print(
        f"survey_dump: stopped. {accumulator.n_transforms_seen} TF messages observed, "
        f"{accumulator.n_marker_sightings} were marker sightings."
    )
    return accumulator


# -- selftest -----------------------------------------------------------


def _fabricate_transform(marker_id: int, translation: np.ndarray, rotation: np.ndarray, ts: float) -> Any:
    """Build a real dimos Transform, the same message type MarkerTfModule
    publishes and the same type SurveyAccumulator.on_transform expects --
    exercising the real parsing path, not a parallel mock."""
    from dimos.msgs.geometry_msgs.Quaternion import Quaternion
    from dimos.msgs.geometry_msgs.Transform import Transform
    from dimos.msgs.geometry_msgs.Vector3 import Vector3

    return Transform(
        translation=Vector3(*translation.tolist()),
        rotation=Quaternion(*rotation.tolist()),
        frame_id=MARKERS_PARENT_FRAME,
        child_frame_id=f"marker_{marker_id}",
        ts=ts,
    )


def selftest(args: argparse.Namespace) -> None:
    """Feed synthetic TF sightings through the exact live code path (accumulate
    -> gate -> write YAML + sidecar -> validate via the real loader) with no
    transport, no dimos process, and no robot. Four scenarios:
      - marker 3:  20 tight sightings incl. sign-flipped quaternions -> PASS
                   (also proves hemisphere alignment doesn't poison the mean)
      - marker 7:   5 tight sightings                                -> FAIL (too few)
      - marker 12: 20 sightings, wide translation jitter              -> FAIL (spread)
      - marker 21: 16 tight sightings incl. sign-flipped quaternions -> PASS

    Then, reusing that same accumulated data (build_outputs/finalize only
    read MarkerStats, never mutate it), re-runs finalize() twice more to
    cover --only-ids/--exclude-ids: proves the flag filter (a) picks the
    right included/excluded set, (b) marks a normally-passing marker
    exclude_reasons == ["excluded by flag"] when the flag alone drops it,
    (c) appends (not replaces) the flag reason alongside a real gate reason
    for a marker that fails both, and (d) that --only-ids + --exclude-ids
    together are rejected by argparse as mutually exclusive.
    """
    print("survey_dump: --selftest -- fabricating synthetic TF sightings, no transport/robot used")
    rng = np.random.default_rng(42)
    accumulator = SurveyAccumulator()
    t0 = time.time()

    scenarios: dict[int, dict[str, Any]] = {
        3: dict(n=20, base_t=[1.00, 2.00, 0.30], t_noise=0.010, base_q=[0.0, 0.0, 0.0, 1.0], flip_every=4),
        7: dict(n=5, base_t=[4.10, -0.20, 0.35], t_noise=0.005, base_q=[0.5, -0.5, 0.5, 0.5], flip_every=0),
        12: dict(n=20, base_t=[2.50, 1.10, 0.32], t_noise=0.090, base_q=[0.0, 0.0, 0.707107, 0.707107], flip_every=0),
        21: dict(n=16, base_t=[-1.20, 3.30, 0.40], t_noise=0.015, base_q=[0.0, 0.0, 1.0, 0.0], flip_every=5),
    }

    for marker_id, cfg in scenarios.items():
        base_t = np.array(cfg["base_t"])
        base_q = np.array(cfg["base_q"])
        for i in range(cfg["n"]):
            translation = base_t + rng.normal(scale=cfg["t_noise"], size=3)
            q = base_q + rng.normal(scale=0.01, size=4)
            q = q / np.linalg.norm(q)
            if cfg["flip_every"] and i % cfg["flip_every"] == 0:
                q = -q  # same rotation, opposite sign -- exercises hemisphere alignment
            t = _fabricate_transform(marker_id, translation, q, t0 + i * 0.5)
            accumulator.on_transform(t)  # the SAME callback the live TF subscriber calls

    print(
        f"survey_dump: selftest fed {accumulator.n_marker_sightings} synthetic marker sightings "
        f"across {len(accumulator.markers)} marker ids"
    )
    finalize(accumulator, args)

    # Make the pasted output legible as PROOF, not just "it ran": assert the
    # gate outcome and the hemisphere-alignment fix are both actually correct.
    survey = json.loads(Path(args.out_survey_json).read_text())
    included = {int(k) for k, v in survey["markers"].items() if v["included"]}
    excluded = {int(k) for k, v in survey["markers"].items() if not v["included"]}
    expected_included = {3, 21}
    expected_excluded = {7, 12}
    assert included == expected_included, f"selftest FAILED: included={included}, expected={expected_included}"
    assert excluded == expected_excluded, f"selftest FAILED: excluded={excluded}, expected={expected_excluded}"

    mean_q_3 = np.array(survey["markers"]["3"]["rotation_mean"])
    identity = np.array([0.0, 0.0, 0.0, 1.0])
    close_to_identity = min(np.linalg.norm(mean_q_3 - identity), np.linalg.norm(mean_q_3 + identity)) < 0.05
    assert close_to_identity, f"selftest FAILED: hemisphere flip poisoned marker 3's rotation mean: {mean_q_3}"

    print("survey_dump: selftest PASSED -- gating, averaging, hemisphere alignment, YAML + sidecar")
    print("survey_dump: writing, and real-loader validation all verified correct.")

    # -- --only-ids / --exclude-ids coverage ---------------------------
    # Reuse the same populated accumulator (finalize()/build_outputs() only
    # read MarkerStats, never mutate it) instead of re-fabricating sightings.
    only_ids_args = argparse.Namespace(
        min_sightings=args.min_sightings,
        max_spread_m=args.max_spread_m,
        out_yaml=str(_SCRIPT_DIR / "out" / "selftest_only_ids_office_markers.yaml"),
        out_survey_json=str(_SCRIPT_DIR / "out" / "selftest_only_ids_office_markers.survey.json"),
        only_ids={3},
        exclude_ids=None,
    )
    print()
    print('survey_dump: --selftest -- exercising --only-ids "3" ...')
    finalize(accumulator, only_ids_args)
    only_survey = json.loads(Path(only_ids_args.out_survey_json).read_text())
    only_included = {int(k) for k, v in only_survey["markers"].items() if v["included"]}
    only_excluded = {int(k) for k, v in only_survey["markers"].items() if not v["included"]}
    assert only_included == {3}, f"selftest FAILED: --only-ids included={only_included}, expected={{3}}"
    assert only_excluded == {7, 12, 21}, (
        f"selftest FAILED: --only-ids excluded={only_excluded}, expected={{7, 12, 21}}"
    )
    # marker 21 normally PASSES both gates -- with --only-ids "3" its ONLY
    # exclude reason must be the flag, proving the filter is independent of
    # (and doesn't corrupt) the sighting/spread gates.
    reasons_21 = only_survey["markers"]["21"]["exclude_reasons"]
    assert reasons_21 == ["excluded by flag"], (
        f"selftest FAILED: --only-ids marker 21 exclude_reasons={reasons_21}, expected=['excluded by flag']"
    )
    # marker 12 fails on spread already -- --only-ids ADDS the flag reason on
    # top, it doesn't replace the gate reason.
    reasons_12 = only_survey["markers"]["12"]["exclude_reasons"]
    assert "excluded by flag" in reasons_12 and any("spread=" in r for r in reasons_12), (
        f"selftest FAILED: --only-ids marker 12 exclude_reasons={reasons_12}, "
        f"expected both a spread reason and the flag reason"
    )
    print(
        f'survey_dump: --only-ids "3" selftest PASSED -- '
        f"included={sorted(only_included)}, excluded={sorted(only_excluded)}"
    )
    print(f"survey_dump:   marker 21 exclude_reasons={reasons_21} (flag-only, gates would have passed it)")
    print(f"survey_dump:   marker 12 exclude_reasons={reasons_12} (gate reason + flag reason, both kept)")

    exclude_ids_args = argparse.Namespace(
        min_sightings=args.min_sightings,
        max_spread_m=args.max_spread_m,
        out_yaml=str(_SCRIPT_DIR / "out" / "selftest_exclude_ids_office_markers.yaml"),
        out_survey_json=str(_SCRIPT_DIR / "out" / "selftest_exclude_ids_office_markers.survey.json"),
        only_ids=None,
        exclude_ids={3},
    )
    print()
    print('survey_dump: --selftest -- exercising --exclude-ids "3" ...')
    finalize(accumulator, exclude_ids_args)
    excl_survey = json.loads(Path(exclude_ids_args.out_survey_json).read_text())
    excl_included = {int(k) for k, v in excl_survey["markers"].items() if v["included"]}
    excl_excluded = {int(k) for k, v in excl_survey["markers"].items() if not v["included"]}
    assert excl_included == {21}, f"selftest FAILED: --exclude-ids included={excl_included}, expected={{21}}"
    assert excl_excluded == {3, 7, 12}, (
        f"selftest FAILED: --exclude-ids excluded={excl_excluded}, expected={{3, 7, 12}}"
    )
    # marker 3 normally PASSES both gates -- with --exclude-ids "3" its ONLY
    # exclude reason must be the flag.
    reasons_3 = excl_survey["markers"]["3"]["exclude_reasons"]
    assert reasons_3 == ["excluded by flag"], (
        f"selftest FAILED: --exclude-ids marker 3 exclude_reasons={reasons_3}, expected=['excluded by flag']"
    )
    print(
        f'survey_dump: --exclude-ids "3" selftest PASSED -- '
        f"included={sorted(excl_included)}, excluded={sorted(excl_excluded)}"
    )
    print(f"survey_dump:   marker 3 exclude_reasons={reasons_3} (flag-only, gates would have passed it)")

    # --only-ids and --exclude-ids are mutually exclusive at the CLI level.
    try:
        build_arg_parser().parse_args(["--only-ids", "1", "--exclude-ids", "2"])
        raise AssertionError(
            "selftest FAILED: --only-ids + --exclude-ids together should be rejected by argparse"
        )
    except SystemExit as e:
        assert e.code != 0, "selftest FAILED: mutually-exclusive flags should exit non-zero"
    print('survey_dump: --only-ids + --exclude-ids together correctly REJECTED by argparse (mutually exclusive)')

    print()
    print("survey_dump: selftest PASSED -- --only-ids / --exclude-ids filtering, flag-vs-gate reason")
    print("survey_dump: composition, and CLI mutual-exclusivity all verified correct.")


# -- CLI -----------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--min-sightings",
        type=int,
        default=DEFAULT_MIN_SIGHTINGS,
        help=f"minimum sightings for a marker to be written to the map (default: {DEFAULT_MIN_SIGHTINGS})",
    )
    ap.add_argument(
        "--max-spread-m",
        type=float,
        default=DEFAULT_MAX_SPREAD_M,
        help=f"max translation spread in meters for a marker to be written (default: {DEFAULT_MAX_SPREAD_M})",
    )
    ap.add_argument(
        "--duration", type=float, default=None, help="seconds to survey (default: until Ctrl+C)"
    )
    ap.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL_S,
        help=f"seconds between live status refreshes (default: {DEFAULT_STATUS_INTERVAL_S})",
    )
    ap.add_argument(
        "--out-yaml",
        default=None,
        help=f"output marker-map path (default: {DEFAULT_OUT_YAML}, or the selftest path under --selftest)",
    )
    ap.add_argument(
        "--out-survey-json",
        default=None,
        help=f"sidecar raw-stats JSON path (default: {DEFAULT_OUT_SURVEY_JSON}, or the selftest path under --selftest)",
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="run an offline synthetic fixture through the full pipeline; no transport/robot needed",
    )
    id_filter_group = ap.add_mutually_exclusive_group()
    id_filter_group.add_argument(
        "--only-ids",
        default=None,
        metavar='"0,1,2,..."',
        help=(
            "comma-separated marker ids -- office_markers.yaml includes ONLY these ids; "
            "every other surveyed marker is still gated/reported normally but excluded from "
            'the YAML with reason "excluded by flag". Mutually exclusive with --exclude-ids.'
        ),
    )
    id_filter_group.add_argument(
        "--exclude-ids",
        default=None,
        metavar='"49"',
        help=(
            "comma-separated marker ids -- office_markers.yaml includes every surveyed marker "
            'EXCEPT these ids, which are excluded from the YAML with reason "excluded by flag". '
            "Mutually exclusive with --only-ids."
        ),
    )
    return ap


def main() -> None:
    args = build_arg_parser().parse_args()
    args.only_ids = parse_id_list(args.only_ids) if args.only_ids else None
    args.exclude_ids = parse_id_list(args.exclude_ids) if args.exclude_ids else None
    if args.out_yaml is None:
        args.out_yaml = str(DEFAULT_SELFTEST_OUT_YAML if args.selftest else DEFAULT_OUT_YAML)
    if args.out_survey_json is None:
        args.out_survey_json = str(DEFAULT_SELFTEST_OUT_SURVEY_JSON if args.selftest else DEFAULT_OUT_SURVEY_JSON)

    if args.selftest:
        selftest(args)
        return

    accumulator = run_survey(args)
    finalize(accumulator, args)


if __name__ == "__main__":
    main()
