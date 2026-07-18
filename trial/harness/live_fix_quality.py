#!/usr/bin/env python3
"""Live fiducial fix quality on the village3 rehearsal: what separates good fixes from bad.

    spy_world_map_fix.log (112 live world->map fixes, wall ts)
        + replay_run.log   (20 accepted source=ransac answers -> reference)
        + hk_village3.db   (offline re-detection, UNGATED     -> per-fix variables)
        + prepared pkl PGO graph + rehearsal marker map       (cross-checks)
    -> per-fix translation error, mechanism decomposition, threshold sweeps
    -> minimal gate recommendation + figure

MEASURED MECHANISM (this script's output, Jul 18 rehearsal data): the fix is
world_T_tag(detection) @ inv(map_T_tag), and map_T_tag places the tag 31.26 m
from the map origin — so every degree of per-detection tag-ORIENTATION error
moves the published world->map translation by 31.26*pi/180 = 0.546 m. Per-
detection orientation deviation (median 4.7 deg, p90 8.9 deg across passing
detections) times that lever reproduces the observed fix scatter almost
exactly (spearman rho +0.97 against fix deviation); tag POSITION deviation
(median 0.29 m) is second-order — swapping in a consensus translation changes
nothing, swapping in a consensus rotation collapses the error. Range, speed,
sharpness, reprojection do NOT separate (|rho| <= 0.25 on the stratified
metric); the offline benchmark's QualityWindow/SpeedLimit gating is therefore
NOT the live-vs-offline gap on this recording. The one live-observable that
tracks orientation error is the IPPE ambiguity ratio (rho -0.57).

TIMELINE JOIN: replay re-publishes recorded messages with ORIGINAL recording
ts (memory2 Replay; ReplayConnection), but the visual module stamps its fix
`.now()` (visual_relocalization_module.py:124), so wall = rec + one constant
offset (speed=1.0), fit by grid search against detections passing the live
gates (residual p50 ~4 ms — the join is exact).

REFERENCE QUALITY (stated, not hidden): primary reference = accepted ransac
answers, nearest-in-time — self-consistent scan matching tracking ~1.2 m of
drift, NOT truth; the first fix burst (52/112) predates the first accept by
~26 s, so its reference is a constant back-extrapolation. Cross-reference =
inv(correction_at(ts)) from the prepared PGO graph (silver, ~6 cm floor, and
the live premap came from a SEPARATE PGO run). The two references agree to
0.07-0.15 m at replay start but diverge to ~1.9 m by end of run (village3's
documented non-monotonic-drift pathology) — that disagreement IS the
reference error budget, and no conclusion here rests on differences smaller
than it. Both error columns are reported for every fix.

Deterministic: no RNG (ArUco, solvePnP, eigendecompositions, least squares);
SEED printed per house rule. Detection pass cached to .npz keyed on db
path+mtime+size and gate params; --no-cache forces recompute.

Run: cd /home/dimos/dimensional-trial/dimos && \
     uv run python ../trial/harness/live_fix_quality.py
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scipy.stats import spearmanr

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped  # noqa: E402
from dimos.msgs.sensor_msgs.Image import Image  # noqa: E402
from dimos.perception.fiducial.marker_pose import (  # noqa: E402
    camera_info_to_cv_matrices,
    create_aruco_detector,
    estimate_marker_pose_candidates,
    marker_reprojection_error,
)
from dimos.perception.fiducial.visual_relocalization import detect_markers  # noqa: E402
from dimos.robot.unitree.go2.connection import (  # noqa: E402
    BASE_TO_OPTICAL,
    _camera_info_static,
)

SEED = 0  # printed per house rule; nothing below draws random numbers

TAG_ID = 10  # the one surveyed tag in hk_village3.marker_map.yaml
MARKER_LENGTH_M = 0.10  # matches every -o marker_length_m in the rehearsal
# The live module's own per-tag gates (VisualRelocalizationModuleConfig
# defaults, unchanged in the rehearsal command) — replicated so "pass" below
# means "the live module would have published a fix for this frame".
REPROJ_GATE_PX = 3.0
AMBIGUITY_MIN = 2.0
ARUCO_DICT = "DICT_APRILTAG_36h11"

JOIN_TOL_S = 0.25  # fix->detection nearest join window; camera period is 70 ms
SPEED_WIN_S = 0.25  # +/- window for odom LSQ velocity at a detection ts
BURST_GAP_S = 5.0  # >5 s between fixes = a new tag-approach pass


# --------------------------------------------------------------- log parsing

_FIX_RE = re.compile(r"^fix#(\d+) ts=([\d.]+) world->map t=\[([^\]]+)\]")
_RELOC_RE = re.compile(
    r"^(\d\d):(\d\d):(\d\d\.\d+) .*relocalize: fitness=([\d.]+) time_cost=([\d.]+)s "
    r"n_pts=\d+ reloc_t=\[[^\]]+\] TF 'world' -> 'map' "
    r"published_t=\[([^\]]+)\] source=(\w+)"
)


def parse_spy(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """spy_world_map_fix.log -> (wall_ts[n], t_world_map[n,3] m)."""
    ts, t = [], []
    for line in path.read_text().splitlines():
        if m := _FIX_RE.match(line):
            ts.append(float(m.group(2)))
            t.append([float(v) for v in m.group(3).split(",")])
    if not ts:
        raise ValueError(f"no 'fix#' lines in {path} — wrong file?")
    return np.array(ts), np.array(t)


def parse_replay_log(path: Path, date_utc: str) -> dict[str, np.ndarray]:
    """Accepted relocalize answers -> wall epoch ts + published world->map t (m).

    Log lines carry UTC HH:MM:SS.mmm only; the date comes from --log-date. A
    printed accept ts is when ICP *finished* — at most 4.6 s stale, ~0.06 m of
    drift at the measured ~0.013 m/s: negligible against meter-scale errors.
    """
    day = datetime.strptime(date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts, t, fit, cost = [], [], [], []
    for line in path.read_text().splitlines():
        if (m := _RELOC_RE.match(line)) and m.group(7) == "ransac":
            hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
            ts.append(day.timestamp() + hh * 3600 + mm * 60 + ss)
            fit.append(float(m.group(4)))
            cost.append(float(m.group(5)))
            t.append([float(v) for v in m.group(6).split(",")])
    if not ts:
        raise ValueError(f"no accepted relocalize lines in {path}")
    return {"wall_ts": np.array(ts), "t": np.array(t),
            "fitness": np.array(fit), "time_cost_s": np.array(cost)}


# --------------------------------------------------- offline detection pass


def detection_pass(db_path: Path, cache: Path, use_cache: bool) -> dict[str, np.ndarray]:
    """The live module's exact detection+solve on every db frame, UNGATED.

    Every tag-10 detection is kept with its variables; the live accept
    decision is recorded as pass_live (ambiguity gate first, then reproj —
    the order in localize_from_detections). Columns (units in the names):
    ts, range_m, side_px, reproj_px, amb_ratio, view_deg, edge_px, sharpness,
    pass_live, plus the raw solve (rvec_0..2 rad, tvec_0..2 m, optical frame)
    so the fix chain can be reconstructed offline.
    """
    key = f"{db_path}:{db_path.stat().st_mtime_ns}:{db_path.stat().st_size}:" \
          f"{TAG_ID}:{MARKER_LENGTH_M}:{REPROJ_GATE_PX}:{AMBIGUITY_MIN}:{ARUCO_DICT}:v3"
    if use_cache and cache.exists():
        z = np.load(cache, allow_pickle=False)
        if str(z["key"]) == key:
            print(f"detection pass: cache hit {cache}")
            return {k: z[k] for k in z.files if k != "key"}
        print("detection pass: cache stale — recomputing")

    info = _camera_info_static()
    k_mat, d_mat = camera_info_to_cv_matrices(info)
    model = info.distortion_model
    cx, cy = k_mat[0, 2], k_mat[1, 2]
    detector = create_aruco_detector(ARUCO_DICT)

    cols: dict[str, list[float]] = {c: [] for c in (
        "ts", "range_m", "side_px", "reproj_px", "amb_ratio", "view_deg",
        "edge_px", "sharpness", "pass_live",
        "rvec_0", "rvec_1", "rvec_2", "tvec_0", "tvec_1", "tvec_2")}
    n_frames = n_other_ids = 0
    store = SqliteStore(path=str(db_path), must_exist=True)
    with store:
        for obs in store.stream("color_image", Image):
            n_frames += 1
            img = obs.data
            for marker_id, corners in detect_markers(img.to_grayscale().as_numpy(), detector):
                if marker_id != TAG_ID:
                    n_other_ids += 1
                    continue
                cands = estimate_marker_pose_candidates(
                    corners, MARKER_LENGTH_M, k_mat, d_mat, distortion_model=model)
                if not cands:
                    continue
                scored = sorted(
                    (marker_reprojection_error(corners, MARKER_LENGTH_M, k_mat, d_mat,
                                               rv, tv, distortion_model=model), rv, tv)
                    for rv, tv in cands)
                err, rvec, tvec = scored[0]
                ratio = scored[1][0] / err if len(scored) > 1 and err > 1e-12 else np.inf
                ok = (ratio >= AMBIGUITY_MIN) and (err <= REPROJ_GATE_PX)
                rot, _ = cv2.Rodrigues(rvec)
                # angle between the camera->tag ray and the tag normal (tag +z
                # in optical frame): 0 deg = head-on, 90 deg = grazing view
                ray = tvec.reshape(3) / np.linalg.norm(tvec)
                view = float(np.degrees(np.arccos(np.clip(abs(rot[:, 2] @ ray), 0, 1))))
                sides = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
                cols["ts"].append(float(obs.ts))
                cols["range_m"].append(float(np.linalg.norm(tvec)))
                cols["side_px"].append(float(sides.mean()))
                cols["reproj_px"].append(float(err))
                cols["amb_ratio"].append(float(min(ratio, 1e6)))
                cols["view_deg"].append(view)
                cols["edge_px"].append(float(np.linalg.norm(corners.mean(axis=0) - (cx, cy))))
                cols["sharpness"].append(float(img.sharpness))
                cols["pass_live"].append(float(ok))
                for i in range(3):
                    cols[f"rvec_{i}"].append(float(rvec.reshape(3)[i]))
                    cols[f"tvec_{i}"].append(float(tvec.reshape(3)[i]))
    out = {c: np.array(v) for c, v in cols.items()}
    print(f"detection pass: {n_frames} frames, {len(out['ts'])} tag-{TAG_ID} detections, "
          f"{int(out['pass_live'].sum())} pass live gates, {n_other_ids} other-id detections")
    np.savez(cache, key=key, **out)
    return out


# ---------------------------------------------------------------- odometry


class OdomTrack:
    """Recorded odom stream -> interpolated world_T_base + speeds at any ts."""

    def __init__(self, db_path: Path) -> None:
        rows = []
        store = SqliteStore(path=str(db_path), must_exist=True)
        with store:
            for obs in store.stream("odom", PoseStamped):
                p, q = obs.data.position, obs.data.orientation
                rows.append((float(obs.ts), p.x, p.y, p.z, q.x, q.y, q.z, q.w))
        arr = np.array(rows)
        order = np.argsort(arr[:, 0])
        arr = arr[order]
        self.ts = arr[:, 0]
        self.pos = arr[:, 1:4]
        self.rot = Rotation.from_quat(arr[:, 4:8])
        self._slerp = Slerp(self.ts, self.rot)

    def world_T_base(self, t: float) -> np.ndarray:
        """Linear position + slerp rotation interpolation (odom is ~19 Hz)."""
        t = float(np.clip(t, self.ts[0], self.ts[-1]))
        i = int(np.clip(np.searchsorted(self.ts, t), 1, len(self.ts) - 1))
        w = (t - self.ts[i - 1]) / (self.ts[i] - self.ts[i - 1])
        T = np.eye(4)
        T[:3, 3] = self.pos[i - 1] * (1 - w) + self.pos[i] * w
        T[:3, :3] = self._slerp([t]).as_matrix()[0]
        return T

    def speeds(self, at_ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(speed_mps, gyro_dps): LSQ position slope / endpoint rotation angle
        over ts +/- SPEED_WIN_S (~9 samples at 19 Hz — robust to pose noise)."""
        speed, gyro = [], []
        for t in at_ts:
            i0, i1 = np.searchsorted(self.ts, (t - SPEED_WIN_S, t + SPEED_WIN_S))
            if i1 - i0 < 3:
                speed.append(np.nan)
                gyro.append(np.nan)
                continue
            tt = self.ts[i0:i1] - t
            a = np.stack([tt, np.ones_like(tt)], axis=1)
            vel, *_ = np.linalg.lstsq(a, self.pos[i0:i1], rcond=None)
            speed.append(float(np.linalg.norm(vel[0])))
            dr = self.rot[i1 - 1] * self.rot[i0].inv()
            gyro.append(float(np.degrees(dr.magnitude()) / (self.ts[i1 - 1] - self.ts[i0])))
        return np.array(speed), np.array(gyro)


