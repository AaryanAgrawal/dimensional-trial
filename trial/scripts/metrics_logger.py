#!/usr/bin/env python3
# Standalone metrics logger for a running dimos stack.
#
# Attaches to the live TF stream (same LCM `/tf` or Zenoh `dimos/tf` topic every
# dimos process publishes on — picked automatically from global_config, matching
# whatever `--transport` the target run used) and logs, timestamped, to a JSONL
# file:
#   - odom_pose        world -> base_link, every tick (raw, un-corrected)
#   - corrected_pose    map -> base_link, resolved via the SAME tf buffer's BFS
#                        chain (world->base_link + world->map), i.e. exactly what
#                        a real downstream consumer (costmap, planner) would see
#   - correction_new / correction_hold   world -> map, as published. "new" =
#                        translation moved > CORRECTION_EPS_M since the last one
#                        (a fresh accepted detection / relocalize); "hold" = an
#                        unchanged periodic republish (RelocalizationModule
#                        style). magnitude_m = jump size vs the previous value.
#   - log_event         tailed from the run's own main.jsonl (if --dimos-log is
#                        given): VisualRelocalizationModule / RelocalizationModule
#                        log lines (gate-rejected, relocalize fitness, etc).
#   - tag_sighting      ONLY when --holdout-tag is given: one record per frame
#                        the holdout tag is seen, logged as `marker_<id> ->
#                        camera_optical` (the camera's pose IN the tag's own
#                        fixed frame, i.e. tag_T_camera). See "Holdout-tag
#                        referee" below.
#
# Does NOT run any detection itself and does NOT touch the module under test —
# it is a pure observer, attachable to any blueprint, any process, after the
# fact. Reprojection-error VALUES are not currently published/logged anywhere
# in the stack (visual_relocalization.py computes them internally and discards
# them once a candidate is chosen or rejected) — see NOTE at the bottom. The
# closest available signal is the rejection event itself (tag count only),
# captured via --dimos-log.
#
# Holdout-tag referee (--holdout-tag). A map-independent end-pose measurement:
# pass the ID of a tag deliberately EXCLUDED from every localization map under
# test. VisualRelocalizationModule detects it (detect_markers() finds every tag
# in view) but immediately drops it (`marker_map.get(marker_id)` is None), so
# no map-aware code path ever computes or logs its pose. This logger runs a
# SECOND, independent solvePnP for that one tag ID, off the same color_image +
# camera_info streams every Go2 blueprint publishes (GO2Connection) — so it
# works unmodified whichever mode (odom/lidar/marker/fused) is under test,
# with no blueprint composition change. It reuses the exact detector/solvePnP/
# reprojection-error functions every VisualRelocalizationModule /
# MarkerDetectionStreamModule call (dimos.perception.fiducial.marker_pose) —
# same shared pipeline, just run again for an ID the map never sees (a
# deliberate simplification vs. a truly independent instrument like a ChArUco
# overhead rig — see trial/benchmark-rig.md's circularity rules — hence "one
# tape-measure check per session certifies the referee" in benchmark-spec.md
# §4, not a full metrology validation).
#
# Recorded as tag_T_camera (marker_<id> -> camera_optical), not world_T_tag:
# the holdout tag is physically static, so expressing the camera's pose IN the
# tag's own frame needs no TF/odom lookup and carries none of the drift this
# whole benchmark is measuring. bench.py's holdout-referee metric (§4) takes
# the median of the first/last N sightings each run to get a measured start/
# end delta, independent of exactly where the tag was mounted.
#
# Usage:
#   cd dimos && uv run python ../trial/scripts/metrics_logger.py \
#       --out ../trial/scripts/out/run1.jsonl \
#       --dimos-log logs/<run_id>/main.jsonl \
#       --duration 400
#
# Ctrl+C stops it early (also honors --duration). Safe to run against a replay
# or a live robot — it only subscribes, never publishes.

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any

from dimos.core.global_config import global_config
from dimos.core.log_viewer import follow_log
from dimos.core.transport_factory import make_transport, tf_backend
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.fiducial.marker_pose import (
    camera_info_to_cv_matrices,
    camera_optical_frame_id,
    create_aruco_detector,
    estimate_marker_pose,
    marker_reprojection_error,
    rvec_tvec_to_transform,
)

