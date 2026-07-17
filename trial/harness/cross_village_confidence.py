#!/usr/bin/env python3
"""Cross-VILLAGE confidence pooling: the four-village risk/coverage picture.

Sibling of cross_recording_confidence.py (which pools five recordings incl. the
go2 office). This one answers the village-deployment question on its own data:
pooling ONLY the four hk_village RANSAC result sets (FULL denominators), what
risk does one fitness gate buy, where is the loosest gate for 5% / 2% risk (raw
counts, not just rates), how well does fitness rank success (AUROC pooled and
per village), and does the live 50k-point query gate (n_pts) stratify success?

The loader, sentinel convention (a no-answer row is a never-accepted failure),
the 50k constant, and the figure palette are imported from the sibling so the
conventions stay single-sourced.

Truth: PGO silver, per-recording-qualified (~6 cm run-to-run floor, WORKSPACE
§7). Rung: replay (real recorded sensor data, offline). success = err_t < 1.0 m
AND err_r < 15.0 deg (run_bench.py), read from the result rows on disk
(out/ is gitignored by design; this script prints everything a reader needs).

Provenance note (checked, not assumed): the summaries' git_rev fields stamp
PREP time — run_bench.py copies prep.git_rev_* from the pkl — NOT the code
that executed the benchmark, so mixed labels across villages are expected
when pkls were prepared in different sessions. Where labels differed at
writing time (dimos a6be7e42e vs fadb41e70), the dimos diff was verified
inert for the ransac config: FiducialPrior is purely additive and
default-off; with both prior flags off, _try_relocalize takes the identical
plain _relocalize path. Result files are rerun IN PLACE by other lanes
(observed live: hk_village1 rewritten mid-analysis), so the report prints
each input's sha256 — two reports are comparable only if the shas match.

Run: cd dimos && uv run python ../trial/harness/cross_village_confidence.py
Deterministic: pure analysis of the on-disk result JSONs — no seeds, no RNG.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
from confidence import auroc, operating_point, risk_coverage_curve  # noqa: E402
from cross_recording_confidence import (  # noqa: E402
    GRID,
    INK,
    MIN_LOCAL_POINTS,
    NO_ANSWER_CONF,
    PALETTE,
    RESULTS,
    STAMP,
    SURFACE,
    git_rev,
    load,
)

VILLAGES = ["hk_village1", "hk_village3", "hk_village5", "hk_village6"]
CANON_GATES = (0.45, 0.60)  # code default / docs value
RISK_TARGETS = (0.05, 0.02)
FIGURES = HARNESS.parent / "results" / "figures"
DIMOS = HARNESS.parents[1] / "dimos"
SERIES = PALETTE[0]  # one pooled series -> categorical slot 1, no legend needed
MUTED = "#8a897f"  # reference-line gray: recessive, not a status color


def main() -> int:
    data = {rec: load(rec) for rec in VILLAGES}

    # ---- pooled arrays + self-checks: pooled == sum of parts, rows == stored summary
    conf_all = np.concatenate([data[r][0] for r in VILLAGES])
    succ_all = np.concatenate([data[r][1] for r in VILLAGES])
    n_pool, s_pool = int(conf_all.size), int(succ_all.sum())
    assert n_pool == sum(len(data[r][2]) for r in VILLAGES)
    assert s_pool == sum(int(data[r][1].sum()) for r in VILLAGES)
    summaries = {}
    for rec in VILLAGES:
        stored = json.loads((RESULTS / f"{rec}.ransac.json").read_text())["summary"]
        assert len(data[rec][2]) == stored["n_sections"], rec
        assert abs(data[rec][1].mean() - stored["success_rate_all"]) < 1e-9, rec
        summaries[rec] = stored

    print("=" * 78)
    print("CROSS-VILLAGE CONFIDENCE — four hk_village recordings, one pooled gate")
    print(STAMP)
    print("=" * 78)
    print("\nper-village provenance (git revs are PREP-time stamps; see module")
    print("docstring — sha256 pins the exact bytes analyzed, results are rerun in place):")
    for rec in VILLAGES:
        s = summaries[rec]
        path = RESULTS / f"{rec}.ransac.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"  {rec:12s} dimos {s['git_rev_dimos']}  trial {s['git_rev_trial']}  "
              f"run_unix {s['unix']:.0f}  sha256:{digest}")

    no_answer_total = sum(data[r][3] for r in VILLAGES)
    print(f"\npooled N = {n_pool} sections "
          f"({' + '.join(str(len(data[r][2])) for r in VILLAGES)})  |  "
          f"no-answer rows: {no_answer_total}")
    print(f"pooled successes = {s_pool}  ->  success rate {s_pool / n_pool:.4f}")
    print("  per village (composition check — hk_village3 is "
          f"{len(data['hk_village3'][2]) / n_pool:.1%} of the pool):")
    for rec in VILLAGES:
        succ = data[rec][1]
        print(f"    {rec:12s} {int(succ.sum()):3d}/{succ.size:3d}  ({succ.mean():.4f})")

    # ---- risk at the canonical gates, pooled (raw counts) + per village
    print("\n-- risk at canonical gates (accept iff fitness >= gate), POOLED --")
    for g in CANON_GATES:
        p = operating_point(conf_all, succ_all, g)
        chk = sum(operating_point(*data[r][:2], g).n_false_accepts for r in VILLAGES)
        assert chk == p.n_false_accepts  # pooled == sum of per-village, every gate
        print(f"  gate {g:.2f}: accepted {p.n_accepted:3d}/{p.n_total}  "
              f"false accepts {p.n_false_accepts:2d}  risk {p.risk:.4f}  "
              f"coverage {p.coverage:.4f}  missed-correct {p.n_missed}")
    print("   per village (false accepts / accepted):")
    for rec in VILLAGES:
        cells = []
        for g in CANON_GATES:
            p = operating_point(*data[rec][:2], g)
            cells.append(f"g{g:.2f}: {p.n_false_accepts:2d}/{p.n_accepted:3d}")
        print(f"     {rec:12s} " + "   ".join(cells))

    # ---- loosest pooled gates meeting the risk targets (raw counts)
    pooled_curve = risk_coverage_curve(conf_all, succ_all)
    best = {}
    print("\n-- loosest pooled gate meeting a risk target --")
    for t in RISK_TARGETS:
        p = pooled_curve.best_threshold(t)
        best[t] = p
        if p is None:
            print(f"  risk <= {t:.2f}: UNREACHABLE at any gate on this data")
        else:
            print(f"  risk <= {t:.2f}: gate {p.threshold:.4f}  ->  risk {p.risk:.4f} "
                  f"({p.n_false_accepts}/{p.n_accepted} accepted of {p.n_total}), "
                  f"coverage {p.coverage:.4f}, missed-correct {p.n_missed}")

    # ---- AUROC pooled and per village
    print("\n-- AUROC (fitness as ranker of success) --")
    print(f"  pooled       {auroc(conf_all, succ_all):.4f}")
    for rec in VILLAGES:
        print(f"  {rec:12s} {auroc(*data[rec][:2]):.4f}")

    # ---- n_pts covariate: the live query gate (module.py min local points)
    print(f"\n-- success by live query gate (n_pts >= {MIN_LOCAL_POINTS}), POOLED --")
    tot = {True: [0, 0], False: [0, 0]}  # reached -> [n_success, n]
    for rec in VILLAGES:
        parts = {}
        for reached in (True, False):
            sub = [r for r in data[rec][2] if (r["n_pts"] >= MIN_LOCAL_POINTS) == reached]
            assert all(r["reached_gate"] == reached for r in sub)  # stored flag == recomputed
            s = sum(bool(r.get("success")) for r in sub)
            tot[reached][0] += s
            tot[reached][1] += len(sub)
            parts[reached] = (s, len(sub))
        print(f"  {rec:12s} >=50k: {parts[True][0]:3d}/{parts[True][1]:3d} "
              f"({parts[True][0] / parts[True][1]:.3f})   "
              f"<50k: {parts[False][0]:3d}/{parts[False][1]:3d} "
              f"({parts[False][0] / parts[False][1]:.3f})")
    assert tot[True][1] + tot[False][1] == n_pool
    print(f"  {'POOLED':12s} >=50k: {tot[True][0]:3d}/{tot[True][1]:3d} "
          f"({tot[True][0] / tot[True][1]:.4f})   "
          f"<50k: {tot[False][0]:3d}/{tot[False][1]:3d} "
          f"({tot[False][0] / tot[False][1]:.4f})")

    # ---- figure: ONE pooled risk-coverage curve with the operating points marked
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    cov = [0.0] + [p.coverage for p in pooled_curve.points]
    risk = [pooled_curve.points[0].risk] + [p.risk for p in pooled_curve.points]
    ax.step(cov, risk, where="post", color=SERIES, lw=2)

    # recessive reference lines for the risk targets (labels stacked to not collide)
    for t, va, dy in ((0.05, "bottom", 3), (0.02, "top", -3)):
        ax.axhline(t, color=MUTED, lw=1, ls=(0, (4, 3)))
        ax.annotate(f"risk target {t:.2f}", xy=(0.005, t), xytext=(0, dy),
                    textcoords="offset points", fontsize=7, color=MUTED, va=va)

    def dot(p):
        ax.scatter([p.coverage], [p.risk], s=55, color=SERIES, zorder=5,
                   edgecolor=SURFACE, linewidth=1.5, clip_on=False)

    # canonical gates nearly coincide (both catch zero failures) -> one shared label
    p045, p060 = (operating_point(conf_all, succ_all, g) for g in CANON_GATES)
    dot(p060)
    dot(p045)
    ax.annotate(f"gates 0.45 & 0.60: risk {p045.risk:.3f} / {p060.risk:.3f}\n"
                f"({p045.n_false_accepts}/{p045.n_accepted} and "
                f"{p060.n_false_accepts}/{p060.n_accepted} accepted wrong —\n"
                f"every failure clears both gates)",
                xy=(p045.coverage, p045.risk), xytext=(-8, 12),
                textcoords="offset points", fontsize=7.5, color=INK, ha="right")
    # loosest-gate labels stack in the empty mid band; leader lines from the
    # right text edge to each point take disjoint x-ranges (no crossing)
    leader = dict(arrowstyle="-", color=MUTED, lw=0.9, relpos=(1.0, 0.5),
                  shrinkA=4, shrinkB=4)
    for t, ty in ((0.05, 0.140), (0.02, 0.095)):
        p = best[t]
        if p is None:
            continue
        dot(p)
        ax.annotate(f"loosest gate for risk<={t:.2f}: {p.threshold:.2f}  "
                    f"({p.n_false_accepts}/{p.n_accepted}, cov {p.coverage:.2f})",
                    xy=(p.coverage, p.risk), xytext=(0.14, ty),
                    textcoords="data", fontsize=7.5, color=INK, ha="left",
                    arrowprops=leader)

    # the strictest-gate spike is real: the pool's TOP-fitness answer is a bust
    top = max(zip(conf_all, succ_all), key=lambda x: x[0])
    if not top[1]:
        ax.annotate(f"highest-fitness section of all {n_pool}\n"
                    f"is a failure (fitness {top[0]:.3f})",
                    xy=(1.0 / n_pool, 1.0), xytext=(14, -8),
                    textcoords="offset points", fontsize=7.5, color=INK)

    ax.set_xlabel("coverage — fraction of the 192 sections the gate accepts", color=INK)
    ax.set_ylabel("risk — fraction of ACCEPTED answers that are wrong", color=INK)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(bottom=0)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_title("Relocalization fitness gate — POOLED risk-coverage, four villages\n"
                 f"(N={n_pool} sections, full denominators; "
                 f"AUROC {auroc(conf_all, succ_all):.2f})",
                 fontsize=11, color=INK)
    fig.text(0.01, 0.01,
             f"{STAMP} · dimos {git_rev(DIMOS)} trial {git_rev(HARNESS)} · "
             f"{Path(sys.argv[0]).name}",
             fontsize=6, alpha=0.7, color=INK)
    out = FIGURES / "confidence_cross_village.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print(f"\nfigure: {out}")
    print(f"stamp: {STAMP}")
    print(f"git: dimos {git_rev(DIMOS)}, trial {git_rev(HARNESS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