# ------------------------------------------------------------- offset + join


def fit_offset(fix_wall: np.ndarray, pass_rec: np.ndarray) -> tuple[float, np.ndarray]:
    """One constant wall = rec + offset (replay speed 1.0). Grid search the
    offset maximizing fixes with a passing detection within JOIN_TOL_S, then
    re-center on the matched-pair median. Returns (offset_s, residuals_s)."""
    center = np.median(fix_wall) - np.median(pass_rec)
    grid = np.arange(center - 40.0, center + 40.0, 0.02)
    best_o, best_n, best_med = center, -1, np.inf
    for o in grid:
        r = fix_wall - o
        j = np.clip(np.searchsorted(pass_rec, r), 1, len(pass_rec) - 1)
        d = np.minimum(np.abs(r - pass_rec[j - 1]), np.abs(r - pass_rec[j]))
        n = int((d < JOIN_TOL_S).sum())
        med = float(np.median(d))
        if n > best_n or (n == best_n and med < best_med):
            best_o, best_n, best_med = o, n, med
    r = fix_wall - best_o
    j = np.clip(np.searchsorted(pass_rec, r), 1, len(pass_rec) - 1)
    nearest = np.where(np.abs(r - pass_rec[j - 1]) < np.abs(r - pass_rec[j]),
                       pass_rec[j - 1], pass_rec[j])
    matched = np.abs(r - nearest) < JOIN_TOL_S
    best_o += float(np.median((r - nearest)[matched]))
    return best_o, (fix_wall - best_o) - nearest