WORLD_FRAME = "world"
MAP_FRAME = "map"
BASE_FRAME = "base_link"

# Below this translation jump, a fresh world->map publish is treated as a
# periodic "hold" republish (RelocalizationModule republishes every 2s even
# when nothing changed) rather than a new correction event.
CORRECTION_EPS_M = 0.01

# How far back the map->base_link lookup is allowed to search for a matching
# world->map sample. VisualRelocalizationModule does not republish on a timer
# (only on a fresh accepted detection), so a correction can be many seconds
# old and still be "the current one" -- this just needs to comfortably exceed
# whatever cadence the deployment sees between tag sightings.
CORRECTED_POSE_LOOKUP_TOLERANCE_S = 30.0


def _t2v(t: Transform) -> tuple[float, float, float]:
    return (t.translation.x, t.translation.y, t.translation.z)


def _dist(a: Transform, b: Transform) -> float:
    return math.dist(_t2v(a), _t2v(b))


def transform_record(t: Transform, kind: str, **extra: object) -> dict:
    return {
        "type": kind,
        "ts": t.ts,
        "logged_at": time.time(),
        "frame_id": t.frame_id,
        "child_frame_id": t.child_frame_id,
        "translation": list(_t2v(t)),
        "rotation": [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w],
        **extra,
    }


