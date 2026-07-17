#!/usr/bin/env python3
"""Single-frame probe: reproduce a gravity-gate WALKOVER — the mechanism behind
every ransac+lastpose regression found on hk_village1/5 (frames 831, 873; 811).

refine_candidates() gravity-filters the pool (tilt > 10 deg dropped) and falls
back to the FULL pool only when NO candidate is upright. Raw point-to-point
RANSAC transforms are routinely 15-45 deg tilted pre-polish, so ransac-only
quietly relies on that fallback and recovers via wall-ICP polish. Any
always-upright seed candidate (LastPosePrior — and, in Phase 2, FiducialPrior)
makes `upright` non-empty on exactly those frames, ORPHANING the entire RANSAC
pool: the stale seed wins unopposed, not on merit (observed fitness 0.21-0.59
vs 0.82-0.96 for the discarded pool's polished winner).

For one section this regenerates the same-seeded RANSAC pool (#2137 recipe:
OMP=1 pre-import, seeds = frame_idx), prints per-candidate tilt vs the gate,
and judges the pool with and without the sequential run's lastpose seed (the
last gate-accepted T_est before this frame, from the ransac_lastpose result
JSON). Bit-exact against both benchmark runs on all three probed frames.

Run: cd dimos && uv run python ../trial/harness/probe_gravity_walkover.py hk_village1 831
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")  # BEFORE open3d import (determinism)

import json
import pickle
import random
import sys
from types import SimpleNamespace

import numpy as np
import open3d as o3d

RECORDING = sys.argv[1]  # e.g. hk_village1
FRAME = int(sys.argv[2])  # e.g. 831

H = os.path.dirname(os.path.abspath(__file__))
with open(f"{H}/out/prepared/{RECORDING}.pkl", "rb") as f:
    prep = SimpleNamespace(**pickle.load(f))
secs = {s["frame_idx"]: SimpleNamespace(**s) for s in prep.sections}
s = secs[FRAME]

# lp seed at FRAME = T_est of the last gate-accepted frame before it (module.py
# update-on-accept semantics, replicated by run_bench's sequential loop).
with open(f"{H}/out/results/{RECORDING}.ransac_lastpose.json") as f:
    lp_run = json.load(f)["results"]
prev = [r for r in lp_run if r["frame_idx"] < FRAME and r.get("accepted_at_gate")]
prev_accept = max(prev, key=lambda r: r["frame_idx"])
print(f"lp seed = T_est of frame {prev_accept['frame_idx']} "
      f"(fit {prev_accept['fitness']:.3f}, err_t {prev_accept['err_t']:.3f} m at its own frame)")
seed_T = np.asarray(prev_accept["T_est"])  # map_T_world, relocalize() convention

from dimos.mapping.relocalization.relocalize import (
    GRAVITY_TILT_MAX_DEG, _gravity_tilt_deg, generate_ransac_candidates,
    refine_candidates)


def cloud(pts):
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(pts.astype(np.float64))
    return pc


gm, lm = cloud(prep.premap_pts), cloud(s.body_pts)

o3d.utility.random.seed(FRAME); np.random.seed(FRAME); random.seed(FRAME)
cands = generate_ransac_candidates(gm, lm)
tilts = [_gravity_tilt_deg(T) for T in cands]
n_up = sum(t <= GRAVITY_TILT_MAX_DEG for t in tilts)
print(f"frame {FRAME}: {len(cands)} ransac candidates, tilt <= {GRAVITY_TILT_MAX_DEG} deg: {n_up}")
print("tilt percentiles 0/25/50/75/100 (deg):", np.round(np.percentile(tilts, [0, 25, 50, 75, 100]), 1))
print("seed tilt (deg):", round(_gravity_tilt_deg(seed_T), 2))


def errs(T):
    from scipy.spatial.transform import Rotation
    et = float(np.linalg.norm(T[:3, 3] - s.T_true[:3, 3]))
    er = float(Rotation.from_matrix(T[:3, :3] @ s.T_true[:3, :3].T).magnitude() * 180 / np.pi)
    return round(et, 3), round(er, 1)


# Judge WITHOUT the seed (ransac-only replication; candidates reused — no
# reseed needed, refine_candidates is deterministic given its inputs).
T1, f1, i1 = refine_candidates(gm, lm, cands)
print(f"judge(ransac pool only):  win_idx={i1} fit={f1:.3f} (err_t m, err_r deg)={errs(T1)}")

# Judge WITH the seed appended (sequential lastpose-run pool replication).
T2, f2, i2 = refine_candidates(gm, lm, cands + [seed_T])
src = "last_pose" if i2 == len(cands) else "ransac"
print(f"judge(ransac+seed pool): win_idx={i2} ({src}) fit={f2:.3f} (err_t m, err_r deg)={errs(T2)}")
print("seed candidate's own error vs THIS frame's truth (err_t m, err_r deg):", errs(seed_T))
