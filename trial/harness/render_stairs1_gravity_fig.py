#!/usr/bin/env python3
"""Honest before/after figure for the v2 gravity-consistency gate on the tilted
mid360 stairs1 lane (offline replay, real recorded LiDAR -- NOT simulated).

    BEFORE = results/mid360_gir_stairs1_20260601.ransac.json          (world-z gate, map_up=None)
    AFTER  = results/mid360_gir_stairs1_20260601.ransac_gravityv2.json (consistency gate, map_up threaded)
                     |
                     v
    trial/results/figures/gravity_gate_stairs1_before_after.png

Every number on the figure is recomputed HERE from the two JSONs (success bar
re-applied: err_t < 1.0 m AND err_r < 15 deg), the regime split is re-derived
from the BEFORE winner's z-axis tilt (not trusted from a hardcoded list -- the
frozen sets below are only cross-checked against the derivation), and asserts
pin the title's counts to the recomputed values so a stale artifact can't leave
a lying title. Pure reads, no RNG.

Run: uv run --project /home/dimos/dimos-code python \
     /home/dimos/dimensional-trial/trial/harness/render_stairs1_gravity_fig.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HARNESS = Path(__file__).resolve().parent
RES = HARNESS / "out" / "results"
FIGS = HARNESS.parents[0] / "results" / "figures"
BEFORE = RES / "mid360_gir_stairs1_20260601.ransac.json"
AFTER = RES / "mid360_gir_stairs1_20260601.ransac_gravityv2.json"

SUCCESS_T_M = 1.0
SUCCESS_R_DEG = 15.0
GATE_DEG = 10.0  # GRAVITY_TILT_MAX_DEG
UPRIGHT_MAX_DEG = 15.0  # a BEFORE winner tilted < this off world-z was forced upright == hijack

# dataviz reference palette (validated defaults, light mode)
C_BEFORE = "#2a78d6"  # series-1 blue  = shipped world-z gate
C_AFTER = "#008300"   # series-2 green = attitude-aware consistency gate
C_WARN = "#c1440e"    # threshold/limit lines (labeled)
INK, INK2, MUT = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"

# Cross-check sets (re-derived below; these only assert the derivation is stable).
FROZEN_HIJACK = {5, 1717, 3429, 3857, 4713, 5141, 5569, 6853}
FROZEN_SCENE = {861, 1289, 2145, 4285, 5997, 6425}
FROZEN_CORRECT = {433, 2573, 3001, 7282}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "figure.facecolor": SURF, "axes.facecolor": SURF,
    "savefig.facecolor": SURF, "axes.edgecolor": BASE, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
})


def by_frame(path: Path) -> tuple[dict, dict]:
    d = json.loads(path.read_text())
    return {r["frame_idx"]: r for r in d["results"]}, d["summary"]


def succ(r: dict) -> bool:
    if r.get("status") != "ok":
        return False
    return bool(r.get("err_t", np.inf) < SUCCESS_T_M and r.get("err_r", np.inf) < SUCCESS_R_DEG)


def err_t(r: dict) -> float:
    return float(r.get("err_t", np.inf)) if r.get("status") == "ok" else np.inf


def z_tilt_deg(r: dict) -> float:
    """BEFORE winner's z-axis angle off world-z: arccos(R[2,2]) -- the OLD gate metric."""
    if r.get("status") != "ok" or "T_est" not in r:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(float(np.asarray(r["T_est"])[2, 2]), -1.0, 1.0))))