def nearest_idx(sorted_ts: np.ndarray, at: np.ndarray) -> np.ndarray:
    j = np.clip(np.searchsorted(sorted_ts, at), 1, len(sorted_ts) - 1)
    return np.where(np.abs(at - sorted_ts[j - 1]) <= np.abs(at - sorted_ts[j]), j - 1, j)


def markley_mean(rots: Rotation) -> Rotation:
    """Eigen-average of rotations (Markley) — sign-independent quaternion mean."""
    qs = rots.as_quat()
    m = np.einsum("ni,nj->ij", qs, qs) / len(qs)
    return Rotation.from_quat(np.linalg.eigh(m)[1][:, -1])


# ------------------------------------------------------------------ analysis


def sweep(name: str, v: np.ndarray, err: np.ndarray, thresholds: list[float],
          keep_below: bool = True, unit: str = "m") -> list[str]:
    """Median error kept vs cut at each threshold (kept = below thr by default)."""
    lines = []
    for thr in thresholds:
        keep = v < thr if keep_below else v >= thr
        cut = ~keep
        if keep.sum() == 0 or cut.sum() == 0:
            continue
        op = "<" if keep_below else ">="
        lines.append(
            f"  {name} {op} {thr:g}: median {np.median(err[keep]):.2f} {unit} "
            f"p90 {np.percentile(err[keep], 90):.2f} {unit} n={keep.sum()}  |  "
            f"cut: median {np.median(err[cut]):.2f} {unit} n={cut.sum()}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rehearsal-dir", type=Path, default=HARNESS / "out" / "rehearsal")
    ap.add_argument("--recording", default="hk_village3")
    ap.add_argument("--log-date", default="2026-07-18",
                    help="UTC date of replay_run.log HH:MM:SS timestamps")
    ap.add_argument("--fig", type=Path,
                    default=HARNESS.parent / "results" / "figures" / "live_fix_quality_village3.png")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    dimos_root = HARNESS.resolve().parents[1] / "dimos"
    db = dimos_root / "data" / f"{a.recording}.db"
    rev = {r: subprocess.run(["git", "-C", str(p), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
           for r, p in (("trial", HARNESS.parent), ("dimos", dimos_root))}
    print(f"live_fix_quality: SEED={SEED} (no RNG used) git trial={rev['trial']} "
          f"dimos={rev['dimos']} db={db}")

    fix_wall, fix_t = parse_spy(a.rehearsal_dir / "spy_world_map_fix.log")
    reloc = parse_replay_log(a.rehearsal_dir / "replay_run.log", a.log_date)
    print(f"parsed: {len(fix_wall)} live fixes, {len(reloc['wall_ts'])} accepted "
          f"ransac answers (fitness {reloc['fitness'].min():.2f}-{reloc['fitness'].max():.2f})")

    det = detection_pass(db, a.rehearsal_dir / "live_fix_quality_cache.npz",
                         use_cache=not a.no_cache)
    order = np.argsort(det["ts"])
    det = {k: v[order] for k, v in det.items()}
    passing = det["pass_live"] > 0

    offset, resid = fit_offset(fix_wall, det["ts"][passing])
    print(f"wall = rec + {offset:.3f} s (fit on {int((np.abs(resid) < JOIN_TOL_S).sum())}"
          f"/{len(fix_wall)} fixes matched to passing detections; residual "
          f"p50={np.median(np.abs(resid)) * 1e3:.0f} ms "
          f"p90={np.percentile(np.abs(resid), 90) * 1e3:.0f} ms)")
    fix_rec = fix_wall - offset

    # join each fix to its detection (fixes exist only for passing frames)
    pass_ts = det["ts"][passing]
    ji_pass = nearest_idx(pass_ts, fix_rec)
    dt = np.abs(fix_rec - pass_ts[ji_pass])
    ok_join = dt < JOIN_TOL_S
    ji = np.flatnonzero(passing)[ji_pass]  # index into the full detection table
    print(f"join: {int(ok_join.sum())}/{len(fix_wall)} fixes matched a passing "
          f"detection within {JOIN_TOL_S}s (max dt {dt[ok_join].max() * 1e3:.0f} ms)")

    odom = OdomTrack(db)
    speed_mps, gyro_dps = odom.speeds(det["ts"][ji])

    # ---- reference 1 (primary, task-specified): accepted ransac, nearest-in-time
    ri = nearest_idx(reloc["wall_ts"], fix_wall)
    err_ransac = np.linalg.norm(fix_t - reloc["t"][ri], axis=1)
    extrapolated = fix_wall < reloc["wall_ts"][0]  # burst 1 predates 1st accept

    # ---- reference 2 (cross-check): prepared-graph PGO correction, inverted
    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        graph = pickle.loads(pickle.load(f)["pose_graph_bytes"])
    t_wm_pgo = np.array([np.linalg.inv(transform_to_mat(graph.correction_at(t)))[:3, 3]
                         for t in fix_rec])
    err_pgo = np.linalg.norm(fix_t - t_wm_pgo, axis=1)
    acc_pgo = np.array([np.linalg.inv(transform_to_mat(graph.correction_at(t)))[:3, 3]
                        for t in reloc["wall_ts"] - offset])
    dref = np.linalg.norm(reloc["t"] - acc_pgo, axis=1)
    print(f"reference budget |ransac - inv(PGO corr)| at the {len(dref)} accept times: "
          f"first {dref[0]:.2f} m, median {np.median(dref):.2f} m, max {dref.max():.2f} m "
          f"(agree at start, diverge with drift — village3 non-monotonic-drift pathology; "
          f"no conclusion below rests on sub-{dref.max():.1f} m differences)")
    drift_norm = np.linalg.norm(reloc["t"], axis=1)
    print(f"drift tracked by ransac reference: |published_t| {drift_norm.min():.2f} -> "
          f"{drift_norm.max():.2f} m over the run")

    for label, e in (("vs ransac ref (primary)", err_ransac), ("vs PGO ref  ", err_pgo)):
        print(f"fix translation error {label}: mean {e.mean():.2f} median "
              f"{np.median(e):.2f} p90 {np.percentile(e, 90):.2f} max {e.max():.2f} m")
    print(f"  burst-1 caveat: {int(extrapolated.sum())}/{len(fix_wall)} fixes predate the "
          f"first accepted answer — their ransac ref is a constant back-extrapolation "
          f"(same fixes vs PGO ref: median {np.median(err_pgo[extrapolated]):.2f} m)")

    # ---- mechanism: world_T_tag per detection; orientation lever to map origin
    mm = __import__("yaml").safe_load(
        (a.rehearsal_dir / f"{a.recording}.marker_map.yaml").read_text())["markers"][TAG_ID]
    T_map_tag = pose7_to_mat((*mm["translation"], *mm["rotation"]))
    M_inv = np.linalg.inv(T_map_tag)
    lever_m = float(np.linalg.norm(M_inv[:3, 3]))  # camera-independent: |map_t_tag|
    lever_m_per_deg = lever_m * np.pi / 180.0
    T_base_opt = transform_to_mat(BASE_TO_OPTICAL)

    n_det = len(det["ts"])
    T_w_tag = np.empty((n_det, 4, 4))
    for i in range(n_det):
        T_opt_tag = np.eye(4)
        T_opt_tag[:3, :3] = cv2.Rodrigues(
            np.array([det["rvec_0"][i], det["rvec_1"][i], det["rvec_2"][i]]))[0]
        T_opt_tag[:3, 3] = (det["tvec_0"][i], det["tvec_1"][i], det["tvec_2"][i])
        T_w_tag[i] = odom.world_T_base(det["ts"][i]) @ T_base_opt @ T_opt_tag
    R_w_tag = Rotation.from_matrix(T_w_tag[:, :3, :3])
    cons_R = markley_mean(R_w_tag[passing])
    dev_deg = np.degrees((cons_R.inv() * R_w_tag).magnitude())
    pos_dev_m = np.linalg.norm(
        T_w_tag[:, :3, 3] - np.median(T_w_tag[passing][:, :3, 3], axis=0), axis=1)

    recon_t = np.array([(T_w_tag[i] @ M_inv)[:3, 3] for i in ji])
    recon_err = np.linalg.norm(fix_t - recon_t, axis=1)
    print(f"\nMECHANISM — offline reconstruction world_T_tag(det) @ inv(map_T_tag): "
          f"matches the spied fixes |dt| median {np.median(recon_err):.3f} m "
          f"p90 {np.percentile(recon_err, 90):.2f} m (chain understood exactly)")
    print(f"orientation lever: tag is {lever_m:.2f} m from the map origin -> "
          f"{lever_m_per_deg:.3f} m of world->map translation PER DEGREE of tag-orientation error")
    print(f"per-detection world tag-orientation deviation from run consensus (passing dets): "
          f"median {np.median(dev_deg[passing]):.1f} p90 "
          f"{np.percentile(dev_deg[passing], 90):.1f} max {dev_deg[passing].max():.1f} deg "
          f"-> lever-predicted scatter median "
          f"{np.median(dev_deg[passing]) * lever_m_per_deg:.2f} m")
    print(f"per-detection world tag-POSITION deviation: median "
          f"{np.median(pos_dev_m[passing]):.2f} p90 {np.percentile(pos_dev_m[passing], 90):.2f} m "
          f"(second-order)")
    glob_dev = np.linalg.norm(fix_t - np.median(fix_t, axis=0), axis=1)
    rho, p = spearmanr(dev_deg[ji], glob_dev)
    print(f"rho(per-detection orientation deviation, fix deviation) = {rho:+.2f} (p={p:.1e}) "
          f"— the fix error IS orientation error through the lever")

    # counterfactuals: swap detection rotation/translation for the burst consensus
    burst = np.zeros(len(fix_wall), dtype=int)
    burst[1:] = np.cumsum(np.diff(fix_wall) > BURST_GAP_S)
    cf = {"live (as published)": [], "consensus rotation": [], "consensus translation": []}
    for i in range(len(fix_wall)):
        members = ji[burst == burst[i]]
        R_b = markley_mean(R_w_tag[members]).as_matrix()
        t_b = np.median(T_w_tag[members][:, :3, 3], axis=0)
        T = T_w_tag[ji[i]].copy()
        cf["live (as published)"].append((T @ M_inv)[:3, 3])
        T2 = T.copy()
        T2[:3, :3] = R_b
        cf["consensus rotation"].append((T2 @ M_inv)[:3, 3])
        T3 = T.copy()
        T3[:3, 3] = t_b
        cf["consensus translation"].append((T3 @ M_inv)[:3, 3])
    print("\ncounterfactual fixes (per-burst consensus swapped in), error vs both refs:")
    for k, v in cf.items():
        v = np.array(v)
        er = np.linalg.norm(v - reloc["t"][ri], axis=1)
        ep = np.linalg.norm(v - t_wm_pgo, axis=1)
        print(f"  {k:>22}: ransac median {np.median(er):.2f} p90 {np.percentile(er, 90):.2f} | "
              f"PGO median {np.median(ep):.2f} p90 {np.percentile(ep, 90):.2f} m")
    print("  -> rotation carries ~all gateable error; the ~1.4-1.5 m consensus floor is "
          "survey-orientation + reference budget, unreachable by any per-detection gate")

    # ---- which live-observable separates
    joined_full = {"range_m": det["range_m"][ji], "side_px": det["side_px"][ji],
                   "reproj_px": det["reproj_px"][ji], "amb_ratio": det["amb_ratio"][ji],
                   "view_deg": det["view_deg"][ji], "edge_px": det["edge_px"][ji],
                   "sharpness": det["sharpness"][ji],
                   "speed_mps": speed_mps, "gyro_dps": gyro_dps}
    in_burst_dev = np.zeros(len(fix_wall))
    for b in np.unique(burst):
        m = burst == b
        in_burst_dev[m] = np.linalg.norm(fix_t[m] - np.median(fix_t[m], axis=0), axis=1)
    print(f"\nspearman rank corr (n={len(fix_wall)} fixes): vs err_ransac | vs in-burst "
          f"deviation (stratified, reference-free):")
    for k, v in joined_full.items():
        m = np.isfinite(v)
        r1, p1 = spearmanr(v[m], err_ransac[m])
        r2, p2 = spearmanr(v[m], in_burst_dev[m])
        print(f"  {k:>10}: {r1:+.2f} (p={p1:.0e}) | {r2:+.2f} (p={p2:.0e})")
    rho, p = spearmanr(det["amb_ratio"][passing], dev_deg[passing])
    print(f"  amb_ratio vs the mechanism variable dev_deg over all "
          f"{int(passing.sum())} passing detections: rho={rho:+.2f} (p={p:.1e})")

    print("\nthreshold sweeps, err vs ransac ref (kept side first):")
    for line in sweep("amb_ratio", joined_full["amb_ratio"], err_ransac,
                      [3, 5, 10, 20], keep_below=False):
        print(line)
    for line in sweep("range_m", joined_full["range_m"], err_ransac, [0.5, 0.75, 1.0]):
        print(line)
    for line in sweep("reproj_px", joined_full["reproj_px"], err_ransac, [0.5, 1.0]):
        print(line)
    for line in sweep("speed_mps", joined_full["speed_mps"], err_ransac, [0.25, 0.5]):
        print(line)
    print("threshold sweeps on the mechanism variable dev_deg over passing detections:")
    for line in sweep("amb_ratio", det["amb_ratio"][passing], dev_deg[passing],
                      [3, 5, 10, 20], keep_below=False, unit="deg"):
        print(line)

    print("\nper-burst (tag approach pass), joined fixes:")
    for b in np.unique(burst):
        m = burst == b
        kept5 = m & (joined_full["amb_ratio"] >= 5.0)
        print(f"  burst {b}: n={m.sum()} range median {np.median(joined_full['range_m'][m]):.2f} m "
              f"err_ransac {np.median(err_ransac[m]):.2f} m err_pgo {np.median(err_pgo[m]):.2f} m "
              f"in-burst dev {np.median(in_burst_dev[m]):.2f} m | amb>=5 keeps {kept5.sum()}")

    rec5 = joined_full["amb_ratio"] >= 5.0
    print(f"\nRECOMMENDED MINIMAL GATE — raise ambiguity_ratio_min 2.0 -> 5.0 "
          f"(existing config knob, zero new code):")
    print(f"  keeps {int(rec5.sum())}/{len(fix_wall)} live fixes "
          f"({int((det['amb_ratio'][passing] >= 5.0).sum())}/{int(passing.sum())} passing detections)")
    print(f"  kept: err_ransac median {np.median(err_ransac[rec5]):.2f} "
          f"p90 {np.percentile(err_ransac[rec5], 90):.2f} m | "
          f"cut: median {np.median(err_ransac[~rec5]):.2f} m")
    print(f"  mechanism: kept dev_deg median "
          f"{np.median(dev_deg[passing][det['amb_ratio'][passing] >= 5.0]):.1f} deg vs cut "
          f"{np.median(dev_deg[passing][det['amb_ratio'][passing] < 5.0]):.1f} deg "
          f"(x{lever_m_per_deg:.2f} m/deg lever)")
    print("  NOT supported by this data: range/speed/sharpness/reproj gates (|rho|<=0.25 "
          "or inverted; 315/316 detections are already < 1.5 m range). Even a perfect "
          "gate floors at the ~1.4-1.5 m consensus bias on this single-tag 31 m-lever "
          "geometry — the deployment levers are tag-near-origin/multi-tag/orientation "
          "smoothing, not a detection gate.")

    # ---- persist the per-fix table for the next agent
    table = {
        "meta": {"seed": SEED, "git": rev, "offset_wall_minus_rec_s": offset,
                 "join_tol_s": JOIN_TOL_S, "n_fixes": int(len(fix_wall)),
                 "n_joined": int(ok_join.sum()), "lever_m": lever_m,
                 "ref_note": ("primary=nearest accepted ransac answer (~1.2 m drift "
                              "tracked; burst 1 back-extrapolated); cross=inv(PGO "
                              "correction_at); references disagree up to "
                              f"{dref.max():.2f} m late-run — the error budget")},
        "fixes": [{
            "wall_ts": float(fix_wall[i]), "rec_ts": float(fix_rec[i]),
            "burst": int(burst[i]),
            "t_world_map_m": fix_t[i].round(4).tolist(),
            "err_ransac_m": round(float(err_ransac[i]), 4),
            "err_pgo_m": round(float(err_pgo[i]), 4),
            "in_burst_dev_m": round(float(in_burst_dev[i]), 4),
            "dev_deg": round(float(dev_deg[ji[i]]), 3),
            "ref_extrapolated": bool(extrapolated[i]),
            "joined": bool(ok_join[i]),
            **{k: round(float(joined_full[k][i]), 4) for k in joined_full},
        } for i in range(len(fix_wall))],
    }
    out_json = a.rehearsal_dir / "live_fix_quality_village3.json"
    out_json.write_text(json.dumps(table, indent=1))
    print(f"\nwrote {out_json}")

    make_figure(a.fig, joined_full["amb_ratio"], err_ransac, dev_deg[ji] * lever_m_per_deg,
                glob_dev, extrapolated, lever_m)
    print(f"wrote {a.fig}")
    return 0


# -------------------------------------------------------------------- figure


def make_figure(path: Path, amb_ratio: np.ndarray, err_ransac: np.ndarray,
                lever_pred_m: np.ndarray, glob_dev: np.ndarray,
                extrapolated: np.ndarray, lever_m: float) -> None:
    """Left: the mechanism (fix deviation = orientation deviation x lever).
    Right: the recommended gate (err vs ambiguity ratio, threshold at 5)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, ink, muted = "#2a78d6", "#0b0b0b", "#52514e"
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.8), facecolor="#fcfcfb")

    solid = ~extrapolated
    ax1.scatter(lever_pred_m[solid], glob_dev[solid], s=18, c=blue, alpha=0.75, lw=0)
    ax1.scatter(lever_pred_m[~solid], glob_dev[~solid], s=22, facecolors="none",
                edgecolors=blue, alpha=0.85, lw=1.0)
    lim = max(lever_pred_m.max(), glob_dev.max()) * 1.05
    ax1.plot([0, lim], [0, lim], color=muted, lw=1.0, ls="--")
    ax1.text(0.60 * lim, 0.63 * lim, "y = x", color=muted, fontsize=9, rotation=38)
    rho, _ = spearmanr(lever_pred_m, glob_dev)
    ax1.text(0.02, 0.97, f"rho = {rho:+.2f}  (n = {len(glob_dev)})\n"
             f"lever: tag {lever_m:.1f} m from map origin\n"
             f"= {lever_m * np.pi / 180:.2f} m per deg of tag-orientation error",
             transform=ax1.transAxes, va="top", fontsize=9, color=ink)
    ax1.set_xlabel("orientation deviation × lever  (m)", color=ink)
    ax1.set_ylabel("fix deviation from consensus (m)", color=ink)
    ax1.set_title("mechanism: orientation error drives the fix", fontsize=10, color=ink)

    thr = 5.0
    keep = amb_ratio >= thr
    ax2.scatter(amb_ratio[solid], err_ransac[solid], s=18, c=blue, alpha=0.75, lw=0,
                label="fix (ref: accepted ransac)")
    ax2.scatter(amb_ratio[~solid], err_ransac[~solid], s=22, facecolors="none",
                edgecolors=blue, alpha=0.85, lw=1.0,
                label="fix, ref back-extrapolated (burst 1)")
    ax2.axvline(thr, color=muted, ls="--", lw=1.2)
    ax2.set_xscale("log")
    ax2.text(0.02, 0.97,
             f"kept (ratio ≥ {thr:g}): median {np.median(err_ransac[keep]):.2f} m, "
             f"n={keep.sum()}\ncut: median {np.median(err_ransac[~keep]):.2f} m, "
             f"n={(~keep).sum()}",
             transform=ax2.transAxes, va="top", fontsize=9, color=ink)
    ax2.set_xlabel("IPPE ambiguity ratio (runner-up / best reproj)", color=ink)
    ax2.set_ylabel("fix translation error vs ransac ref (m)", color=ink)
    ax2.set_title("the gate: ambiguity_ratio_min 2 → 5", fontsize=10, color=ink)
    ax2.legend(loc="upper right", fontsize=8, frameon=False)

    for ax in (ax1, ax2):
        ax.grid(True, color="#e8e7e2", lw=0.6)
        for s in ax.spines.values():
            s.set_color("#d8d7d2")
        ax.tick_params(colors=muted)
    fig.suptitle("village3 rehearsal: live fiducial world->map fix error — 112 fixes, "
                 f"SEED={SEED}, deterministic", fontsize=10, color=ink)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())


if __name__ == "__main__":
    sys.exit(main())
