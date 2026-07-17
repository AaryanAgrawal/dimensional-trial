#!/usr/bin/env python3
"""Cross-recording confidence pooling: does ONE fitness gate transfer across environments?

Pools the five full-denominator RANSAC result sets (4 villages + go2 office) and
asks the deployment question a single-recording curve cannot: if dimos ships one
fitness_threshold for every site, what risk does it actually buy? Denominators
are FULL — every benchmark section counts, including any attempt that produced
no answer (counted as never-accepted, success=False; n_no_answer is printed so
zero is a checked claim, not an assumption).

Truth: PGO silver, per-recording-qualified (~6 cm run-to-run floor, WORKSPACE §7).
Rung: replay (real recorded sensor data, offline). success = err_t < 1.0 m AND
err_r < 15.0 deg (run_bench.py), read from the committed result rows.

Outputs: stdout report (the table view for the figure) +
trial/results/figures/confidence_cross_recording.png (per-recording
risk-coverage curves on one axes).

Run: cd dimos && uv run python ../trial/harness/cross_recording_confidence.py
Deterministic: pure analysis of the committed result JSONs — no seeds, no RNG.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
from confidence import auroc, operating_point, risk_coverage_curve  # noqa: E402

RESULTS = HARNESS / "out" / "results"
FIGURES = HARNESS.parent / "results" / "figures"
DIMOS = HARNESS.parents[1] / "dimos"
RECORDINGS = ["hk_village1", "hk_village3", "hk_village5", "hk_village6", "go2_hongkong_office"]
VILLAGES = RECORDINGS[:4]
CANON_GATES = (0.45, 0.60, 0.80)  # code default / docs value / round strict point
RISK_TARGETS = (0.05, 0.02)
MIN_LOCAL_POINTS = 50_000  # module.py live query gate (prep.py flags it as reached_gate)
NO_ANSWER_CONF = -1.0  # sentinel < any real fitness: a no-answer is never accepted
STAMP = "replay - truth: PGO silver per-recording-qualified - full denominators"

# Reference dataviz palette, categorical slots 1-5 (light mode), fixed order by
# recording — documented as passing CVD/normal-vision gates on adjacent pairs.
PALETTE = ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a"]
SURFACE, INK, GRID = "#fcfcfb", "#0b0b0b", "#e1e0d9"


def load(recording: str) -> tuple[np.ndarray, np.ndarray, list[dict], int]:
    """(confidence, success, raw rows, n_no_answer) with FULL denominators."""
    rows = json.loads((RESULTS / f"{recording}.ransac.json").read_text())["results"]
    conf, succ, n_no_answer = [], [], 0
    for r in rows:
        if r["status"] == "ok" and r.get("fitness") is not None:
            conf.append(float(r["fitness"]))
            succ.append(bool(r["success"]))
        else:  # crashed / no candidates: no published answer, counts in denominator
            conf.append(NO_ANSWER_CONF)
            succ.append(False)
            n_no_answer += 1
    return np.array(conf), np.array(succ), rows, n_no_answer


def git_rev(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    data = {rec: load(rec) for rec in RECORDINGS}

    # ---- pooled arrays + self-check: pooled counts must equal the per-recording sums
    conf_all = np.concatenate([data[r][0] for r in RECORDINGS])
    succ_all = np.concatenate([data[r][1] for r in RECORDINGS])
    n_pool, s_pool = int(conf_all.size), int(succ_all.sum())
    assert n_pool == sum(len(data[r][2]) for r in RECORDINGS)
    assert s_pool == sum(int(data[r][1].sum()) for r in RECORDINGS)
    for rec in RECORDINGS:  # rows must agree with the file's own stored summary
        stored = json.loads((RESULTS / f"{rec}.ransac.json").read_text())["summary"]
        assert len(data[rec][2]) == stored["n_sections"], rec
        assert abs(data[rec][1].mean() - stored["success_rate_all"]) < 1e-9, rec

    print("=" * 78)
    print("CROSS-RECORDING CONFIDENCE — one fitness gate across five environments")
    print(STAMP)
    print("=" * 78)
    no_answer_total = sum(data[r][3] for r in RECORDINGS)
    print(f"\npooled N = {n_pool} sections  ({' + '.join(str(len(data[r][2])) for r in RECORDINGS)})"
          f"  |  no-answer rows: {no_answer_total}")
    print(f"pooled successes = {s_pool}  ->  success rate {s_pool / n_pool:.4f}")

    # ---- risk at the canonical gates, pooled (raw counts) + per recording
    print("\n-- risk at canonical gates (accept iff fitness >= gate), POOLED --")
    for g in CANON_GATES:
        p = operating_point(conf_all, succ_all, g)
        chk = int(sum(operating_point(*data[r][:2], g).n_false_accepts for r in RECORDINGS))
        assert chk == p.n_false_accepts  # pooled == sum of per-recording, every gate
        print(f"  gate {g:.2f}: accepted {p.n_accepted:3d}/{p.n_total}  "
              f"false accepts {p.n_false_accepts:2d}  risk {p.risk:.4f}  "
              f"coverage {p.coverage:.4f}")
    print("   per recording (false accepts / accepted):")
    for rec in RECORDINGS:
        cells = []
        for g in CANON_GATES:
            p = operating_point(*data[rec][:2], g)
            cells.append(f"g{g:.2f}: {p.n_false_accepts:2d}/{p.n_accepted:3d}")
        print(f"     {rec:22s} " + "   ".join(cells))

    # ---- loosest gates meeting the risk targets, pooled
    pooled_curve = risk_coverage_curve(conf_all, succ_all)
    print("\n-- loosest pooled gate meeting a risk target --")
    for t in RISK_TARGETS:
        p = pooled_curve.best_threshold(t)
        if p is None:
            print(f"  risk <= {t:.2f}: UNREACHABLE at any gate on this data")
        else:
            print(f"  risk <= {t:.2f}: gate {p.threshold:.4f}  ->  risk {p.risk:.4f}  "
                  f"coverage {p.coverage:.4f} ({p.n_accepted}/{p.n_total}, "
                  f"{p.n_false_accepts} false)")

    # ---- AUROC pooled and per recording
    print("\n-- AUROC (fitness as ranker of success) --")
    print(f"  pooled                 {auroc(conf_all, succ_all):.4f}")
    for rec in RECORDINGS:
        print(f"  {rec:22s} {auroc(*data[rec][:2]):.4f}")

    # ---- per-recording failure-fitness ranges: the environment dependence
    print("\n-- failure fitness per recording (environment dependence of the score) --")
    print("   zero-risk gate = strictly above that recording's max failing fitness")
    for rec in RECORDINGS:
        conf, succ = data[rec][:2]
        f = np.sort(conf[(~succ) & (conf > NO_ANSWER_CONF)])
        if f.size == 0:
            print(f"  {rec:22s} 0 failures")
            continue
        busts = int((f >= 0.80).sum())
        print(f"  {rec:22s} {f.size:2d} failures  fitness {f[0]:.4f}-{f[-1]:.4f}  "
              f"(median {np.median(f):.4f})  confident busts >=0.80: {busts:2d}")
    vf = np.concatenate([data[r][0][(~data[r][1]) & (data[r][0] > NO_ANSWER_CONF)]
                         for r in VILLAGES])
    of = data["go2_hongkong_office"][0][
        (~data["go2_hongkong_office"][1])
        & (data["go2_hongkong_office"][0] > NO_ANSWER_CONF)]
    print(f"  villages pooled: {vf.size} failures, fitness {vf.min():.4f}-{vf.max():.4f}, "
          f"{int((vf >= 0.80).sum())} at >=0.80")
    print(f"  office:          {of.size} failures, fitness {of.min():.4f}-{of.max():.4f}, "
          f"{int((of >= 0.80).sum())} at >=0.80")
    print(f"  => a gate just above the office's worst bust ({of.max():.4f}) zeroes office "
          f"risk yet still admits {int((vf > of.max()).sum())} village failures; "
          f"zeroing village risk needs > {vf.max():.4f}. One number does not transfer.")

    # ---- gate-split by the live query gate (n_pts >= 50k)
    print(f"\n-- success by live query gate (n_pts >= {MIN_LOCAL_POINTS}) --")
    tot = {True: [0, 0], False: [0, 0]}  # reached -> [success, n]
    for rec in RECORDINGS:
        parts = {}
        for reached in (True, False):
            sub = [r for r in data[rec][2] if (r["n_pts"] >= MIN_LOCAL_POINTS) == reached]
            assert all(r["reached_gate"] == reached for r in sub)  # flag == recomputed
            s = sum(bool(r.get("success")) for r in sub)
            tot[reached][0] += s
            tot[reached][1] += len(sub)
            parts[reached] = (s, len(sub))
        print(f"  {rec:22s} >=50k: {parts[True][0]:3d}/{parts[True][1]:3d} "
              f"({parts[True][0] / parts[True][1]:.3f})   "
              f"<50k: {parts[False][0]:3d}/{parts[False][1]:3d} "
              f"({parts[False][0] / parts[False][1]:.3f})")
    print(f"  {'POOLED':22s} >=50k: {tot[True][0]:3d}/{tot[True][1]:3d} "
          f"({tot[True][0] / tot[True][1]:.4f})   "
          f"<50k: {tot[False][0]:3d}/{tot[False][1]:3d} "
          f"({tot[False][0] / tot[False][1]:.4f})")
    assert tot[True][1] + tot[False][1] == n_pool

    # ---- figure: per-recording risk-coverage curves, one axes
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for rec, color in zip(RECORDINGS, PALETTE):
        curve = risk_coverage_curve(*data[rec][:2])
        cov = [0.0] + [p.coverage for p in curve.points]
        risk = [curve.points[0].risk] + [p.risk for p in curve.points]
        n = data[rec][0].size
        ax.step(cov, risk, where="post", color=color, lw=2,
                label=f"{rec}  (N={n}, AUROC {auroc(*data[rec][:2]):.2f})")
    ax.set_xlabel("coverage — fraction of attempts the gate accepts", color=INK)
    ax.set_ylabel("risk — fraction of ACCEPTED answers that are wrong", color=INK)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(bottom=0)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_title("Relocalization fitness gate — risk-coverage per recording\n"
                 "(sweep the accept threshold; same score, five environments)",
                 fontsize=11, color=INK)
    ax.legend(fontsize=8, loc="upper center", framealpha=0.9, labelcolor=INK)
    fig.text(0.01, 0.01,
             f"{STAMP} · dimos {git_rev(DIMOS)} trial {git_rev(HARNESS)} · "
             f"{Path(sys.argv[0]).name}",
             fontsize=6, alpha=0.7, color=INK)
    out = FIGURES / "confidence_cross_recording.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print(f"\nfigure: {out}")
    print(f"stamp: {STAMP}")
    print(f"git: dimos {git_rev(DIMOS)}, trial {git_rev(HARNESS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
