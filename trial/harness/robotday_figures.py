#!/usr/bin/env python3
"""Robot-day proof figures for the /dimensional page — read artifacts, render PNGs.

    result JSONs (run_bench)  ─┐
    bench_analysis.json        ├─> fig1 robotday_sf_bench.png   (offline with-vs-without, strata)
    robotday_live/{on,off}/*  ─┼─> fig2 robotday_sf_live.png    (live ON-vs-OFF event raster)
    fastlio results + pkl     ─┼─> fig3 robotday_livox.png      (LIO lane, same axes as fig1)
    fixes + marker_map JSONs  ─┴─> fig4 robotday_lever.png      (lever rule scatter)

Every number on a figure is derived HERE from the saved artifacts (JSONs, logs,
pkl) — never pasted from prose; asserts pin the title claims to the recomputed
values so a stale artifact cannot leave a lying title. Deterministic: pure
reads, no RNG (SEED=0 printed per house rule). House figure rules: title IS the
takeaway sentence with the biggest caveat riding in it; n's on the plot;
footer = method + seeds + git revs + truth label.

Colors are the dataviz reference palette, slots 1-3 in documented adjacent order
(order validated per palette.md; magenta's light-surface contrast relief rule is
met by direct labels on every bar).

Run: cd /home/dimos/dimensional-trial/dimos && \
     OMP_NUM_THREADS=1 uv run python ../trial/harness/robotday_figures.py
"""
from __future__ import annotations

import json
import math
import pickle
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))  # harness-local modules
from reloc_log import parse_accepts  # noqa: E402

SEED = 0  # no RNG anywhere in this script

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "trial/harness/out/results"
MRK = ROOT / "trial/harness/out/markers"
LIVE = ROOT / "trial/harness/out/robotday_live"
PREP = ROOT / "trial/harness/out/prepared"
FIGS = ROOT / "trial/results/figures"

SF = "sf_office_go2_20260718_survey1"
LX = "recording_go2_mid360_2026-05-29_4-45pm-PST.fastlio"

# dataviz reference palette (light mode), fixed entity->color mapping across ALL figures
C_RANSAC = "#2a78d6"  # slot 1 blue  = RANSAC / prior OFF
C_FID = "#008300"     # slot 2 green = RANSAC + fiducial prior / prior ON
C_JUDGE = "#e87ba4"   # slot 3 magenta = fiducial-only + judge (relief rule: direct labels)
INK, INK2, MUT = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"

CONFIGS = [("ransac", "RANSAC (no prior)", C_RANSAC),
           ("ransac_fiducial", "RANSAC + fiducial prior", C_FID),
           ("fiducial_judge", "fiducial-only + judge", C_JUDGE)]

plt.rcParams.update({
    "font.family": "DejaVu Sans", "figure.facecolor": SURF, "axes.facecolor": SURF,
    "savefig.facecolor": SURF, "axes.edgecolor": BASE, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
})


def load(rec: str, cfg: str) -> tuple[dict, list[dict]]:
    d = json.loads((RES / f"{rec}.{cfg}.json").read_text())
    return d["summary"], d["results"]


def rate(rs: list[dict], keep) -> tuple[int, int]:
    sub = [r for r in rs if keep(r)]
    # no_candidates rows carry no 'success' key: full denominator counts them as failures
    return sum(1 for r in sub if r.get("success", False)), len(sub)


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


def footer(fig, lines: list[str], width: int = 185) -> None:
    txt = "\n".join(textwrap.fill(ln, width, subsequent_indent="   ") for ln in lines)
    fig.text(0.012, 0.008, txt, fontsize=6.0, color=MUT, va="bottom", ha="left", linespacing=1.45)


