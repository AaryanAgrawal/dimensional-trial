#!/usr/bin/env python3
# Benchmark runner -- turns trial/benchmarks.md's cards into repeatable
# one-command runs and a growing comparison table.
#
# Extends metrics_logger.py / report.py -- does not reimplement them.
# `start` wraps MetricsLogger (metrics_logger.py) with a mode/route/notes
# tag and a run id. `stop`/`report` score a finished run using report.py's
# own load_jsonl/by_type/compute_ate/detection_stats, add metrics report.py
# doesn't compute (loop-closure error, correction-magnitude stats, a
# same-run marker-vs-lidar log_event source breakdown, and -- when
# --holdout-tag is given -- the holdout-tag referee's closure error against
# a map-independent physical reference, see benchmark-spec.md §4), and
# append one row to trial/results/benchmarks.csv + regenerate
# trial/results/RESULTS.md.
#
# Files, not databases. `start` and `calibrate-referee` are the only verbs
# that need the dimos venv (both import metrics_logger.py, which imports
# dimos.core.*); `stop` and `report` are pure stdlib and run anywhere.
#
# Two ways to end a run:
#   - Ctrl+C in the terminal running `start` (same UX as metrics_logger.py)
#   - `bench.py stop` from a second terminal -- reads the active-run state
#     file, sends SIGINT to the `start` process (same effect as Ctrl+C),
#     waits for it to finish scoring + appending the row.
# Either path runs the exact same finalize code exactly once, in the
# `start` process, right after MetricsLogger.run() returns.
#
# `calibrate-referee` is a separate guided flow, not a scored run: STEP 1
# static-hold -> noise floor (per-axis std + 3-sigma radius, position and
# rotation), STEP 2 a tape-measured slide -> scale-error %, STEP 3 writes
# trial/results/referee-budget.json, after which every `report` appends a
# "start/end tag referee: ..." footer line to RESULTS.md. See "referee
# calibration" below and day1-runbook.md's "Noise floor" section.
#
# Usage:
#   cd dimos
#   uv run python ../trial/scripts/bench.py start --mode marker --route drift-recovery --notes "loop A"
#   # ... drive the loop, Ctrl+C when back at the tape mark ...
#   uv run python ../trial/scripts/bench.py report
#   uv run python ../trial/scripts/bench.py calibrate-referee --holdout-tag 42
#
# See trial/day1-runbook.md ("Acceptance drill cards") for the exact
# per-drill commands, and trial/benchmarks.md for what each route proves.

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
TRIAL_DIR = SCRIPTS_DIR.parent
OUT_DIR = SCRIPTS_DIR / "out"
RUNS_DIR = OUT_DIR / "runs"
ACTIVE_STATE_PATH = OUT_DIR / ".bench_active.json"
RESULTS_DIR = TRIAL_DIR / "results"
BENCHMARKS_CSV = RESULTS_DIR / "benchmarks.csv"
RESULTS_MD = RESULTS_DIR / "RESULTS.md"

MODES = ("odom", "marker", "lidar", "fused")

# report.py has no dimos import (by design -- see its own header comment),
# so this is safe for `stop`/`report` to do unconditionally. metrics_logger.py
# DOES import dimos.core.* at module level, so it's imported lazily, only
# inside cmd_start, to keep `stop`/`report` runnable without the dimos venv.
sys.path.insert(0, str(SCRIPTS_DIR))
import report as rpt  # noqa: E402

CSV_FIELDS = [
    "timestamp",
    "run_id",
    "route",
    "mode",
    "duration_s",
    "odom_ticks",
    "corrected_ticks",
    "corrected_pose_coverage",
    "corrections_accepted",
    "corrections_held",
    "corrections_rejected",
    "correction_mag_mean_m",
    "correction_mag_max_m",
    "loop_closure_error_odom_m",
    "loop_closure_error_corrected_m",
    "ate_proxy_rmse_m",
    "ate_proxy_n_matched",
    "holdout_tag",
    "holdout_window",
    "holdout_readings_start",
    "holdout_readings_end",
    "holdout_closure_error_m",
    "holdout_closure_error_deg",
    "holdout_reproj_mean_px",
    "holdout_reproj_max_px",
    "holdout_claim_source",
    "holdout_reason",
    "marker_log_events",
    "lidar_log_events",
    "notes",
    "log_path",
]

HOLDOUT_DEFAULT_WINDOW = 10

# calibrate-referee defaults (see the "referee calibration" section below).
DEFAULT_CALIBRATION_DURATION_S = 60.0
DEFAULT_BIAS_CAPTURE_DURATION_S = 8.0
REFEREE_BUDGET_PATH = RESULTS_DIR / "referee-budget.json"


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in s.strip().lower())


# -- metric computations (new; not in report.py) ---------------------------

# How far a corrected_pose sample is allowed to be, in time, from the run's
# start/end instant and still count as "the pose at that instant" -- same
# rationale and same value as metrics_logger.py's own
# CORRECTED_POSE_LOOKUP_TOLERANCE_S: MarkerLocalizationModule only publishes
# on a fresh accept, not on a timer, so a correction can be many seconds old
# and still be "the current one."
LOOP_CLOSURE_MAX_DT_S = 30.0


def _loop_closure_errors(
    odom: list[dict], corrected: list[dict], max_dt: float = LOOP_CLOSURE_MAX_DT_S
) -> tuple[float | None, float | None]:
    """Start/end pose delta -- the cheapest real ground truth for a route
    driven as a loop back to a taped floor mark: if start == end physically,
    any measured delta between the pose estimate at drill-start and at
    drill-end IS the error.

    odom_pose ticks continuously for the whole run, so its own first/last
    sample IS drill-start/drill-end. corrected_pose only has samples once a
    correction has landed, so its first/last *record* can be well inside
    the run (e.g. no tag in view yet at t=0) -- comparing corrected_pose[0]
    to corrected_pose[-1] directly would silently compare two different
    physical points, not the loop's endpoints. Instead, resolve the
    corrected pose nearest odom's start/end *timestamps* (reusing report.py's
    own nearest_match) and only report a corrected loop-closure number when
    both ends actually resolve.
    """
    if len(odom) < 2:
        return None, None
    odom_err = round(math.dist(rpt.as_xyz(odom[0]), rpt.as_xyz(odom[-1])), 4)

    corr_err = None
    if corrected:
        ref = sorted((r["ts"], *rpt.as_xyz(r)) for r in corrected)
        p_start = rpt.nearest_match(odom[0]["ts"], ref, max_dt)
        p_end = rpt.nearest_match(odom[-1]["ts"], ref, max_dt)
        if p_start is not None and p_end is not None:
            corr_err = round(math.dist(p_start, p_end), 4)

    return odom_err, corr_err


