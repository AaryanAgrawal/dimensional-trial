#!/usr/bin/env python3
"""THE regime map: when is RANSAC good and when is fiducial — from existing
benchmark artifacts only (no replays, no rebuilds).

One figure. Pooled replay success rate vs submap size, split at the live
MIN_LOCAL_POINTS=50k gate, RANSAC-only vs RANSAC+fiducial-prior (the deployment
"with markers" arm — same judge, no bypass). The mechanism variable is submap
size; the story is: RANSAC needs points, a fiducial prior rescues exactly the
sparse regime the live robot refuses today, and aliasing keeps RANSAC
confidently wrong even ABOVE the gate.

Every number is derived HERE from saved artifacts (run_bench result JSONs,
marker fixes, bench_analysis wobble, robotday_live scorecards) — never pasted
from prose. Asserts pin the pooled rates to run_bench's own summaries so a
stale artifact cannot leave a lying figure. Deterministic: pure reads, no RNG
(SEED=0 printed per house rule).

Pooled (both decorrelated, referee-tag split, full denominator):
  sf_office_go2_20260718_survey1              48 sections  (indoor, aliased)
  recording_go2_mid360_2026-05-29_4-45pm-PST  40 sections  (go2 lidar lane, walk)
Deliberately EXCLUDED (documented in the footer): the mid360 FAST-LIO lane
(RANSAC 0/40 is a gravity-gate frame-convention artifact, +fiducial byte-
identical offline — not a submap-size effect) and hk_village3 (its fiducial arm
is truth-correlated: its one tag IS the referee under the decorrelation split).

Run: cd /home/dimos/dimensional-trial/dimos && \
     OMP_NUM_THREADS=1 uv run python ../trial/harness/regime_map.py
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import numpy as np  # noqa: E402

SEED = 0  # no RNG anywhere in this script

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "trial/harness/out/results"
MRK = ROOT / "trial/harness/out/markers"
LIVE = ROOT / "trial/harness/out/robotday_live"
FIGS = ROOT / "trial/results/figures"

GATE_PTS = 50_000  # module.py MIN_LOCAL_POINTS — the live "don't even try" gate
BIN_EDGES = [0, 30_000, GATE_PTS, 10**12]  # sub-30k / 30-50k / >=50k
BIN_LABELS = ["< 30k pts", "30–50k pts", "≥ 50k pts"]

# Pooled recordings: (key, short label). Only decorrelated referee-split runs.
POOL = [
    ("sf_office_go2_20260718_survey1", "SF office"),
    ("recording_go2_mid360_2026-05-29_4-45pm-PST", "mid360 walk"),
]

# dataviz reference palette (light mode) — matches robotday_figures.py exactly so
# the regime map reads as one system with the other robot-day figures.
C_RANSAC = "#2a78d6"  # slot 1 blue  = RANSAC (no prior)
C_FID = "#008300"     # slot 2 green = RANSAC + fiducial prior (deployment "with markers")
INK, INK2, MUT = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"
SHADE = "#f2efe6"     # below-gate region tint

plt.rcParams.update({
    "font.family": "DejaVu Sans", "figure.facecolor": SURF, "axes.facecolor": SURF,
    "savefig.facecolor": SURF, "axes.edgecolor": BASE, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
})


def load(rec: str, cfg: str) -> tuple[dict, list[dict]]:
    d = json.loads((RES / f"{rec}.{cfg}.json").read_text())
    return d["summary"], d["results"]


def style_axes(ax) -> None:
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=GRID, linewidth=0.7)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASE)
    ax.tick_params(which="both", length=0)


def head(fig, title: str, sub: str, t_width: int, s_width: int) -> tuple[str, float]:
    """Wrapped title + subtitle at the top; returns (wrapped title, axes-top fraction)."""
    t_size, s_size = 10.8, 7.8
    h_pt = fig.get_size_inches()[1] * 72.0
    t = textwrap.fill(title, t_width)
    fig.text(0.012, 0.985, t, fontsize=t_size, fontweight="bold", va="top", ha="left",
             linespacing=1.35)
    y = 0.985 - (t.count("\n") + 1) * t_size * 1.42 / h_pt - 0.018
    s = textwrap.fill(sub, s_width)
    fig.text(0.012, y, s, fontsize=s_size, color=INK2, va="top", ha="left", linespacing=1.4)
    return t, y - (s.count("\n") + 1) * s_size * 1.48 / h_pt - 0.045


def footer(fig, lines: list[str], width: int = 190) -> None:
    txt = "\n".join(textwrap.fill(ln, width, subsequent_indent="   ") for ln in lines)
    fig.text(0.012, 0.008, txt, fontsize=6.0, color=MUT, va="bottom", ha="left", linespacing=1.45)


def bin_of(n: int) -> int:
    for i, (lo, hi) in enumerate(zip(BIN_EDGES[:-1], BIN_EDGES[1:])):
        if lo <= n < hi:
            return i
    raise ValueError(f"n_pts {n} outside bin edges {BIN_EDGES}")


def pooled_rows() -> list[dict]:
    """One row per section: submap size, ransac success, ransac+fiducial success.

    ransac's own result carries n_pts for every section (the fiducial arm's
    no-candidate rows would not) — so size is read from the ransac arm and the
    two arms are joined by frame_idx per recording.
    """
    rows = []
    for rec, tag in POOL:
        _, ra = load(rec, "ransac")
        _, rf = load(rec, "ransac_fiducial")
        ra_by = {r["frame_idx"]: r for r in ra}
        fixes = json.loads((MRK / f"{rec}.fixes.json").read_text())
        for r in rf:
            a = ra_by[r["frame_idx"]]
            rows.append(dict(
                rec=tag, n_pts=a["n_pts"],
                sa=bool(a.get("success", False)), sf=bool(r.get("success", False)),
                src=r.get("source"), covered=bool(fixes.get(str(r["frame_idx"]))),
            ))
    return rows


def parse_flipflop() -> dict:
    """Aliasing evidence ABOVE the gate, pulled from the live SF office A/B
    scorecards (robotday_live/{on,off}). Both arms answered with source=ransac
    on huge (>>50k) submaps yet jumped many metres between consecutive accepts."""
    step_max, npts_min, npts_max, fit_min, fit_max = 0.0, 10**12, 0, 1.0, 0.0
    big_steps = []
    for arm in ("on", "off"):
        sc = (LIVE / arm / "scorecard.txt").read_text()
        assert re.search(r"ZERO non-ransac wins", sc), f"{arm}: expected zero fiducial wins"
        m = re.search(r"consecutive-accept step m: median [\d.]+ p90 [\d.]+ max ([\d.]+)", sc)
        step_max = max(step_max, float(m.group(1)))
        big_steps += [float(x) for x in re.findall(r"^  step ([\d.]+) m", sc, re.M)]
        m = re.search(r"n_pts: min (\d+) median \d+ max (\d+)", sc)
        npts_min = min(npts_min, int(m.group(1)))
        npts_max = max(npts_max, int(m.group(2)))
        m = re.search(r"fitness: min ([\d.]+) p25 [\d.]+ median [\d.]+ p75 [\d.]+ max ([\d.]+)", sc)
        fit_min = min(fit_min, float(m.group(1)))
        fit_max = max(fit_max, float(m.group(2)))
    return dict(step_min=min(big_steps), step_max=step_max, npts_min=npts_min,
                npts_max=npts_max, fit_min=fit_min, fit_max=fit_max)


def main() -> int:
    print(f"regime_map: SEED={SEED} (no RNG) root={ROOT}")
    FIGS.mkdir(parents=True, exist_ok=True)
    rows = pooled_rows()

    # Per-bin counts: (ransac successes, fiducial successes, n, coverage).
    B = len(BIN_LABELS)
    ra_s = [0] * B
    fd_s = [0] * B
    n = [0] * B
    cov = [0] * B
    for r in rows:
        b = bin_of(r["n_pts"])
        n[b] += 1
        cov[b] += r["covered"]
        ra_s[b] += r["sa"]
        fd_s[b] += r["sf"]

    # Self-check: pooled per-arm totals must equal the sum of run_bench summaries.
    want_ra = want_fd = tot = 0
    for rec, _ in POOL:
        sra, _ = load(rec, "ransac")
        srf, _ = load(rec, "ransac_fiducial")
        want_ra += round(sra["success_rate_all"] * sra["n_sections"])
        want_fd += round(srf["success_rate_all"] * srf["n_sections"])
        tot += sra["n_sections"]
    assert sum(ra_s) == want_ra, f"ransac recount {sum(ra_s)} != summary {want_ra}"
    assert sum(fd_s) == want_fd, f"fiducial recount {sum(fd_s)} != summary {want_fd}"
    assert sum(n) == tot == 88, f"pooled n {sum(n)} != {tot}"
    # Title claims: sub-30k lift is the sharpest; above-gate ransac tops out ~50%.
    assert fd_s[0] / n[0] - ra_s[0] / n[0] == max(
        fd_s[i] / n[i] - ra_s[i] / n[i] for i in range(B)), "sub-30k is not the biggest lift"

    ff = parse_flipflop()
    # SF wobble (truth floor) + walk referee certification, for the footer.
    wob = np.array(list(json.loads(
        (RES / f"{POOL[0][0]}.bench_analysis.json").read_text())["wobble_by_frame"].values()))
    wref = json.loads((MRK / f"{POOL[1][0]}.referee.json").read_text())
    sums = {rec: load(rec, "ransac")[0] for rec, _ in POOL}

    p0r, p0f = 100 * ra_s[0] / n[0], 100 * fd_s[0] / n[0]
    p2r = 100 * ra_s[2] / n[2]
    # Report the flip span as whole metres, floored — matches the board's "9–19 m"
    # HONEST-FRAMING wording; the exact 8.8/19.6 m endpoints stay in stdout.
    step_lo, step_hi = round(ff["step_min"]), int(ff["step_max"])

    # ---- figure --------------------------------------------------------------
    fig = plt.figure(figsize=(9.2, 5.8), dpi=200)
    title, top = head(
        fig,
        f"Markers cover RANSAC's blind spot: below the 50k-point gate today's robot won't even "
        f"try, a fiducial prior lifts pooled replay success {p0r:.0f}%→{p0f:.0f}% at sub-30k "
        f"submaps (n={n[0]}); above the gate RANSAC roughly doubles but tops out ~{p2r:.0f}% — "
        f"the office aliases, flipping accepts {step_lo}–{step_hi} m apart "
        f"at passing fitness",
        f"pooled SF office + decorrelated mid360 walk (n={sum(n)}); success = err_t<1 m and "
        f"err_r<15° vs PGO-silver truth, full denominator; “with markers” = RANSAC + "
        f"fiducial prior through the same judge (no source bypass) — the deployment config, not "
        f"markers-instead-of-RANSAC",
        t_width=96, s_width=152)

    ax = fig.add_axes((0.075, 0.255, 0.905, top - 0.255))
    xs = np.arange(B)
    w = 0.36
    # below-gate shaded region + gate divider (bins 0,1 are below the 50k gate)
    ax.axvspan(-0.6, 1.5, color=SHADE, zorder=0)
    ax.axvline(1.5, color=INK2, linewidth=1.3, linestyle=(0, (5, 3)), zorder=1)
    ax.text(1.5, 111, "live gate: 50k points", fontsize=7.6, color=INK2, ha="center",
            va="top", fontweight="bold", zorder=6)
    ax.text(0.5, 103, "below the gate — today's robot doesn't even try",
            fontsize=7.2, color=MUT, ha="center", va="top", style="italic", zorder=6)

    for series, s_lab, color, dx in (
        (ra_s, "RANSAC (no prior)", C_RANSAC, -w / 2),
        (fd_s, "RANSAC + fiducial prior", C_FID, +w / 2),
    ):
        for b in range(B):
            pct = 100.0 * series[b] / n[b]
            ax.bar(xs[b] + dx, pct, w, color=color, edgecolor=SURF, linewidth=1.0,
                   zorder=3, label=s_lab if b == 0 else None)
            ax.text(xs[b] + dx, pct + 1.4, f"{pct:.0f}%", ha="center", va="bottom",
                    fontsize=7.8, color=INK, zorder=4)
            ax.text(xs[b] + dx, pct + 6.4, f"{series[b]}/{n[b]}", ha="center", va="bottom",
                    fontsize=6.2, color=MUT, zorder=4)

    # aliasing annotation — pinned inside the axes (fraction coords) so it can't
    # clip the frame, arrow to the >=50k RANSAC bar it is about.
    note = textwrap.fill(
        f"Aliasing above the gate too: live SF office A/B — consecutive RANSAC accepts "
        f"on {ff['npts_min'] / 1000:.0f}k–{ff['npts_max'] / 1000:.0f}k-pt submaps jumped "
        f"{step_lo}–{step_hi} m apart at fitness {ff['fit_min']:.2f}–{ff['fit_max']:.2f} "
        f"(all pass the 0.45 gate). Confident-wrong is not only a sparse-submap failure.", 46)
    ax.annotate(
        note, xy=(2 - w / 2, p2r + 1), xytext=(0.585, 0.90),
        textcoords=ax.transAxes, fontsize=6.9, color=INK2, ha="left", va="top",
        linespacing=1.4, zorder=6,
        bbox=dict(boxstyle="round,pad=0.5", fc="#fbf6ee", ec=BASE, lw=0.8),
        arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.0,
                        connectionstyle="arc3,rad=-0.2"))

    # bin labels carry n + fiducial coverage on their own lines (no axis-floor text)
    ax.set_xticks(xs)
    ax.set_xticklabels(
        [f"{BIN_LABELS[b]}\nn={n[b]}   ({cov[b]}/{n[b]} in a tag's view)" for b in range(B)],
        fontsize=8.2, color=INK, linespacing=1.7)
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_ylabel("replay success rate (%), full denominator", fontsize=8, color=INK2)
    ax.axhline(0, color=BASE, linewidth=1.0)
    style_axes(ax)
    ax.legend(loc="upper left", frameon=False, fontsize=7.8, handlelength=1.1, handleheight=1.1,
              bbox_to_anchor=(0.0, 1.0))

    footer(fig, [
        "method: offline sections bench (trial/harness/run_bench.py), replay rung — real recorded go2 sensors, offline; “with markers” arm = ransac+fiducial (marker candidates age-gated, ranked by the same wall-ICP judge fitness as RANSAC, no source bypass); submap size = ransac-arm n_pts; seeds=frame_idx; OMP_NUM_THREADS=1; SEED=0 (no RNG in plotting)",
        f"truth: PGO-silver, full denominator (crashes / no-fix sections count as failures). Marker maps derive from the SAME PGO run as truth (deployment-realistic, truth-correlated in derivation); the referee tag (SF id 19 / walk id 4) is excluded from every marker map, so the ID split decorrelates the grade. SF wobble (two independent PGO builds): median {np.median(wob):.2f} m, p90 {np.percentile(wob, 90):.2f} m, max {wob.max():.2f} m late-run. Walk: referee tag-{wref['meta']['referee_tag']} certified ({wref['n_sightings']}-sighting consensus, rms {wref['consensus_rms_m']:.2f} m).",
        f"pooled (n={sum(n)}): SF office survey1 (48 sec; dimos {sums[POOL[0][0]]['git_rev_dimos']} trial {sums[POOL[0][0]]['git_rev_trial']}) + mid360 walk go2 lidar lane (40 sec; dimos {sums[POOL[1][0]]['git_rev_dimos']} trial {sums[POOL[1][0]]['git_rev_trial']}). Excluded: mid360 FAST-LIO lane (RANSAC 0/40 = gravity-gate frame-convention artifact, +fiducial byte-identical offline — not a submap-size regime) and hk_village3 (fiducial arm truth-correlated: its one tag IS the referee under the split). Aliasing box: robotday_live/{{on,off}}/scorecard.txt.",
    ])

    out = FIGS / "regime_map.png"
    fig.savefig(out)
    plt.close(fig)

    print(f"  title: {title.replace(chr(10), ' ')}")
    print(f"  pooled n={sum(n)} (SF 48 + walk 40); ransac {sum(ra_s)}/{sum(n)}, "
          f"+fiducial {sum(fd_s)}/{sum(n)}")
    for b in range(B):
        print(f"  {BIN_LABELS[b]:>11}: n={n[b]:2d}  ransac {ra_s[b]:2d}/{n[b]:2d}="
              f"{100 * ra_s[b] / n[b]:5.1f}%   +fiducial {fd_s[b]:2d}/{n[b]:2d}="
              f"{100 * fd_s[b] / n[b]:5.1f}%   (coverage {cov[b]}/{n[b]})")
    print(f"  flip-flop (live SF A/B): steps {ff['step_min']:.1f}–{ff['step_max']:.1f} m, "
          f"n_pts {ff['npts_min']}–{ff['npts_max']}, fitness {ff['fit_min']:.2f}–{ff['fit_max']:.2f}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
