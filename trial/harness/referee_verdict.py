#!/usr/bin/env python3
"""Referee-tag verdict for a decorrelated benchmark run (replay).

The benchmark_setup.yaml decorrelation rule gives each recording ONE referee
tag that never enters the marker map and never produces fiducial fixes. This
script grades run_bench.py results against that referee:

1. Per-config summary re-derived from the raw per-section results (success,
   medians, per-source wins, fix/gate splits) — cross-check of run_bench's own
   summary block.
2. Flip table ransac -> ransac+fiducial: which sections changed verdict, and
   which fiducial tags supplied their fixes.
3. THE REFEREE VERDICT: sections whose accumulation window contains referee
   sightings get their T_est graded by the referee — implied tag placement
   (T_est o inv(world_raw_T_body(query)) applied to each in-window raw
   sighting) vs (a) the referee's consensus map position (co-truth, PGO-
   correlated centroid) and (b) the config's OWN placement cloud spread across
   start/end visits (pure revisit logic, no PGO in the grading). T_true gets
   the same treatment, so the truth's own referee agreement is on the table.
   world_raw_T_body(query) = inv(correction_at(ts)) @ T_true — markers.py's
   recipe; the raw lidar stream carries identity placeholders on re-posed
   recordings, so it cannot be used here.
4. Decorrelation denominators, printed plainly: how many sections saw the
   referee, how many of those carried fiducial fixes, whether the two arms
   even differ on that subset.

Run: cd dimos && uv run python ../trial/harness/referee_verdict.py \
         recording_go2_mid360_2026-05-29_4-45pm-PST
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import transform_to_mat  # noqa: E402

CONFIGS = ["ransac", "ransac_fiducial", "fiducial_judge"]


def load(recording: str):
    with open(HARNESS / "out" / "prepared" / f"{recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])
    mk = HARNESS / "out" / "markers"
    referee = json.load(open(mk / f"{recording}.referee.json"))
    fixes = {int(k): v for k, v in
             json.load(open(mk / f"{recording}.fixes.json")).items()}
    runs = {}
    for c in CONFIGS:
        p = HARNESS / "out" / "results" / f"{recording}.{c}.json"
        if p.exists():
            runs[c] = json.load(open(p))
    return prep, graph, referee, fixes, runs


def summarize(name: str, run: dict, fixes: dict) -> None:
    rs = run["results"]
    ok = [r for r in rs if r["status"] == "ok"]
    n = len(rs)
    succ = [r for r in ok if r["success"]]
    print(f"\n== {name} (n={n} sections, full denominator) ==")
    print(f"  success_rate_all: {len(succ)}/{n} = {100*len(succ)/n:.1f}%  "
          f"(ok={len(ok)} crashed={sum(r['status']=='crashed' for r in rs)} "
          f"no_candidates={sum(r['status']=='no_candidates' for r in rs)})")
    if ok:
        print(f"  median err_t (ok): {np.median([r['err_t'] for r in ok]):.3f} m   "
              f"median err_t (successes): "
              f"{np.median([r['err_t'] for r in succ]):.3f} m" if succ else "")
    print(f"  median dt: {np.median([r['dt'] for r in rs]):.2f} s  "
          f"(p90 {np.percentile([r['dt'] for r in rs], 90):.2f} s)")
    src = {}
    for r in ok:
        src[r["source"]] = src.get(r["source"], 0) + 1
    print(f"  per-source wins (ok sections): {src}")
    fx_set = set(fixes)
    for label, sel in [("fix-carrying", [r for r in rs if r["frame_idx"] in fx_set]),
                       ("no-fix", [r for r in rs if r["frame_idx"] not in fx_set])]:
        sn = sum(r.get("success", False) for r in sel)
        print(f"  {label:>12} sections: {sn}/{len(sel)} succeed")
    for label, sel in [("gate-reached", [r for r in rs if r.get("reached_gate")]),
                       ("below-gate", [r for r in rs if not r.get("reached_gate")])]:
        sn = sum(r.get("success", False) for r in sel)
        print(f"  {label:>12} sections: {sn}/{len(sel)} succeed")


def flips(a: dict, b: dict, fixes: dict, la: str, lb: str) -> None:
    ra = {r["frame_idx"]: r for r in a["results"]}
    rb = {r["frame_idx"]: r for r in b["results"]}
    print(f"\n== flips {la} -> {lb} ==")
    any_flip = False
    for fi in sorted(ra):
        sa, sb = ra[fi].get("success", False), rb[fi].get("success", False)
        if sa == sb:
            continue
        any_flip = True
        tags = sorted({c["marker_id"] for c in fixes.get(fi, [])})
        print(f"  frame {fi}: {'FAIL->SUCCESS' if sb else 'SUCCESS->FAIL'}  "
              f"err {ra[fi].get('err_t', float('nan')):.2f}m/"
              f"{ra[fi].get('err_r', float('nan')):.0f}deg -> "
              f"{rb[fi].get('err_t', float('nan')):.2f}m/"
              f"{rb[fi].get('err_r', float('nan')):.0f}deg  "
              f"src {ra[fi].get('source','-')}->{rb[fi].get('source','-')}  "
              f"fix tags {tags or 'NONE'}")
    if not any_flip:
        print("  none")


def referee_verdict(prep, graph, referee, fixes, runs) -> None:
    dets = referee["detections"]
    det_ts = np.array([d["ts"] for d in dets])
    det_raw = np.array([d["pos_world_raw_m"] for d in dets])
    cons = np.array(referee["consensus_map_position_m"])
    t0 = det_ts.min()
    sections = {s["frame_idx"]: s for s in prep["sections"]}

    # world_raw_T_body at each query — markers.py's recipe (graph-derived).
    P = {fi: np.linalg.inv(transform_to_mat(graph.correction_at(s["ts"]))) @ s["T_true"]
         for fi, s in sections.items()}

    saw = {}
    for fi, s in sections.items():
        m = (det_ts >= s["ts"] - s["window_s"]) & (det_ts <= s["ts"])
        if m.any():
            saw[fi] = m
    print(f"\n== REFEREE VERDICT (tag {referee['meta']['referee_tag']}, "
          f"{len(dets)} sightings, consensus rms {referee['consensus_rms_m']:.3f} m) ==")
    print(f"  sections that SAW the referee in-window: {sorted(saw)} (n={len(saw)})")
    with_fix = sorted(set(saw) & set(fixes))
    print(f"  of those, carrying fiducial fixes: {with_fix or 'NONE'} "
          f"(n={len(with_fix)}) <- decorrelated within-section denominator")

    # Do the arms even differ on the referee subset?
    if "ransac" in runs and "ransac_fiducial" in runs:
        ra = {r["frame_idx"]: r for r in runs["ransac"]["results"]}
        rf = {r["frame_idx"]: r for r in runs["ransac_fiducial"]["results"]}
        deltas = []
        for fi in sorted(saw):
            Ta, Tf = ra[fi].get("T_est"), rf[fi].get("T_est")
            if Ta is None or Tf is None:
                deltas.append((fi, None))
            else:
                deltas.append((fi, float(np.linalg.norm(
                    np.asarray(Ta)[:3, 3] - np.asarray(Tf)[:3, 3]))))
        print("  |t(ransac) - t(ransac+fiducial)| on referee sections: "
              + ", ".join(f"{fi}:{d if d is None else f'{d:.4f}m'}" for fi, d in deltas))

    # Placement clouds: per config + truth. Placement = est_map_T_worldraw
    # applied to each in-window raw sighting position.
    def placements(get_T):
        pts, per_sec = [], {}
        for fi, m in sorted(saw.items()):
            T = get_T(fi)
            if T is None:
                continue
            M = np.asarray(T) @ np.linalg.inv(P[fi])  # est map_T_worldraw
            p = (M[:3, :3] @ det_raw[m].T).T + M[:3, 3]
            pts.append(p)
            per_sec[fi] = p
        return (np.vstack(pts) if pts else np.empty((0, 3))), per_sec

    def report(label, cloud, per_sec):
        if not len(cloud):
            print(f"  {label:>16}: no placements")
            return
        d_cons = np.linalg.norm(cloud - cons, axis=1)
        spread = np.linalg.norm(cloud - cloud.mean(0), axis=1)
        start = np.vstack([p for fi, p in per_sec.items()
                           if sections[fi]["ts"] - t0 < 100]) if any(
            sections[fi]["ts"] - t0 < 100 for fi in per_sec) else np.empty((0, 3))
        end = np.vstack([p for fi, p in per_sec.items()
                         if sections[fi]["ts"] - t0 > 600]) if any(
            sections[fi]["ts"] - t0 > 600 for fi in per_sec) else np.empty((0, 3))
        se = (float(np.linalg.norm(start.mean(0) - end.mean(0)))
              if len(start) and len(end) else float("nan"))
        per = "  ".join(f"{fi}:{np.median(np.linalg.norm(p - cons, axis=1)):.2f}m"
                        for fi, p in per_sec.items())
        print(f"  {label:>16}: vs consensus median {np.median(d_cons):.3f} m "
              f"(p90 {np.percentile(d_cons, 90):.3f})  own-cloud median dev "
              f"{np.median(spread):.3f} m  start-vs-end centroid gap {se:.3f} m  "
              f"n={len(cloud)}")
        print(f"  {'':>16}  per-section vs consensus: {per}")

    report("T_true (truth)", *placements(lambda fi: sections[fi]["T_true"]))
    for c in CONFIGS:
        if c not in runs:
            continue
        rr = {r["frame_idx"]: r for r in runs[c]["results"]}
        report(c, *placements(lambda fi: rr[fi].get("T_est")))
        # success-vs-PGO-truth on the referee subset, for the same table
        sel = [rr[fi] for fi in sorted(saw)]
        print(f"  {'':>16}  PGO-graded on referee subset: "
              f"{sum(r.get('success', False) for r in sel)}/{len(sel)} succeed "
              f"({', '.join(str(r['frame_idx']) + ':' + ('S' if r.get('success') else 'F') for r in sel)})")


def main() -> int:
    recording = sys.argv[1]
    prep, graph, referee, fixes, runs = load(recording)
    print(f"referee_verdict: {recording}  configs loaded: {sorted(runs)}  "
          f"rung=replay  truth labels per block")
    for c in CONFIGS:
        if c in runs:
            summarize(c, runs[c], fixes)
    if "ransac" in runs and "ransac_fiducial" in runs:
        flips(runs["ransac"], runs["ransac_fiducial"], fixes,
              "ransac", "ransac+fiducial")
    if "ransac" in runs and "fiducial_judge" in runs:
        flips(runs["ransac"], runs["fiducial_judge"], fixes,
              "ransac", "fiducial+judge")
    referee_verdict(prep, graph, referee, fixes, runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
