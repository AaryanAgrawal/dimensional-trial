"""Run the demo end to end: build the world, simulate a drifting robot,
render real camera frames, run real tag detection + PnP, fuse/gate/correct,
and write plots + metrics to `out/`.

    python -m marker_loc_demo.main [--out out] [--seed 42] [--scenario nominal|elevator|all]
"""

from __future__ import annotations

import argparse
import json
import os

from . import elevator
from . import pipeline
from . import trajectory as traj
from . import world

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(DEMO_ROOT, "out"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--scenario", choices=["nominal", "elevator", "elevator-motion", "all"], default="all"
    )
    args = ap.parse_args()

    markers = world.build_marker_map()
    world.save_marker_map(markers, os.path.join(DEMO_ROOT, "marker_map.yaml"))

    if args.scenario in ("nominal", "all"):
        gt = traj.build_ground_truth()
        out_dir = args.out
        result = pipeline.run_scenario("nominal", gt, markers, out_dir, seed=args.seed)
        world.save_marker_map(markers, os.path.join(out_dir, "marker_map.yaml"))
        print(json.dumps(result, indent=2))

    if args.scenario in ("elevator", "all"):
        e_markers, e_gt, room_w, room_d = elevator.build_elevator_scenario()
        out_dir = os.path.join(args.out, "elevator")
        result = pipeline.run_scenario(
            "elevator", e_gt, e_markers, out_dir, seed=args.seed + 1000,
            noise_scale=elevator.ODOM_NOISE_SCALE, room_w=room_w, room_d=room_d,
            use_pan=False,  # a 2x2m cab has nothing else to scan; robot just watches the anchor tags ahead
        )
        world.save_marker_map(e_markers, os.path.join(out_dir, "marker_map.yaml"))
        print(json.dumps(result, indent=2))

    if args.scenario == "elevator-motion":
        # Harness upgrade: same cab geometry + trajectory as the "elevator"
        # scenario, plus cab-motion odom disturbance (accel/decel bursts) and
        # door-open/closed visibility windows (see `elevator.py`). Not part
        # of "all" -- this is the trial/elevator-protocol.md synthetic
        # baseline run, kept separate so it never perturbs the already-cited
        # baseline elevator numbers.
        e_markers, e_gt, room_w, room_d = elevator.build_elevator_scenario()
        out_dir = os.path.join(args.out, "elevator_motion")
        result = pipeline.run_scenario(
            "elevator-motion", e_gt, e_markers, out_dir, seed=args.seed + 2000,
            noise_scale=elevator.ODOM_NOISE_SCALE, room_w=room_w, room_d=room_d,
            use_pan=False,
            disturbance_fn=elevator.cab_motion_disturbance,
            render_overrides_fn=elevator.render_overrides,
        )
        world.save_marker_map(e_markers, os.path.join(out_dir, "marker_map.yaml"))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
