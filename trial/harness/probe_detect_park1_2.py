#!/usr/bin/env python3
"""One-off DETECT probe for mid360_gir_park1_2_20260602 (flashdrive recording).

Enumerate streams, check lidar/odom pose validity, body gravity-tilt, and run
the harness marker detector (DICT_APRILTAG_36h11, reproj<=3px) on ~40 sampled
color frames. Read-only. Prints raw facts; verifies nothing on faith.
"""
from __future__ import annotations

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import cv2
from scipy.spatial.transform import Rotation

HARNESS = Path("/home/dimos/dimensional-trial/trial/harness")
sys.path.insert(0, str(HARNESS))

DB = "/home/dimos/dimensional-trial/dimos/data/mid360_gir_park1_2_20260602.db"

from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.perception.fiducial.marker_pose import (
    camera_info_to_cv_matrices, create_aruco_detector,
    estimate_marker_pose_candidates, marker_reprojection_error)
from dimos.robot.unitree.go2.connection import _camera_info_static

MARKER_LENGTH_M = 0.1
REPROJ_GATE_PX = 3.0


def gravity_tilt_deg(q) -> float:
    """Angle between body +z (rotated into world) and world +z."""
    R = Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix()
    body_z_world = R @ np.array([0.0, 0.0, 1.0])
    cosang = np.clip(body_z_world[2], -1.0, 1.0)
    return float(np.degrees(np.arccos(cosang)))