def _correction_magnitude_stats(new_records: list[dict]) -> tuple[float | None, float | None]:
    mags = [r["magnitude_m"] for r in new_records if r.get("magnitude_m") is not None]
    if not mags:
        return None, None
    return round(statistics.mean(mags), 4), round(max(mags), 4)


def _source_breakdown(log_events: list[dict]) -> tuple[int, int, int]:
    """Classify log_events by which module they came from, using the
    `logger`/`event` text metrics_logger.py already captures. This is the
    only place source is attributable at all -- MarkerLocalizationModule
    and RelocalizationModule both publish world->map on the same TF
    contract (they can't even run in the same process, per
    day1-runbook.md Drill C), so correction_new/correction_hold records
    themselves carry no source tag. A fused run's log_events can still
    show both loggers active; that's the honest head-to-head signal this
    tool can produce for a single run."""
    marker = lidar = other = 0
    for r in log_events:
        blob = f"{r.get('logger', '')} {r.get('event', '')}".lower()
        if "marker" in blob:
            marker += 1
        elif "relocaliz" in blob or "lidar" in blob:
            lidar += 1
        else:
            other += 1
    return marker, lidar, other


# -- holdout-tag referee (new; not in report.py) ----------------------------
#
# A tag id deliberately EXCLUDED from every localization map under test.
# metrics_logger.py --holdout-tag runs a second, independent solvePnP for that
# one id (see its header comment) and logs each sighting as tag_T_camera
# ("marker_<id> -> camera_optical") -- the camera's pose IN the tag's own
# fixed frame. Because the tag never moves, comparing that pose at drill-start
# vs drill-end IS the true physical camera displacement, with no TF/odom chain
# and none of the drift the rest of this benchmark measures. Median of the
# first/last `window` sightings gives the measured start/end; the same
# median-of-a-window statistic applied to whichever pose stream the run
# actually produced (corrected_pose if any landed, else odom_pose) -- matched
# to the SAME sighting timestamps -- gives that mode's claim over the
# identical span. holdout_closure_error_m/deg is |claim - measured|: a mode's
# own loop-closure error, checked against a real physical reference instead of
# an assumed taped start==end.
#
# Shared-pipeline caveat (benchmark-spec.md §4): this referee's PnP runs the
# exact same detector/solvePnP code as the mode under test, on the same
# physical camera -- it is not an independent instrument (contrast
# benchmark-rig.md's ChArUco-overhead-rig / laser-meter options). One
# tape-measure check per session certifies that shared pipeline; it does not
# certify the mode's own map-building/gating logic, which is what the rest of
# this benchmark measures.


def _quat_angle_deg(q1: tuple[float, ...], q2: tuple[float, ...]) -> float:
    """Angle (deg) between two orientations -- robust to the q/-q double
    cover (same rotation, opposite sign) via abs() on the dot product."""
    dot = sum(a * b for a, b in zip(q1, q2, strict=True))
    dot = max(-1.0, min(1.0, abs(dot)))
    return math.degrees(2.0 * math.acos(dot))


def _median_pose(records: list[dict]) -> tuple[tuple[float, float, float], tuple[float, ...]]:
    """Component-wise median translation + a hemisphere-aligned, renormalized
    median quaternion across a small window of same-shape records
    (tag_sighting / odom_pose / corrected_pose all carry translation+rotation).
    Not a proper SLERP average -- a cheap, dependency-free, outlier-resistant
    central tendency, good enough for an N~=10 referee window, not a smoothing
    filter."""
    xs, ys, zs = zip(*(rpt.as_xyz(r) for r in records), strict=True)
    translation = (statistics.median(xs), statistics.median(ys), statistics.median(zs))

    ref = records[0]["rotation"]
    aligned = [
        q if sum(a * b for a, b in zip(q, ref, strict=True)) >= 0 else [-c for c in q]
        for q in (r["rotation"] for r in records)
    ]
    components = list(zip(*aligned, strict=True))
    med = [statistics.median(c) for c in components]
    norm = math.sqrt(sum(c * c for c in med))
    rotation = tuple(c / norm for c in med) if norm > 1e-9 else tuple(ref)
    return translation, rotation


def _nearest_record(ts: float, sorted_records: list[dict], max_dt: float) -> dict | None:
    best = None
    best_dt = max_dt
    for r in sorted_records:
        dt = abs(r["ts"] - ts)
        if dt <= best_dt:
            best_dt = dt
            best = r
    return best


def _holdout_null(reason: str, readings_start: int = 0, readings_end: int = 0) -> dict[str, Any]:
    return {
        "holdout_readings_start": readings_start,
        "holdout_readings_end": readings_end,
        "holdout_closure_error_m": None,
        "holdout_closure_error_deg": None,
        "holdout_reproj_mean_px": None,
        "holdout_reproj_max_px": None,
        "holdout_claim_source": None,
        "holdout_reason": reason,
    }


