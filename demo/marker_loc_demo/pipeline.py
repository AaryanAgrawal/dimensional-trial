"""The reusable run loop shared by the nominal and elevator scenarios: render
-> detect -> solve -> fuse -> gate -> correct -> metrics + plots."""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import numpy as np

from . import camera as cam
from . import features
from . import localization as loc
from . import metrics
from . import odom as odom_mod
from . import render
from . import trajectory as traj
from . import viz
from . import world


def _frame_rng(seed: int, k: int) -> np.random.Generator:
    return np.random.default_rng((seed * 1_000_003 + k) % (2**63 - 1))


def _wrap(a: float) -> float:
    return (a + np.pi) % (2 * np.pi) - np.pi


# Hybrid-mode yaw fusion: an online EMA bias estimator, not a per-step swap.
# `features.track_step`'s per-frame yaw estimate is noisy (std ~2deg/step in
# this camera/texture setup, measured directly -- see markerless-seed.md's
# "feature-tracking math check") relative to what it's meant to correct (the
# wheel odom's own slowly-wandering yaw-RATE BIAS, increment std 0.004deg/
# step, see odom.py) -- swapping wheel's yaw delta for vision's raw one
# outright makes the raw trajectory *worse* (measured: 5.4m vs 1.75m raw ATE
# with naive replacement). Instead we track a slow bias estimate from the
# (vision - wheel) disagreement at each trusted step and correct wheel's
# delta by that learned bias -- the same "let vision fix the slow thing
# wheel gets wrong, don't let it dominate the fast thing wheel already gets
# right" idea as any complementary/comp-filter IMU+vision fusion. Gain
# chosen a priori from a raw-ATE sensitivity sweep over
# [0.003, 0.01, 0.02, 0.05, 0.1] (see markerless-seed.md); none beat plain
# wheel odom outright in this synthetic setup -- reported honestly, not
# cherry-picked for a flattering number.
HYBRID_YAW_BIAS_GAIN = 0.02