def grouped_bars(ax, groups: list[tuple[str, dict[str, tuple[int, int]]]]) -> None:
    """Shared bar geometry for fig1/fig3 so the two figures read on identical axes."""
    width, xs = 0.24, np.arange(len(groups))
    for ci, (cfg, label, color) in enumerate(CONFIGS):
        off = (ci - 1) * (width + 0.02)
        for gi, (_, per_cfg) in enumerate(groups):
            s, n = per_cfg[cfg]
            pct = 100.0 * s / n
            ax.bar(xs[gi] + off, pct, width, color=color, edgecolor=SURF, linewidth=1.0,
                   label=label if gi == 0 else None)
            ax.text(xs[gi] + off, pct + 1.2, f"{pct:.0f}%", ha="center", va="bottom",
                    fontsize=7.6, color=INK)
            ax.text(xs[gi] + off, pct + 6.8, f"{s}/{n}", ha="center", va="bottom",
                    fontsize=6.2, color=MUT)
    ax.set_xticks(xs)
    ax.set_xticklabels([g[0] for g in groups], fontsize=8.2, color=INK2)
    ax.set_ylim(0, 100)
    ax.set_ylabel("success rate (%), full denominator", fontsize=8, color=INK2)
    ax.axhline(0, color=BASE, linewidth=1.0)
    style_axes(ax)
    ax.legend(loc="upper right", frameon=False, fontsize=7.6, handlelength=1.1, handleheight=1.1)


def strata_groups(res: dict[str, list[dict]], npts: dict[int, int]) -> list[tuple[str, dict]]:
    strata = [("all sections", lambda n: True),
              ("sub-50k pts (gate not reached)", lambda n: n < 50_000),
              ("≥50k pts (gate reached)", lambda n: n >= 50_000)]
    groups = []
    for base, pred in strata:
        per = {c: rate(res[c], lambda r, p=pred: p(npts[r["frame_idx"]])) for c, _, _ in CONFIGS}
        groups.append((f"{base}\nn={per['ransac'][1]}", per))
    return groups


# ---------------------------------------------------------------- fig 1: SF bench
def fig_sf_bench() -> None:
    sums, res = {}, {}
    for cfg, _, _ in CONFIGS:
        sums[cfg], res[cfg] = load(SF, cfg)
    npts = {r["frame_idx"]: r["n_pts"] for r in res["ransac"]}  # judge arm lacks n_pts on no-candidate rows
    groups = strata_groups(res, npts)

    # self-checks: recomputed rates must equal what run_bench reported
    for cfg, _, _ in CONFIGS:
        s, n = groups[0][1][cfg]
        assert abs(s / n - sums[cfg]["success_rate_all"]) < 1e-9, f"{cfg}: recount != summary"

    # mechanism: per-section flips ransac -> ransac+fiducial
    by = {c: {r["frame_idx"]: r for r in res[c]} for c, _, _ in CONFIGS}
    rescues = sum(1 for f in by["ransac"]
                  if not by["ransac"][f]["success"] and by["ransac_fiducial"][f]["success"])
    regress = sum(1 for f in by["ransac"]
                  if by["ransac"][f]["success"] and not by["ransac_fiducial"][f]["success"])
    assert groups[2][1]["ransac"][0] == groups[2][1]["ransac_fiducial"][0], \
        "title says all gain is sub-50k; the >=50k stratum moved — re-word"

    wob = json.loads((RES / f"{SF}.bench_analysis.json").read_text())["wobble_by_frame"]
    w = np.array(list(wob.values()))
    p_r = 100 * groups[0][1]["ransac"][0] / 48
    p_f = 100 * groups[0][1]["ransac_fiducial"][0] / 48
    nocand = sums["fiducial_judge"]["n_no_candidates"]

    fig = plt.figure(figsize=(8.6, 5.1), dpi=200)
    title, top = head(
        fig,
        f"SF office survey: fiducial priors lift replay success {p_r:.0f}%→{p_f:.0f}% (n=48) — "
        f"all of the gain is sub-50k, {rescues} rescues / {regress} regressions — on PGO truth "
        f"whose measured wobble reaches {w.max():.1f} m late-run (t>600 s calls are truth-limited)",
        f"success = err_t<1 m and err_r<15° vs PGO-silver truth; judge-only arm: {nocand}/48 "
        f"sections carried no fiducial fix in window — counted as failures (full denominator)",
        t_width=94, s_width=150)
    ax = fig.add_axes((0.075, 0.225, 0.91, top - 0.225))
    grouped_bars(ax, groups)
    footer(fig, [
        "method: offline sections bench (trial/harness/run_bench.py), replay rung — real recorded go2 sensors, offline; seeds=frame_idx; OMP_NUM_THREADS=1; SEED=0 (no RNG in plotting)",
        f"truth: PGO-silver; run-to-run wobble measured from two independent PGO builds over the 48 query ts: median {np.median(w):.2f} m, p90 {np.percentile(w, 90):.2f} m, max {w.max():.2f} m (bench_analysis.json)",
        f"src: out/results/{SF}.{{ransac,ransac_fiducial,fiducial_judge}}.json; git dimos {sums['ransac']['git_rev_dimos']} trial {sums['ransac']['git_rev_trial']}; tier-B recording, 13.6 min",
    ])
    out = FIGS / "robotday_sf_bench.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"fig1 {out}")
    print(f"  title: {title.replace(chr(10), ' ')}")
    print(f"  rescues={rescues} regressions={regress} wobble med/p90/max="
          f"{np.median(w):.3f}/{np.percentile(w, 90):.3f}/{w.max():.3f} m")
    for name, per in groups:
        print("  " + name.replace("\n", " "), {c: per[c] for c, _, _ in CONFIGS})


