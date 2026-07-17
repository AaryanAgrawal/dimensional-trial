#!/usr/bin/env python3
"""Marker pipeline: PGO-accuracy evidence, marker map, fiducial fixes.

Referee/fiducial split (benchmark_setup.yaml decorrelation rule): each
recording designates ONE referee tag — BENCHMARK ONLY. It stays in the
scatter/revisit stats and gets its own <rec>.referee.json (detections +
consensus map position), but it NEVER enters the marker map and NEVER
produces fiducial fixes; every other id is the live-fiducial set. Default
referee id comes from benchmark_setup.yaml; --benchmark-tag overrides. On
referee-only recordings (the villages, single tag id 10) an empty marker
map + zero fixes is the CORRECT output.

Four outputs from one detection pass (eval.py's own pipeline, smoothing OFF
so every sighting counts):

1. SCATTER — per marker id, RMS scatter of repeated sighting positions under
   raw odometry vs under PGO correction. A physical tag never moves, so this
   is the only truth signal that does NOT share PGO's failure modes: if PGO
   is accurate, corrected scatter must tighten. This is the missing PGO
   accuracy evidence, at real n (not the n=4 wash from Jul 16).

2. MARKER MAP — per id, aggregated PGO-corrected pose (map_T_tag): exactly
   what a deployment survey would produce. Derived from the SAME PoseGraph
   as the benchmark truth (prep.py pickles it) — deployment-realistic and
   truth-correlated; labeled as such wherever it is scored.

3. FIDUCIAL FIXES — per benchmark section, candidate map_T_body transforms a
   FiducialPrior would propose: map_T_tag o (world_T_tag @ det)^-1 bridged to
   the query frame over raw odometry (so the candidate honestly inherits the
   odom drift accumulated since the sighting — that drift IS the age cost).
   confidence = CONF_MAX * exp(-age/AGE_TAU_S), capped by AGE_MAX_S; these
   are documented, unverified defaults for the Phase-4 autoresearch loop to
   tune. The judge never reads confidence anyway — candidates win on fitness.

4. REFEREE JSON — the referee tag's detections + consensus map position,
   the offline instrument the benchmark grades against.

Run: cd dimos && uv run python ../trial/harness/markers.py hk_village3
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.memory2.transform import QualityWindow, SpeedLimit  # noqa: E402
from dimos.msgs.geometry_msgs.Transform import Transform  # noqa: E402
from dimos.msgs.sensor_msgs.Image import Image  # noqa: E402
from dimos.perception.fiducial.marker_transformer import DetectMarkers  # noqa: E402
from dimos.robot.unitree.go2.connection import _camera_info_static  # noqa: E402

SETUP_YAML = HARNESS / "benchmark_setup.yaml"

MARKER_LENGTH_M = 0.1  # every in-repo default; physical print kit is 100 mm
REPROJ_GATE_PX = 3.0  # visual reloc module's own gate
CONF_MAX = 0.9
AGE_TAU_S = 30.0  # unverified default — Phase 4 autoresearch tunes this
AGE_MAX_S = 120.0
MAX_FIXES_PER_SECTION = 3


def referee_from_setup(recording: str) -> int | None:
    """Default referee tag id: benchmark_setup.yaml recordings.<rec>.referee_tag."""
    if not SETUP_YAML.exists():
        return None
    rec = (yaml.safe_load(SETUP_YAML.read_text()) or {}).get("recordings", {}).get(recording) or {}
    tag = rec.get("referee_tag")
    return None if tag is None else int(tag)


def detect_all(store: SqliteStore, graph) -> list[dict]:
    """Every quality-gated sighting: raw + PGO-corrected world pose."""
    pipeline = (
        store.stream("color_image", Image)
        .transform(QualityWindow(lambda img: img.sharpness, window=0.1))
        .transform(SpeedLimit(max_mps=0.5, max_dps=50.0))
        .transform(DetectMarkers(camera_info=_camera_info_static(),
                                 marker_length_m=MARKER_LENGTH_M,
                                 smoothing_window=0.0))
    )
    rows = []
    for obs in pipeline:
        d = obs.data
        raw_tf = Transform(translation=d.center, rotation=d.orientation,
                           frame_id="world", child_frame_id=f"marker_{d.marker_id}",
                           ts=obs.ts)
        corr_tf = graph.correct(raw_tf)
        rows.append({
            "ts": float(obs.ts),
            "marker_id": int(d.marker_id),
            "reproj_px": float(d.reprojection_error),
            "T_world_tag_raw": pose7_to_mat((d.center.x, d.center.y, d.center.z,
                                             d.orientation.x, d.orientation.y,
                                             d.orientation.z, d.orientation.w)),
            "T_map_tag_corr": transform_to_mat(corr_tf),
        })
    return rows


REVISIT_BUCKETS_S = [(0, 10), (10, 30), (30, 60), (60, float("inf"))]


def scatter_stats(rows: list[dict]) -> dict:
    """Per-marker agreement, stratified by sighting time gap (leshy's PGO
    verification: observe marker -> drive a loop -> observe again -> the two
    locations must match). Aggregate scatter alone is a composition trap:
    short-gap pairs (no drift accrued) dilute it.

    Read bucket results per-visit, not per-gap: on village3 the entire 30-60s
    damage was ONE misplaced revisit pass (t~58-68s, wrong by ~1.3-1.5m even
    AT loop-anchor keyframes) — adversarially verified mechanism: PGO spreads
    the end-of-drive correction smoothly along the stiff odom chain (odom var
    1e-4 m2/edge vs loop var >=0.015 m2) and cannot represent non-monotonic
    drift (+0.5m by 58s reversing to -0.95m by 91s). No loop subset fixes it
    (ablated at thresh 0.3/0.15/0.10). Long-gap medians can likewise be
    dominated by particular visit pairs — quote which visits carry a bucket."""
    out = {}
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["marker_id"]].append(r)
    for mid, rs in sorted(by_id.items()):
        rs = sorted(rs, key=lambda r: r["ts"])
        raw = np.array([r["T_world_tag_raw"][:3, 3] for r in rs])
        cor = np.array([r["T_map_tag_corr"][:3, 3] for r in rs])
        ts = np.array([r["ts"] for r in rs])
        entry = {
            "n_sightings": len(rs),
            "rms_about_centroid_raw_m": float(np.sqrt(((raw - raw.mean(0)) ** 2).sum(1).mean())),
            "rms_about_centroid_pgo_m": float(np.sqrt(((cor - cor.mean(0)) ** 2).sum(1).mean())),
            "revisit_by_gap": {},
        }
        for lo, hi in REVISIT_BUCKETS_S:
            dr, dc = [], []
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    if lo <= ts[j] - ts[i] < hi:
                        dr.append(np.linalg.norm(raw[i] - raw[j]))
                        dc.append(np.linalg.norm(cor[i] - cor[j]))
            key = f"{lo}-{'inf' if hi == float('inf') else int(hi)}s"
            entry["revisit_by_gap"][key] = (
                {"n_pairs": len(dr),
                 "median_raw_m": float(np.median(dr)),
                 "median_pgo_m": float(np.median(dc)),
                 "p90_raw_m": float(np.percentile(dr, 90)),
                 "p90_pgo_m": float(np.percentile(dc, 90))}
                if dr else {"n_pairs": 0}
            )
        out[str(mid)] = entry
    return out


def derive_marker_map(rows: list[dict]) -> dict[int, np.ndarray]:
    """Per id: corrected-centroid position + lowest-reproj sighting's rotation."""
    by_id = defaultdict(list)
    for r in rows:
        if r["reproj_px"] <= REPROJ_GATE_PX:
            by_id[r["marker_id"]].append(r)
    marker_map = {}
    for mid, rs in by_id.items():
        pos = np.array([r["T_map_tag_corr"][:3, 3] for r in rs]).mean(0)
        best = min(rs, key=lambda r: r["reproj_px"])
        T = best["T_map_tag_corr"].copy()
        T[:3, 3] = pos
        marker_map[mid] = T
    return marker_map


