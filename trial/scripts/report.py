#!/usr/bin/env python3
# Post-run report generator for metrics_logger.py output.
#
# Reads the JSONL a metrics_logger.py run produced and computes:
#   - ATE (RMSE) vs a chosen reference trajectory, if --reference-log is given
#     (e.g. a separate run's log where the reference module -- typically the
#     on-robot lidar RelocalizationModule -- was the one publishing
#     world->map; pass its corrected_pose stream here as "silver truth").
#     Also reports raw-odom-vs-reference for the same before/after story the
#     synthetic harness (demo/) already tells.
#   - detection-rate stats: odom ticks, corrected-pose coverage, accepted vs
#     rejected correction counts, mean tags-seen-when-rejected.
#   - a correction-magnitude timeline plot (skipped, with a note, if zero
#     corrections were published -- e.g. a scene with no tags in view).
#   - an odom-vs-corrected XY trajectory plot.
#
# Zero correction events (a tagless replay scene, for instance) is a valid,
# expected input: the report still generates cleanly and says so explicitly
# rather than silently emitting an all-zero/blank plot.
#
# Usage:
#   uv run python trial/scripts/report.py --log out/run1.jsonl --out-dir out/report1
#   uv run python trial/scripts/report.py --log out/camera.jsonl \
#       --reference-log out/lidar.jsonl --out-dir out/report2
#
# No dimos import -- runs in any Python with numpy + matplotlib (the dimos
# venv has both; `cd dimos && uv run python ../trial/scripts/report.py ...`
# is the documented one-liner so there's exactly one venv to remember).

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def by_type(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r.get("type") == kind]


def as_xyz(r: dict) -> tuple[float, float, float]:
    x, y, z = r["translation"]
    return (float(x), float(y), float(z))


_REJECT_RE = re.compile(r"gate rejected \((\d+) tags? seen\)")


def reject_tag_counts(log_events: list[dict]) -> list[int]:
    counts = []
    for r in log_events:
        m = _REJECT_RE.search(str(r.get("event", "")))
        if m:
            counts.append(int(m.group(1)))
    return counts


def nearest_match(
    ts: float, sorted_ref: list[tuple[float, float, float, float]], max_dt: float
) -> tuple[float, float, float] | None:
    """sorted_ref: list of (ts, x, y, z) sorted by ts. Binary-search-free linear
    scan is fine here -- these logs are, at most, tens of thousands of rows."""
    best = None
    best_dt = max_dt
    for rt, x, y, z in sorted_ref:
        dt = abs(rt - ts)
        if dt <= best_dt:
            best_dt = dt
            best = (x, y, z)
    return best


def compute_ate(
    primary: list[dict], reference: list[dict], max_dt: float = 1.0
) -> dict[str, Any]:
    if not primary:
        return {"ate_rmse_m": None, "n_matched": 0, "reason": "no samples in primary trajectory"}
    if not reference:
        return {"ate_rmse_m": None, "n_matched": 0, "reason": "no samples in reference trajectory"}
    ref_sorted = sorted((r["ts"], *as_xyz(r)) for r in reference)
    errors = []
    for r in primary:
        match = nearest_match(r["ts"], ref_sorted, max_dt)
        if match is None:
            continue
        px, py, pz = as_xyz(r)
        rx, ry, rz = match
        errors.append(math.dist((px, py, pz), (rx, ry, rz)))
    if not errors:
        return {
            "ate_rmse_m": None,
            "n_matched": 0,
            "reason": f"no primary/reference samples within {max_dt}s of each other",
        }
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    return {
        "ate_rmse_m": round(rmse, 4),
        "n_matched": len(errors),
        "max_error_m": round(max(errors), 4),
        "mean_error_m": round(statistics.mean(errors), 4),
    }


def detection_stats(records: list[dict]) -> dict[str, Any]:
    odom = by_type(records, "odom_pose")
    corrected = by_type(records, "corrected_pose")
    new = by_type(records, "correction_new")
    hold = by_type(records, "correction_hold")
    log_events = by_type(records, "log_event")
    rejects = reject_tag_counts(log_events)

    n_odom = len(odom)
    coverage = (len(corrected) / n_odom) if n_odom else None
    accepted = len(new)
    total_attempts = accepted + len(rejects)
    accept_rate = (accepted / total_attempts) if total_attempts else None

    return {
        "odom_ticks": n_odom,
        "corrected_ticks": len(corrected),
        "corrected_pose_coverage": round(coverage, 4) if coverage is not None else None,
        "corrections_accepted": accepted,
        "corrections_held_unchanged": len(hold),
        "corrections_rejected": len(rejects),
        "rejected_mean_tags_seen": round(statistics.mean(rejects), 2) if rejects else None,
        "acceptance_rate": round(accept_rate, 4) if accept_rate is not None else None,
        "acceptance_rate_note": (
            "accepted / (accepted + rejected); rejected count only available if "
            "metrics_logger.py was run with --dimos-log"
        ),
    }


def correction_magnitude_plot(records: list[dict], out_path: Path) -> str | None:
    new = by_type(records, "correction_new")
    points = [(r["ts"], r["magnitude_m"]) for r in new if r.get("magnitude_m") is not None]
    if not points:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points.sort()
    t0 = points[0][0]
    xs = [p[0] - t0 for p in points]
    ys = [p[1] for p in points]

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.stem(xs, ys, basefmt=" ")
    ax.set_xlabel("time since first correction (s)")
    ax.set_ylabel("correction jump magnitude (m)")
    ax.set_title(f"world->map correction magnitude over time (n={len(points)})")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return str(out_path)


