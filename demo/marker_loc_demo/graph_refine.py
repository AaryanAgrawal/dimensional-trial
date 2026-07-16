"""tagSLAM-lite: joint least-squares refinement of a self-surveyed marker map.

Nodes: a subset of survey-drive camera/base poses (planar x,y,yaw -- the
robot only ever moves in-plane, so a full 6-DOF camera node would just be
three unconstrained, unobserved extra parameters) + one full-SE(3) pose per
tag (6-DOF: translation + rotation vector -- tags are real wall-mounted
markers with real roll/pitch, not a robot on a floor). Edges: per-sighting
tag observations (relative pose implied by [camera node, raw PnP solve] vs.
the tag node's own pose, weighted by the same per-tag sigma model
`localization.py` already uses for the online filter) + odometry edges
between temporally-consecutive nodes (the exact chained raw per-step
odometry measurements between them, weighted by elapsed-step count). One
node is held fixed as the gauge anchor -- see `map_refinement.py` for why
node 0 (the survey's start pose) is the natural anchor here.

`scipy.optimize.least_squares` (Trust Region Reflective, dense numerical
Jacobian) does the solve -- this is a prototype-scale graph (tens to a few
hundred nodes, ~15 tags), not a production-scale bundle adjustment; a sparse
analytic-Jacobian solver (g2o/GTSAM/Ceres-style) would be the production
follow-up once tag count/node count grows past what dense finite-difference
least_squares comfortably handles (see `trial/map-refinement.md`).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares

from . import camera as cam
from . import transforms as tf


def _wrap(a: float) -> float:
    return (a + np.pi) % (2 * np.pi) - np.pi


@dataclass
class Sighting:
    node_idx: int  # index into the node list (position, not raw frame index)
    t: float
    tag_id: int
    T_camera_marker: np.ndarray  # raw PnP solve, camera <- marker
    sigma_pos: float
    sigma_yaw: float


@dataclass
class OdomEdge:
    a: int  # node index
    b: int  # node index, temporally after a
    dx: float  # chained raw odometry delta, a's local frame
    dy: float
    dyaw: float
    sigma_pos: float
    sigma_yaw: float


def pose6_to_T(p6: np.ndarray) -> np.ndarray:
    t = p6[:3]
    rvec = np.asarray(p6[3:6], dtype=np.float64).reshape(3, 1)
    R, _ = cv2.Rodrigues(rvec)
    return tf.make_T(R, t)


def T_to_pose6(T: np.ndarray) -> np.ndarray:
    rvec, _ = cv2.Rodrigues(T[:3, :3])
    return np.concatenate([T[:3, 3], rvec.flatten()])


@dataclass
class RefineResult:
    refined_tags: dict[int, np.ndarray]  # tag_id -> T_world_marker (4x4)
    refined_cam_xytheta: dict[int, tuple[float, float, float]]  # node index -> (x,y,yaw)
    n_nodes: int
    n_sightings_used: int
    n_odom_edges: int
    n_params: int
    n_residuals: int
    cost_initial: float
    cost_final: float
    nfev: int
    success: bool
    message: str
    # per-sighting post-refinement residual, for the survey-quality gate:
    # (tag_id, node_idx, pos_err_m, rot_err_deg) unweighted, i.e. actual
    # physical discrepancy between the observation edge's two endpoints
    # after optimization -- not the sigma-normalized residual scipy sees.
    sighting_residuals: list[tuple[int, int, float, float]]


def refine(
    *,
    anchor_xytheta: tuple[float, float, float],
    sightings: list[Sighting],
    odom_edges: list[OdomEdge],
    init_cam_xytheta: dict[int, tuple[float, float, float]],  # node idx -> initial guess, excludes anchor (idx 0)
    init_tag_T: dict[int, np.ndarray],
    tag_ids: list[int],
    cam_t_by_node: list[float],  # time at each node, for the known T_base_camera(pan) lookup
    n_nodes: int,
) -> RefineResult:
    free_cam_positions = list(range(1, n_nodes))  # node 0 is the fixed anchor
    cam_off = {pos: 3 * i for i, pos in enumerate(free_cam_positions)}
    n_cam_params = 3 * len(free_cam_positions)

    tag_off = {tid: n_cam_params + 6 * i for i, tid in enumerate(tag_ids)}
    n_params = n_cam_params + 6 * len(tag_ids)

    x0 = np.zeros(n_params)
    for pos in free_cam_positions:
        x0[cam_off[pos] : cam_off[pos] + 3] = init_cam_xytheta[pos]
    for tid in tag_ids:
        x0[tag_off[tid] : tag_off[tid] + 6] = T_to_pose6(init_tag_T[tid])

    T_base_cam_by_node = [cam.base_to_camera_T(pan_yaw_rad=cam.cam_pan_yaw(t)) for t in cam_t_by_node]

    def cam_pose(params: np.ndarray, pos: int) -> tuple[float, float, float]:
        if pos == 0:
            return anchor_xytheta
        off = cam_off[pos]
        return float(params[off]), float(params[off + 1]), float(params[off + 2])

    def residuals(params: np.ndarray) -> np.ndarray:
        tag_Ts = {tid: pose6_to_T(params[tag_off[tid] : tag_off[tid] + 6]) for tid in tag_ids}
        cam_poses = [cam_pose(params, pos) for pos in range(n_nodes)]
        out: list[float] = []
        for s in sightings:
            x, y, yaw = cam_poses[s.node_idx]
            T_wb = tf.T_from_xyzyaw(x, y, 0.0, yaw)
            T_pred = T_wb @ T_base_cam_by_node[s.node_idx] @ s.T_camera_marker
            T_tag = tag_Ts[s.tag_id]
            dt = (T_pred[:3, 3] - T_tag[:3, 3]) / s.sigma_pos
            R_rel = T_pred[:3, :3].T @ T_tag[:3, :3]
            rv, _ = cv2.Rodrigues(R_rel)
            dr = rv.flatten() / s.sigma_yaw
            out.extend(dt.tolist())
            out.extend(dr.tolist())
        for e in odom_edges:
            xa, ya, yawa = cam_poses[e.a]
            xb, yb, yawb = cam_poses[e.b]
            c, s_ = np.cos(yawa), np.sin(yawa)
            dxw, dyw = xb - xa, yb - ya
            pred_dx = c * dxw + s_ * dyw
            pred_dy = -s_ * dxw + c * dyw
            pred_dyaw = _wrap(yawb - yawa)
            out.append((pred_dx - e.dx) / e.sigma_pos)
            out.append((pred_dy - e.dy) / e.sigma_pos)
            out.append(_wrap(pred_dyaw - e.dyaw) / e.sigma_yaw)
        return np.array(out)

    res0 = residuals(x0)
    cost0 = float(0.5 * np.sum(res0**2))

    result = least_squares(residuals, x0, method="trf", xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=2000)

    refined_tags = {tid: pose6_to_T(result.x[tag_off[tid] : tag_off[tid] + 6]) for tid in tag_ids}
    refined_cam = {pos: cam_pose(result.x, pos) for pos in range(n_nodes)}

    # unweighted per-sighting residual after refinement, for the quality gate
    tag_Ts_final = refined_tags
    sighting_residuals = []
    for s in sightings:
        x, y, yaw = refined_cam[s.node_idx]
        T_wb = tf.T_from_xyzyaw(x, y, 0.0, yaw)
        T_pred = T_wb @ T_base_cam_by_node[s.node_idx] @ s.T_camera_marker
        T_tag = tag_Ts_final[s.tag_id]
        pos_err = float(np.linalg.norm(T_pred[:3, 3] - T_tag[:3, 3]))
        R_rel = T_pred[:3, :3].T @ T_tag[:3, :3]
        ang = float(np.degrees(np.arccos(np.clip((np.trace(R_rel) - 1) / 2, -1, 1))))
        sighting_residuals.append((s.tag_id, s.node_idx, pos_err, ang))

    return RefineResult(
        refined_tags=refined_tags,
        refined_cam_xytheta=refined_cam,
        n_nodes=n_nodes,
        n_sightings_used=len(sightings),
        n_odom_edges=len(odom_edges),
        n_params=n_params,
        n_residuals=len(res0),
        cost_initial=cost0,
        cost_final=float(result.cost),
        nfev=int(result.nfev),
        success=bool(result.success),
        message=str(result.message),
        sighting_residuals=sighting_residuals,
    )
