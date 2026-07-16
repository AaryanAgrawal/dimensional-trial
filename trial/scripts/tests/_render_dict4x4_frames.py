# A2 render stage -- runs inside the DEMO venv (demo/.venv), cwd=demo/.
#
# Renders a DICT_4X4_50 tag (id 49, 100 mm) at 3 known camera poses using the
# demo harness's own renderer (marker_loc_demo/render.py -- real homography
# warp + blur + sensor noise; no detection injected), and writes:
#   frame_<i>.png            the rendered pixels
#   truth.json               intrinsics + per-frame ground-truth tag_T_camera
#
# The demo's tag bitmap generator is retargeted to DICT_4X4_50 at RUNTIME
# (module-global patch + lru_cache clear) -- no demo file is modified; every
# other consumer of the demo still sees its default 36h11 dictionary.
#
# Invoked by test_start_end_referee_render.py; can also be run by hand:
#   cd demo && .venv/bin/python ../trial/scripts/tests/_render_dict4x4_frames.py <out_dir>

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "demo"))

from marker_loc_demo import camera as cam  # noqa: E402
from marker_loc_demo import render, tags  # noqa: E402
from marker_loc_demo import transforms as tf  # noqa: E402
from marker_loc_demo.world import MarkerSpec  # noqa: E402

DICTIONARY = "DICT_4X4_50"
MARKER_ID = 49
MARKER_LENGTH_M = 0.10

# Retarget the demo's tag bitmaps to the holdout dictionary (runtime only).
tags.DICTIONARY_NAME = DICTIONARY
tags.get_dictionary.cache_clear()
tags.get_detector.cache_clear()
tags.tag_canvas.cache_clear()

# 100 mm tag flat on the x=0 wall. Camera mount sits at z=0.35 with an 8-deg
# pitch; at ~0.5 m stand-off the optical axis meets the wall ~7 cm below the
# mount height, so a tag center at z=0.28 lands mid-frame.
marker = MarkerSpec(
    id=MARKER_ID,
    size_m=MARKER_LENGTH_M,
    translation=np.array([0.0, 0.0, 0.28]),
    R_world_marker=tf.wall_marker_rotation(np.array([1.0, 0.0, 0.0])),
    wall="x=0",
)

# 3 known base poses aimed at the tag (yaw = atan2 toward the tag), at
# ~0.35-0.5 m stand-off and 20-30 deg view obliquity. Both choices are
# deliberate conditioning, established by a pose sweep (2026-07-15): a
# NEAR-FRONTAL view of a planar square leaves out-of-plane rotation and
# depth poorly conditioned (IPPE's two solutions converge), and the
# renderer's warp aliasing biases detected corners by ~0.7 px, which at
# 45 px tag size costs 1-4 cm of depth -- oblique + close keeps the
# recovered pose inside the 1 cm / 1 deg gate (measured: 2.7-7.2 mm,
# 0.17-0.62 deg). The demo's placement-envelope gate (min depth 1.3 m,
# tuned for 15 cm survey tags at room scale) is bypassed -- PnP geometry,
# not placement realism, is what this test verifies.
BASE_POSES = [
    (0.40, 0.20, math.atan2(-0.20, -0.40)),
    (0.34, 0.17, math.atan2(-0.17, -0.34)),
    (0.30, -0.16, math.atan2(0.16, -0.30)),
]


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    intr = cam.Intrinsics.default()
    rng = np.random.default_rng(20260715)

    frames = []
    for i, (x, y, yaw) in enumerate(BASE_POSES):
        T_world_camera = cam.world_to_base_T(x, y, yaw) @ cam.base_to_camera_T()
        res = render.render_frame(
            T_world_camera, [marker], intr, rng=rng, bypass_envelope_gate=True
        )
        assert MARKER_ID in res.visible_ids, (
            f"pose {i}: tag {MARKER_ID} not rendered (visible_ids={res.visible_ids})"
        )
        png = out_dir / f"frame_{i}.png"
        cv2.imwrite(str(png), res.image)

        # Ground truth. camera <- marker (what solvePnP recovers):
        T_camera_marker = tf.invert(T_world_camera) @ marker.T_world_marker
        # tag_T_camera (marker <- camera): the camera's pose IN the tag's own
        # fixed frame -- exactly what metrics_logger logs per sighting.
        tag_T_camera = tf.invert(T_camera_marker)
        frames.append(
            {
                "png": png.name,
                "base_pose_xyyaw": [x, y, yaw],
                "T_world_camera": T_world_camera.tolist(),
                "T_camera_marker": T_camera_marker.tolist(),
                "tag_T_camera": tag_T_camera.tolist(),
            }
        )

    (out_dir / "truth.json").write_text(
        json.dumps(
            {
                "dictionary": DICTIONARY,
                "marker_id": MARKER_ID,
                "marker_length_m": MARKER_LENGTH_M,
                "K": intr.K.tolist(),
                "D": intr.dist.tolist(),
                "width": intr.width,
                "height": intr.height,
                "frames": frames,
            },
            indent=2,
        )
    )
    print(f"rendered {len(frames)} DICT_4X4_50 id={MARKER_ID} frames -> {out_dir}")


if __name__ == "__main__":
    main()
