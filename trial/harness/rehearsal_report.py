#!/usr/bin/env python3
"""Log-level scorecard for one replay rehearsal dir (rehearsal/, rehearsal2/, ...).

Parses ONLY the two logs a rehearsal produces —
    spy_world_map_fix.log  (every /world_map_fix Transform the visual module published)
    replay_run.log         (RelocalizationModule accepts/rejects + visual-module gate warns)
— and prints the judge-level facts the deeper live_fix_quality.py does not:
accept source split, every non-ransac win with its ransac neighbours, gate-reject
warn tally, traceback count, and fix scatter vs the accepted-ransac reference.

Reference caveat (same as live_fix_quality.py): the "error" is distance to the
nearest-in-time accepted source=ransac answer — scan-matching self-consistency
tracking ~1.2 m of drift, NOT ground truth; fixes published before the first
accept are scored against a constant back-extrapolation and flagged.

Gate-warn caveat: the module throttles gate warnings to one line per 5 s
(visual_relocalization_module.py _warn_throttled), so the warn tally is a
sampled LOWER BOUND on rejected frames, not a count.

Deterministic: pure log parsing, no RNG (SEED printed per house rule).

Run: uv run python ../trial/harness/rehearsal_report.py --rehearsal-dir out/rehearsal2
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent

SEED = 0  # no RNG anywhere below; printed per house rule

_FIX_RE = re.compile(r"fix#(\d+) ts=([\d.]+) world->map t=\[([-\d.,]+)\]")
_ACCEPT_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}\.\d+) .*relocalize: fitness=([\d.]+) time_cost=([\d.]+)s "
    r"n_pts=(\d+) reloc_t=\[([-\d.e, ]+)\] TF 'world' -> 'map' "
    r"published_t=\[([-\d.e, ]+)\] source=(\w+)"
)
_REJECT_RE = re.compile(r"relocalize rejected: fitness=([\d.]+)")
_GATE_RE = re.compile(r"gate rejected \((\d+) tags seen(?:, rejects=\{([^}]*)\})?\)")


def _hms_to_epoch(day: datetime, hh: str, mm: str, ss: str) -> float:
    return day.timestamp() + int(hh) * 3600 + int(mm) * 60 + float(ss)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rehearsal-dir", type=Path, default=HARNESS / "out" / "rehearsal2")
    ap.add_argument("--log-date", default="2026-07-18",
                    help="UTC date of replay_run.log HH:MM:SS timestamps")
    a = ap.parse_args()
    day = datetime.strptime(a.log_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    rev = {r: subprocess.run(["git", "-C", str(p), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
           for r, p in (("trial", HARNESS.parent), ("dimos", HARNESS.parent.parent / "dimos"))}
    print(f"rehearsal_report: SEED={SEED} (no RNG) git trial={rev['trial']} "
          f"dimos={rev['dimos']} dir={a.rehearsal_dir}")

    replay_txt = (a.rehearsal_dir / "replay_run.log").read_text()

    # ---- visual-module fixes (spy) + gate warns (replay log)
    fixes = [(float(m.group(2)), [float(v) for v in m.group(3).split(",")])
             for m in map(_FIX_RE.match, (a.rehearsal_dir / "spy_world_map_fix.log")
                          .read_text().splitlines()) if m]
    fix_ts = np.array([f[0] for f in fixes])
    fix_t = np.array([f[1] for f in fixes])
    gate_warns = _GATE_RE.findall(replay_txt)
    reasons: dict[str, int] = {}
    for _, reject_kv in gate_warns:
        for part in filter(None, (p.strip() for p in reject_kv.split(","))):
            k, v = part.split(":")
            k = k.strip().strip("'\"")
            reasons[k] = reasons.get(k, 0) + int(v)
    print(f"\nfixes published on /world_map_fix: {len(fixes)}")
    print(f"gate-reject warn lines (>=5s throttle -> sampled lower bound): "
          f"{len(gate_warns)}, summed per-frame reasons {reasons or '{}'}")

    # ---- judge accepts / rejects / crashes
    accepts = [m for m in map(_ACCEPT_RE.match, replay_txt.splitlines()) if m]
    acc_ts = np.array([_hms_to_epoch(day, m.group(1), m.group(2), m.group(3)) for m in accepts])
    acc_src = [m.group(9) for m in accepts]
    acc_fit = np.array([float(m.group(4)) for m in accepts])
    acc_t = np.array([[float(v) for v in m.group(8).split(",")] for m in accepts])
    n_below = len(_REJECT_RE.findall(replay_txt))
    n_tb = replay_txt.count("Traceback (most recent call last)")
    src_split = {s: acc_src.count(s) for s in sorted(set(acc_src))}
    print(f"\nrelocalize accepts: {len(accepts)}  source split: {src_split}")
    print(f"relocalize rejected (fitness<threshold): {n_below}")
    print(f"tracebacks in replay_run.log: {n_tb}")
    if accepts:
        print(f"accept fitness: min {acc_fit.min():.3f} max {acc_fit.max():.3f}")

    # ---- every non-ransac win, bracketed by its ransac neighbours
    ransac = [i for i, s in enumerate(acc_src) if s == "ransac"]
    for i, src in enumerate(acc_src):
        if src == "ransac":
            continue
        print(f"\nWIN source={src}: t={datetime.fromtimestamp(acc_ts[i], timezone.utc):%H:%M:%S} "
              f"fitness={acc_fit[i]:.3f} published_t={acc_t[i].round(3).tolist()} m")
        for j in ransac:
            dt = acc_ts[j] - acc_ts[i]
            if abs(dt) < 15.0:  # the 2-5 s reloc cadence -> ~3 neighbours each side
                print(f"  ransac {dt:+6.1f}s: fitness={acc_fit[j]:.3f} "
                      f"published_t={acc_t[j].round(3).tolist()} "
                      f"|delta_t|={np.linalg.norm(acc_t[i] - acc_t[j]):.3f} m")

    # ---- fix scatter vs accepted-ransac reference (nearest-in-time join)
    if len(fixes) and ransac:
        r_ts, r_t = acc_ts[ransac], acc_t[ransac]
        ji = np.abs(fix_ts[:, None] - r_ts[None, :]).argmin(axis=1)
        err = np.linalg.norm(fix_t - r_t[ji], axis=1)
        extrap = fix_ts < r_ts[0]
        print(f"\nfix |t - nearest accepted ransac published_t|: mean {err.mean():.2f} "
              f"median {np.median(err):.2f} p90 {np.percentile(err, 90):.2f} "
              f"max {err.max():.2f} m  (n={len(err)}; {int(extrap.sum())} pre-first-accept "
              f"fixes scored vs back-extrapolated reference)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