class MetricsLogger:
    def __init__(
        self,
        out_path: str | Path,
        dimos_log_path: str | Path | None = None,
        *,
        holdout_tag: int | None = None,
        holdout_marker_length_m: float = 0.10,
        holdout_aruco_dictionary: str = "DICT_APRILTAG_36h11",
    ) -> None:
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._out_f = self.out_path.open("w")
        self._write_lock = threading.Lock()
        self._dimos_log_path = Path(dimos_log_path) if dimos_log_path else None
        self._stop_flag = False

        self._last_correction: Transform | None = None
        self.n_odom_ticks = 0
        self.n_corrected_ticks = 0
        self.n_corrections_new = 0
        self.n_corrections_hold = 0
        self.n_log_events = 0

        tf_class = tf_backend()
        outer = self

        class _LoggingTF(tf_class):  # type: ignore[misc, valid-type]
            def receive_transform(self, *args: Transform) -> None:
                for t in args:
                    outer._on_transform(t)
                super().receive_transform(*args)

        self.tf = _LoggingTF()

        # -- holdout-tag referee (see header comment) ------------------------
        self._holdout_tag = holdout_tag
        self._holdout_marker_length_m = holdout_marker_length_m
        self.n_holdout_sightings = 0
        self._holdout_detector: Any | None = None
        self._holdout_camera_info: CameraInfo | None = None
        self._holdout_cam_mtx = None
        self._holdout_dist = None
        self._holdout_transports: list[Any] = []

        if self._holdout_tag is not None:
            self._holdout_detector = create_aruco_detector(holdout_aruco_dictionary)
            camera_info_t = make_transport("camera_info", CameraInfo)
            camera_info_t.subscribe(self._on_holdout_camera_info)
            color_image_t = make_transport("color_image", Image)
            color_image_t.subscribe(self._on_holdout_color_image)
            self._holdout_transports = [camera_info_t, color_image_t]

    # -- TF firehose -----------------------------------------------------
    def _write(self, record: dict) -> None:
        with self._write_lock:
            self._out_f.write(json.dumps(record) + "\n")
            self._out_f.flush()

    def _on_transform(self, t: Transform) -> None:
        if t.frame_id == WORLD_FRAME and t.child_frame_id == BASE_FRAME:
            self.n_odom_ticks += 1
            self._write(transform_record(t, "odom_pose"))
            # Only probe for a correction once one has ever existed -- avoids
            # spamming "no transform found" warnings for the (normal) case of
            # a run with zero tag sightings so far.
            if self._last_correction is not None:
                corrected = self.tf.get(
                    MAP_FRAME,
                    BASE_FRAME,
                    time_point=t.ts,
                    time_tolerance=CORRECTED_POSE_LOOKUP_TOLERANCE_S,
                )
                if corrected is not None:
                    self.n_corrected_ticks += 1
                    self._write(transform_record(corrected, "corrected_pose"))
        elif t.frame_id == WORLD_FRAME and t.child_frame_id == MAP_FRAME:
            magnitude = _dist(t, self._last_correction) if self._last_correction else None
            is_new = magnitude is None or magnitude > CORRECTION_EPS_M
            if is_new:
                self.n_corrections_new += 1
            else:
                self.n_corrections_hold += 1
            self._write(
                transform_record(
                    t,
                    "correction_new" if is_new else "correction_hold",
                    magnitude_m=magnitude,
                )
            )
            self._last_correction = t

    # -- holdout-tag referee (map-independent end-pose measurement) -------
    def _on_holdout_camera_info(self, info: CameraInfo) -> None:
        self._holdout_camera_info = info
        self._holdout_cam_mtx, self._holdout_dist = camera_info_to_cv_matrices(info)

    def _on_holdout_color_image(self, image: Image) -> None:
        """Independent solvePnP for ONE tag id that every localization map
        excludes. Logs tag_T_camera (`marker_<id> -> camera_optical`) --
        the camera's pose IN the tag's own fixed frame -- which needs no
        TF/odom lookup at all: the tag doesn't move, so its own frame is a
        reference untouched by whatever drift the run under test accumulates.
        See the module header comment for why this is a second PnP, not a
        TF read."""
        if self._holdout_detector is None or self._holdout_camera_info is None:
            return
        info = self._holdout_camera_info
        if info.width and info.height and (image.width != info.width or image.height != info.height):
            return

        gray = image.to_grayscale().as_numpy()
        corners, ids, _ = self._holdout_detector.detectMarkers(gray)
        if ids is None or len(ids) == 0:
            return

        optical_frame = camera_optical_frame_id(image, info)
        for corner_set, mid_arr in zip(corners, ids, strict=True):
            mid = int(mid_arr[0])
            if mid != self._holdout_tag:
                continue
            pose = estimate_marker_pose(
                corner_set,
                self._holdout_marker_length_m,
                self._holdout_cam_mtx,
                self._holdout_dist,
                distortion_model=info.distortion_model,
            )
            if pose is None:
                continue
            rvec, tvec = pose
            optical_t_marker = rvec_tvec_to_transform(
                rvec, tvec, frame_id=optical_frame, child_frame_id=f"marker_{mid}", ts=image.ts
            )
            marker_t_camera = optical_t_marker.inverse()  # tag's fixed frame -> camera
            corners_2d = corner_set.reshape(4, 2).astype("float32")
            reproj = marker_reprojection_error(
                corners_2d,
                self._holdout_marker_length_m,
                self._holdout_cam_mtx,
                self._holdout_dist,
                rvec,
                tvec,
                distortion_model=info.distortion_model,
            )
            self.n_holdout_sightings += 1
            self._write(
                transform_record(
                    marker_t_camera,
                    "tag_sighting",
                    marker_id=mid,
                    reprojection_error_px=round(float(reproj), 4),
                )
            )

    # -- dimos structured-log tail ----------------------------------------
    _WATCH_SUBSTRINGS = ("gate rejected", "relocalize", "VisualRelocalizationModule")

    def _tail_dimos_log(self) -> None:
        if self._dimos_log_path is None:
            return
        if not self._dimos_log_path.exists():
            print(f"metrics_logger: --dimos-log {self._dimos_log_path} not found yet, waiting...")
            deadline = time.time() + 60.0
            while not self._dimos_log_path.exists() and time.time() < deadline and not self._stop_flag:
                time.sleep(0.5)
            if not self._dimos_log_path.exists():
                print(f"metrics_logger: gave up waiting for {self._dimos_log_path}")
                return
        for line in follow_log(self._dimos_log_path, stop=lambda: self._stop_flag):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = str(rec.get("event", ""))
            logger_name = str(rec.get("logger", ""))
            if any(s in event or s in logger_name for s in self._WATCH_SUBSTRINGS):
                self.n_log_events += 1
                self._write(
                    {
                        "type": "log_event",
                        "ts": rec.get("timestamp"),
                        "logged_at": time.time(),
                        "level": rec.get("level"),
                        "logger": logger_name,
                        "event": event,
                    }
                )

    # -- lifecycle ---------------------------------------------------------
    def run(self, duration: float | None = None) -> None:
        tail_thread = threading.Thread(target=self._tail_dimos_log, daemon=True)
        tail_thread.start()
        start = time.monotonic()
        try:
            while duration is None or (time.monotonic() - start) < duration:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("metrics_logger: Ctrl+C, stopping...")
        finally:
            self._stop_flag = True
            tail_thread.join(timeout=2.0)
            self.close()

    def close(self) -> None:
        self._out_f.close()
        try:
            self.tf.stop()
        except Exception:
            pass
        for t in self._holdout_transports:
            try:
                t.stop()
            except Exception:
                pass

    def summary(self) -> dict:
        s = {
            "odom_ticks": self.n_odom_ticks,
            "corrected_ticks": self.n_corrected_ticks,
            "corrections_new": self.n_corrections_new,
            "corrections_hold": self.n_corrections_hold,
            "log_events": self.n_log_events,
        }
        if self._holdout_tag is not None:
            s["holdout_tag"] = self._holdout_tag
            s["holdout_sightings"] = self.n_holdout_sightings
        return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument(
        "--duration", type=float, default=None, help="seconds to run (default: until Ctrl+C)"
    )
    ap.add_argument(
        "--dimos-log",
        default=None,
        help="path to the target run's main.jsonl (from `dimos run ... --daemon`'s "
        "printed Log: path, or logs/<run_id>/main.jsonl) -- enables detection-reject "
        "event capture. Optional; omit to log TF only.",
    )
    ap.add_argument(
        "--holdout-tag",
        type=int,
        default=None,
        help="marker ID excluded from every localization map under test -- an "
        "automated, map-independent PnP referee for start/end pose (see "
        "benchmark-spec.md §4). Omit to log TF/log_event only, as before.",
    )
    ap.add_argument(
        "--holdout-marker-length-m",
        type=float,
        default=0.10,
        help="physical edge length (m) of the printed holdout tag -- independent "
        "of whatever marker_length_m the mode under test uses (default 0.10, "
        "matches the standard 100mm survey tags)",
    )
    ap.add_argument(
        "--holdout-aruco-dictionary",
        default="DICT_APRILTAG_36h11",
        help="ArUco/AprilTag dictionary the holdout tag was printed from "
        "(default DICT_APRILTAG_36h11, matches every VisualRelocalizationModule default)",
    )
    args = ap.parse_args()

    ml = MetricsLogger(
        args.out,
        args.dimos_log,
        holdout_tag=args.holdout_tag,
        holdout_marker_length_m=args.holdout_marker_length_m,
        holdout_aruco_dictionary=args.holdout_aruco_dictionary,
    )
    print(
        f"metrics_logger: transport={global_config.transport} -> writing {args.out}"
        + (f" (+ tailing {args.dimos_log})" if args.dimos_log else "")
    )
    ml.run(duration=args.duration)
    print(f"metrics_logger: stopped. summary={ml.summary()}")


if __name__ == "__main__":
    main()

# NOTE on reprojection error: `localize_from_detections()` in
# dimos/perception/fiducial/visual_relocalization.py computes a per-tag
# reprojection error (px) for every candidate but only ever returns the
# winning Transform -- the error value itself never crosses a topic or a log
# line on the accept path, and on the reject path only the tag COUNT is
# logged ("gate rejected (N tags seen)"), not the error that triggered the
# gate. This logger surfaces what's actually observable today: accepted
# corrections (as world->map TF, with jump magnitude) and reject events (as
# tag count, via --dimos-log). Getting real per-tag reprojection-error
# numbers would need a one-line addition to visual_relocalization_module.py's
# logger.warning call (and an equivalent debug line on the accept path) --
# worth proposing upstream, intentionally NOT done here since it would touch
# the already-verified PR #2808 diff.
#
# NOTE on the holdout-tag referee's reprojection error: unlike the gap above,
# --holdout-tag's tag_sighting records DO carry a real reprojection_error_px
# every sighting -- this logger computes it itself (marker_reprojection_error,
# same function visual_relocalization.py uses internally) rather than reading a
# value dimos publishes, since the holdout tag's PnP is this logger's own
# second, independent solvePnP call in the first place.