def _holdout_referee_metrics(
    records: list[dict],
    odom: list[dict],
    corrected: list[dict],
    window: int = HOLDOUT_DEFAULT_WINDOW,
    max_dt: float = LOOP_CLOSURE_MAX_DT_S,
) -> dict[str, Any]:
    sightings = sorted(rpt.by_type(records, "tag_sighting"), key=lambda r: r["ts"])
    n = len(sightings)
    if n == 0:
        return _holdout_null(
            "no holdout sightings logged -- was --holdout-tag set and the tag in view at both ends?"
        )
    if n < 2:
        return _holdout_null(
            f"only {n} holdout sighting -- need at least one at each end for a start/end delta",
            readings_start=n,
        )

    eff_window = window if n >= 2 * window else max(1, n // 2)
    shrink_note = (
        None
        if eff_window == window
        else f"window shrunk to {eff_window} (requested {window}); only {n} holdout sightings logged this run"
    )
    first, last = sightings[:eff_window], sightings[-eff_window:]

    reproj = [s["reprojection_error_px"] for s in sightings if s.get("reprojection_error_px") is not None]
    reproj_mean = round(statistics.mean(reproj), 3) if reproj else None
    reproj_max = round(max(reproj), 3) if reproj else None

    claim_source, claim_label = (corrected, "corrected_pose") if corrected else (odom, "odom_pose")
    if not claim_source:
        result = _holdout_null(
            shrink_note or f"no {claim_label} samples this run -- nothing to compare the holdout referee against",
            readings_start=len(first),
            readings_end=len(last),
        )
        result["holdout_reproj_mean_px"] = reproj_mean
        result["holdout_reproj_max_px"] = reproj_max
        return result

    claim_sorted = sorted(claim_source, key=lambda r: r["ts"])
    claim_first = [m for s in first if (m := _nearest_record(s["ts"], claim_sorted, max_dt)) is not None]
    claim_last = [m for s in last if (m := _nearest_record(s["ts"], claim_sorted, max_dt)) is not None]
    if not claim_first or not claim_last:
        result = _holdout_null(
            shrink_note or f"no {claim_label} sample within {max_dt}s of the holdout window",
            readings_start=len(first),
            readings_end=len(last),
        )
        result["holdout_reproj_mean_px"] = reproj_mean
        result["holdout_reproj_max_px"] = reproj_max
        return result

    measured_start_t, measured_start_q = _median_pose(first)
    measured_end_t, measured_end_q = _median_pose(last)
    measured_delta_m = math.dist(measured_start_t, measured_end_t)
    measured_delta_deg = _quat_angle_deg(measured_start_q, measured_end_q)

    claimed_start_t, claimed_start_q = _median_pose(claim_first)
    claimed_end_t, claimed_end_q = _median_pose(claim_last)
    claimed_delta_m = math.dist(claimed_start_t, claimed_end_t)
    claimed_delta_deg = _quat_angle_deg(claimed_start_q, claimed_end_q)

    return {
        "holdout_readings_start": len(first),
        "holdout_readings_end": len(last),
        "holdout_closure_error_m": round(abs(claimed_delta_m - measured_delta_m), 4),
        "holdout_closure_error_deg": round(abs(claimed_delta_deg - measured_delta_deg), 2),
        "holdout_reproj_mean_px": reproj_mean,
        "holdout_reproj_max_px": reproj_max,
        "holdout_claim_source": claim_label,
        "holdout_reason": shrink_note,
    }


# -- referee calibration: noise floor + bias check (new; not in report.py) --
#
# `calibrate-referee` automates the two no-hardware checks day1-runbook.md's
# "Noise floor" section used to ask an operator to run and eyeball by hand:
# (1) a static hold -- the referee's tag_sighting stream while the robot (and
# the start/end tag) don't move at all, characterizing how much the shared
# PnP pipeline's own jitter alone contributes to a reported closure number;
# (2) a bias check -- a known, tape-measured slide, to catch a systematic
# scale error (e.g. a misprinted tag -- see day1-runbook.md §3's print-scale
# warning) that a same-session noise-floor check alone can't see. Both reuse
# _median_pose/_quat_angle_deg above; neither needs a mode/route/CSV row --
# this calibrates the referee itself, not a run under test.


def static_hold_noise_floor(sightings: list[dict]) -> dict[str, Any]:
    """Per-axis position std (m) + a combined 3-sigma radius, and a rotation
    std/3-sigma spread (deg), over a burst of tag_sighting readings logged
    while the robot and the start/end tag were both stationary. Any nonzero
    spread here is pure PnP/detector jitter, not real motion -- it's the
    floor every holdout_closure_error_m/_deg number should be read against.

    Position: 3-sigma radius = 3 * RSS(std_x, std_y, std_z) -- the 3-sigma
    edge of an isotropic-equivalent noise ball. Per-axis stds are reported
    too, since real jitter is rarely isotropic.

    Rotation: quaternion components don't have a physically meaningful
    per-axis std, so rotation noise is one scalar -- each reading's angular
    deviation (via _quat_angle_deg) from the window's own median orientation
    -- and that scalar's std / 3x-std, not a 3-axis breakdown."""
    n = len(sightings)
    if n < 2:
        raise SystemExit(f"calibrate-referee: need >=2 tag_sighting readings for a noise floor, got {n}")

    xs, ys, zs = zip(*(rpt.as_xyz(r) for r in sightings), strict=True)
    std_x, std_y, std_z = statistics.pstdev(xs), statistics.pstdev(ys), statistics.pstdev(zs)
    pos_3sigma_radius_m = 3.0 * math.sqrt(std_x**2 + std_y**2 + std_z**2)

    _, median_q = _median_pose(sightings)
    angles_deg = [_quat_angle_deg(median_q, r["rotation"]) for r in sightings]
    rot_std_deg = statistics.pstdev(angles_deg)

    return {
        "n_readings": n,
        "pos_std_m": {"x": round(std_x, 5), "y": round(std_y, 5), "z": round(std_z, 5)},
        "pos_3sigma_radius_m": round(pos_3sigma_radius_m, 5),
        "rot_std_deg": round(rot_std_deg, 3),
        "rot_3sigma_deg": round(3.0 * rot_std_deg, 3),
    }


def bias_check(before: list[dict], after: list[dict], taped_mm: float) -> dict[str, Any]:
    """Scale-error check: median tag-referee pose before vs after a
    tape-measured slide. `measured_mm` is what the shared PnP pipeline says
    the robot moved; `scale_error_pct` is how far that is from the tape --
    a systematic print-scale or calibration error shows up here as a
    consistent nonzero %, not noise (see day1-runbook.md §3)."""
    if not before or not after:
        raise SystemExit("calibrate-referee: bias check needs readings on both sides of the slide")
    if taped_mm <= 0:
        raise SystemExit(f"calibrate-referee: taped distance must be positive, got {taped_mm}")

    before_t, _ = _median_pose(before)
    after_t, _ = _median_pose(after)
    measured_mm = math.dist(before_t, after_t) * 1000.0
    scale_error_pct = (measured_mm - taped_mm) / taped_mm * 100.0

    return {
        "n_before": len(before),
        "n_after": len(after),
        "taped_mm": round(taped_mm, 2),
        "measured_mm": round(measured_mm, 2),
        "scale_error_pct": round(scale_error_pct, 2),
    }


def _load_referee_budget() -> dict[str, Any] | None:
    if not REFEREE_BUDGET_PATH.exists():
        return None
    try:
        return json.loads(REFEREE_BUDGET_PATH.read_text())
    except json.JSONDecodeError:
        return None


def format_referee_budget_line(budget: dict[str, Any]) -> str:
    noise = budget["noise_floor"]
    bias = budget["bias"]
    radius_mm = noise["pos_3sigma_radius_m"] * 1000.0
    date = budget["timestamp"][:10]
    scale = bias.get("scale_error_pct")
    scale_str = f"{scale:+.2f}%" if scale is not None else "n/a"
    return f"start/end tag referee: +/-{radius_mm:.1f}mm (3-sigma), scale bias {scale_str} (calibrated {date})"


def compute_run_metrics(
    log_path: Path,
    mode: str,
    route: str,
    notes: str,
    run_id: str,
    duration_s: float,
    holdout_tag: int | None = None,
    holdout_window: int = HOLDOUT_DEFAULT_WINDOW,
) -> dict[str, Any]:
    records = rpt.load_jsonl(Path(log_path))
    if not records:
        raise SystemExit(f"bench: {log_path} has no records -- nothing to score")

    odom = rpt.by_type(records, "odom_pose")
    corrected = rpt.by_type(records, "corrected_pose")
    new = rpt.by_type(records, "correction_new")
    log_events = rpt.by_type(records, "log_event")

    det = rpt.detection_stats(records)  # reused as-is from report.py

    # ATE-proxy = drift-vs-corrected divergence: reuses report.py's own
    # compute_ate, fed this run's raw odom stream as "primary" and its own
    # corrected stream as "reference" -- an RMSE of how far dead-reckoning
    # strays from the corrected estimate over the run, with no external
    # ground truth required. Proxy, not real ATE (needs an outside
    # reference, e.g. --reference-log against a silver-truth run, for that).
    ate_proxy = (
        rpt.compute_ate(odom, corrected, max_dt=1.0)
        if corrected
        else {
            "ate_rmse_m": None,
            "n_matched": 0,
            "reason": "no corrected_pose samples -- zero corrections landed this run",
        }
    )

    mag_mean, mag_max = _correction_magnitude_stats(new)
    marker_events, lidar_events, _other = _source_breakdown(log_events)
    loop_odom_m, loop_corrected_m = _loop_closure_errors(odom, corrected)

    if holdout_tag is not None:
        holdout = _holdout_referee_metrics(records, odom, corrected, window=holdout_window)
    else:
        holdout = _holdout_null("holdout referee not enabled for this run (no --holdout-tag)")

    return {
        "run_id": run_id,
        "route": route,
        "mode": mode,
        "duration_s": round(duration_s, 1),
        "odom_ticks": det["odom_ticks"],
        "corrected_ticks": det["corrected_ticks"],
        "corrected_pose_coverage": det["corrected_pose_coverage"],
        "corrections_accepted": det["corrections_accepted"],
        "corrections_held": det["corrections_held_unchanged"],
        "corrections_rejected": det["corrections_rejected"],
        "correction_mag_mean_m": mag_mean,
        "correction_mag_max_m": mag_max,
        "loop_closure_error_odom_m": loop_odom_m,
        "loop_closure_error_corrected_m": loop_corrected_m,
        "ate_proxy_rmse_m": ate_proxy.get("ate_rmse_m"),
        "ate_proxy_n_matched": ate_proxy.get("n_matched"),
        "holdout_tag": holdout_tag,
        "holdout_window": holdout_window if holdout_tag is not None else None,
        **holdout,
        "marker_log_events": marker_events,
        "lidar_log_events": lidar_events,
        "notes": notes or "",
        "log_path": str(log_path),
    }


def append_csv_row(row: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    row_out = {"timestamp": datetime.now().isoformat(timespec="seconds"), **row}
    is_new = not BENCHMARKS_CSV.exists() or BENCHMARKS_CSV.stat().st_size == 0
    with BENCHMARKS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(row_out)


# -- report rendering --------------------------------------------------------


def _f(v: Any, digits: int = 3) -> str | None:
    if v in (None, "", "None"):
        return None
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return None


def _fmt_loop_closure(row: dict[str, str]) -> str:
    o = _f(row.get("loop_closure_error_odom_m"))
    c = _f(row.get("loop_closure_error_corrected_m"))
    if o and c:
        return f"{o}m -> {c}m"
    if c:
        return f"{c}m (corrected)"
    if o:
        return f"{o}m (odom)"
    return "n/a"


def _fmt_ate_proxy(row: dict[str, str]) -> str:
    v = _f(row.get("ate_proxy_rmse_m"))
    if not v:
        return "n/a"
    n = row.get("ate_proxy_n_matched") or "0"
    return f"{v}m (n={n})"


def _fmt_corrections(row: dict[str, str]) -> str:
    acc = row.get("corrections_accepted") or "0"
    rej = row.get("corrections_rejected") or "0"
    held = row.get("corrections_held") or "0"
    return f"{acc} acc / {rej} rej / {held} held"


def _fmt_holdout(row: dict[str, str]) -> str:
    tag = row.get("holdout_tag")
    if not tag:
        return "n/a"
    m = _f(row.get("holdout_closure_error_m"))
    deg = _f(row.get("holdout_closure_error_deg"), 2)
    if m:
        return f"{m}m / {deg}deg" if deg else f"{m}m"
    reason = row.get("holdout_reason") or "n/a"
    return f"n/a ({reason})"


_MODE_ORDER = {m: i for i, m in enumerate(MODES)}


def _mode_sort_key(mode: str) -> tuple[int, str]:
    return (_MODE_ORDER.get(mode, len(MODES)), mode)


def render_results_md(rows: list[dict[str, str]]) -> str:
    lines = [
        "# Benchmark results",
        "",
        f"Generated by `bench.py report` from `{BENCHMARKS_CSV.relative_to(TRIAL_DIR)}` "
        f"-- {len(rows)} run(s). Routes named after the cards in `../benchmarks.md`.",
        "",
        "## All runs",
        "",
        "| Route | Mode | Loop-closure error | Start/End closure | ATE-proxy | Corrections | Notes | Timestamp |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True):
        lines.append(
            "| {route} | {mode} | {lc} | {holdout} | {ate} | {corr} | {notes} | {ts} |".format(
                route=row.get("route", ""),
                mode=row.get("mode", ""),
                lc=_fmt_loop_closure(row),
                holdout=_fmt_holdout(row),
                ate=_fmt_ate_proxy(row),
                corr=_fmt_corrections(row),
                notes=(row.get("notes") or "").replace("|", "/") or "--",
                ts=row.get("timestamp", ""),
            )
        )
    lines.append("")

    holdout_tags = sorted({row.get("holdout_tag") for row in rows if row.get("holdout_tag")})
    for tag in holdout_tags:
        lines.append(
            f"*start/end tag referee: tag {tag}, excluded from all maps; session certified by "
            "tape-check (see rig doc).*"
        )
    if holdout_tags:
        lines.append("")

    # Head-to-head: group by route, only routes with >=2 distinct modes logged.
    by_route: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_route.setdefault(row.get("route", ""), []).append(row)

    h2h_routes = {r: rs for r, rs in by_route.items() if len({x["mode"] for x in rs}) >= 2}
    if h2h_routes:
        lines += ["## Head-to-head (by route)", ""]
        for route in sorted(h2h_routes):
            rows_for_route = h2h_routes[route]
            # latest row per mode
            latest: dict[str, dict[str, str]] = {}
            for r in sorted(rows_for_route, key=lambda r: r.get("timestamp", "")):
                latest[r["mode"]] = r
            lines.append(f"### {route}")
            lines.append("")
            lines.append("| Mode | Loop-closure error | Start/End closure | ATE-proxy | Corrections |")
            lines.append("|---|---|---|---|---|")
            for mode in sorted(latest, key=_mode_sort_key):
                r = latest[mode]
                lines.append(
                    f"| {mode} | {_fmt_loop_closure(r)} | {_fmt_holdout(r)} | "
                    f"{_fmt_ate_proxy(r)} | {_fmt_corrections(r)} |"
                )
            baseline = latest.get("odom")
            base_err = _f(baseline.get("loop_closure_error_odom_m")) if baseline else None
            if base_err:
                deltas = []
                for mode, r in latest.items():
                    if mode == "odom":
                        continue
                    corr = _f(r.get("loop_closure_error_corrected_m")) or _f(
                        r.get("loop_closure_error_odom_m")
                    )
                    if corr and float(corr) > 0:
                        factor = float(base_err) / float(corr)
                        deltas.append(f"{mode} {factor:.1f}x tighter than odom-only")
                if deltas:
                    lines.append("")
                    lines.append("- " + "; ".join(deltas))
            lines.append("")

    # Same-run marker-vs-lidar source breakdown (fused runs only, honest gap
    # noted in _source_breakdown's docstring -- this is the only place a
    # single run's log can show both modules' activity).
    fused_both = [
        r
        for r in rows
        if r.get("mode") == "fused"
        and int(r.get("marker_log_events") or 0) > 0
        and int(r.get("lidar_log_events") or 0) > 0
    ]
    if fused_both:
        lines += ["## Fused-run source breakdown", "", (
            "Correction TF (`world->map`) carries no source tag -- marker and lidar "
            "corrections are indistinguishable on the wire. This is the log_event "
            "(`--dimos-log`) attribution instead: which module's log lines fired, "
            "same run."
        ), ""]
        for r in fused_both:
            lines.append(
                f"- **{r.get('route')}** (`{r.get('run_id')}`): "
                f"{r.get('marker_log_events')} marker-side log events vs "
                f"{r.get('lidar_log_events')} lidar-side log events"
            )
        lines.append("")

    budget = _load_referee_budget()
    if budget:
        lines.append(format_referee_budget_line(budget))
        lines.append("")

    return "\n".join(lines)


def regenerate_report() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not BENCHMARKS_CSV.exists():
        lines = ["# Benchmark results", "", "No runs yet -- `bench.py start ...` then `bench.py stop`.", ""]
        budget = _load_referee_budget()
        if budget:
            lines += [format_referee_budget_line(budget), ""]
        RESULTS_MD.write_text("\n".join(lines))
        return 0
    with BENCHMARKS_CSV.open() as f:
        rows = list(csv.DictReader(f))
    RESULTS_MD.write_text(render_results_md(rows))
    return len(rows)


# -- active-run state (for `stop` from another terminal) --------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    return True


def _write_active_state(state: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_STATE_PATH.write_text(json.dumps(state, indent=2))


def _read_active_state() -> dict[str, Any] | None:
    if not ACTIVE_STATE_PATH.exists():
        return None
    try:
        return json.loads(ACTIVE_STATE_PATH.read_text())
    except json.JSONDecodeError:
        return None


def _clear_active_state() -> None:
    ACTIVE_STATE_PATH.unlink(missing_ok=True)


# -- verbs --------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> None:
    existing = _read_active_state()
    if existing and _pid_alive(existing.get("pid", -1)):
        raise SystemExit(
            f"bench: a run is already active -- route={existing.get('route')} "
            f"mode={existing.get('mode')} pid={existing.get('pid')}. "
            f"Run `bench.py stop` first, or Ctrl+C that process."
        )
    if existing:
        print("bench: clearing a stale active-run state file (process no longer alive)")
        _clear_active_state()

    # Lazy import: metrics_logger.py pulls in dimos.core.* at module level.
    # Only `start` needs the dimos venv; `stop`/`report` must not.
    try:
        from metrics_logger import MetricsLogger
    except ImportError as e:
        raise SystemExit(
            "bench: could not import metrics_logger.py's dimos dependencies -- "
            f"run from the dimos/ dir with its venv (`cd dimos && uv run python "
            f"../trial/scripts/bench.py start ...`). Original error: {e}"
        )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{_slug(args.route)}__{args.mode}__{time.strftime('%Y%m%d-%H%M%S')}"
    out_path = RUNS_DIR / f"{run_id}.jsonl"

    _write_active_state(
        {
            "pid": os.getpid(),
            "run_id": run_id,
            "out_path": str(out_path),
            "mode": args.mode,
            "route": args.route,
            "notes": args.notes or "",
            "dimos_log": args.dimos_log,
            "start_ts": time.time(),
            "duration": args.duration,
            "holdout_tag": args.holdout_tag,
            "holdout_window": args.holdout_window,
        }
    )

    print(
        f"bench: starting run_id={run_id} mode={args.mode} route={args.route} "
        f"-> {out_path}"
        + (f" (+ tailing {args.dimos_log})" if args.dimos_log else "")
        + (f" (+ holdout tag {args.holdout_tag})" if args.holdout_tag is not None else "")
    )
    print("bench: Ctrl+C here (or `bench.py stop` from another terminal) to end the run.")

    ml = MetricsLogger(
        out_path,
        args.dimos_log,
        holdout_tag=args.holdout_tag,
        holdout_marker_length_m=args.holdout_marker_length_m,
        holdout_aruco_dictionary=args.holdout_aruco_dictionary,
    )
    start_ts = time.time()
    ml.run(duration=args.duration)  # blocks; returns on duration expiry, Ctrl+C, or SIGINT from `stop`
    duration_s = time.time() - start_ts

    print(f"bench: logger stopped. summary={ml.summary()}")
    print("bench: scoring run...")
    row = compute_run_metrics(
        out_path,
        args.mode,
        args.route,
        args.notes or "",
        run_id,
        duration_s,
        holdout_tag=args.holdout_tag,
        holdout_window=args.holdout_window,
    )
    append_csv_row(row)
    n_rows = regenerate_report()
    _clear_active_state()

    print(f"bench: appended row to {BENCHMARKS_CSV.relative_to(TRIAL_DIR)} ({n_rows} total)")
    print(f"bench: regenerated {RESULTS_MD.relative_to(TRIAL_DIR)}")
    print(
        f"bench: loop-closure {row['loop_closure_error_odom_m']}m odom -> "
        f"{row['loop_closure_error_corrected_m']}m corrected | "
        f"ATE-proxy {row['ate_proxy_rmse_m']}m | "
        f"corrections {row['corrections_accepted']} acc / {row['corrections_rejected']} rej"
        + (
            f" | start/end closure {row['holdout_closure_error_m']}m / "
            f"{row['holdout_closure_error_deg']}deg"
            if row.get("holdout_tag") is not None
            else ""
        )
    )


def cmd_stop(args: argparse.Namespace) -> None:
    state = _read_active_state()
    if not state:
        raise SystemExit("bench: no active run found (no state file at " f"{ACTIVE_STATE_PATH})")
    pid = state.get("pid")
    if not _pid_alive(pid):
        print("bench: state file's process is already gone -- clearing stale state")
        _clear_active_state()
        return

    print(
        f"bench: stopping run_id={state.get('run_id')} route={state.get('route')} "
        f"mode={state.get('mode')} (pid={pid})..."
    )
    os.kill(pid, signal.SIGINT)

    timeout_s = args.timeout
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not ACTIVE_STATE_PATH.exists():
            print("bench: run stopped, scored, and appended.")
            if BENCHMARKS_CSV.exists():
                with BENCHMARKS_CSV.open() as f:
                    rows = list(csv.DictReader(f))
                if rows:
                    last = rows[-1]
                    print(
                        f"bench: last row -- route={last['route']} mode={last['mode']} "
                        f"loop-closure={_fmt_loop_closure(last)} ate-proxy={_fmt_ate_proxy(last)}"
                    )
            return
        time.sleep(0.5)
    print(
        f"bench: timed out after {timeout_s}s waiting for pid={pid} to finish scoring -- "
        "it may still be writing a large log; check trial/results/benchmarks.csv, "
        "or re-run `bench.py stop`."
    )


def cmd_report(args: argparse.Namespace) -> None:
    n_rows = regenerate_report()
    print(f"bench: regenerated {RESULTS_MD} from {n_rows} row(s) in {BENCHMARKS_CSV}")


def cmd_calibrate_referee(args: argparse.Namespace) -> None:
    if args.selftest:
        _calibrate_referee_selftest()
        return
    if args.holdout_tag is None:
        raise SystemExit("bench calibrate-referee: --holdout-tag is required (unless --selftest)")

    # Lazy import, same reason/rationale as cmd_start.
    try:
        from metrics_logger import MetricsLogger
    except ImportError as e:
        raise SystemExit(
            "bench: could not import metrics_logger.py's dimos dependencies -- "
            "run from the dimos/ dir with its venv (`cd dimos && uv run python "
            f"../trial/scripts/bench.py calibrate-referee ...`). Original error: {e}"
        )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    print(f"bench calibrate-referee: STEP 1/3 -- static hold ({args.duration:.0f}s)")
    print(f"  Park the robot facing tag {args.holdout_tag} (the start/end tag). Hold still -- recording...")
    static_path = RUNS_DIR / f"calibrate-referee__static-hold__{ts}.jsonl"
    ml1 = MetricsLogger(
        static_path,
        None,
        holdout_tag=args.holdout_tag,
        holdout_marker_length_m=args.holdout_marker_length_m,
        holdout_aruco_dictionary=args.holdout_aruco_dictionary,
    )
    ml1.run(duration=args.duration)  # blocks for args.duration -- same lifecycle `start` uses
    static_sightings = rpt.by_type(rpt.load_jsonl(static_path), "tag_sighting")
    if len(static_sightings) < 2:
        raise SystemExit(
            f"bench calibrate-referee: only {len(static_sightings)} tag_sighting reading(s) logged -- "
            f"was tag {args.holdout_tag} in view the whole {args.duration:.0f}s?"
        )
    noise = static_hold_noise_floor(static_sightings)
    print(
        f"  noise floor: pos 3-sigma +/-{noise['pos_3sigma_radius_m'] * 1000:.1f}mm "
        f"(std x={noise['pos_std_m']['x'] * 1000:.2f} y={noise['pos_std_m']['y'] * 1000:.2f} "
        f"z={noise['pos_std_m']['z'] * 1000:.2f}mm), rot 3-sigma +/-{noise['rot_3sigma_deg']:.2f}deg "
        f"(n={noise['n_readings']})"
    )

    print("\nbench calibrate-referee: STEP 2/3 -- bias check")
    input(
        f"  Slide the robot a measured distance along a straightedge (recommend >=1.0m). "
        f"Press Enter once it's stopped and tag {args.holdout_tag} is back in view..."
    )
    print(f"  Recording {args.bias_capture_duration:.0f}s at the new position...")
    bias_path = RUNS_DIR / f"calibrate-referee__bias-check__{ts}.jsonl"
    ml2 = MetricsLogger(
        bias_path,
        None,
        holdout_tag=args.holdout_tag,
        holdout_marker_length_m=args.holdout_marker_length_m,
        holdout_aruco_dictionary=args.holdout_aruco_dictionary,
    )
    ml2.run(duration=args.bias_capture_duration)
    after_sightings = rpt.by_type(rpt.load_jsonl(bias_path), "tag_sighting")
    if not after_sightings:
        raise SystemExit(
            f"bench calibrate-referee: no tag_sighting readings at the new position -- "
            f"was tag {args.holdout_tag} in view?"
        )
    before_window = static_sightings[-args.holdout_window :]
    after_window = after_sightings[-args.holdout_window :]

    taped_mm: float | None = None
    while taped_mm is None:
        raw = input("  Enter the tape-measured slide distance in millimeters: ").strip()
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError
            taped_mm = value
        except ValueError:
            print("  not a positive number -- try again")

    bias = bias_check(before_window, after_window, taped_mm)
    print(
        f"  measured {bias['measured_mm']:.1f}mm vs taped {bias['taped_mm']:.1f}mm -> "
        f"scale bias {bias['scale_error_pct']:+.2f}%"
    )

    print("\nbench calibrate-referee: STEP 3/3 -- writing referee-budget.json")
    budget = {
        "holdout_tag": args.holdout_tag,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "noise_floor": noise,
        "bias": bias,
        "static_hold_log": str(static_path),
        "bias_check_log": str(bias_path),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    REFEREE_BUDGET_PATH.write_text(json.dumps(budget, indent=2))
    n_rows = regenerate_report()
    print(f"bench calibrate-referee: wrote {REFEREE_BUDGET_PATH.relative_to(TRIAL_DIR)}")
    print(f"bench calibrate-referee: {format_referee_budget_line(budget)}")
    print(
        f"bench calibrate-referee: regenerated {RESULTS_MD.relative_to(TRIAL_DIR)} "
        f"({n_rows} run row(s)) -- footer now carries this calibration"
    )


def _calibrate_referee_selftest() -> None:
    """No camera, no dimos, no robot: writes two fixture JSONL files (a
    static-hold burst + a before/after slide) into out/fixtures/ with
    constructed, independently-derived truth baked in, runs them through
    static_hold_noise_floor / bias_check exactly as cmd_calibrate_referee
    does, and asserts the computed numbers match. Also exercises STEP 3's
    footer render against a temp results dir (never the real
    trial/results/) -- same "no real output" convention as
    holdout_overlay.py's own --selftest.
    `bench.py calibrate-referee --selftest`."""
    fixtures_dir = OUT_DIR / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    tag = 42

    # -- STEP 1 fixture: static-hold burst, 11 readings, deliberately NOT all
    # identical (real PnP jitter, not a perfectly frozen fixture). Position:
    # dx alternates -30mm/+30mm around a fixed point with one exact 0mm
    # thrown in (sum == 0, so pstdev is derivable independently by hand).
    # Rotation: 6 of 11 readings sit at identity, 5 at an exact 12deg yaw --
    # identity is a strict majority (6 > 11/2), so _median_pose's
    # component-wise median lands on identity exactly, making each reading's
    # angular deviation exactly 0deg (identity readings) or 12deg (yaw
    # readings) by construction -- see the module's own doc comment on
    # static_hold_noise_floor for the formula this checks.
    dx_mm = [-30.0, -30.0, -30.0, -30.0, -30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 0.0]
    assert sum(dx_mm) == 0.0, "fixture construction bug: dx_mm must sum to zero"
    identity_q = [0.0, 0.0, 0.0, 1.0]
    yaw12_q = [0.0, 0.0, math.sin(math.radians(6.0)), math.cos(math.radians(6.0))]
    rotations = [identity_q] * 6 + [yaw12_q] * 5
    static_records = [
        {
            "type": "tag_sighting",
            "ts": float(i),
            "translation": [0.5 + dx / 1000.0, 0.0, 1.2],
            "rotation": rotations[i],
            "marker_id": tag,
            "reprojection_error_px": 0.4,
        }
        for i, dx in enumerate(dx_mm)
    ]
    static_path = fixtures_dir / "calibrate-referee_static-hold.jsonl"
    static_path.write_text("\n".join(json.dumps(r) for r in static_records) + "\n")

    static_sightings = rpt.by_type(rpt.load_jsonl(static_path), "tag_sighting")
    noise = static_hold_noise_floor(static_sightings)

    expected_std_x_m = statistics.pstdev([dx / 1000.0 for dx in dx_mm])
    expected_radius_m = 3.0 * expected_std_x_m  # y, z stds are exactly 0 in this fixture
    expected_rot_std_deg = statistics.pstdev([0.0] * 6 + [12.0] * 5)

    assert noise["n_readings"] == 11, noise
    assert noise["pos_std_m"]["x"] == round(expected_std_x_m, 5), (noise, expected_std_x_m)
    assert noise["pos_std_m"]["y"] == 0.0, noise
    assert noise["pos_std_m"]["z"] == 0.0, noise
    assert noise["pos_3sigma_radius_m"] == round(expected_radius_m, 5), (noise, expected_radius_m)
    assert noise["rot_std_deg"] == round(expected_rot_std_deg, 3), (noise, expected_rot_std_deg)
    assert noise["rot_3sigma_deg"] == round(3.0 * expected_rot_std_deg, 3), noise
    print(
        "calibrate-referee --selftest: STEP 1 (static hold) OK -- "
        f"pos 3-sigma {noise['pos_3sigma_radius_m'] * 1000:.2f}mm "
        f"(expected {expected_radius_m * 1000:.2f}mm), "
        f"rot 3-sigma {noise['rot_3sigma_deg']:.2f}deg "
        f"(expected {3.0 * expected_rot_std_deg:.2f}deg)"
    )

    # -- STEP 2 fixture: known slide. Before cluster at x=0.500m, after
    # cluster at x=1.497m -- a constructed, exact 997mm displacement --
    # against an operator-entered tape measurement of 1000mm, for an exact
    # -0.30% scale bias.
    def _cluster(ts0: float, x: float) -> list[dict]:
        return [
            {
                "type": "tag_sighting",
                "ts": ts0 + i * 0.1,
                "translation": [x, 0.0, 1.2],
                "rotation": identity_q,
                "marker_id": tag,
                "reprojection_error_px": 0.4,
            }
            for i in range(5)
        ]

    before_records = _cluster(0.0, 0.5)
    after_records = _cluster(60.0, 1.497)
    bias_path = fixtures_dir / "calibrate-referee_bias-check.jsonl"
    bias_path.write_text("\n".join(json.dumps(r) for r in before_records + after_records) + "\n")

    bias_sightings = rpt.by_type(rpt.load_jsonl(bias_path), "tag_sighting")
    before_loaded = [r for r in bias_sightings if r["ts"] < 60.0]
    after_loaded = [r for r in bias_sightings if r["ts"] >= 60.0]
    taped_mm = 1000.0
    bias = bias_check(before_loaded, after_loaded, taped_mm)

    assert bias["n_before"] == 5 and bias["n_after"] == 5, bias
    assert bias["measured_mm"] == 997.0, bias
    assert bias["scale_error_pct"] == -0.3, bias
    print(
        "calibrate-referee --selftest: STEP 2 (bias check) OK -- "
        f"measured {bias['measured_mm']:.1f}mm vs taped {bias['taped_mm']:.1f}mm -> "
        f"scale bias {bias['scale_error_pct']:+.2f}% (expected -0.30%)"
    )

    # -- STEP 3: budget write + footer render, against a temp path -- never
    # the real trial/results/referee-budget.json.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_budget_path = Path(tmp) / "referee-budget.json"
        budget = {
            "holdout_tag": tag,
            "timestamp": "2026-07-15T12:00:00",
            "noise_floor": noise,
            "bias": bias,
            "static_hold_log": str(static_path),
            "bias_check_log": str(bias_path),
        }
        tmp_budget_path.write_text(json.dumps(budget, indent=2))
        line = format_referee_budget_line(json.loads(tmp_budget_path.read_text()))
        expected_radius_mm = noise["pos_3sigma_radius_m"] * 1000.0
        expected_line = (
            f"start/end tag referee: +/-{expected_radius_mm:.1f}mm (3-sigma), "
            f"scale bias {bias['scale_error_pct']:+.2f}% (calibrated 2026-07-15)"
        )
        assert line == expected_line, (line, expected_line)
        print(f"calibrate-referee --selftest: STEP 3 (footer render) OK -- {line}")

    print("calibrate-referee --selftest: PASS")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p_start = sub.add_parser("start", help="begin a logged benchmark run")
    p_start.add_argument("--mode", required=True, choices=MODES)
    p_start.add_argument(
        "--route",
        required=True,
        help="route/card name, e.g. drift-recovery, kidnapped-robot, outdoor-sightline, "
        "long-corridor, glass-lobby, head-to-head, dynamic-clutter (see ../benchmarks.md)",
    )
    p_start.add_argument("--notes", default="", help="free-text note, stored with the run")
    p_start.add_argument(
        "--dimos-log",
        default=None,
        help="path to the run's main.jsonl (enables gate-rejected / relocalize log capture, "
        "same flag as metrics_logger.py)",
    )
    p_start.add_argument(
        "--duration", type=float, default=None, help="seconds to run (default: until Ctrl+C/stop)"
    )
    p_start.add_argument(
        "--holdout-tag",
        type=int,
        default=None,
        help="marker ID excluded from every localization map -- an automated, "
        "map-independent PnP referee for start/end pose (see benchmark-spec.md §4). "
        "Omit to run without the holdout referee, as before.",
    )
    p_start.add_argument(
        "--holdout-window",
        type=int,
        default=HOLDOUT_DEFAULT_WINDOW,
        help=f"number of holdout-tag sightings to median at run-start and run-end "
        f"for the referee's measured delta (default {HOLDOUT_DEFAULT_WINDOW})",
    )
    p_start.add_argument(
        "--holdout-marker-length-m",
        type=float,
        default=0.10,
        help="physical edge length (m) of the holdout tag, independent of the mode "
        "under test's own marker_length_m (default 0.10, matches the standard 100mm survey tags)",
    )
    p_start.add_argument(
        "--holdout-aruco-dictionary",
        default="DICT_APRILTAG_36h11",
        help="ArUco/AprilTag dictionary the holdout tag was printed from "
        "(default DICT_APRILTAG_36h11, matches every MarkerLocalizationModule default)",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="end the active run, score it, append a row")
    p_stop.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the run to finish scoring")
    p_stop.set_defaults(func=cmd_stop)

    p_report = sub.add_parser("report", help="regenerate RESULTS.md from benchmarks.csv")
    p_report.set_defaults(func=cmd_report)

    p_calib = sub.add_parser(
        "calibrate-referee",
        help="guided noise-floor + bias calibration for the start/end tag referee",
    )
    p_calib.add_argument(
        "--holdout-tag",
        type=int,
        default=None,
        help="marker ID of the start/end tag being calibrated (must stay excluded from every "
        "localization map). Required unless --selftest.",
    )
    p_calib.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_CALIBRATION_DURATION_S,
        help=f"seconds to hold still for the STEP 1 noise-floor capture (default "
        f"{DEFAULT_CALIBRATION_DURATION_S:.0f})",
    )
    p_calib.add_argument(
        "--bias-capture-duration",
        type=float,
        default=DEFAULT_BIAS_CAPTURE_DURATION_S,
        help=f"seconds to capture at the new position for STEP 2's bias check (default "
        f"{DEFAULT_BIAS_CAPTURE_DURATION_S:.0f})",
    )
    p_calib.add_argument(
        "--holdout-window",
        type=int,
        default=HOLDOUT_DEFAULT_WINDOW,
        help=f"number of sightings to median at each end of the slide (default "
        f"{HOLDOUT_DEFAULT_WINDOW}, same semantics as `start`'s --holdout-window)",
    )
    p_calib.add_argument(
        "--holdout-marker-length-m",
        type=float,
        default=0.10,
        help="physical edge length (m) of the start/end tag (default 0.10)",
    )
    p_calib.add_argument(
        "--holdout-aruco-dictionary",
        default="DICT_APRILTAG_36h11",
        help="ArUco/AprilTag dictionary the start/end tag was printed from "
        "(default DICT_APRILTAG_36h11)",
    )
    p_calib.add_argument(
        "--selftest",
        action="store_true",
        help="run the whole flow on constructed fixture data (no camera/dimos needed); "
        "asserts computed noise floor + bias match known constructed truth",
    )
    p_calib.set_defaults(func=cmd_calibrate_referee)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
