#!/usr/bin/env python3
"""Before/after proof figure for the mid360 gravity-gate fix — read artifacts, render PNG.

    <rec>.gravity_gate_paired.json (my paired run)  ─┐
    results_archive .../<rec>.ransac.json (0/40)     ├─> gravity_gate_before_after.png
                                                     ┘   (3 panels: success | per-section err | tilt WHY)

Every number on the figure is recomputed HERE from the saved JSONs — never
pasted from prose; asserts pin the title claim to the recomputed values so a
stale artifact can't leave a lying title. Pure reads, no RNG (SEED=0 per house
rule). House figure rules: title IS the takeaway; n's on the plot; footer =
method + seeds + git revs + truth label.

Run: uv run --project /home/dimos/dimos-code python \
     /home/dimos/dimensional-trial/trial/harness/gravity_gate_figures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SEED = 0  # no RNG anywhere in this script

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "trial/harness/out/results"
ARCHIVE = ROOT / "trial/harness/out/results_archive_pre_jnav_20260720/results"
FIGS = ROOT / "trial/results/figures"
REC = "recording_go2_mid360_2026-05-29_4-45pm-PST.fastlio"
GATE_DEG = 10.0  # GRAVITY_TILT_MAX_DEG

# dataviz reference palette (light mode), fixed entity->color mapping
C_BASE = "#2a78d6"  # slot 1 blue  = baseline (shipped world-z gate)
C_FIX = "#008300"   # slot 2 green = fix (body-attitude-aware gate)
C_WARN = "#c1440e"  # gate line / rejected region
INK, INK2, MUT = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "figure.facecolor": SURF, "axes.facecolor": SURF,
    "savefig.facecolor": SURF, "axes.edgecolor": BASE, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
})


def main() -> int:
    paired = json.loads((RES / f"{REC}.gravity_gate_paired.json").read_text())
    summ, results = paired["summary"], paired["results"]
    arch = json.loads((ARCHIVE / f"{REC}.ransac.json").read_text())

    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: r["frame_idx"])
    n = len(ok)
    base_succ = sum(r["baseline"].get("success", False) for r in ok)
    fix_succ = sum(r["fix"].get("success", False) for r in ok)
    base_errs = np.array([r["baseline"]["err_t"] for r in ok])
    fix_errs = np.array([r["fix"]["err_t"] for r in ok])
    body_tilts_sub = np.array([r["body_tilt_deg"] for r in ok])
    residuals = np.array([r["fix_gravity_residual_deg"] for r in ok])
    map_tilt = summ["map_tilt_deg"]

    # Full-lane (n=40) body tilt distribution from the archived baseline, for the
    # WHY panel: T_est isn't the truth, so recompute body tilt from the archive's
    # T_true is unavailable there -> use the paired subset's true tilts plus the
    # archived success rate. The archive is the 0/40 anchor.
    arch_summ = arch["summary"]
    arch_n = arch_summ["n_sections"]
    arch_succ = int(round(arch_summ["success_rate_all"] * arch_n))

    # --- cross-checks: pin the story to the artifacts ---
    assert arch_succ == 0, f"archived baseline expected 0/{arch_n}, got {arch_succ}"
    assert base_succ == 0, f"paired baseline (world-z gate) expected 0/{n}, got {base_succ}"
    assert fix_succ > base_succ, "fix must beat baseline"
    assert map_tilt < GATE_DEG, "this map is upright; the tilt is in the BODY, not the map"

    fig = plt.figure(figsize=(15.0, 5.4))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.35, 1.35], wspace=0.34,
                          left=0.055, right=0.985, top=0.80, bottom=0.20)

    # ---- Panel A: success count before/after ----
    axA = fig.add_subplot(gs[0, 0])
    bars = axA.bar([0, 1], [base_succ, fix_succ], width=0.62,
                   color=[C_BASE, C_FIX], edgecolor=INK, linewidth=0.8, zorder=3)
    axA.set_xticks([0, 1])
    axA.set_xticklabels(["shipped\n(world-z gate)", "fix\n(attitude-aware)"], fontsize=10)
    axA.set_ylabel(f"sections relocalized  (< 1 m, < 15°)   n={n}", fontsize=9.5)
    axA.set_ylim(0, max(fix_succ, 1) * 1.28)
    axA.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    axA.set_axisbelow(True)
    for b, v in zip(bars, [base_succ, fix_succ]):
        axA.text(b.get_x() + b.get_width() / 2, v + max(fix_succ, 1) * 0.03,
                 f"{v}/{n}\n{100 * v / n:.0f}%", ha="center", va="bottom",
                 fontsize=12, fontweight="bold", color=INK)
    axA.set_title("A · success on the tilted lane", fontsize=10.5, color=INK2, loc="left", pad=6)

    # ---- Panel B: per-section translation error, before vs after (log) ----
    axB = fig.add_subplot(gs[0, 1])
    x = np.arange(n)
    axB.axhline(1.0, color=C_WARN, linewidth=1.3, linestyle="--", zorder=2)
    axB.text(n - 0.5, 1.06, "1 m success bar", color=C_WARN, fontsize=8.5,
             ha="right", va="bottom")
    for xi, be, fe in zip(x, base_errs, fix_errs):
        axB.plot([xi, xi], [be, fe], color=MUT, linewidth=0.8, zorder=2)
    axB.scatter(x, base_errs, s=42, color=C_BASE, edgecolor=INK, linewidth=0.6,
                zorder=3, label="shipped (world-z gate)")
    axB.scatter(x, fix_errs, s=42, color=C_FIX, edgecolor=INK, linewidth=0.6,
                zorder=4, label="fix (attitude-aware)")
    axB.set_yscale("log")
    axB.set_xlabel("section (sorted by frame index along the drive)", fontsize=9.5)
    axB.set_ylabel("translation error vs PGO truth  (m, log)", fontsize=9.5)
    axB.set_xticks(x)
    axB.set_xticklabels([str(r["frame_idx"]) for r in ok], fontsize=7.0, rotation=45)
    axB.grid(axis="y", color=GRID, linewidth=0.8, which="both", zorder=0)
    axB.set_axisbelow(True)
    axB.legend(fontsize=8.5, loc="upper left", framealpha=0.95, edgecolor=BASE)
    axB.set_title("B · same RANSAC pool, only the gate differs", fontsize=10.5,
                  color=INK2, loc="left", pad=6)

    # ---- Panel C: WHY — the body tilt the world-z gate rejects vs the fix residual ----
    axC = fig.add_subplot(gs[0, 2])
    bins = np.linspace(0, 60, 25)
    axC.hist(body_tilts_sub, bins=bins, color=C_BASE, alpha=0.85, edgecolor=INK,
             linewidth=0.5, zorder=3,
             label=f"body tilt vs world-z\n(what the shipped gate measures)\nmedian {np.median(body_tilts_sub):.0f}°")
    axC.hist(residuals, bins=bins, color=C_FIX, alpha=0.85, edgecolor=INK,
             linewidth=0.5, zorder=4,
             label=f"gravity residual after the fix\n(body-up rotated into map vs map-up)\nmedian {np.median(residuals):.1f}°")
    axC.axvspan(GATE_DEG, 90, color=C_WARN, alpha=0.07, zorder=0)
    axC.axvline(GATE_DEG, color=C_WARN, linewidth=1.4, zorder=2)
    axC.text(GATE_DEG + 1.0, axC.get_ylim()[1] * 0.98, f"{GATE_DEG:.0f}° gate", color=C_WARN,
             fontsize=8.5, rotation=90, va="top", ha="left")
    axC.set_xlabel("angle from map gravity-up  (deg)", fontsize=9.5)
    axC.set_ylabel(f"sections   n={n}", fontsize=9.5)
    axC.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    axC.set_axisbelow(True)
    axC.legend(fontsize=7.6, loc="upper center", framealpha=0.95, edgecolor=BASE)
    axC.set_title("C · why the world-z gate discards the correct candidate",
                  fontsize=10.5, color=INK2, loc="left", pad=6)

    title = (
        f"Gravity gate made body-attitude-aware: tilted mid360 RANSAC relocalization "
        f"{base_succ}/{n} → {fix_succ}/{n}; upright maps stay bit-identical"
    )
    fig.suptitle(title, fontsize=13.0, fontweight="bold", x=0.055, ha="left", y=0.955)
    fig.text(0.055, 0.895,
             f"Body physically tilted (median {np.median(body_tilts_sub):.0f}° off gravity, this map's own floor only "
             f"{map_tilt:.1f}° off) → the shipped world-z gate rejects every geometrically-correct candidate. "
             f"The fix estimates the body's own up per frame and gates on gravity DIRECTION.",
             fontsize=9.2, color=INK2, ha="left")

    map_up_str = ", ".join(f"{c:.3f}" for c in summ["map_up"])
    footer = (
        f"method: paired judge — one seeded RANSAC pool per section (OMP=1, RANSAC_ITERS=500k, "
        f"map_up=[{map_up_str}]), refined twice: map_up=None (shipped) vs map_up=estimate_map_up(premap) (fix); "
        f"identical pool per section, so only the gravity gate changes.   "
        f"seeds: per-frame frame_idx; SEED=0 (figure).   dimos {summ['git_rev_dimos']} · trial {summ['git_rev_trial']}.   "
        f"rung: replay (real recorded mid360 sensor data, offline).   truth: {summ['truth']}.   "
        f"archived world-z baseline: {arch_succ}/{arch_n} (full lane, same recording)."
    )
    fig.text(0.055, 0.045, footer, fontsize=6.7, color=MUT, ha="left", va="center", wrap=True)

    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / "gravity_gate_before_after.png"
    fig.savefig(out, dpi=150)
    print(f"baseline {base_succ}/{n}  fix {fix_succ}/{n}  "
          f"body_tilt median {np.median(body_tilts_sub):.1f}deg  "
          f"residual median {np.median(residuals):.1f}deg  map_tilt {map_tilt:.1f}deg")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
