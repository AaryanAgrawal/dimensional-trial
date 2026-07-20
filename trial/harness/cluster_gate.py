#!/usr/bin/env python3
"""protocol.validity_gate — single-linkage 1.0 m spatial-cluster duplicate-id check.

The benchmark_setup.yaml gate that promotes or excludes a PROVISIONAL referee:
"spatial-cluster check per id (single-linkage, 1.0 m) before trusting any id as
referee or map entry" (cf. hk_village2/4 exclusions — 2-3 physical tags sharing
one id). One physical tag revisited = ONE cluster (chain of sightings, drift
bridged); two physical tags with the same id = TWO clusters > 1 m apart.

Runs markers.detect_all ONCE (same DetectMarkers pipeline + the same pose graph
prep.py pickled — reproj-gated), clusters each id's sighting positions by
single-linkage at 1.0 m, and prints per id: n_sightings (gated), n_clusters,
cluster sizes, max inter-cluster centroid separation. Referee must be 1 cluster
to be valid; any fiducial with >1 cluster must leave the map.

Primary basis = RAW/onboard positions: a physical tag revisited under a globally
consistent pose source lands in ONE spot. PGO is a SECONDARY read — on low-drift
LIO recordings prep's PGO can WARP a single tag's revisits metres apart (measured:
gir_park1 referee raw 0.04 m vs pgo 4.21 m across two visits), which would falsely
split it. The referee is a known single physical tag: 1 raw cluster self-validates
raw as the consistent frame for that recording.

Run: cd dimos && uv run python ../trial/harness/cluster_gate.py <rec>
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))
from markers import REPROJ_GATE_PX, detect_all, referee_from_setup  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402

LINK_M = 1.0


def single_linkage(pts: np.ndarray, thresh: float) -> list[list[int]]:
    """Connected components of the graph with an edge for every pair <= thresh."""
    n = len(pts)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            if np.linalg.norm(pts[i] - pts[j]) <= thresh:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comps: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)
    return list(comps.values())


def main() -> int:
    rec = sys.argv[1]
    referee = referee_from_setup(rec)
    with open(H / "out" / "prepared" / f"{rec}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{rec}.db"), must_exist=True)
    with store:
        rows = detect_all(store, graph)

    by_id: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r["reproj_px"] <= REPROJ_GATE_PX:
            by_id[r["marker_id"]].append(r)

    def clusters(pts):
        comps = sorted(single_linkage(pts, LINK_M), key=len, reverse=True)
        cents = [pts[c].mean(0) for c in comps]
        sep = 0.0
        for i in range(len(cents)):
            for j in range(i + 1, len(cents)):
                sep = max(sep, float(np.linalg.norm(cents[i] - cents[j])))
        return comps, sep

    print(f"=== validity_gate {rec} (referee={referee}, single-linkage {LINK_M} m, "
          f"reproj<= {REPROJ_GATE_PX}px, {len(rows)} raw sightings) ===")
    print("   [primary = RAW/onboard positions; pgo shown for contrast]")
    verdict = {}
    for mid in sorted(by_id):
        rs = by_id[mid]
        raw_pts = np.array([r["T_world_tag_raw"][:3, 3] for r in rs])
        pgo_pts = np.array([r["T_map_tag_corr"][:3, 3] for r in rs])
        rcomps, rsep = clusters(raw_pts)
        pcomps, psep = clusters(pgo_pts)
        role = "REFEREE" if mid == referee else "fiducial"
        flag = "OK (single-instance)" if len(rcomps) == 1 else \
               f"DUPLICATE-ID ({len(rcomps)} raw clusters, {rsep:.2f} m apart)"
        print(f"  id {mid:3d} [{role:8s}] n_gated={len(rs):4d}  "
              f"RAW clusters={len(rcomps)} sizes={[len(c) for c in rcomps]} sep={rsep:.2f}m  |  "
              f"pgo clusters={len(pcomps)} sep={psep:.2f}m  -> {flag}")
        verdict[mid] = {"role": role, "n_gated": len(rs),
                        "n_clusters_raw": len(rcomps), "cluster_sizes_raw": [len(c) for c in rcomps],
                        "max_sep_raw_m": rsep, "n_clusters_pgo": len(pcomps),
                        "max_sep_pgo_m": psep, "single_instance": len(rcomps) == 1}
    out = H / "out" / "markers" / f"{rec}.clusters.json"
    out.write_text(json.dumps({"referee": referee, "link_m": LINK_M, "per_id": verdict}, indent=1))
    print(f"wrote {out}")
    if referee is not None:
        v = verdict.get(referee)
        if v is None:
            print(f"REFEREE VERDICT: tag {referee} NOT DETECTED (reproj-gated) — cannot validate")
        elif v["single_instance"]:
            print(f"REFEREE VERDICT: tag {referee} PASSES validity_gate (single-instance) — VALID referee")
        else:
            print(f"REFEREE VERDICT: tag {referee} FAILS validity_gate ({v['n_clusters_raw']} raw clusters) "
                  f"— EXCLUDE recording (village2/4 pattern)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
