"""Markerless-seed spike (week-3 design input, `trial/markerless-seed.md`):
does anchoring sparse tags with natural-feature tracking approach dense-tag
accuracy? Reruns the standard 100s/4-lap nominal trajectory (same seed, same
ground truth -- envelope_sweep's own density-sweep convention) three ways:

  (a) tags-dense   -- world.build_marker_map(), 15 tags (the demo's own
                       baseline; corrected ATE ~0.26m, see README.md)
  (b) tags-sparse   -- 3 tags only (envelope_sweep.build_density_markers(3)),
                       no feature tracking -- isolates what losing 12 tags
                       costs on its own
  (c) hybrid        -- the SAME 3 sparse tags, PLUS real checkerboard wall
                       texture (posters.py) tracked frame-to-frame with real
                       LK optical flow (features.py's "visual gyro") to hold
                       the odometry's dominant drift source (heading-rate
                       bias) down between sparse tag sightings

Same `pipeline.run_scenario` / `CorrectionFilter` / tag gating / ATE metric
for all three -- tag count (and, for (c), the feature-tracking front end) is
the only thing that changes, matching envelope_sweep.sweep_density's own
"same seed and trajectory" honesty convention.

    ./.venv/bin/python -m marker_loc_demo.markerless_seed [--out out/markerless_seed] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import os

from . import envelope_sweep
from . import pipeline
from . import posters
from . import trajectory as traj
from . import world

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(DEMO_ROOT, "out", "markerless_seed")

N_SPARSE_TAGS = 3


def run(out_root: str = DEFAULT_OUT, seed: int = 42) -> dict:
    os.makedirs(out_root, exist_ok=True)
    gt = traj.build_ground_truth()

    dense_markers = world.build_marker_map()
    sparse_markers = envelope_sweep.build_density_markers(N_SPARSE_TAGS)
    poster_map = posters.build_poster_map()

    print(f"[1/3] tags-dense  ({len(dense_markers)} tags)...")
    dense = pipeline.run_scenario("tags-dense", gt, dense_markers, os.path.join(out_root, "dense"), seed=seed)

    print(f"[2/3] tags-sparse ({len(sparse_markers)} tags, no features)...")
    sparse = pipeline.run_scenario("tags-sparse", gt, sparse_markers, os.path.join(out_root, "sparse"), seed=seed)

    print(f"[3/3] hybrid      ({len(sparse_markers)} tags + {len(poster_map)} posters, features on)...")
    hybrid = pipeline.run_scenario(
        "hybrid-sparse-tags-features", gt, sparse_markers, os.path.join(out_root, "hybrid"),
        seed=seed, poster_map=poster_map, use_features=True,
    )

    ate_dense = dense["ate_rmse_corrected_m"]
    ate_sparse = sparse["ate_rmse_corrected_m"]
    ate_hybrid = hybrid["ate_rmse_corrected_m"]
    # 0 = no better than sparse-alone, 1 = fully recovers dense-level accuracy
    # (can be negative if hybrid is worse than sparse-alone, >1 if it beats dense).
    closure = (ate_sparse - ate_hybrid) / (ate_sparse - ate_dense) if abs(ate_sparse - ate_dense) > 1e-9 else None

    summary = {
        "seed": seed,
        "n_dense_tags": len(dense_markers),
        "n_sparse_tags": len(sparse_markers),
        "n_posters": len(poster_map),
        "ate_rmse_corrected_m": {"dense": ate_dense, "sparse": ate_sparse, "hybrid": ate_hybrid},
        "detection_rate_ge1_tag": {
            "dense": dense["detection_rate_frames_with_ge1_tag"],
            "sparse": sparse["detection_rate_frames_with_ge1_tag"],
            "hybrid": hybrid["detection_rate_frames_with_ge1_tag"],
        },
        "corrections_accepted": {
            "dense": dense["corrections_accepted"], "sparse": sparse["corrections_accepted"],
            "hybrid": hybrid["corrections_accepted"],
        },
        "vision_yaw_trusted_fraction": hybrid.get("vision_yaw_trusted_fraction"),
        "vision_yaw_mean_inliers": hybrid.get("vision_yaw_mean_inliers"),
        "gap_closure_sparse_to_dense": closure,
        "hybrid_beats_sparse_alone": ate_hybrid < ate_sparse,
        "hybrid_matches_dense_within_10pct": ate_hybrid <= ate_dense * 1.10,
    }

    with open(os.path.join(out_root, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    run(out_root=args.out, seed=args.seed)


if __name__ == "__main__":
    main()
