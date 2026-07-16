"""markerless-v2 (`trial/markerless-seed.md` v2): rebuilds the week-2
feasibility prototype on the v1 spike's own recommendation -- ORB/PnP
one-shot relocalization against a persistent map, not frame-to-frame visual-
gyro fusion. A tag-anchored SURVEY pass (`survey.py`) builds a 3D ORB
feature map; a LOCALIZE pass (`orb_localize.py`) matches this frame's ORB
descriptors against that map, solves PnP, and feeds the SAME
`CorrectionFilter` the tag path already uses (`pipeline.run_scenario`'s
`extra_localizer` hook). Reruns the standard 100s/4-lap nominal trajectory
(same seed, same ground truth -- every other scenario's own convention):

  (a) tags-dense    -- world.build_marker_map(), 15 tags (the demo's own
                        baseline; corrected ATE ~0.26m, see README.md)
  (b) tags-sparse   -- 3 tags only (envelope_sweep.build_density_markers(3)),
                        no feature map -- isolates what losing 12 tags costs
                        on its own
  (c) orb-hybrid    -- the SAME 3 sparse tags + a feature map built from a
                        SURVEY pass anchored by those same 3 tags
  (d) texture-changed robustness probe -- (c)'s SAME frozen feature map
                        (built once, never rebuilt), LOCALIZE pass rerun
                        with 20% of wall posters moved to new positions --
                        does the map degrade gracefully or catastrophically?

    ./.venv/bin/python -m marker_loc_demo.markerless_v2 [--out out/markerless_v2] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

from . import camera as cam
from . import envelope_sweep
from . import orb_localize
from . import pipeline
from . import posters
from . import survey
from . import trajectory as traj
from . import transforms as tf
from . import world

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(DEMO_ROOT, "out", "markerless_v2")

N_SPARSE_TAGS = 3
TEXTURE_CHANGE_FRAC = 0.20


def perturb_poster_map(poster_map: list[world.MarkerSpec], frac: float, seed: int) -> list[world.MarkerSpec]:
    """Models "someone rearranged the wall posters after the map was built":
    relocate a random `frac` of posters to a new position (random wall,
    random along/height). The SURVEY pass and the frozen feature map it
    produced never see this -- only the LOCALIZE pass's rendered frames do,
    exactly the "does the map degrade gracefully" question."""
    rng = np.random.default_rng(seed + 777)
    n_move = int(round(len(poster_map) * frac))
    idx_to_move = rng.choice(len(poster_map), size=n_move, replace=False)
    walls = ["x=0", "x=10", "y=0", "y=8"]
    lengths = {
        "x=0": world.ROOM_DEPTH_Y, "x=10": world.ROOM_DEPTH_Y,
        "y=0": world.ROOM_WIDTH_X, "y=8": world.ROOM_WIDTH_X,
    }
    new_map = list(poster_map)
    for i in idx_to_move:
        p = new_map[i]
        wall = walls[int(rng.integers(0, 4))]
        along = float(rng.uniform(0.8, lengths[wall] - 0.8))
        height = float(rng.uniform(0.6, 2.2))
        R = tf.wall_marker_rotation(world.WALL_NORMALS[wall])
        t = posters._wall_point(wall, along, height)
        new_map[i] = world.MarkerSpec(id=p.id, size_m=p.size_m, translation=t, R_world_marker=R, wall=wall)
    return new_map


def run(out_root: str = DEFAULT_OUT, seed: int = 42) -> dict:
    os.makedirs(out_root, exist_ok=True)
    gt = traj.build_ground_truth()

    dense_markers = world.build_marker_map()
    sparse_markers = envelope_sweep.build_density_markers(N_SPARSE_TAGS)
    poster_map = posters.build_poster_map()
    intr = cam.Intrinsics.default()

    print(f"[1/5] tags-dense  ({len(dense_markers)} tags)...")
    dense = pipeline.run_scenario("tags-dense", gt, dense_markers, os.path.join(out_root, "dense"), seed=seed)

    print(f"[2/5] tags-sparse ({len(sparse_markers)} tags, no feature map)...")
    sparse = pipeline.run_scenario("tags-sparse", gt, sparse_markers, os.path.join(out_root, "sparse"), seed=seed)

    print(f"[3/5] SURVEY pass ({len(sparse_markers)} anchor tags + {len(poster_map)} posters)...")
    fmap, survey_diag = survey.run_survey(gt, sparse_markers, poster_map, os.path.join(out_root, "survey"), seed=seed)
    fmap.save(os.path.join(out_root, "feature_map.npz"))
    reproj = survey_diag["mean_triangulation_reproj_px"]
    print(f"      map: {len(fmap)} points, mean triangulation reproj {reproj:.2f}px" if reproj is not None else "      map: 0 points")

    print("[4/5] LOCALIZE pass (ORB/PnP hybrid: 3 tags + feature map)...")
    localizer = orb_localize.make_localizer(fmap, intr)
    hybrid = pipeline.run_scenario(
        "orb-hybrid-sparse-tags-feature-map", gt, sparse_markers, os.path.join(out_root, "hybrid"),
        seed=seed, poster_map=poster_map, extra_localizer=localizer, unique_poster_texture=True,
    )

    print(f"[5/5] robustness probe: {int(TEXTURE_CHANGE_FRAC * 100)}% of posters moved (same frozen map)...")
    perturbed_poster_map = perturb_poster_map(poster_map, TEXTURE_CHANGE_FRAC, seed)
    localizer_probe = orb_localize.make_localizer(fmap, intr)  # same map, fresh call state
    probe = pipeline.run_scenario(
        "orb-hybrid-texture-changed", gt, sparse_markers, os.path.join(out_root, "texture_changed"),
        seed=seed, poster_map=perturbed_poster_map, extra_localizer=localizer_probe, unique_poster_texture=True,
    )

    ate_dense = dense["ate_rmse_corrected_m"]
    ate_sparse = sparse["ate_rmse_corrected_m"]
    ate_hybrid = hybrid["ate_rmse_corrected_m"]
    ate_probe = probe["ate_rmse_corrected_m"]

    summary = {
        "seed": seed,
        "n_dense_tags": len(dense_markers),
        "n_sparse_tags": len(sparse_markers),
        "n_posters": len(poster_map),
        "n_map_points": len(fmap),
        "survey": survey_diag,
        "ate_rmse_corrected_m": {
            "tags_dense": ate_dense, "tags_sparse": ate_sparse,
            "orb_hybrid": ate_hybrid, "orb_hybrid_texture_changed": ate_probe,
        },
        "ate_rmse_raw_odom_m": dense["ate_rmse_raw_odom_m"],
        "detection_rate_ge1_tag": {
            "tags_dense": dense["detection_rate_frames_with_ge1_tag"],
            "tags_sparse": sparse["detection_rate_frames_with_ge1_tag"],
            "orb_hybrid": hybrid["detection_rate_frames_with_ge1_tag"],
        },
        "corrections_accepted": {
            "tags_dense": dense["corrections_accepted"], "tags_sparse": sparse["corrections_accepted"],
            "orb_hybrid": hybrid["corrections_accepted"], "orb_hybrid_texture_changed": probe["corrections_accepted"],
        },
        "orb_localizer_accepted": {"hybrid": hybrid["extra_localizer_accepted"], "texture_changed": probe["extra_localizer_accepted"]},
        "orb_localizer_rejected": {"hybrid": hybrid["extra_localizer_rejected"], "texture_changed": probe["extra_localizer_rejected"]},
        "orb_localizer_accept_rate": {
            "hybrid": hybrid["extra_localizer_accepted"] / max(1, hybrid["extra_localizer_accepted"] + hybrid["extra_localizer_rejected"]),
            "texture_changed": probe["extra_localizer_accepted"] / max(1, probe["extra_localizer_accepted"] + probe["extra_localizer_rejected"]),
        },
        "gap_closure_sparse_to_dense": (
            (ate_sparse - ate_hybrid) / (ate_sparse - ate_dense) if abs(ate_sparse - ate_dense) > 1e-9 else None
        ),
        "hybrid_beats_sparse_alone": ate_hybrid < ate_sparse,
        "hybrid_matches_dense_within_10pct": ate_hybrid <= ate_dense * 1.10,
        "texture_change_degradation_pct": (
            100.0 * (ate_probe - ate_hybrid) / ate_hybrid if ate_hybrid > 1e-9 else None
        ),
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
