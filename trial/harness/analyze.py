#!/usr/bin/env python3
"""Confidence-quality analysis + figures over run_bench.py results.

Answers, with data, the questions the trial exists for:
  1. Does the published fitness PREDICT correctness? (AUROC, risk-coverage)
  2. What accept threshold does the data support? (operating points, incl.
     the code's 0.45 and the docs' 0.6)
  3. Is fitness calibrated enough to read as a probability? (reliability, ECE)
  4. What covariates explain failures? (submap size / live-gate state)

Usage: uv run python ../trial/harness/analyze.py hk_village3.ransac [more results...]
Figures land in trial/results/figures/ (tracked); tables print + save JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))

from confidence import (  # noqa: E402
    auroc, expected_calibration_error, operating_point, reliability_bins,
    risk_coverage_curve)

RESULTS = HARNESS / "out" / "results"
FIGURES = HARNESS.parent / "results" / "figures"


def _load(name: str) -> dict:
    with open(RESULTS / f"{name}.json") as f:
        return json.load(f)


def analyze_one(name: str) -> dict:
    d = _load(name)
    ok = [r for r in d["results"] if r["status"] == "ok"]
    n_all = len(d["results"])
    conf = np.array([r["fitness"] for r in ok])
    succ = np.array([r["success"] for r in ok], dtype=bool)

    rc = risk_coverage_curve(conf, succ)
    out = {
        "name": name,
        "n_sections": n_all,
        "n_ok": len(ok),
        "success_rate_all": d["summary"]["success_rate_all"],
        "auroc": auroc(conf, succ),
        "aurc": rc.aurc,
        "ece_10bin": expected_calibration_error(conf, succ, n_bins=10),
        "op_code_gate_0.45": vars(operating_point(conf, succ, 0.45)),
        "op_docs_gate_0.60": vars(operating_point(conf, succ, 0.60)),
        "reliability": reliability_bins(conf, succ, n_bins=10),
        "risk_coverage": [vars(p) for p in rc.points],
        "truth": d["summary"]["truth"],
        "rung": d["summary"]["rung"],
        "git_rev_dimos": d["summary"]["git_rev_dimos"],
        "git_rev_trial": d["summary"]["git_rev_trial"],
    }
    for target in (0.05, 0.02, 0.0):
        p = rc.best_threshold(max_risk=target)
        out[f"best_gate_risk<={target}"] = vars(p) if p else None

    # Covariate: submap size (the live MIN_LOCAL_POINTS story)
    npts = np.array([r["n_pts"] for r in ok])
    gate = np.array([r["reached_gate"] for r in ok], dtype=bool)
    out["success_rate_gate_reached"] = float(succ[gate].mean()) if gate.any() else None
    out["success_rate_gate_missed"] = float(succ[~gate].mean()) if (~gate).any() else None
    out["median_npts_failures"] = float(np.median(npts[~succ])) if (~succ).any() else None
    out["median_npts_successes"] = float(np.median(npts[succ])) if succ.any() else None
    return out


def figures(analyses: list[dict], results: dict[str, dict]) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(parents=True, exist_ok=True)
    trial_rev = analyses[0]["git_rev_trial"]
    dimos_rev = analyses[0]["git_rev_dimos"]
    stamp = (f"replay · truth: PGO silver (±6cm floor) · dimos {dimos_rev} · "
             f"trial {trial_rev} · seeds=frame_idx")
    written = []

    # Fig 1: risk-coverage curves, all configs on one axes
    fig, ax = plt.subplots(figsize=(7, 5))
    for a in analyses:
        pts = a["risk_coverage"]
        ax.plot([p["coverage"] for p in pts], [p["risk"] for p in pts],
                marker=".", label=f"{a['name']} (AUROC {a['auroc']:.2f})")
        for gate, mark in (("op_code_gate_0.45", "o"), ("op_docs_gate_0.60", "s")):
            g = a[gate]
            ax.plot(g["coverage"], g["risk"], mark, ms=10, mfc="none")
    ax.set_xlabel("coverage (fraction of attempts accepted)")
    ax.set_ylabel("risk (fraction of ACCEPTED answers wrong >1m/15°)")
    ax.set_title("Relocalization confidence: risk vs coverage\n"
                 "(o = code gate 0.45, □ = docs gate 0.60)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.text(0.01, 0.01, stamp, fontsize=6, alpha=0.7)
    p = FIGURES / "confidence_risk_coverage.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    written.append(p)

    # Fig 2: fitness vs translation error scatter (the raw truth of the matter)
    fig, ax = plt.subplots(figsize=(7, 5))
    for a in analyses:
        ok = [r for r in results[a["name"]]["results"] if r["status"] == "ok"]
        errs = np.array([r["err_t"] for r in ok])
        fit = np.array([r["fitness"] for r in ok])
        good = errs < 1.0
        ax.scatter(fit[good], np.clip(errs[good], 1e-3, None), s=18, alpha=0.7,
                   label=f"{a['name']} correct")
        ax.scatter(fit[~good], np.clip(errs[~good], 1e-3, None), s=30, alpha=0.9,
                   marker="x", label=f"{a['name']} WRONG")
    ax.axvline(0.45, ls="--", lw=1, alpha=0.6)
    ax.axvline(0.60, ls=":", lw=1, alpha=0.6)
    ax.axhline(1.0, ls="-", lw=0.5, alpha=0.4)
    ax.set_yscale("log")
    ax.set_xlabel("published fitness (stage-2 wall-ICP)")
    ax.set_ylabel("translation error vs PGO silver truth [m], log")
    ax.set_title("Every accepted-but-wrong answer lives up here →\n"
                 "gate lines: -- 0.45 (code)  ·· 0.60 (docs)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.text(0.01, 0.01, stamp, fontsize=6, alpha=0.7)
    p = FIGURES / "confidence_fitness_vs_error.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    written.append(p)

    # Fig 3: reliability diagram
    fig, ax = plt.subplots(figsize=(6, 5))
    for a in analyses:
        bins = a["reliability"]
        ax.plot([b["mean_confidence"] for b in bins],
                [b["empirical_success"] for b in bins],
                marker="o", label=f"{a['name']} (ECE {a['ece_10bin']:.2f})")
    ax.plot([0, 1], [0, 1], ls="--", lw=1, alpha=0.5, label="perfectly calibrated")
    ax.set_xlabel("mean published fitness in bin")
    ax.set_ylabel("empirical success rate in bin")
    ax.set_title("Is fitness a probability? (reliability diagram)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.text(0.01, 0.01, stamp, fontsize=6, alpha=0.7)
    p = FIGURES / "confidence_reliability.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    written.append(p)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", nargs="+", help="result basenames, e.g. hk_village3.ransac")
    ap.add_argument("--no-figures", action="store_true")
    a = ap.parse_args()

    analyses = [analyze_one(n) for n in a.names]
    results = {n: _load(n) for n in a.names}
    for an in analyses:
        print(f"\n=== {an['name']} ===")
        for k in ("n_sections", "n_ok", "success_rate_all", "auroc", "aurc",
                  "ece_10bin", "success_rate_gate_reached", "success_rate_gate_missed",
                  "median_npts_failures", "median_npts_successes"):
            print(f"  {k}: {an[k]}")
        for k in ("op_code_gate_0.45", "op_docs_gate_0.60", "best_gate_risk<=0.05",
                  "best_gate_risk<=0.02", "best_gate_risk<=0.0"):
            v = an[k]
            if v:
                print(f"  {k}: thr={v['threshold']:.3f} coverage={v['coverage']:.2f} "
                      f"risk={v['risk']:.3f} false_accepts={v['n_false_accepts']}/{v['n_accepted']}")
            else:
                print(f"  {k}: UNREACHABLE on this data")

    outp = RESULTS / ("analysis." + "_".join(a.names)[:120] + ".json")
    with open(outp, "w") as f:
        json.dump(analyses, f, indent=1)
    print(f"\nwrote {outp}")

    if not a.no_figures:
        for p in figures(analyses, results):
            print(f"figure: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
