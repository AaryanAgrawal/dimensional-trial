#!/usr/bin/env python3
"""Runtime marker-map yaml for the replay rehearsal (pre-IRL wiring gate).

Derives map_T_tag for one or more tag ids from the recording's own detections —
markers.py's detect_all pass, reproj-gated at the visual module's 3.0 px —
and writes the yaml schema VisualRelocalizationModule.load_marker_map reads:

    markers:
      10:
        translation: [x, y, z]      # meters, map frame
        rotation: [x, y, z, w]      # map_T_tag quaternion

Position: gated PGO-corrected centroid — cross-checked against the recording's
<rec>.marker_map.json (same pipeline + graph, must agree).
Orientation: eigen-average (Markley) of the gated sightings' map_T_tag
rotations; the angular spread about it and the angle to derive_marker_map's
lowest-reproj pick are printed as health signals.

This is the producer of the live fiducial prior read by eval.py — see
out/robotday_build_gated/sf_office_go2_20260718_survey1.marker_map.yaml.

Run: cd dimos && uv run python ../trial/harness/make_rehearsal_marker_map.py \
         hk_village3 --tags 10
     cd dimos && uv run python ../trial/harness/make_rehearsal_marker_map.py \
         recording_go2_mid360_2026-05-29_4-45pm-PST --tags 0,1,2,6,7 --out-dir rehearsal_mid360
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial.transform import Rotation

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from markers import REPROJ_GATE_PX, detect_all  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402


def consensus_quaternion(rotations: list[np.ndarray]) -> tuple[np.ndarray, float]:
    """Markley eigen-average of rotation matrices -> (quat_xyzw, max_dev_deg).

    The mean is the max-eigenvector of sum(q q^T) — sign-independent, so no
    hemisphere alignment pass is needed. max_dev_deg is the largest angular
    distance of any input from the mean: the health signal that says whether
    one consensus orientation is even a meaningful summary of the sightings.
    """
    qs = np.array([Rotation.from_matrix(R).as_quat() for R in rotations])  # xyzw
    m = np.einsum("ni,nj->ij", qs, qs) / len(qs)
    eigvals, eigvecs = np.linalg.eigh(m)
    mean_q = eigvecs[:, np.argmax(eigvals)]
    mean_rot = Rotation.from_quat(mean_q)
    devs = [
        float(np.degrees((mean_rot.inv() * Rotation.from_matrix(R)).magnitude()))
        for R in rotations
    ]
    return mean_q, max(devs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--tags", required=True,
                    help="comma-separated tag ids to map")
    ap.add_argument("--out-dir", default="rehearsal",
                    help="subdir of out/ for the yaml (e.g. rehearsal_mid360)")
    ap.add_argument("--force", action="store_true",
                    help="write even if the position cross-check disagrees with the standing "
                         "map (use when re-surveying with a different gate, e.g. ambiguity)")
    a = ap.parse_args()

    tags = [int(t) for t in a.tags.split(",")]

    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])

    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{a.recording}.db"), must_exist=True)
    with store:
        rows = detect_all(store, graph)

    # Standing per-tag consensus from the benchmark instrument (same pipeline +
    # the same pose graph): translations must agree or the inputs drifted.
    marker_map_file = HARNESS / "out" / "markers" / f"{a.recording}.marker_map.json"
    standing = (json.loads(marker_map_file.read_text()).get("markers", {})
                if marker_map_file.exists() else {})

    entries: dict[int, dict] = {}
    for tag in tags:
        gated = [r for r in rows
                 if r["marker_id"] == tag and r["reproj_px"] <= REPROJ_GATE_PX]
        print(f"tag {tag}: {len(gated)} reproj-gated sightings "
              f"(of {sum(1 for r in rows if r['marker_id'] == tag)} total)")
        if not gated:
            print(f"tag {tag}: nothing to map — refusing to write a partial map")
            return 1

        pos = np.array([r["T_map_tag_corr"][:3, 3] for r in gated]).mean(0)

        if str(tag) in standing:
            delta_m = float(np.linalg.norm(pos - np.array(standing[str(tag)])[:3, 3]))
            print(f"  position vs {marker_map_file.name}: delta={delta_m:.4f} m")
            if delta_m > 0.05 and not a.force:
                print("  cross-check FAILED (>0.05 m): refusing to write a marker map "
                      "that disagrees with the standing benchmark marker map")
                return 1

        quat, max_dev_deg = consensus_quaternion([r["T_map_tag_corr"][:3, :3] for r in gated])
        best = min(gated, key=lambda r: r["reproj_px"])
        best_quat = Rotation.from_matrix(best["T_map_tag_corr"][:3, :3])
        angle_to_best_deg = float(np.degrees(
            (Rotation.from_quat(quat).inv() * best_quat).magnitude()))
        print(f"  consensus orientation: max deviation of any sighting {max_dev_deg:.1f} deg; "
              f"angle to lowest-reproj sighting (derive_marker_map's pick) "
              f"{angle_to_best_deg:.1f} deg")
        entries[tag] = {"translation": [float(x) for x in pos],
                        "rotation": [float(x) for x in quat],
                        "_n_gated": len(gated), "_max_dev_deg": max_dev_deg}

    git_rev = subprocess.run(["git", "-C", str(dimos_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    out_path = HARNESS / "out" / a.out_dir / f"{a.recording}.marker_map.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    per_tag = "; ".join(f"tag {t}: {e['_n_gated']} sightings, Markley max dev "
                        f"{e['_max_dev_deg']:.1f} deg" for t, e in entries.items())
    header = (
        f"# map_T_tag for tags {sorted(entries)} of {a.recording} — REHEARSAL artifact.\n"
        f"# Derived by make_rehearsal_marker_map.py: PGO-corrected centroid of "
        f"reproj-gated (<= {REPROJ_GATE_PX} px) sightings; Markley quaternion mean.\n"
        f"# {per_tag}\n"
        f"# git_rev_dimos={git_rev} prep={prep['git_rev_dimos']}/{prep['git_rev_trial']}\n"
    )
    body = yaml.safe_dump(
        {"markers": {t: {"translation": e["translation"], "rotation": e["rotation"]}
                     for t, e in sorted(entries.items())}},
        sort_keys=False,
    )
    out_path.write_text(header + body)
    print(f"wrote {out_path}")
    print(body.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