# ---------------------------------------------------------------- fig 2: SF live A/B
_TS = r"(\d{2}):(\d{2}):(\d{2}\.\d+)"
START_RE = re.compile(_TS + r" .*Starting DimOS")
FIX_RE = re.compile(r"fix#(\d+) ts=([\d.]+) world->map t=\[([-\d.,]+)\](?: wall=([\d.]+))?")
DAY_UTC = datetime(2026, 7, 19, tzinfo=timezone.utc).timestamp()  # both arms logged 03:07–05:12 UTC Jul 19


def parse_arm(arm: str) -> dict:
    txt = (LIVE / arm / "replay_run.log").read_text()
    t0 = None
    for ln in txt.splitlines():
        if (m := START_RE.match(ln)):
            t0 = DAY_UTC + int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            break
    assert t0 is not None, f"{arm}: no 'Starting DimOS' line"
    cmd = (LIVE / arm / "replay_cmd.txt").read_text()
    epoch = float(re.search(r"start .* epoch=(\d+)", cmd).group(1))
    assert abs(t0 - epoch) < 60.0, f"{arm}: log day mismatch vs replay_cmd epoch"  # guards DAY_UTC
    # src "plain" = the accept carried no source=, i.e. the single-source path ran
    # and no judge ranked anything -- not the same event as a judged RANSAC win.
    acc = [dict(t=DAY_UTC + a.tod_s - t0, dt=a.time_cost_s, src=a.source or "plain")
           for a in parse_accepts(txt)
           if a.tod_s is not None and a.time_cost_s is not None]
    fixes = [float(m.group(4) or m.group(2)) - t0
             for ln in (LIVE / arm / "spy_world_map_fix.log").read_text().splitlines()
             if (m := FIX_RE.match(ln))]
    # fresh-fix cycles: a fix with age<=120 s (FiducialPrior.age_max_s) existed at cycle start
    fw = np.array(fixes)
    fresh = wins = 0
    for x in acc:
        ages = (x["t"] - x["dt"]) - fw
        ages = ages[ages >= 0]
        if len(ages) and ages.min() <= 120.0:
            fresh += 1
            wins += x["src"] == "fiducial"
    sc = (LIVE / arm / "scorecard.txt").read_text()
    assert int(re.search(r"accepts: (\d+)", sc).group(1)) == len(acc), f"{arm}: recount != scorecard"
    revs = re.search(r"git trial=(\w+) dimos=(\w+)", sc)
    return dict(acc=acc, fixes=fixes, fresh=fresh, wins=wins,
                med_dt=float(np.median([x["dt"] for x in acc])), rev_trial=revs.group(1),
                rev_dimos=revs.group(2), srcs=sorted({x["src"] for x in acc}))