def main() -> int:
    store = SqliteStore(path=DB, must_exist=True)
    with store:
        names = store.list_streams()
        print("=== STREAMS ===")
        inv = {}
        for n in names:
            try:
                st = store.stream(n)
                cnt = st.count()
                first = st.first()
                last = st.last()
                t0 = float(first.ts) if first is not None else float("nan")
                t1 = float(last.ts) if last is not None else float("nan")
                dtype = type(first.data).__name__ if first is not None else "?"
                dur = t1 - t0
                hz = cnt / dur if dur > 0 else float("nan")
                inv[n] = (cnt, t0, t1, dur, dtype, hz)
                print(f"  {n:22s} type={dtype:16s} n={cnt:6d} dur={dur:7.1f}s hz={hz:6.2f} "
                      f"[{t0:.2f}..{t1:.2f}]")
            except Exception as e:
                print(f"  {n:22s} ERROR {type(e).__name__}: {e}")

        # --- pick streams ---
        color_stream = "color_image" if "color_image" in names else None
        lidar_candidates = [n for n in names if "lidar" in n.lower() or n == "lidar"]
        odom_candidates = [n for n in names if any(k in n.lower() for k in
                                                   ("odom", "fastlio", "pointlio", "lio"))]
        print(f"\ncolor_stream={color_stream} lidar_candidates={lidar_candidates} "
              f"odom_candidates={odom_candidates}")

        # --- lidar pose validity ---
        print("\n=== LIDAR STORAGE-POSE VALIDITY ===")
        for ls in lidar_candidates:
            try:
                obss = []
                for i, obs in enumerate(store.stream(ls, PointCloud2)):
                    obss.append(obs)
                    if i >= 20:
                        break
                placeholder = 0
                sample_poses = []
                for obs in obss:
                    p = obs.pose_tuple
                    if p is None or (p[0] == 0 and p[1] == 0 and p[2] == 0):
                        placeholder += 1
                    else:
                        sample_poses.append(p[:3])
                print(f"  {ls}: sampled {len(obss)} obs, placeholder/none poses = "
                      f"{placeholder}/{len(obss)}; sample real xyz = "
                      f"{[list(np.round(s,2)) for s in sample_poses[:3]]}")
            except Exception as e:
                print(f"  {ls}: ERROR {type(e).__name__}: {e}")

        # --- odom payload poses: trajectory extent + tilt ---
        print("\n=== ODOM PAYLOAD POSES (trajectory + gravity tilt) ===")
        for os_name in odom_candidates:
            for ptype in (PoseStamped, Odometry):
                try:
                    st = store.stream(os_name, ptype)
                    cnt = st.count()
                    # sample ~30 evenly
                    all_obs = list(st)
                    if not all_obs:
                        print(f"  {os_name} as {ptype.__name__}: 0 obs")
                        continue
                    idxs = np.unique(np.linspace(0, len(all_obs) - 1, 30).astype(int))
                    xyz = []
                    tilts = []
                    for k in idxs:
                        p = all_obs[k].data
                        pos = p.position
                        q = p.orientation
                        xyz.append([pos.x, pos.y, pos.z])
                        tilts.append(gravity_tilt_deg(q))
                    xyz = np.array(xyz)
                    extent = xyz.max(0) - xyz.min(0)
                    path_len = float(np.sum(np.linalg.norm(np.diff(xyz, axis=0), axis=1)))
                    print(f"  {os_name} as {ptype.__name__}: n={cnt} "
                          f"extent(xyz)m={np.round(extent,2).tolist()} "
                          f"sampled_pathlen={path_len:.1f}m")
                    print(f"    gravity-tilt deg (body z vs world z), sampled n={len(tilts)}: "
                          f"median={np.median(tilts):.1f} min={min(tilts):.1f} "
                          f"max={max(tilts):.1f} p25/75={np.percentile(tilts,25):.1f}/"
                          f"{np.percentile(tilts,75):.1f}")
                    break  # this ptype worked
                except Exception as e:
                    print(f"  {os_name} as {ptype.__name__}: ERROR {type(e).__name__}: {str(e)[:80]}")

        # --- MARKERS: sample ~40 color frames spread over recording ---
        print("\n=== MARKER CENSUS (DICT_APRILTAG_36h11, reproj<=3px) ===")
        if color_stream is None:
            print("  no color_image stream -> cannot grade fiducial")
            return 0
        info = _camera_info_static()
        k, d = camera_info_to_cv_matrices(info)
        model = info.distortion_model
        det = create_aruco_detector("DICT_APRILTAG_36h11")
        cimgs = list(store.stream(color_stream, Image))
        n_c = len(cimgs)
        n_sample = 40
        idxs = np.unique(np.linspace(0, n_c - 1, n_sample).astype(int))
        print(f"  color frames total={n_c}, sampling {len(idxs)} "
              f"(intrinsics: go2 front {info.width}x{info.height}, model={model})")
        seen = defaultdict(list)  # id -> list of (ts, reproj, xyz_opt)
        raw_seen = defaultdict(int)  # id -> raw detections pre-gate
        for i in idxs:
            obs = cimgs[i]
            img = obs.data
            if img.width != info.width or img.height != info.height:
                continue
            corners, ids, _ = det.detectMarkers(img.to_grayscale().as_numpy())
            if ids is None:
                continue
            for c, mid in zip(corners, ids):
                mid = int(mid[0])
                raw_seen[mid] += 1
                cp = c.reshape(4, 2)
                cands = estimate_marker_pose_candidates(cp, MARKER_LENGTH_M, k, d,
                                                        distortion_model=model)
                if not cands:
                    continue
                scored = sorted(
                    (marker_reprojection_error(cp, MARKER_LENGTH_M, k, d, rv, tv,
                                               distortion_model=model), rv, tv)
                    for rv, tv in cands)
                err, rvec, tvec = scored[0]
                if err > REPROJ_GATE_PX:
                    continue
                seen[mid].append((float(obs.ts), float(err), tvec.reshape(3).tolist()))

        t_span = cimgs[-1].ts - cimgs[0].ts
        print(f"  recording color span = {t_span:.1f}s")
        print(f"  raw detections (pre-gate) per id: {dict(sorted(raw_seen.items()))}")
        if not seen:
            print("  has_markers=False (0 gated sightings in sampled frames)")
        else:
            print(f"  has_markers=True; gated ids = {sorted(seen)}")
            for mid in sorted(seen):
                rows = sorted(seen[mid], key=lambda r: r[0])
                ts = [r[0] for r in rows]
                t_lo = ts[0] - cimgs[0].ts
                t_hi = ts[-1] - cimgs[0].ts
                frac_span = (ts[-1] - ts[0]) / t_span if t_span > 0 else 0
                # spatial cluster hint: spread of optical tvec positions
                xyz = np.array([r[2] for r in rows])
                pos_spread = float(np.linalg.norm(xyz.max(0) - xyz.min(0))) if len(xyz) > 1 else 0.0
                print(f"    id {mid:3d}: gated_sightings={len(rows):3d} "
                      f"t=[{t_lo:.0f}s..{t_hi:.0f}s] span_frac={frac_span:.2f} "
                      f"reproj_min/med={min(r[1] for r in rows):.2f}/"
                      f"{np.median([r[1] for r in rows]):.2f}px "
                      f"opt_pos_range={pos_spread:.2f}m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
