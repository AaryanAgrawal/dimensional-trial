"""Day-5 deliverable: self-survey simulation + graph-refined maps + survey
error propagation into downstream ATE.

1. Self-survey simulation: drive the standard 100s/4-lap loop with DRIFTING
   odometry (no ground truth used anywhere in the estimate -- mirrors the
   real installer flow, `spec/leshy-reply.md` step 2 / `unitree-go2-markers`).
   Every gated tag sighting's world pose = f(the robot's odometry-believed
   pose at that instant), exactly the error source a real self-survey has.
   Build two maps: (a) single sighting per tag, (b) naive multi-sighting
   average (equal-weight mean of every sighting's implied pose).
2. Graph refinement (`graph_refine.py`, tagSLAM-lite): joint least-squares
   over camera-pose nodes + tag-pose nodes, edges = tag observations +
   odometry between nodes, gauge-anchored at the survey's start pose.
3. Downstream ATE: run the SAME tag-corrected localization pipeline
   (`pipeline.run_scenario`, unmodified correction filter) against each
   candidate map (single / naive / refined / ground-truth) via the new
   `localization_markers` param -- frames always render against the true
   world, only the map used to invert tag sightings into a pose changes.

Usage:
    ./.venv/bin/python -m marker_loc_demo.map_refinement [--seed 42] [--out out/map_refinement]
    ./.venv/bin/python -m marker_loc_demo.map_refinement --seeds 42,7,99
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

from . import camera as cam
from . import localization as loc
from . import odom as odom_mod
from . import pipeline
from . import render
from . import trajectory as traj
from . import transforms as tf
from . import world
from .graph_refine import OdomEdge, Sighting, refine

DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Thin survey-drive frames down to at most one graph node per this many
# frames (0.4s @ 10Hz) -- keeps the least-squares problem's parameter count
# (and therefore its dense finite-difference Jacobian cost) tractable for a
# prototype-scale run. Every dropped frame's sightings are simply not added
# to the graph (still counted/reported); this is a stated tractability
# simplification, not a hidden one -- see trial/map-refinement.md.
MIN_NODE_GAP_FRAMES = 4


def _wrap(a: float) -> float:
    return (a + np.pi) % (2 * np.pi) - np.pi


def _frame_rng(seed: int, k: int) -> np.random.Generator:
    return np.random.default_rng((seed * 1_000_003 + k) % (2**63 - 1))


def _chain_odom(dx: np.ndarray, dy: np.ndarray, dyaw: np.ndarray, i: int, j: int) -> tuple[float, float, float]:
    """SE(2) relative transform (frame i -> frame j) by composing the exact
    raw per-step odometry measurements from i to j -- the real definition of
    an "odometry edge" between two graph nodes, not an approximation."""
    x = y = yaw = 0.0
    for k in range(i, j):
        c, s = np.cos(yaw), np.sin(yaw)
        x += c * dx[k] - s * dy[k]
        y += s * dx[k] + c * dy[k]
        yaw = _wrap(yaw + dyaw[k])
    return x, y, yaw


def run_self_survey(markers: list[world.MarkerSpec], gt: traj.GroundTruth, seed: int):
    """Real detector on real rendered pixels; the robot's own pose estimate
    at each sighting comes from drifting odometry ONLY -- ground truth is
    used solely to synthesize what the camera would see (the render step),
    exactly like every other scenario in this harness, never to inform the
    survey's own pose estimate. Returns:
      - sightings_by_tag: {tag_id: [(t, T_world_marker_est, sigma_pos, sigma_yaw), ...]}
      - raw_sightings: [(frame_idx, t, tag_id, T_camera_marker_raw, sigma_pos, sigma_yaw)]
      - od: the Odometry the survey ran on
      - dx_meas, dy_meas, dyaw_meas: the exact per-step raw odometry measurements
        (needed unchanged by graph_refine's odometry edges)
    """
    intr = cam.Intrinsics.default()
    dx_meas, dy_meas, dyaw_meas = odom_mod.simulate_odometry_steps(gt, seed=seed)
    od = odom_mod.integrate_odometry(gt.t, gt.x[0], gt.y[0], gt.yaw[0], dx_meas, dy_meas, dyaw_meas)

    # What a real self-survey knows ahead of time: the tag IDs + physical
    # size that were printed (`dimos apriltag --ids ... --size-mm ...`) --
    # NOT their world pose, which is exactly what's being surveyed. Poses
    # below are unused placeholders; `detect_and_solve` only needs size_m to
    # build object points, and we read the RAW `T_camera_marker` off each
    # detection rather than its (placeholder-map-composed) `T_world_camera`.
    placeholder = {
        m.id: world.MarkerSpec(id=m.id, size_m=m.size_m, translation=np.zeros(3), R_world_marker=np.eye(3), wall=m.wall)
        for m in markers
    }

    sightings_by_tag: dict[int, list] = {m.id: [] for m in markers}
    raw_sightings: list[tuple[int, float, int, np.ndarray, float, float]] = []

    n = len(gt.t)
    for k in range(n):
        pan = cam.cam_pan_yaw(gt.t[k])
        T_base_cam = cam.base_to_camera_T(pan_yaw_rad=pan)
        T_world_base_true = cam.world_to_base_T(gt.x[k], gt.y[k], gt.yaw[k])
        T_world_camera_true = T_world_base_true @ T_base_cam
        rr = render.render_frame(T_world_camera_true, markers, intr, rng=_frame_rng(seed, k))

        dets = loc.detect_and_solve(rr.image, placeholder, intr)
        T_world_base_odom_k = cam.world_to_base_T(od.x[k], od.y[k], od.yaw[k])
        T_world_camera_odom_k = T_world_base_odom_k @ T_base_cam
        for d in dets:
            if not loc.tag_gate_ok(d):
                continue
            sigma_pos, sigma_yaw = loc.per_tag_sigma(d)
            T_world_marker_est = T_world_camera_odom_k @ d.T_camera_marker
            sightings_by_tag[d.marker_id].append((float(gt.t[k]), T_world_marker_est, sigma_pos, sigma_yaw))
            raw_sightings.append((k, float(gt.t[k]), d.marker_id, d.T_camera_marker.copy(), sigma_pos, sigma_yaw))

    return sightings_by_tag, raw_sightings, od, dx_meas, dy_meas, dyaw_meas


def build_single_map(sightings_by_tag: dict, markers: list[world.MarkerSpec]) -> list[world.MarkerSpec]:
    out = []
    for m in markers:
        s = sightings_by_tag.get(m.id, [])
        if not s:
            continue
        _, T0, _, _ = s[0]  # first chronological accepted sighting
        out.append(world.MarkerSpec(id=m.id, size_m=m.size_m, translation=T0[:3, 3].copy(), R_world_marker=T0[:3, :3].copy(), wall=m.wall))
    return out


def build_naive_avg_map(sightings_by_tag: dict, markers: list[world.MarkerSpec]) -> list[world.MarkerSpec]:
    out = []
    for m in markers:
        s = sightings_by_tag.get(m.id, [])
        if not s:
            continue
        trans = np.mean([T[:3, 3] for _, T, _, _ in s], axis=0)
        quats = [tf.quat_from_matrix(T[:3, :3]) for _, T, _, _ in s]
        q_mean = tf.weighted_quat_mean(quats, [1.0] * len(quats))  # naive = EQUAL weight, on purpose
        R_mean = tf.matrix_from_quat(q_mean)
        out.append(world.MarkerSpec(id=m.id, size_m=m.size_m, translation=trans, R_world_marker=R_mean, wall=m.wall))
    return out


def map_error(est_markers: list[world.MarkerSpec], true_by_id: dict[int, world.MarkerSpec]) -> dict:
    pos_errs, rot_errs, per_tag = [], [], {}
    for m in est_markers:
        t = true_by_id[m.id]
        pos_err = float(np.linalg.norm(m.translation - t.translation))
        R_rel = m.R_world_marker.T @ t.R_world_marker
        ang = float(np.degrees(np.arccos(np.clip((np.trace(R_rel) - 1) / 2, -1, 1))))
        pos_errs.append(pos_err)
        rot_errs.append(ang)
        per_tag[m.id] = {"pos_err_m": round(pos_err, 4), "rot_err_deg": round(ang, 3)}
    return {
        "n_tags": len(est_markers),
        "pos_err_mean_m": float(np.mean(pos_errs)) if pos_errs else None,
        "pos_err_max_m": float(np.max(pos_errs)) if pos_errs else None,
        "rot_err_mean_deg": float(np.mean(rot_errs)) if rot_errs else None,
        "rot_err_max_deg": float(np.max(rot_errs)) if rot_errs else None,
        "per_tag": per_tag,
    }


def build_graph_and_refine(
    markers: list[world.MarkerSpec],
    gt: traj.GroundTruth,
    od: odom_mod.Odometry,
    dx_meas: np.ndarray,
    dy_meas: np.ndarray,
    dyaw_meas: np.ndarray,
    raw_sightings: list,
    naive_map_by_id: dict[int, world.MarkerSpec],
    true_by_id: dict[int, world.MarkerSpec],
    min_gap: int = MIN_NODE_GAP_FRAMES,
):
    frames_with_obs = sorted(set(r[0] for r in raw_sightings))
    node_frames: list[int] = []
    last = -(10**9)
    for k in frames_with_obs:
        if k - last >= min_gap:
            node_frames.append(k)
            last = k
    if 0 not in node_frames:
        node_frames = [0] + node_frames  # frame 0 = the survey's known start pose, zero drift yet
    node_frames = sorted(set(node_frames))
    node_index = {k: i for i, k in enumerate(node_frames)}

    sightings = [
        Sighting(node_idx=node_index[k], t=t, tag_id=tag_id, T_camera_marker=T_cm, sigma_pos=sp, sigma_yaw=sy)
        for (k, t, tag_id, T_cm, sp, sy) in raw_sightings
        if k in node_index
    ]

    odom_edges = []
    for i in range(len(node_frames) - 1):
        a_frame, b_frame = node_frames[i], node_frames[i + 1]
        dx, dy, dyaw = _chain_odom(dx_meas, dy_meas, dyaw_meas, a_frame, b_frame)
        gap = b_frame - a_frame
        sigma_pos = loc.MEAS_SIGMA_POS_BASE_M + loc.PROCESS_RATE_POS_M_PER_STEP * gap
        sigma_yaw = loc.MEAS_SIGMA_YAW_BASE_RAD + loc.PROCESS_RATE_YAW_RAD_PER_STEP * gap
        odom_edges.append(OdomEdge(a=i, b=i + 1, dx=dx, dy=dy, dyaw=dyaw, sigma_pos=sigma_pos, sigma_yaw=sigma_yaw))

    anchor_xytheta = (float(gt.x[0]), float(gt.y[0]), float(gt.yaw[0]))
    init_cam = {i: (float(od.x[k]), float(od.y[k]), float(od.yaw[k])) for i, k in enumerate(node_frames) if i != 0}

    tag_ids = sorted(naive_map_by_id.keys())
    init_tag_T = {tid: naive_map_by_id[tid].T_world_marker for tid in tag_ids}
    cam_t_by_node = [float(gt.t[k]) for k in node_frames]

    result = refine(
        anchor_xytheta=anchor_xytheta,
        sightings=sightings,
        odom_edges=odom_edges,
        init_cam_xytheta=init_cam,
        init_tag_T=init_tag_T,
        tag_ids=tag_ids,
        cam_t_by_node=cam_t_by_node,
        n_nodes=len(node_frames),
    )

    refined_map = [
        world.MarkerSpec(
            id=tid,
            size_m=true_by_id[tid].size_m,
            translation=result.refined_tags[tid][:3, 3].copy(),
            R_world_marker=result.refined_tags[tid][:3, :3].copy(),
            wall=true_by_id[tid].wall,
        )
        for tid in tag_ids
    ]

    # per-tag post-refinement residual summary, for the survey-quality gate
    per_tag_resid: dict[int, list[float]] = {tid: [] for tid in tag_ids}
    for tag_id, _node_idx, pos_err, _ang in result.sighting_residuals:
        per_tag_resid[tag_id].append(pos_err)
    gate_stats = {
        tid: {
            "n_sightings_in_graph": len(vs),
            "max_resid_m": round(max(vs), 4) if vs else None,
            "mean_resid_m": round(float(np.mean(vs)), 4) if vs else None,
        }
        for tid, vs in per_tag_resid.items()
    }

    report = {
        "n_nodes": result.n_nodes,
        "n_params": result.n_params,
        "n_residuals": result.n_residuals,
        "n_sightings_used": result.n_sightings_used,
        "n_sightings_available": len(raw_sightings),
        "n_odom_edges": result.n_odom_edges,
        "n_tags_refined": len(tag_ids),
        "cost_initial": result.cost_initial,
        "cost_final": result.cost_final,
        "nfev": result.nfev,
        "success": result.success,
        "message": result.message,
        "per_tag_residual": gate_stats,
    }
    return refined_map, report


def run_one_seed(seed: int, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    markers = world.build_marker_map()
    true_by_id = {m.id: m for m in markers}
    gt = traj.build_ground_truth()

    sightings_by_tag, raw_sightings, od, dx_meas, dy_meas, dyaw_meas = run_self_survey(markers, gt, seed)
    n_sightings_per_tag = {tid: len(s) for tid, s in sightings_by_tag.items()}

    single_map = build_single_map(sightings_by_tag, markers)
    naive_map = build_naive_avg_map(sightings_by_tag, markers)
    naive_by_id = {m.id: m for m in naive_map}

    refined_map, graph_report = build_graph_and_refine(
        markers, gt, od, dx_meas, dy_meas, dyaw_meas, raw_sightings, naive_by_id, true_by_id
    )

    map_errs = {
        "single": map_error(single_map, true_by_id),
        "naive": map_error(naive_map, true_by_id),
        "refined": map_error(refined_map, true_by_id),
    }

    # downstream ATE: same tag-corrected pipeline, only the localization map varies
    ate = {}
    for name, loc_map in [("ground_truth_map", None), ("single", single_map), ("naive", naive_map), ("refined", refined_map)]:
        sub = os.path.join(out_dir, f"downstream_{name}")
        result = pipeline.run_scenario(
            f"map_refinement_{name}", gt, markers, sub, seed=seed, localization_markers=loc_map
        )
        ate[name] = {
            "ate_rmse_corrected_m": result["ate_rmse_corrected_m"],
            "ate_rmse_raw_odom_m": result["ate_rmse_raw_odom_m"],
            "corrections_accepted": result["corrections_accepted"],
            "corrections_rejected": result["corrections_rejected"],
            "detection_rate_frames_with_ge1_tag": result["detection_rate_frames_with_ge1_tag"],
        }

    summary = {
        "seed": seed,
        "n_sightings_per_tag": n_sightings_per_tag,
        "n_sightings_total": len(raw_sightings),
        "map_error": map_errs,
        "downstream_ate": ate,
        "graph_refinement": graph_report,
    }
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="42", help="comma-separated seeds, e.g. 42,7,99")
    ap.add_argument("--out", default=os.path.join(DEMO_ROOT, "out", "map_refinement"))
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    all_summaries = []
    for seed in seeds:
        out_dir = os.path.join(args.out, f"seed{seed}")
        print(f"=== seed {seed} ===")
        summary = run_one_seed(seed, out_dir)
        all_summaries.append(summary)
        print(json.dumps({
            "seed": seed,
            "map_error_pos_mean_m": {k: v["pos_err_mean_m"] for k, v in summary["map_error"].items()},
            "downstream_ate_m": {k: v["ate_rmse_corrected_m"] for k, v in summary["downstream_ate"].items()},
        }, indent=2))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "all_seeds_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2)


if __name__ == "__main__":
    main()