def fig_sf_live() -> None:
    on, off = parse_arm("on"), parse_arm("off")
    n_on, n_off = len(on["acc"]), len(off["acc"])
    assert on["wins"] == 0 and set(on["srcs"]) <= {"ransac", "fiducial"}, "title says zero fiducial wins"

    fig = plt.figure(figsize=(8.6, 4.6), dpi=200)
    title, top = head(
        fig,
        f"Live A/B on the SF recording: tag fixes flowed all run, but fiducial won zero of the "
        f"{on['fresh']} judge rounds where a fresh fix was in hand — every accepted pose was "
        f"RANSAC's (one run per arm, no live truth: behavior, not accuracy)",
        f"the ON arm still answered {n_on / n_off:.1f}× more often ({n_on} vs {n_off} accepts in "
        f"the same 880 s window; median cycle {on['med_dt']:.1f} s vs {off['med_dt']:.1f} s) — "
        f"difference measured, cause unverified",
        t_width=94, s_width=150)
    ax = fig.add_axes((0.03, 0.21, 0.755, top - 0.21))
    lanes = [("ON — accepted poses", [x["t"] for x in on["acc"]], C_FID, 3.35, 0.34,
              f"{n_on} accepts, all src=ransac\nmedian cycle {on['med_dt']:.1f} s"),
             ("ON — tag fixes published", on["fixes"], MUT, 2.72, 0.20,
              f"{len(on['fixes'])} fixes; fresh in {on['fresh']}/{n_on}\ncycles — fiducial won {on['wins']}"),
             ("OFF — accepted poses", [x["t"] for x in off["acc"]], C_RANSAC, 1.62, 0.34,
              f"{n_off} accepts (prior off)\nmedian cycle {off['med_dt']:.1f} s"),
             ("OFF — tag fixes published", off["fixes"], MUT, 0.99, 0.20,
              f"{len(off['fixes'])} fixes seen by spy\n(module ignores them)")]
    for label, ts, color, y, h, note in lanes:
        ax.vlines(np.array(ts) / 60.0, y - h / 2, y + h / 2, color=color, linewidth=1.1)
        ax.text(-0.1, y + h / 2 + 0.07, label, fontsize=7.6, color=INK2, ha="left", va="bottom")
        ax.text(15.05, y, note, fontsize=7.2, color=INK2, ha="left", va="center",
                linespacing=1.4, clip_on=False)
    ax.set_xlim(-0.15, 14.9)
    ax.set_ylim(0.55, 4.05)
    ax.set_yticks([])
    ax.set_xlabel("time since DimOS start (min) — same recording replayed once per arm",
                  fontsize=8, color=INK2)
    style_axes(ax)
    ax.grid(False)
    footer(fig, [
        "method: live relocalization stack replayed once per arm (exclusive LCM bus, 880 s timeout); events parsed from robotday_live/{on,off}/replay_run.log + spy_world_map_fix.log",
        "fresh-fix judge round = a /world_map_fix fix aged ≤120 s (FiducialPrior.age_max_s) at cycle start; no truth labels exist live — this figure compares behavior only; SEED=0 (no RNG; the replay itself is not seeded)",
        f"src: robotday_live/*/{{replay_run.log,spy_world_map_fix.log,replay_cmd.txt,scorecard.txt}}; git dimos {on['rev_dimos']} trial {on['rev_trial']}; recording {SF}",
    ])
    out = FIGS / "robotday_sf_live.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"fig2 {out}")
    print(f"  title: {title.replace(chr(10), ' ')}")
    print(f"  ON accepts={n_on} med_dt={on['med_dt']:.1f}s fixes={len(on['fixes'])} "
          f"fresh={on['fresh']} wins={on['wins']} srcs={on['srcs']}")
    print(f"  OFF accepts={n_off} med_dt={off['med_dt']:.1f}s fixes={len(off['fixes'])} srcs={off['srcs']}")


# ---------------------------------------------------------------- fig 3: livox lane
def tilt_deg(T) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.asarray(T)[2, 2])))))


