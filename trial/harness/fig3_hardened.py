#!/usr/bin/env python3
"""Hardened long-gap revisit figure — the send-to-a-top-engineer version.

Same question as the revisit sweep (one physical referee tag per recording:
observe -> walk -> observe again; the two placements must match), with every
named hole in the naive figure closed:

  1. PGO wobble       — N_RUNS independent PGO runs per recording (fresh graph
                        each; detections recomputed against each graph). Dots =
                        per-run long-gap medians; bar = median across runs.
                        3 runs proved too few: a verification draw on village3
                        landed at 0.223 m, below the 3-run band envelope
                        (known v3 draws span 0.22-0.47 m) — hence 5.
  2. Uncertainty      — bootstrap over VISITS, not pairs (pairs within a visit
                        are massively correlated): cluster sightings into
                        visits by >5 s gaps, resample visits with replacement
                        (B=1000), recompute the long-gap median over pairs
                        spanning distinct sampled visits. 5-95% band per run,
                        enveloped across runs. It quantifies visit sampling,
                        NOT the PGO re-run distribution — the dots carry that,
                        and the footer says so. Effective n (visits) stated on
                        each panel.
  3. Tail             — p90 tick above each bar from the real pair
                        distribution (pooled across the runs).
  4. Floor            — dashed line at the 0-10 s-gap bucket median (raw):
                        what the detector+platform report for the SAME tag
                        with essentially no drift accrued. Improvements below
                        this line are unresolvable.
  5. Honesty block    — figure footer names the exclusions, the metric's
                        blindness, provenance, camera timing jitter (0.13 m
                        measured, e45773a), dropped bootstrap replicates,
                        arbitrary edges.
  6. One bucket       — LONG gap only (>=60 s villages, >=300 s walk), linear
                        independent y per panel, values annotated.

Raw pair distances are graph-independent (detection is deterministic), so the
three runs move only the PGO series — asserted at runtime, not assumed.

Run: cd dimos && uv run python ../trial/harness/fig3_hardened.py
Output: trial/results/figures/revisit_medians_hardened.png
        + trial/harness/out/markers/fig3_hardened.json (all numbers)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from markers import detect_all  # noqa: E402
from prep import _git_rev, build_graph, reposed_lidar_obs  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402

FIGURES = HARNESS.parent / "results" / "figures"
OUT_JSON = HARNESS / "out" / "markers" / "fig3_hardened.json"

N_RUNS = 5  # 3 under-sampled PGO wobble: a 4th v3 graph fell below the 3-run envelope
B = 1000
VISIT_GAP_S = 5.0  # >5 s between consecutive sightings starts a new visit
FLOOR_HI_S = 10.0  # floor bucket = gaps in [0, 10) s
SEED_BASE = 20260718  # bootstrap rng seed root; full seed printed per run

# (recording, panel label, referee tag id, long-gap lower edge [s],
#  (lidar_stream, odom_stream) to re-pose from payload — walk only)
RECS = [
    ("hk_village1", "village1", 10, 60.0, None),
    ("hk_village3", "village3", 10, 60.0, None),
    ("hk_village5", "village5", 10, 60.0, None),
    ("hk_village6", "village6", 10, 60.0, None),
    ("recording_go2_mid360_2026-05-29_4-45pm-PST",
     "mid360 walk (go2 lane)", 4, 300.0, ("lidar", "odom")),
]

INK = "#222222"
C_RAW = "#7f7f7f"  # raw odometry — recessive neutral (repo-wide identity)
C_PGO = "#1f77b4"  # PGO-corrected (repo-wide identity)


def cluster_visits(ts: np.ndarray) -> np.ndarray:
    """Visit id per sighting: consecutive-sighting gap > VISIT_GAP_S splits."""
    vid = np.zeros(len(ts), dtype=int)
    for i in range(1, len(ts)):
        vid[i] = vid[i - 1] + (1 if ts[i] - ts[i - 1] > VISIT_GAP_S else 0)
    return vid


def pair_table(rows: list[dict]) -> dict:
    """All sighting pairs of one run: gaps, raw/pgo distances, visit ids."""
    ts = np.array([r["ts"] for r in rows])
    raw = np.array([r["T_world_tag_raw"][:3, 3] for r in rows])
    pgo = np.array([r["T_map_tag_corr"][:3, 3] for r in rows])
    vid = cluster_visits(ts)
    i, j = np.triu_indices(len(ts), k=1)
    return {
        "ts": ts, "vid": vid, "i": i, "j": j,
        "gap": ts[j] - ts[i],
        "d_raw": np.linalg.norm(raw[i] - raw[j], axis=1),
        "d_pgo": np.linalg.norm(pgo[i] - pgo[j], axis=1),
    }


def visit_bootstrap(pt: dict, long_lo: float, dist_key: str,
                    rng: np.random.Generator) -> dict:
    """5-95% band of the long-gap median under resampling of VISITS.

    Resample the n_visits visit slots with replacement; the replicate's
    statistic is the median over pair distances spanning two DISTINCT sampled
    visits (gap filter applied on real timestamps). Replicates whose sampled
    visit multiset yields zero qualifying pairs are dropped and counted."""
    vid, i, j = pt["vid"], pt["i"], pt["j"]
    mask = pt["gap"] >= long_lo
    n_visits = int(vid.max()) + 1
    dist_by_vp: dict[tuple[int, int], np.ndarray] = {}
    for a in range(n_visits):
        for b in range(a + 1, n_visits):
            m = mask & (vid[i] == a) & (vid[j] == b)  # ts sorted => vid[i]<=vid[j]
            if m.any():
                dist_by_vp[(a, b)] = pt[dist_key][m]
    meds = np.full(B, np.nan)
    for t in range(B):
        s = rng.integers(0, n_visits, n_visits)
        chunks = [dist_by_vp[(min(s[a], s[c]), max(s[a], s[c]))]
                  for a in range(n_visits) for c in range(a + 1, n_visits)
                  if s[a] != s[c]
                  and (min(s[a], s[c]), max(s[a], s[c])) in dist_by_vp]
        if chunks:
            meds[t] = np.median(np.concatenate(chunks))
    valid = meds[~np.isnan(meds)]
    return {"p5": float(np.percentile(valid, 5)),
            "p95": float(np.percentile(valid, 95)),
            "n_valid_replicates": int(len(valid))}


def run_recording(name: str, referee: int, long_lo: float,
                  repose: tuple[str, str] | None, rec_idx: int) -> dict:
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{name}.db"), must_exist=True)
    runs = []
    with store:
        obs_list = None
        if repose:
            lidar_stream, odom_stream = repose
            t0 = time.perf_counter()
            obs_list = reposed_lidar_obs(store, lidar_stream, odom_stream)
            print(f"[{name}] re-posed {len(obs_list)} scans from '{odom_stream}' "
                  f"payload ({time.perf_counter()-t0:.0f}s)", flush=True)
        else:
            lidar_stream = "lidar"
        for k in range(N_RUNS):
            graph, pgo_s = build_graph(store, lidar_stream, obs_list=obs_list)
            t0 = time.perf_counter()
            rows = sorted((r for r in detect_all(store, graph)
                           if r["marker_id"] == referee), key=lambda r: r["ts"])
            det_s = time.perf_counter() - t0
            pt = pair_table(rows)
            long_m = pt["gap"] >= long_lo
            floor_m = pt["gap"] < FLOOR_HI_S
            cross_m = long_m & (pt["vid"][pt["i"]] != pt["vid"][pt["j"]])
            seed = [SEED_BASE, rec_idx, k]
            run = {
                "run": k, "seed": seed,
                "n_keyframes": len(graph.keyframes), "n_loops": len(graph.loops),
                "pgo_seconds": round(pgo_s, 1), "detect_seconds": round(det_s, 1),
                "n_sightings": len(rows), "n_visits": int(pt["vid"].max()) + 1,
                "n_long_pairs": int(long_m.sum()),
                "n_long_pairs_within_visit": int((long_m & ~cross_m).sum()),
                "median_raw_m": float(np.median(pt["d_raw"][long_m])),
                "median_pgo_m": float(np.median(pt["d_pgo"][long_m])),
                "p90_raw_m": float(np.percentile(pt["d_raw"][long_m], 90)),
                "p90_pgo_m": float(np.percentile(pt["d_pgo"][long_m], 90)),
                "floor_raw_m": float(np.median(pt["d_raw"][floor_m])),
                "floor_pgo_m": float(np.median(pt["d_pgo"][floor_m])),
                "boot_raw": visit_bootstrap(pt, long_lo, "d_raw",
                                            np.random.default_rng(seed)),
                "boot_pgo": visit_bootstrap(pt, long_lo, "d_pgo",
                                            np.random.default_rng(seed)),
                "_d_raw_long": pt["d_raw"][long_m], "_d_pgo_long": pt["d_pgo"][long_m],
            }
            runs.append(run)
            print(f"[{name}] run {k}: kf={run['n_keyframes']} loops={run['n_loops']} "
                  f"pgo={pgo_s:.1f}s det={det_s:.1f}s | long(>={long_lo:.0f}s) "
                  f"n={run['n_long_pairs']} raw {run['median_raw_m']:.3f} "
                  f"pgo {run['median_pgo_m']:.3f} | boot pgo 5-95% "
                  f"[{run['boot_pgo']['p5']:.3f}, {run['boot_pgo']['p95']:.3f}] "
                  f"({run['boot_pgo']['n_valid_replicates']}/{B} valid) | "
                  f"floor raw {run['floor_raw_m']:.3f} | seed {seed}", flush=True)

    # Raw is graph-independent and detection deterministic — assert, don't assume.
    raw_meds = [r["median_raw_m"] for r in runs]
    assert max(raw_meds) - min(raw_meds) < 1e-9, f"raw medians differ across runs: {raw_meds}"

    agg = {
        "recording": name, "referee_tag": referee, "long_lo_s": long_lo,
        "n_sightings": runs[0]["n_sightings"], "n_visits": runs[0]["n_visits"],
        "n_long_pairs": runs[0]["n_long_pairs"],
        "n_long_pairs_within_visit": runs[0]["n_long_pairs_within_visit"],
        "bar_raw_m": float(np.median(raw_meds)),
        "bar_pgo_m": float(np.median([r["median_pgo_m"] for r in runs])),
        # p90 from the real pair distribution, pooled across the runs
        "p90_raw_m": float(np.percentile(np.concatenate([r["_d_raw_long"] for r in runs]), 90)),
        "p90_pgo_m": float(np.percentile(np.concatenate([r["_d_pgo_long"] for r in runs]), 90)),
        # 5-95% visit-bootstrap band, envelope across the runs
        "band_raw_m": [min(r["boot_raw"]["p5"] for r in runs),
                       max(r["boot_raw"]["p95"] for r in runs)],
        "band_pgo_m": [min(r["boot_pgo"]["p5"] for r in runs),
                       max(r["boot_pgo"]["p95"] for r in runs)],
        "floor_raw_m": runs[0]["floor_raw_m"],  # graph-independent
        "runs": [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs],
        "_run_medians_raw": raw_meds,
        "_run_medians_pgo": [r["median_pgo_m"] for r in runs],
    }
    return agg


def draw_panel(ax, a: dict, label: str) -> None:
    xs = {"raw": 0.0, "pgo": 1.0}
    ymax = 1.22 * max(a["p90_raw_m"], a["p90_pgo_m"], a["band_raw_m"][1],
                      a["band_pgo_m"][1], a["bar_raw_m"], a["bar_pgo_m"],
                      a["floor_raw_m"])
    ax.bar(xs["raw"], a["bar_raw_m"], 0.62, color=C_RAW, zorder=2)
    ax.bar(xs["pgo"], a["bar_pgo_m"], 0.62, color=C_PGO, zorder=2)
    for key, bar, band, p90, meds in (
        ("raw", a["bar_raw_m"], a["band_raw_m"], a["p90_raw_m"], a["_run_medians_raw"]),
        ("pgo", a["bar_pgo_m"], a["band_pgo_m"], a["p90_pgo_m"], a["_run_medians_pgo"]),
    ):
        x = xs[key]
        lo, hi = band
        ax.vlines(x, lo, hi, color=INK, lw=1.3, zorder=4)
        ax.hlines([lo, hi], x - 0.07, x + 0.07, color=INK, lw=1.1, zorder=4)
        ax.scatter(np.linspace(x - 0.2, x + 0.2, len(meds)), meds, s=15,
                   facecolors="white", edgecolors=INK, linewidths=0.9, zorder=6)
        ax.hlines(p90, x - 0.21, x + 0.21, color=INK, lw=1.6, zorder=5)
        ax.text(x - 0.36, bar, f"{bar:.3f}", ha="right", va="center",
                fontsize=7.2, color=INK)
        # beside the tick, not centered on it — the band vline runs through
        # centered text on v1/v5 (seen at 150 dpi)
        ax.text(x + 0.25, p90, f"p90 {p90:.2f}", ha="left", va="center",
                fontsize=6.0, color=INK, alpha=0.85)
    ax.axhline(a["floor_raw_m"], ls=(0, (4, 3)), color=INK, lw=0.9, zorder=1)
    ax.text(0.03, 0.985,
            f"visits n={a['n_visits']} · sightings {a['n_sightings']}\n"
            f"pairs {a['n_long_pairs']} · floor {a['floor_raw_m']:.3f}",
            transform=ax.transAxes, ha="left", va="top", fontsize=6.4, color=INK)
    ax.set_title(f"{label}\ngap ≥ {a['long_lo_s']:.0f} s", fontsize=9)
    ax.set_xlim(-0.85, 1.85)
    ax.set_ylim(0, ymax)
    ax.set_xticks([0, 1], ["raw odom", "PGO"], fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def load_results() -> tuple[list[dict], str, str]:
    """Reconstruct panel inputs from a previous full run's JSON (layout iteration
    without paying for 15 PGO+detection passes again). Returns the git revs the
    RUNS were made at — replot must not restamp provenance with today's HEAD."""
    with open(OUT_JSON) as f:
        data = json.load(f)
    results = data["recordings"]
    for a in results:
        a["_run_medians_raw"] = [r["median_raw_m"] for r in a["runs"]]
        a["_run_medians_pgo"] = [r["median_pgo_m"] for r in a["runs"]]
    return results, data["meta"]["git_rev_dimos"], data["meta"]["git_rev_trial"]