def trajectory_plot(records: list[dict], out_path: Path) -> str | None:
    odom = by_type(records, "odom_pose")
    corrected = by_type(records, "corrected_pose")
    if not odom and not corrected:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 7))
    if odom:
        xs = [as_xyz(r)[0] for r in odom]
        ys = [as_xyz(r)[1] for r in odom]
        ax.plot(xs, ys, color="#888", linewidth=1, label=f"odom (world->base_link), n={len(odom)}")
    if corrected:
        xs = [as_xyz(r)[0] for r in corrected]
        ys = [as_xyz(r)[1] for r in corrected]
        ax.plot(
            xs, ys, color="#1a7f37", linewidth=1.5, label=f"corrected (map->base_link), n={len(corrected)}"
        )
    else:
        ax.text(
            0.5,
            0.02,
            "no corrected_pose samples in this run (zero accepted corrections)",
            transform=ax.transAxes,
            ha="center",
            fontsize=9,
            color="#a33",
        )
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title("trajectory: raw odom vs corrected")
    ax.axis("equal")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return str(out_path)


def build_report(
    log_path: Path,
    out_dir: Path,
    reference_log_path: Path | None,
    max_dt: float,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = load_jsonl(log_path)
    if not records:
        raise SystemExit(f"{log_path}: no records (empty or missing log)")

    det = detection_stats(records)

    ate: dict[str, Any]
    ate_raw: dict[str, Any]
    if reference_log_path is not None:
        ref_records = load_jsonl(reference_log_path)
        ref_corrected = by_type(ref_records, "corrected_pose") or by_type(ref_records, "odom_pose")
        primary_corrected = by_type(records, "corrected_pose")
        primary_odom = by_type(records, "odom_pose")
        ate = compute_ate(primary_corrected, ref_corrected, max_dt=max_dt)
        ate_raw = compute_ate(primary_odom, ref_corrected, max_dt=max_dt)
    else:
        ate = {"ate_rmse_m": None, "n_matched": 0, "reason": "no --reference-log given"}
        ate_raw = {"ate_rmse_m": None, "n_matched": 0, "reason": "no --reference-log given"}

    mag_plot = correction_magnitude_plot(records, out_dir / "correction_magnitude.png")
    traj_plot = trajectory_plot(records, out_dir / "trajectory.png")

    metrics = {
        "source_log": str(log_path),
        "reference_log": str(reference_log_path) if reference_log_path else None,
        "detection": det,
        "ate_corrected_vs_reference": ate,
        "ate_raw_odom_vs_reference": ate_raw,
        "plots": {
            "correction_magnitude": mag_plot,
            "trajectory": traj_plot,
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (out_dir / "report.md").write_text(render_markdown(metrics))
    return metrics


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_markdown(m: dict[str, Any]) -> str:
    det = m["detection"]
    ate = m["ate_corrected_vs_reference"]
    ate_raw = m["ate_raw_odom_vs_reference"]
    coverage_str = _pct(det["corrected_pose_coverage"])
    accept_str = _pct(det["acceptance_rate"])
    lines = [
        "# Localization metrics report",
        "",
        f"Source: `{m['source_log']}`",
        f"Reference: `{m['reference_log']}`" if m["reference_log"] else "Reference: none given",
        "",
        "## Detection / correction stats",
        f"- odom ticks logged: {det['odom_ticks']}",
        f"- ticks with a resolvable corrected pose: {det['corrected_ticks']} ({coverage_str})",
        f"- corrections accepted (new): {det['corrections_accepted']}",
        f"- corrections held (unchanged republish): {det['corrections_held_unchanged']}",
        f"- corrections rejected (from log tail): {det['corrections_rejected']}"
        + (
            f", mean tags seen when rejected: {det['rejected_mean_tags_seen']}"
            if det["rejected_mean_tags_seen"] is not None
            else ""
        ),
        f"- acceptance rate: {accept_str}",
        "",
        "## ATE vs reference",
        f"- corrected pose: {ate.get('ate_rmse_m')} m RMSE"
        + (f" (n={ate.get('n_matched')})" if ate.get("n_matched") else f" -- {ate.get('reason')}"),
        f"- raw odom: {ate_raw.get('ate_rmse_m')} m RMSE"
        + (
            f" (n={ate_raw.get('n_matched')})"
            if ate_raw.get("n_matched")
            else f" -- {ate_raw.get('reason')}"
        ),
        "",
        "## Plots",
        f"- correction magnitude timeline: {m['plots']['correction_magnitude'] or 'skipped -- zero corrections in this run'}",
        f"- trajectory: {m['plots']['trajectory'] or 'skipped -- no pose samples'}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", required=True, type=Path, help="metrics_logger.py JSONL output")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument(
        "--reference-log",
        type=Path,
        default=None,
        help="another metrics_logger.py JSONL run to use as silver-truth reference "
        "(e.g. a RelocalizationModule/lidar run's corrected_pose stream)",
    )
    ap.add_argument(
        "--max-dt", type=float, default=1.0, help="max seconds between matched reference samples"
    )
    args = ap.parse_args()

    metrics = build_report(args.log, args.out_dir, args.reference_log, args.max_dt)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