def run_scenario(
    name: str,
    gt: traj.GroundTruth,
    markers: list[world.MarkerSpec],
    out_dir: str,
    *,
    seed: int,
    noise_scale: float = 1.0,
    room_w: float = world.ROOM_WIDTH_X,
    room_d: float = world.ROOM_DEPTH_Y,
    use_pan: bool = True,
    disturbance_fn: Callable[[float], tuple[float, float]] | None = None,
    render_overrides_fn: Callable[[float], tuple[float, float]] | None = None,
    poster_map: list[world.MarkerSpec] | None = None,
    use_features: bool = False,
    extra_localizer: Callable[[np.ndarray, np.ndarray], "loc.FusedMeasurement | None"] | None = None,
    frame_observer_fn: Callable[[int, np.ndarray, np.ndarray, np.ndarray], None] | None = None,
    localization_markers: list[world.MarkerSpec] | None = None,
    unique_poster_texture: bool = False,
) -> dict:
    """`disturbance_fn(t) -> (extra_trans_sigma_m, extra_yaw_sigma_rad)` layers
    a time-windowed odom noise burst on top of `noise_scale` (see `odom.py`);
    `render_overrides_fn(t) -> (blur_sigma, noise_std)` overrides the render
    module's fixed defaults per-frame (see `render.py`). Both default to None
    (no-op) so every existing caller is bit-for-bit unaffected.

    `poster_map` + `use_features=True` (default off, no-op for every existing
    caller) is the markerless-seed spike's hybrid mode (`trial/markerless-
    seed.md`): `poster_map` draws real trackable wall texture (`posters.py`);
    `use_features` replaces the plain wheel-odom "raw" stream with wheel
    translation + a vision-corrected yaw (`features.py`'s frame-to-frame LK/
    homography "visual gyro" feeds an online EMA bias estimator —
    `HYBRID_YAW_BIAS_GAIN` — rather than swapping wheel's yaw delta outright;
    see that constant's comment for why) *before* the same tag detect/fuse/
    gate/hold correction filter runs on top — the correction filter, tag
    gating, and metrics are completely unchanged, only what "raw" means
    changes.

    `extra_localizer(image, T_base_camera) -> loc.FusedMeasurement | None`
    (default None, no-op for every existing caller) is the markerless-v2
    ORB/PnP relocalization hook (`trial/markerless-seed.md` v2,
    `orb_localize.py`): called once per frame after the tag fusion attempt;
    if it returns a measurement, that measurement is fed into the SAME
    `CorrectionFilter` the tag path uses (`loc.to_world_odom_measurement` +
    `filt.try_update`) — one more independent, gated correction source, not
    a parallel filter. `frame_observer_fn(k, image, T_base_camera,
    T_world_base_corr)` (default None, no-op) is called every frame with the
    already-computed tag-corrected pose — the markerless-v2 SURVEY pass
    (`survey.py`) piggybacks on a normal (tags-only) `run_scenario` call via
    this hook to harvest per-frame camera poses for feature-map building,
    instead of duplicating the render/detect/fuse/gate/hold loop.

    `localization_markers` (default `None` -- every existing caller
    unaffected, identical to today's behavior of localizing against the same
    map that was rendered) lets the map used for detection/PnP-inversion
    differ from the map used to render ground-truth frames. `map_refinement.py`
    (Day-5 graph-refined-maps deliverable) uses this: frames always render
    against the true world map (`markers`), but localization runs against a
    self-surveyed map (single-sighting / naive-averaged / graph-refined) that
    may itself carry error -- exactly the map-error-propagates-into-ATE
    measurement Day 5 asks for.

    `unique_poster_texture` (default False, v1 spike unaffected) -- see
    `render.render_frame`'s docstring; markerless-v2's ORB survey/localize
    passes (`survey.py`, `orb_localize.py`) pass True.
    """
    os.makedirs(out_dir, exist_ok=True)
    loc_markers = localization_markers if localization_markers is not None else markers
    marker_by_id = {m.id: m for m in loc_markers}

    intr = cam.Intrinsics.default()
    filt = loc.CorrectionFilter()

    n = len(gt.t)
    true_x, true_y = gt.x.copy(), gt.y.copy()
    corr_x, corr_y = np.zeros(n), np.zeros(n)
    detected_mask = np.zeros(n, dtype=bool)
    accepted_mask = np.zeros(n, dtype=bool)
    n_tags_frame = np.zeros(n, dtype=int)
    reproj_errs: list[float] = []
    sample_candidates: list[tuple[int, np.ndarray, list, np.ndarray | None]] = []
    n_extra_accepted = 0
    n_extra_rejected = 0

    if use_features:
        dx_wheel, dy_wheel, dyaw_wheel = odom_mod.simulate_odometry_steps(
            gt, seed=seed, noise_scale=noise_scale, disturbance_fn=disturbance_fn
        )
        raw_x, raw_y, raw_yaw = np.zeros(n), np.zeros(n), np.zeros(n)
        raw_x[0], raw_y[0], raw_yaw[0] = gt.x[0], gt.y[0], gt.yaw[0]
        feature_state = features.FeatureState()
        prev_T_base_cam: np.ndarray | None = None
        vision_trusted_mask = np.zeros(n - 1, dtype=bool)
        n_inliers_trace: list[int] = []
        yaw_bias_hat = 0.0  # see HYBRID_YAW_BIAS_GAIN
    else:
        od = odom_mod.simulate_odometry(gt, seed=seed, noise_scale=noise_scale, disturbance_fn=disturbance_fn)
        raw_x, raw_y, raw_yaw = od.x.copy(), od.y.copy(), od.yaw.copy()

    t0 = time.monotonic()
    for k in range(n):
        pan = cam.cam_pan_yaw(gt.t[k]) if use_pan else 0.0
        T_base_cam = cam.base_to_camera_T(pan_yaw_rad=pan)
        T_world_base_true = cam.world_to_base_T(gt.x[k], gt.y[k], gt.yaw[k])
        T_world_camera_true = T_world_base_true @ T_base_cam
        render_kwargs = {}
        if render_overrides_fn is not None:
            blur_sigma, noise_std = render_overrides_fn(float(gt.t[k]))
            render_kwargs = {"blur_sigma": blur_sigma, "noise_std": noise_std}
        rr = render.render_frame(
            T_world_camera_true, markers, intr, rng=_frame_rng(seed, k), poster_map=poster_map,
            unique_poster_texture=unique_poster_texture, **render_kwargs
        )

        if use_features:
            prev_cam = prev_T_base_cam if prev_T_base_cam is not None else T_base_cam
            feature_state, dyaw_vision, finfo = features.track_step(feature_state, rr.image, intr.K, prev_cam, T_base_cam)
            if k >= 1:
                vision_trusted_mask[k - 1] = dyaw_vision is not None
                if dyaw_vision is not None:
                    n_inliers_trace.append(finfo["n_inliers"])
                    disagreement = dyaw_vision - dyaw_wheel[k - 1]
                    yaw_bias_hat += HYBRID_YAW_BIAS_GAIN * (disagreement - yaw_bias_hat)
                dyaw_step = dyaw_wheel[k - 1] + yaw_bias_hat
                c0, s0 = np.cos(raw_yaw[k - 1]), np.sin(raw_yaw[k - 1])
                raw_x[k] = raw_x[k - 1] + c0 * dx_wheel[k - 1] - s0 * dy_wheel[k - 1]
                raw_y[k] = raw_y[k - 1] + s0 * dx_wheel[k - 1] + c0 * dy_wheel[k - 1]
                raw_yaw[k] = _wrap(raw_yaw[k - 1] + dyaw_step)
            prev_T_base_cam = T_base_cam

        filt.predict_step()
        dets = loc.detect_and_solve(rr.image, marker_by_id, intr)
        detected_mask[k] = len(dets) > 0
        n_tags_frame[k] = len(dets)
        reproj_errs.extend(d.reproj_err_px for d in dets)

        T_odom_base_raw = cam.world_to_base_T(raw_x[k], raw_y[k], raw_yaw[k])
        fused = loc.fuse_detections(dets, T_base_cam)
        if fused is not None:
            meas = loc.to_world_odom_measurement(fused, T_odom_base_raw)
            accepted_mask[k] = filt.try_update(meas)

        if extra_localizer is not None:
            extra_fused = extra_localizer(rr.image, T_base_cam)
            if extra_fused is not None:
                extra_meas = loc.to_world_odom_measurement(extra_fused, T_odom_base_raw)
                if filt.try_update(extra_meas):
                    n_extra_accepted += 1
                else:
                    n_extra_rejected += 1

        T_world_base_corr = filt.T_world_odom @ T_odom_base_raw
        corr_x[k], corr_y[k] = T_world_base_corr[0, 3], T_world_base_corr[1, 3]

        if frame_observer_fn is not None:
            frame_observer_fn(k, rr.image, T_base_cam, T_world_base_corr)

        if k % max(n // 24, 1) == 0:
            corners, ids, _ = loc.tags.get_detector().detectMarkers(rr.image)
            sample_candidates.append((k, rr.image, corners, ids))

    dt = time.monotonic() - t0

    ate_raw = metrics.ate_rmse(true_x, true_y, raw_x, raw_y)
    ate_corr = metrics.ate_rmse(true_x, true_y, corr_x, corr_y)
    err_raw = metrics.per_frame_error(true_x, true_y, raw_x, raw_y)
    err_corr = metrics.per_frame_error(true_x, true_y, corr_x, corr_y)

    result = {
        "scenario": name,
        "frames_total": int(n),
        "duration_s": float(gt.t[-1]),
        "hz": traj.HZ,
        "num_markers": len(markers),
        "odom_noise_scale": noise_scale,
        "cab_motion_disturbance": disturbance_fn is not None,
        "door_visibility_render_overrides": render_overrides_fn is not None,
        "ate_rmse_raw_odom_m": ate_raw,
        "ate_rmse_corrected_m": ate_corr,
        "improvement_factor": (ate_raw / ate_corr) if ate_corr > 1e-9 else float("inf"),
        "max_error_raw_odom_m": float(err_raw.max()),
        "max_error_corrected_m": float(err_corr.max()),
        "detection_rate_frames_with_ge1_tag": float(np.mean(detected_mask)),
        "frames_with_ge1_tag": int(detected_mask.sum()),
        "mean_tags_per_detected_frame": float(np.mean(n_tags_frame[detected_mask])) if detected_mask.any() else 0.0,
        "max_tags_in_any_frame": int(n_tags_frame.max()),
        "mean_reprojection_error_px": float(np.mean(reproj_errs)) if reproj_errs else None,
        "corrections_accepted": filt.n_accepted,
        "corrections_rejected": filt.n_rejected,
        "wall_clock_s": round(dt, 2),
        "use_features": use_features,
        "poster_map_size": len(poster_map) if poster_map else 0,
    }
    if use_features:
        result["vision_yaw_trusted_fraction"] = float(np.mean(vision_trusted_mask))
        result["vision_yaw_mean_inliers"] = float(np.mean(n_inliers_trace)) if n_inliers_trace else 0.0
    if extra_localizer is not None:
        result["extra_localizer_accepted"] = n_extra_accepted
        result["extra_localizer_rejected"] = n_extra_rejected

    viz.plot_trajectory(
        os.path.join(out_dir, "trajectory.png"),
        true_x, true_y, raw_x, raw_y, corr_x, corr_y, markers, room_w, room_d,
    )
    viz.plot_error(os.path.join(out_dir, "error.png"), gt.t, err_raw, err_corr, detected_mask)

    with_det = [c for c in sample_candidates if c[3] is not None]
    pool = with_det if len(with_det) >= 4 else sample_candidates
    picks = [pool[i] for i in np.linspace(0, len(pool) - 1, num=min(4, len(pool)), dtype=int)]
    for i, (k, img, corners, ids) in enumerate(picks):
        n_det = 0 if ids is None else len(ids)
        title = f"t={gt.t[k]:.1f}s  tags_detected={n_det}"
        viz.save_sample_frame(os.path.join(out_dir, f"sample_frame_{i}.png"), img, corners, ids, title)

    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump(result, f, indent=2)

    return result