def fiducial_fixes(rows, marker_map, sections, lidar_poses) -> dict[int, list[dict]]:
    """Per section: candidates map_T_body(query) a FiducialPrior would propose."""
    fixes: dict[int, list[dict]] = {}
    for s in sections:
        ts_q, fi = s["ts"], s["frame_idx"]
        P = lidar_poses.get(fi)  # world_raw_T_body at the query frame
        if P is None:
            continue
        window = [r for r in rows
                  if ts_q - s["window_s"] <= r["ts"] <= ts_q
                  and ts_q - r["ts"] <= AGE_MAX_S
                  and r["reproj_px"] <= REPROJ_GATE_PX
                  and r["marker_id"] in marker_map]
        window.sort(key=lambda r: (ts_q - r["ts"], r["reproj_px"]))
        out = []
        for r in window[:MAX_FIXES_PER_SECTION]:
            age = ts_q - r["ts"]
            T_map_world = marker_map[r["marker_id"]] @ np.linalg.inv(r["T_world_tag_raw"])
            T_cand = T_map_world @ P
            out.append({
                "T": T_cand.tolist(),
                "confidence": float(CONF_MAX * np.exp(-age / AGE_TAU_S)),
                "age_s": float(age),
                "marker_id": r["marker_id"],
                "reproj_px": r["reproj_px"],
            })
        if out:
            fixes[fi] = out
    return fixes