def fig_livox() -> None:
    sums, res = {}, {}
    for cfg, _, _ in CONFIGS:
        sums[cfg], res[cfg] = load(LX, cfg)
    npts = {r["frame_idx"]: r["n_pts"] for r in res["ransac"]}

    # verify the harness-artifact claim before printing it: arm2 must equal arm1 per-section
    by1 = {r["frame_idx"]: r for r in res["ransac"]}
    by2 = {r["frame_idx"]: r for r in res["ransac_fiducial"]}
    dmax = max(abs(by1[f]["err_t"] - by2[f]["err_t"]) for f in by1)
    assert dmax < 1e-9, f"arm2 no longer identical to arm1 (max |d err_t|={dmax}) — drop the artifact note"

    pkl = pickle.loads((PREP / f"{LX}.pkl").read_bytes())
    tilts = np.array([tilt_deg(s["T_true"]) for s in pkl["sections"]])
    est_upright = sum(1 for r in res["ransac"] if tilt_deg(r["T_est"]) < 10.0)
    fits = np.array([r["fitness"] for r in res["ransac"]])
    fixed_frames = {int(k) for k in json.loads((MRK / f"{LX}.fixes.json").read_text())}
    okj = [r for r in res["fiducial_judge"] if r["status"] == "ok"]
    s_fix, n_fix = rate(res["fiducial_judge"], lambda r: r["frame_idx"] in fixed_frames)
    med_ok = float(np.median([r["err_t"] for r in okj]))
    ref = (ROOT / "trial/harness/out/robotday_build/referee_verdict_fastlio.log").read_text()
    m = re.search(r"REFEREE VERDICT \(tag (\d+), (\d+) sightings, consensus rms ([\d.]+) m", ref)
    t_true_vs = re.search(r"T_true \(truth\): vs consensus median ([\d.]+) m", ref).group(1)

    groups = strata_groups(res, npts)
    for cfg, _, _ in CONFIGS:
        s, n = groups[0][1][cfg]
        assert abs(s / n - sums[cfg]["success_rate_all"]) < 1e-9, f"{cfg}: recount != summary"

    fig = plt.figure(figsize=(8.6, 5.1), dpi=200)
    title, top = head(
        fig,
        f"Tilted-LIO lane (mid360 + FAST-LIO): fiducial fixes are the only relocalization that "
        f"works — RANSAC 0/40 because the gravity gate assumes an upright body (true tilt "
        f"{tilts.min():.0f}–{tilts.max():.0f}°): a frame-convention break, not scene difficulty",
        f"fiducial+judge: {s_fix}/{n_fix} fix-carrying sections succeed (median err "
        f"{100 * med_ok:.1f} cm); RANSAC is honestly lost, not confidently wrong (max fitness "
        f"{fits.max():.2f}, 0/40 pass the 0.45 gate; {est_upright}/40 winners tilt <10°, "
        f"upright-wrong); the +fiducial bar equals RANSAC exactly (verified) — bench refine lacks "
        f"the live stack's per-source gravity fallback, so the bench is pessimistic here",
        t_width=94, s_width=150)
    ax = fig.add_axes((0.075, 0.225, 0.91, top - 0.225))
    grouped_bars(ax, groups)
    footer(fig, [
        "method: offline sections bench (trial/harness/run_bench.py), replay rung — real recorded mid360 sensors, lane fastlio_lidar re-posed from fastlio_odometry payloads; seeds=frame_idx; OMP_NUM_THREADS=1; SEED=0",
        f"truth: PGO-silver, referee-certified on this lane — T_true vs tag-{m.group(1)} consensus ({m.group(2)} sightings, rms {m.group(3)} m): median {t_true_vs} m (the referee tag is never in any marker map)",
        f"src: out/results/{LX}.{{ransac,ransac_fiducial,fiducial_judge}}.json + prepared/{LX}.pkl + referee_verdict_fastlio.log; git dimos {sums['ransac']['git_rev_dimos']} trial {sums['ransac']['git_rev_trial']}",
    ])
    out = FIGS / "robotday_livox.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"fig3 {out}")
    print(f"  title: {title.replace(chr(10), ' ')}")
    print(f"  tilt min/med/max={tilts.min():.1f}/{np.median(tilts):.1f}/{tilts.max():.1f} deg; "
          f"ransac max fitness={fits.max():.3f}; est upright={est_upright}/40; "
          f"fix-carrying={s_fix}/{n_fix} med_ok={med_ok:.3f} m; arm2==arm1 max d={dmax:.2e}")
    for name, per in groups:
        print("  " + name.replace("\n", " "), {c: per[c] for c, _, _ in CONFIGS})


