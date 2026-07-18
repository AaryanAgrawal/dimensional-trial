#!/usr/bin/env python3
"""Runtime marker-map yaml for the replay rehearsal (pre-IRL wiring gate).

Derives map_T_tag for ONE tag id from the recording's own detections —
markers.py's detect_all pass, reproj-gated at the visual module's 3.0 px —
and writes the yaml schema VisualRelocalizationModule.load_marker_map reads:

    markers:
      10:
        translation: [x, y, z]      # meters, map frame
        rotation: [x, y, z, w]      # map_T_tag quaternion

Position: gated PGO-corrected centroid — cross-checked against the recording's
<rec>.referee.json consensus_map_position_m (same pipeline, must agree).
Orientation: eigen-average (Markley) of the gated sightings' map_T_tag
rotations; the angular spread about it and the angle to derive_marker_map's
lowest-reproj pick are printed as health signals.

NOTE: on referee-only recordings (villages, single tag id 10) this REUSES THE
REFEREE TAG as the runtime fiducial. Fine for a WIRING rehearsal; never valid
for benchmark scoring (benchmark_setup.yaml decorrelation rule).

Run: cd dimos && uv run python ../trial/harness/make_rehearsal_marker_map.py hk_village3
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
from markers import REPROJ_GATE_PX, detect_all, referee_from_setup  # noqa: E402

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
    ap.add_argument("--tag", type=int, default=None,
                    help="tag id to map (default: the recording's referee tag)")
    a = ap.parse_args()

    tag = a.tag if a.tag is not None else referee_from_setup(a.recording)
    if tag is None:
        print(f"no tag given and no referee tag configured for {a.recording}")
        return 1

    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])

    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{a.recording}.db"), must_exist=True)
    with store:
        rows = detect_all(store, graph)

    gated = [r for r in rows
             if r["marker_id"] == tag and r["reproj_px"] <= REPROJ_GATE_PX]
    print(f"tag {tag}: {len(gated)} reproj-gated sightings "
          f"(of {sum(1 for r in rows if r['marker_id'] == tag)} total)")
    if not gated:
        print("nothing to map")
        return 1

    pos = np.array([r["T_map_tag_corr"][:3, 3] for r in gated]).mean(0)

    # Cross-check against the referee json's consensus (same pipeline + gate:
    # any disagreement means the inputs drifted since that file was written).
    referee_file = HARNESS / "out" / "markers" / f"{a.recording}.referee.json"
    if referee_file.exists():
        ref = json.loads(referee_file.read_text())
        ref_pos = ref.get("consensus_map_position_m")
        if ref.get("meta", {}).get("referee_tag") == tag and ref_pos is not None:
            delta_m = float(np.linalg.norm(pos - np.array(ref_pos)))
            print(f"consensus position vs {referee_file.name}: delta={delta_m:.4f} m "
                  f"(n_used here={len(gated)}, there={ref.get('consensus_n_used')})")
            if delta_m > 0.05:
                print("cross-check FAILED (>0.05 m): refusing to write a marker map "
                      "that disagrees with the standing referee consensus")
                return 1

    quat, max_dev_deg = consensus_quaternion([r["T_map_tag_corr"][:3, :3] for r in gated])
    best = min(gated, key=lambda r: r["reproj_px"])
    best_quat = Rotation.from_matrix(best["T_map_tag_corr"][:3, :3])
    angle_to_best_deg = float(np.degrees(
        (Rotation.from_quat(quat).inv() * best_quat).magnitude()))
    print(f"consensus orientation: max deviation of any sighting {max_dev_deg:.1f} deg; "
          f"angle to lowest-reproj sighting (derive_marker_map's pick) "
          f"{angle_to_best_deg:.1f} deg")

    git_rev = subprocess.run(["git", "-C", str(dimos_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    out_path = HARNESS / "out" / "rehearsal" / f"{a.recording}.marker_map.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# map_T_tag for tag {tag} of {a.recording} — REHEARSAL artifact (wiring gate).\n"
        f"# Derived by make_rehearsal_marker_map.py: PGO-corrected centroid of "
        f"{len(gated)} reproj-gated (<= {REPROJ_GATE_PX} px) sightings; Markley "
        f"quaternion mean (max dev {max_dev_deg:.1f} deg).\n"
        f"# git_rev_dimos={git_rev} prep={prep['git_rev_dimos']}/{prep['git_rev_trial']}\n"
        f"# Referee-tag reuse: NOT valid for benchmark scoring.\n"
    )
    body = yaml.safe_dump(
        {"markers": {tag: {"translation": [float(x) for x in pos],
                           "rotation": [float(x) for x in quat]}}},
        sort_keys=False,
    )
    out_path.write_text(header + body)
    print(f"wrote {out_path}")
    print(body.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