def print_referee_table(referee: int, stats: dict) -> None:
    st = stats.get(str(referee))
    if not st:
        print(f"referee tag {referee}: NEVER DETECTED")
        return
    print(f"referee tag {referee} revisit ({st['n_sightings']} sightings), "
          f"pair distance median/p90:")
    for k, b in st["revisit_by_gap"].items():
        if b["n_pairs"]:
            print(f"  gap {k:>7}: n={b['n_pairs']:>5}  raw {b['median_raw_m']:.3f}/"
                  f"{b['p90_raw_m']:.3f} m -> pgo {b['median_pgo_m']:.3f}/{b['p90_pgo_m']:.3f} m")
        else:
            print(f"  gap {k:>7}: n=    0")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--benchmark-tag", type=int, default=None, metavar="ID",
                    help="referee tag id — BENCHMARK ONLY, excluded from marker map "
                         "+ fiducial fixes; default: benchmark_setup.yaml referee_tag")
    a = ap.parse_args()

    referee = a.benchmark_tag
    ref_src = "--benchmark-tag" if referee is not None else "benchmark_setup.yaml"
    if referee is None:
        referee = referee_from_setup(a.recording)
    if referee is None:
        ref_src = "none"
        print("referee tag: none configured — every detected id is a fiducial")
    else:
        print(f"referee tag: {referee} ({ref_src}) — BENCHMARK ONLY; "
              f"excluded from marker map + fiducial fixes")

    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])

    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{a.recording}.db"), must_exist=True)
    with store:
        t0 = time.perf_counter()
        rows = detect_all(store, graph)
        print(f"detections: {len(rows)} sightings in {time.perf_counter()-t0:.1f}s")

    # world_raw_T_body at each query frame, recovered from the section's own
    # truth: prep built T_true = correction_at(ts) @ world_raw_T_body with THIS
    # graph, so inverting its correction returns the exact raw pose. The raw
    # lidar stream cannot be trusted for this: re-posed recordings (the mid360
    # walk) carry identity placeholders on their lidar obs (probed live).
    lidar_poses = {
        s["frame_idx"]:
            np.linalg.inv(transform_to_mat(graph.correction_at(s["ts"]))) @ s["T_true"]
        for s in prep["sections"]}

    stats = scatter_stats(rows)
    print(json.dumps(stats, indent=1))

    ref_rows = [r for r in rows if r["marker_id"] == referee]
    fid_rows = [r for r in rows if r["marker_id"] != referee]
    if referee is not None:
        print_referee_table(referee, stats)
        if rows and not fid_rows:
            print(f"note: every sighting is referee tag {referee} — fiducial set "
                  f"empty; empty marker map + zero fixes is CORRECT for "
                  f"referee-only recordings (villages)")

    mm = derive_marker_map(fid_rows)
    print(f"marker map (fiducial set): {sorted(mm)} ids")
    fixes = fiducial_fixes(fid_rows, mm, prep["sections"], lidar_poses)
    n_fix = sum(len(v) for v in fixes.values())
    print(f"fiducial fixes: {len(fixes)}/{len(prep['sections'])} sections covered, {n_fix} fixes")

    out_dir = HARNESS / "out" / "markers"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"recording": a.recording, "truth_note": (
        "marker map derived from the SAME PoseGraph as benchmark truth — "
        "deployment-realistic (a survey would do the same), truth-correlated; "
        "the judge's fitness remains the independent check"),
        "conf_model": f"{CONF_MAX}*exp(-age/{AGE_TAU_S}s), cutoff {AGE_MAX_S}s (unverified defaults)",
        "referee_tag": referee, "referee_source": ref_src,
        "git_rev_dimos": prep["git_rev_dimos"], "git_rev_trial": prep["git_rev_trial"]}
    with open(out_dir / f"{a.recording}.scatter.json", "w") as f:
        json.dump({"meta": meta, "per_marker": stats}, f, indent=1)
    with open(out_dir / f"{a.recording}.marker_map.json", "w") as f:
        json.dump({"meta": meta, "markers": {str(k): v.tolist() for k, v in mm.items()}}, f, indent=1)
    with open(out_dir / f"{a.recording}.fixes.json", "w") as f:
        json.dump({str(k): v for k, v in fixes.items()}, f, indent=1)
    wrote = "{scatter,marker_map,fixes}"
    if referee is not None:
        gated = [r for r in ref_rows if r["reproj_px"] <= REPROJ_GATE_PX]
        pos = np.array([r["T_map_tag_corr"][:3, 3] for r in gated])
        referee_out = {
            "meta": {**meta, "policy": (
                "BENCHMARK ONLY — never enters the marker map, never produces "
                "fiducial fixes (benchmark_setup.yaml decorrelation rule)")},
            "n_sightings": len(ref_rows),
            # consensus mirrors derive_marker_map: reproj-gated, PGO-corrected centroid
            "consensus_map_position_m": pos.mean(0).tolist() if len(gated) else None,
            "consensus_n_used": len(gated),
            "consensus_rms_m": (float(np.sqrt(((pos - pos.mean(0)) ** 2).sum(1).mean()))
                                if len(gated) else None),
            "detections": [{"ts": r["ts"], "reproj_px": r["reproj_px"],
                            "pos_world_raw_m": r["T_world_tag_raw"][:3, 3].tolist(),
                            "pos_map_pgo_m": r["T_map_tag_corr"][:3, 3].tolist()}
                           for r in ref_rows],
        }
        with open(out_dir / f"{a.recording}.referee.json", "w") as f:
            json.dump(referee_out, f, indent=1)
        wrote = "{scatter,marker_map,fixes,referee}"
    print(f"wrote {out_dir}/{a.recording}.{wrote}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