def main() -> int:
    import argparse

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replot", action="store_true",
                    help="redraw from out/markers/fig3_hardened.json (no re-run)")
    args = ap.parse_args()

    t_all = time.perf_counter()
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    if args.replot:
        results, rev_dimos, rev_trial = load_results()
    else:
        rev_dimos = _git_rev(dimos_root)
        rev_trial = _git_rev(HARNESS.parent.parent)
        results = [run_recording(name, ref, lo, repose, idx)
                   for idx, (name, _, ref, lo, repose) in enumerate(RECS)]

    fig, axes = plt.subplots(1, len(RECS), figsize=(16.5, 5.6))
    for ax, a, (_, label, *_rest) in zip(axes, results, RECS):
        draw_panel(ax, a, label)
    axes[0].set_ylabel("same-tag pair distance [m]", fontsize=9)
    fig.suptitle(
        "Relocalization benchmark — long-gap revisit, hardened: one physical referee tag per "
        "recording; distance between its repeated placements across the long gap · replay",
        fontsize=10.5, y=0.995)
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=C_RAW),
                        plt.Rectangle((0, 0), 1, 1, color=C_PGO)],
               labels=["raw odometry", "PGO-corrected"],
               loc="upper right", fontsize=8, frameon=False,
               bbox_to_anchor=(0.995, 0.985))

    method = (f"{N_RUNS} independent PGO runs/recording (fresh graph, detections recomputed) · bar = "
              f"median of {N_RUNS} run medians · dots = run medians · band = 5–95% bootstrap over visits "
              f"(>{VISIT_GAP_S:.0f} s clusters, B={B}, pairs spanning distinct sampled visits; "
              "per run, enveloped) · p90 = pooled pairs · dashed line = detection floor "
              "(0–10 s-gap median, raw): sub-floor differences are unresolvable")
    n_rep = [b["n_valid_replicates"] for a in results for r in a["runs"]
             for b in (r["boot_raw"], r["boot_pgo"])]
    honesty = ("n=4 of 6 villages (2 excluded: duplicate physical ids) · consistency, not absolute "
               "accuracy (blind to common-mode shifts) · camera-pose provenance verified on "
               "village3 + walk, inherited for villages 1/5/6")
    honesty2 = ("walk bars carry <=0.13 m measured camera-pose timing jitter · band is visit "
                "sampling, NOT a PGO re-run interval (v3 medians 0.22–0.47 m across repeated "
                f"fresh graphs) · bootstrap keeps {min(n_rep)}–{max(n_rep)}/{B} "
                "replicates (degenerate visit resamples dropped) · bucket edges are arbitrary "
                "constants")
    repro = (f"cmd: cd dimos && uv run python ../trial/harness/fig3_hardened.py · "
             f"dimos {rev_dimos} · trial {rev_trial} · "
             f"bootstrap seeds [{SEED_BASE}, rec_idx, run] · "
             f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')}")
    fig.text(0.005, 0.076, method, fontsize=6.2, color=INK, alpha=0.9)
    fig.text(0.005, 0.052, honesty, fontsize=6.2, color=INK, alpha=0.9)
    fig.text(0.005, 0.028, honesty2, fontsize=6.2, color=INK, alpha=0.9)
    fig.text(0.005, 0.004, repro, fontsize=6.2, color=INK, alpha=0.9)
    fig.subplots_adjust(left=0.045, right=0.99, top=0.83, bottom=0.175, wspace=0.30)

    FIGURES.mkdir(parents=True, exist_ok=True)
    out_png = FIGURES / "revisit_medians_hardened.png"
    fig.savefig(out_png, dpi=150)
    plt.close(fig)

    if args.replot:
        print(f"\nwrote {out_png} (replot from {OUT_JSON.name}; JSON untouched)")
        return 0
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump({"meta": {"n_runs": N_RUNS, "B": B, "visit_gap_s": VISIT_GAP_S,
                            "floor_bucket_s": [0, FLOOR_HI_S], "seed_base": SEED_BASE,
                            "git_rev_dimos": rev_dimos, "git_rev_trial": rev_trial,
                            "honesty": honesty + " · " + honesty2, "method": method},
                   "recordings": [{k: v for k, v in a.items() if not k.startswith("_")}
                                  for a in results]}, f, indent=1)
    print(f"\nwrote {out_png}\nwrote {OUT_JSON}\ntotal {time.perf_counter()-t_all:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