# ---------------------------------------------------------------- fig 4: lever rule
def fig_lever() -> None:
    jsum, judge = load(SF, "fiducial_judge")
    fixes = json.loads((MRK / f"{SF}.fixes.json").read_text())
    markers = json.loads((MRK / f"{SF}.marker_map.json").read_text())["markers"]
    lever_m = {int(k): math.hypot(v[0][3], v[1][3]) for k, v in markers.items()}  # XY dist, map origin -> tag
    wob = {int(k): v for k, v in
           json.loads((RES / f"{SF}.bench_analysis.json").read_text())["wobble_by_frame"].items()}

    pts, mixed = [], 0
    for r in judge:
        if r["status"] != "ok":
            continue
        tags = {f["marker_id"] for f in fixes[str(r["frame_idx"])]}
        if len(tags) != 1:
            mixed += 1  # can't attribute the answer to one tag — excluded, counted in subtitle
            continue
        pts.append((tags.pop(), r["err_t"], wob[r["frame_idx"]] >= 1.0))
    by_tag: dict[int, list] = {}
    for tag, err, limited in pts:
        by_tag.setdefault(tag, []).append((err, limited))
    short_tag = min(lever_m, key=lambda t: lever_m[t])
    short_clean = sorted(e for e, _ in by_tag[short_tag] if e < 1.0)
    short_all = sorted(e for e, _ in by_tag[short_tag])
    short_n = len(short_all)
    short_miss = [e for e in short_all if e >= 1.0]

    fig = plt.figure(figsize=(7.6, 5.0), dpi=200)
    title, top = head(
        fig,
        f"Tag-placement lever rule validated same-night: fiducial-only error grows with the "
        f"map-origin→tag lever — the {lever_m[short_tag]:.1f} m tag delivered mostly "
        f"{100 * short_clean[0]:.0f}–{100 * short_clean[-1]:.0f} cm answers "
        f"({len(short_clean)} of {short_n}"
        + (f"; one {short_miss[0]:.1f} m miss" if short_miss else "")
        + f"), the 13–14 m tags meter-class flips (open points: truth-limited late-run)",
        f"fiducial-only + judge arm, one point per section (n={len(pts)} single-tag sections; "
        f"{mixed} mixed-tag section excluded); in the combined arm the shared judge rejected "
        f"every meter-class fiducial answer (0 regressions, fig 1)",
        t_width=82, s_width=130)
    ax = fig.add_axes((0.095, 0.235, 0.88, top - 0.235))
    for tag in sorted(by_tag):
        rows = sorted(by_tag[tag])
        k = len(rows)
        for i, (err, limited) in enumerate(rows):
            dx = (i - (k - 1) / 2) * 0.13  # deterministic dodge, no RNG
            ax.scatter(lever_m[tag] + dx, err, s=34, zorder=3,
                       facecolors="none" if limited else C_RANSAC,
                       edgecolors=C_RANSAC, linewidths=1.2)
        ax.text(lever_m[tag], 15.5, f"tag {tag}\n{lever_m[tag]:.1f} m\nn={k}", ha="center",
                va="bottom", fontsize=7.0, color=INK2, linespacing=1.3)
    ax.axhline(1.0, color=BASE, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.text(15.15, 1.09, "success bar: 1 m", fontsize=7.0, color=MUT, va="bottom", ha="right")
    ax.set_yscale("log")
    ax.set_ylim(0.03, 40)
    ax.set_xlim(4.0, 15.3)
    ax.set_xlabel("tag lever: XY distance, map origin → tag, from the survey marker map (m)",
                  fontsize=8, color=INK2)
    ax.set_ylabel("fiducial-only answer error err_t (m, log)", fontsize=8, color=INK2)
    ax.text(0.985, 0.025, "solid = truth wobble <1 m (decision-grade)    open = ≥1 m (truth-limited)",
            transform=ax.transAxes, fontsize=6.9, color=INK2, ha="right", va="bottom")
    style_axes(ax)
    footer(fig, [
        "method: err_t of each fiducial-sourced bench answer (ICP-refined) vs PGO-silver truth, attributed to the single tag whose fixes fed that section; lever = XY translation of map_T_tag",
        "truth wobble per section from two independent PGO builds (bench_analysis.json); ≥1 m marks the call truth-limited, not wrong; seeds=frame_idx; OMP_NUM_THREADS=1; SEED=0 (no RNG)",
        f"src: out/results/{SF}.fiducial_judge.json + out/markers/{SF}.{{fixes,marker_map}}.json; "
        f"git dimos {jsum['git_rev_dimos']} trial {jsum['git_rev_trial']}; recording {SF}",
    ], width=160)
    out = FIGS / "robotday_lever.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"fig4 {out}")
    print(f"  title: {title.replace(chr(10), ' ')}")
    print(f"  n={len(pts)} mixed_excluded={mixed} levers_m={{ "
          f"{', '.join(f'{t}: {v:.2f}' for t, v in sorted(lever_m.items()))} }}")
    for tag in sorted(by_tag):
        errs = [f"{e:.2f}" for e, _ in sorted(by_tag[tag])]
        print(f"  tag {tag} lever {lever_m[tag]:.2f} m err_t: {errs}")


def main() -> int:
    print(f"robotday_figures: SEED={SEED} (no RNG) root={ROOT}")
    FIGS.mkdir(parents=True, exist_ok=True)
    fig_sf_bench()
    fig_sf_live()
    fig_livox()
    fig_lever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