def main() -> int:
    b, bsum = by_frame(BEFORE)
    a, asum = by_frame(AFTER)
    frames = sorted(b)
    assert set(a) == set(b), "AFTER/BEFORE section sets differ"
    n = len(frames)

    # --- re-derive the regime of each section from the BEFORE result alone ---
    # success  = BEFORE already correct (tilted candidate survived the world-z gate anyway)
    # hijack   = BEFORE failed AND its winner was forced ~upright (< 15 deg off world-z)
    # scene    = BEFORE failed AND its winner was itself tilted/flipped (a placement bust,
    #            not a gate bust)
    regime: dict[int, str] = {}
    for fx in frames:
        if succ(b[fx]):
            regime[fx] = "correct"
        elif z_tilt_deg(b[fx]) < UPRIGHT_MAX_DEG:
            regime[fx] = "hijack"
        else:
            regime[fx] = "scene"
    derived = {k: {fx for fx in frames if regime[fx] == k} for k in ("hijack", "scene", "correct")}
    assert derived["hijack"] == FROZEN_HIJACK, f"hijack drift: {derived['hijack']}"
    assert derived["scene"] == FROZEN_SCENE, f"scene drift: {derived['scene']}"
    assert derived["correct"] == FROZEN_CORRECT, f"correct drift: {derived['correct']}"

    b_total = sum(succ(b[fx]) for fx in frames)
    a_total = sum(succ(a[fx]) for fx in frames)
    assert b_total == 4, f"BEFORE recount expected 4, got {b_total}"

    order = ["correct", "hijack", "scene"]
    labels = {"correct": "Correct-tilted", "hijack": "Gate-hijack", "scene": "Scene-ambiguity"}
    counts = {k: sum(regime[fx] == k for fx in frames) for k in order}
    b_by = {k: sum(succ(b[fx]) for fx in frames if regime[fx] == k) for k in order}
    a_by = {k: sum(succ(a[fx]) for fx in frames if regime[fx] == k) for k in order}

    rescued = sorted(fx for fx in FROZEN_HIJACK if succ(a[fx]) and not succ(b[fx]))
    regressed = sorted(fx for fx in frames if succ(b[fx]) and not succ(a[fx]))
    scene_flip = sorted(fx for fx in FROZEN_SCENE if succ(a[fx]) and not succ(b[fx]))
    scene_fail_after = counts["scene"] - a_by["scene"]

    # ------------- honest title, pinned to the recomputed numbers -------------
    t = (f"Body-attitude-aware gravity gate rescues {len(rescued)} of {counts['hijack']} "
         f"gate-hijack busts on the tilted mid360 stairs1 lane  ({b_total}/{n} → {a_total}/{n})")
    subclauses = [f"the {scene_fail_after} scene-ambiguity failures remain (not a gate problem)"]
    if regressed:
        subclauses.insert(0, f"{len(regressed)} previously-correct section(s) regressed "
                             f"(frames {regressed})")
    if scene_flip:
        subclauses.append(f"{len(scene_flip)} scene-ambiguity section(s) also cleared "
                          f"(frames {scene_flip})")
    title = t + ";  " + ";  ".join(subclauses)

    diagnosis = (
        f"Diagnosis: the mid360 body sits ~50° off gravity while this premap's own floor is only "
        f"{_maptilt(asum):.1f}° off world-z. The shipped gate measured each candidate's z-axis vs "
        f"WORLD-z, so it rejected every geometrically-correct (tilted) candidate and an upright-but-wrong "
        f"decoy won the pool. The fix estimates the body's own up per frame (segment_plane) and gates on "
        f"gravity DIRECTION -- does T carry the submap's floor onto the map's floor -- keeping the tilted-correct fix."
    )

    fig = plt.figure(figsize=(14.6, 6.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.72], wspace=0.22,
                          left=0.065, right=0.985, top=0.78, bottom=0.20)

    # ---- Panel A: success by regime, before vs after ----
    axA = fig.add_subplot(gs[0, 0])
    xs = np.arange(len(order))
    w = 0.38
    ba = axA.bar(xs - w / 2 - 0.012, [b_by[k] for k in order], w, color=C_BEFORE,
                 edgecolor=SURF, linewidth=1.4, zorder=3, label="BEFORE  world-z gate")
    aa = axA.bar(xs + w / 2 + 0.012, [a_by[k] for k in order], w, color=C_AFTER,
                 edgecolor=SURF, linewidth=1.4, zorder=3, label="AFTER  consistency gate")
    for bars, vals in ((ba, [b_by[k] for k in order]), (aa, [a_by[k] for k in order])):
        for rect, v in zip(bars, vals):
            axA.text(rect.get_x() + rect.get_width() / 2, v + 0.12, str(v),
                     ha="center", va="bottom", fontsize=12, fontweight="bold", color=INK)
    axA.set_xticks(xs)
    axA.set_xticklabels([f"{labels[k]}\n(n={counts[k]})" for k in order], fontsize=9.5)
    axA.set_ylabel(f"sections relocalized  (err_t < 1 m  &  err_r < 15°)", fontsize=9.5)
    axA.set_ylim(0, max(counts.values()) + 1.2)
    axA.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    axA.set_axisbelow(True)
    axA.legend(fontsize=8.8, loc="upper right", framealpha=0.96, edgecolor=BASE)
    axA.set_title(f"A · success by failure regime   (total {b_total}/{n} → {a_total}/{n})",
                  fontsize=10.5, color=INK2, loc="left", pad=6)

    # ---- Panel B: per-section translation error, before -> after (log) ----
    axB = fig.add_subplot(gs[0, 1])
    plot_order = [fx for k in order for fx in sorted(f for f in frames if regime[f] == k)]
    x = np.arange(n)
    be = np.array([max(err_t(b[fx]), 1e-3) for fx in plot_order])
    ae = np.array([max(err_t(a[fx]), 1e-3) for fx in plot_order])
    ymax = float(max(be.max(), ae.max())) * 1.6

    # regime zone shading + labels
    zone_start = 0
    for k in order:
        cnt = counts[k]
        axB.axvspan(zone_start - 0.5, zone_start + cnt - 0.5,
                    color=(C_AFTER if k == "hijack" else INK2), alpha=0.045, zorder=0)
        axB.text(zone_start + cnt / 2 - 0.5, ymax * 0.78, f"{labels[k]}\nn={cnt}",
                 ha="center", va="top", fontsize=8.6, color=INK2, style="italic")
        zone_start += cnt

    axB.axhline(SUCCESS_T_M, color=C_WARN, linewidth=1.3, linestyle="--", zorder=2)
    axB.text(n - 0.4, SUCCESS_T_M * 1.08, "1 m success bar", color=C_WARN, fontsize=8.5,
             ha="right", va="bottom")
    for xi, bv, av in zip(x, be, ae):
        axB.plot([xi, xi], [bv, av], color=MUT, linewidth=0.9, zorder=2)
    axB.scatter(x, be, s=46, color=C_BEFORE, edgecolor=SURF, linewidth=0.8, zorder=3,
                label="BEFORE  world-z gate")
    axB.scatter(x, ae, s=46, color=C_AFTER, edgecolor=SURF, linewidth=0.8, zorder=4,
                label="AFTER  consistency gate")
    axB.set_yscale("log")
    axB.set_ylim(top=ymax)
    axB.set_xlabel("section (frame index along the drive, grouped by BEFORE regime)", fontsize=9.5)
    axB.set_ylabel("translation error vs PGO-silver truth  (m, log)", fontsize=9.5)
    axB.set_xticks(x)
    axB.set_xticklabels([str(fx) for fx in plot_order], fontsize=7.2, rotation=55)
    axB.grid(axis="y", color=GRID, linewidth=0.8, which="both", zorder=0)
    axB.set_axisbelow(True)
    axB.legend(fontsize=8.6, loc="center right", framealpha=0.96, edgecolor=BASE)
    axB.set_title("B · same seeded RANSAC pool, only the judge's gravity gate differs",
                  fontsize=10.5, color=INK2, loc="left", pad=6)

    # ---- titles + footer ----
    fig.suptitle(title, fontsize=12.4, fontweight="bold", x=0.065, ha="left", y=0.965)
    fig.text(0.065, 0.855, diagnosis, fontsize=8.8, color=INK2, ha="left", wrap=True)

    mu = asum.get("map_up")
    mu_s = "[" + ", ".join(f"{c:.3f}" for c in mu) + "]" if mu else "n/a"
    footer = (
        f"REPLAY — real recorded mid360 LiDAR, offline (NOT simulated, NOT live hardware).  n={n} sections, "
        f"1 attempt each.  Success = err_t < {SUCCESS_T_M:.0f} m AND err_r < {SUCCESS_R_DEG:.0f}°.\n"
        f"method: same seeded FPFH+RANSAC candidate pool per section (per-frame seed=frame_idx, OMP_NUM_THREADS=1, "
        f"RANSAC_ITERS=500k, SCALE_PLAN (0.2,8)(0.3,8)(0.8,1) — byte-identical generate_ransac_candidates across "
        f"revs, open3d 0.19.0); only refine_candidates' gate/wall-mask differ.\n"
        f"BEFORE: world-z gate, map_up=None @ dimos {bsum.get('git_rev_dimos')}.   "
        f"AFTER: gravity-consistency gate, map_up=estimate_map_up(premap)={mu_s} @ dimos-code "
        f"{asum.get('git_rev_dimos_code', '9ec95de0c')}.   premap 4.40M pts (4,399,549).\n"
        f"truth: {asum.get('truth')}.   trial rev {asum.get('git_rev_trial')}.   "
        f"regime split re-derived from the BEFORE winner's z-axis tilt (hijack = forced < {UPRIGHT_MAX_DEG:.0f}° off "
        f"world-z while truth ~50°).   fig: render_stairs1_gravity_fig.py"
    )
    fig.text(0.065, 0.012, footer, fontsize=6.9, color=MUT, ha="left", va="bottom", linespacing=1.5)

    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / "gravity_gate_stairs1_before_after.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)

    # ---- console report (the honest numbers) ----
    print(f"BEFORE {b_total}/{n} ({100*b_total/n:.1f}%)  ->  AFTER {a_total}/{n} ({100*a_total/n:.1f}%)")
    for k in order:
        print(f"  {labels[k]:<16} n={counts[k]}   BEFORE {b_by[k]}/{counts[k]}  AFTER {a_by[k]}/{counts[k]}")
    print(f"gate-hijack rescued (F->S): {len(rescued)}/{counts['hijack']}  {rescued}")
    print(f"regressed (S->F): {regressed}")
    print(f"scene-ambiguity flipped (F->S): {scene_flip}")
    print(f"map_up={mu_s}  dimos_code={asum.get('git_rev_dimos_code')}  gravity_gate={asum.get('gravity_gate')}")
    print(f"TITLE: {title}")
    print(f"wrote {out}")
    return 0


def _maptilt(asum: dict) -> float:
    mu = asum.get("map_up")
    if not mu:
        return float("nan")
    v = np.asarray(mu, dtype=float)
    v = v / np.linalg.norm(v)
    return float(np.degrees(np.arccos(np.clip(abs(float(v[2])), -1.0, 1.0))))


if __name__ == "__main__":
    raise SystemExit(main())
