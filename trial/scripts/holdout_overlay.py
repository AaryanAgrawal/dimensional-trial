#!/usr/bin/env python3
# Benchmark run controller -- one live camera window that wraps bench.py
# around the start/end tag (the physical reference tag bench.py and
# metrics_logger.py address via their --holdout-tag flags; "holdout" survives
# in flag/column names for compatibility, every user-facing string here says
# "start/end tag").
#
# One OpenCV window (cv2.imshow + cv2.setMouseCallback), two states:
#
#   WAITING  Live frames + a live detection box on the start/end tag, and a
#            drawn [START] button. The start/end tag is AUTO-ADOPTED: among
#            tags stably visible over the last ~10 frames, the most prominent
#            (largest image area) whose ID is NOT in the localization map
#            (--marker-map, loaded with the module's own load_marker_map) is
#            adopted -- the UI shows "start/end tag: ID <n> (adopted)".
#            --holdout-tag remains as an optional override that pins the ID
#            and skips adoption. If every stable visible tag IS in the map, a
#            red banner says so and START stays disarmed. Click START -> the
#            adopted tag's current 4 corner pixels freeze as a thin reference
#            outline, the current tag_T_camera pose is recorded as the run's
#            start reference, and `bench.py start` launches as a child process
#            with --holdout-tag <adopted id> + the other --holdout-*
#            passthrough flags. Exactly ONE metrics logger runs: the bench
#            child's MetricsLogger. This window detects for DISPLAY only and
#            never writes a log record.
#
#   RUNNING  The frozen outline persists. The live box + on-frame delta text
#            (cm + deg, current PnP vs the frozen reference -- the same
#            pose-delta math as bench.py's referee, imported from it, so what
#            the screen shows IS the metric) recolor by distance-to-start:
#            red > --yellow-m, yellow between, green < --green-m.
#            [STOP] is drawn from the moment RUNNING starts -- gray normally
#            (imperfect runs still get recorded; the protocol tables every
#            run) -- and turns GREEN with a pulsing border once the delta has
#            been < --green-m sustained ~1s (--green-hold-s). Click STOP ->
#            SIGINT to the bench child (the exact mechanism `bench.py stop`
#            uses), which scores the run and appends the row; the row's
#            numbers are then printed to this console.
#
# The PnP is the same helper path metrics_logger.py's referee uses
# (dimos.perception.fiducial.marker_pose: create_aruco_detector /
# estimate_marker_pose / marker_reprojection_error / rvec_tvec_to_transform,
# then .inverse() for tag_T_camera), and the live frames come off the same
# color_image + camera_info transports (make_transport), so the window sees
# exactly what the referee logs.
#
# --record <dir> saves every displayed (annotated) frame as numbered PNGs --
# grab the return sequence for GIF-making.
#
# --to-rerun (default ON) also mirrors the annotated frames into the running
# dimos rerun viewer as entity "benchmark/start_end_tag_overlay" (~5fps).
# RerunBridgeModule serves a gRPC proxy at rerun+http://<host>:9877/proxy
# (dimos/visualization/rerun/bridge.py + constants.RERUN_GRPC_PORT); any
# process can join the same viewer via rr.init + rr.connect_grpc -- the exact
# port-already-in-use path dimos's own rerun_init() takes. If no viewer/bridge
# is reachable, falls back cleanly to the cv2 window with one printed line.
# The cv2 window stays the click surface -- rerun has no buttons.
#
# --selftest runs the whole state machine on rendered fixture frames (a
# DICT_4X4_50 tag warped at known camera poses -- self-contained, no robot, no
# camera, no transport, no GUI window): simulated START click -> reference
# frozen -> synthetic approach frames transition red->yellow->green ->
# simulated STOP click -> a metrics row lands in a TEMP results dir (never the
# real trial/results/), scored by bench.py's own compute_run_metrics.
#
# Usage (live, from the dimos venv -- same convention as bench.py start):
#   cd dimos
#   uv run python ../trial/scripts/holdout_overlay.py \
#       --mode marker --route drift-recovery --holdout-tag 42 --notes "loop A"
#
# Keyboard: SPACE = START/STOP (same code path as the buttons), q/ESC = quit
# (quitting mid-run stops + scores the run first -- every run is recorded).
# If this window ever dies mid-run, the bench child keeps logging; end it with
# `bench.py stop` as usual.

from __future__ import annotations

import argparse
import csv
import math
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import bench  # noqa: E402  (stdlib-only at import time, same as `bench.py stop`)

# Same PnP helper path metrics_logger.py's holdout referee uses -- needs the
# dimos venv (cd dimos && uv run ...), both live and selftest.
from dimos.perception.fiducial.visual_relocalization import load_marker_map  # noqa: E402
from dimos.perception.fiducial.marker_pose import (  # noqa: E402
    camera_info_to_cv_matrices,
    camera_optical_frame_id,
    create_aruco_detector,
    estimate_marker_pose_candidates,
    marker_reprojection_error,
    rvec_tvec_to_transform,
)

DEFAULT_MARKER_MAP = "/Users/aaryan/Files/Side Projects/Dimensional/office_markers.yaml"

WINDOW_TITLE = "start/end tag -- benchmark run controller"

STATE_WAITING = "WAITING"
STATE_RUNNING = "RUNNING"

# BGR
ZONE_COLORS = {"green": (70, 200, 70), "yellow": (40, 200, 230), "red": (60, 60, 230)}
COLOR_NEUTRAL = (230, 230, 230)
COLOR_FROZEN = (255, 220, 160)  # thin reference outline
COLOR_BTN_GRAY = (90, 90, 90)
COLOR_BTN_GREEN = (60, 170, 60)
FONT = cv2.FONT_HERSHEY_SIMPLEX

BTN_W, BTN_H, BTN_MARGIN = 170, 48, 12

DET_FRESH_S = 0.75  # a detection older than this no longer drives the overlay

MAPPED_ONLY_MSG = (
    "facing a mapped tag -- start in front of any tag that is not in the localization map"
)


def _zone(delta_m: float, green_m: float, yellow_m: float) -> str:
    if delta_m < green_m:
        return "green"
    if delta_m < yellow_m:
        return "yellow"
    return "red"


# -- detection (display-side twin of metrics_logger's referee PnP) -----------


class TagTracker:
    """Detect every visible tag (for adoption) and solve the pose of ONE.

    The pose path is identical to metrics_logger.MetricsLogger's referee:
    create_aruco_detector -> detectMarkers -> solvePnP (marker_pose helpers)
    -> rvec_tvec_to_transform(...).inverse() -> marker_reprojection_error.
    Display-only: nothing is ever logged from here.
    """

    def __init__(
        self, marker_length_m: float, dictionary: str, reproj_gate_px: float = 3.0
    ) -> None:
        self.marker_length_m = marker_length_m
        self.reproj_gate_px = reproj_gate_px
        self.detector = create_aruco_detector(dictionary)
        self.cam_mtx: np.ndarray | None = None
        self.dist: np.ndarray | None = None
        self.distortion_model: str | None = None
        self.frame_id = "camera_optical"

    def set_intrinsics(
        self, cam_mtx: np.ndarray, dist: np.ndarray, distortion_model: str | None
    ) -> None:
        self.cam_mtx, self.dist, self.distortion_model = cam_mtx, dist, distortion_model

    @property
    def ready(self) -> bool:
        return self.cam_mtx is not None

    def detect_all(self, gray: np.ndarray) -> dict[int, np.ndarray]:
        """Every detected tag this frame: id -> corners (4,2) float32."""
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {
            int(mid[0]): cs.reshape(4, 2).astype(np.float32)
            for cs, mid in zip(corners, ids, strict=True)
        }

    def solve(self, corners_2d: np.ndarray, tag_id: int, ts: float) -> tuple[Any, float] | None:
        """(tag_T_camera, reproj_px) for one detected tag, or None if the fit
        fails the gate."""
        if not self.ready:
            return None
        # All IPPE candidates, keep the min-reprojection one -- the same
        # planar mirror-ambiguity disambiguation VisualRelocalizationModule
        # itself applies. Single-solution estimate_marker_pose can return
        # the flipped pose (observed in the selftest: 68px reprojection
        # picked over 0.03px near the ambiguity), which would make the
        # on-screen delta jump wildly frame to frame.
        candidates = estimate_marker_pose_candidates(
            corners_2d,
            self.marker_length_m,
            self.cam_mtx,
            self.dist,
            distortion_model=self.distortion_model,
        )
        best: tuple[np.ndarray, np.ndarray] | None = None
        best_err = math.inf
        for rvec, tvec in candidates:
            err = marker_reprojection_error(
                corners_2d,
                self.marker_length_m,
                self.cam_mtx,
                self.dist,
                rvec,
                tvec,
                distortion_model=self.distortion_model,
            )
            if err < best_err:
                best, best_err = (rvec, tvec), err
        # Same 3px reprojection gate VisualRelocalizationModule applies: a
        # frame whose best fit still reprojects badly has bad corners
        # (observed in the selftest: occasional 120px frames from a
        # mis-decoded corner order) -- treat it as "tag not in view"
        # rather than flashing a garbage delta.
        if best is None or best_err > self.reproj_gate_px:
            return None
        rvec, tvec = best
        optical_t_marker = rvec_tvec_to_transform(
            rvec,
            tvec,
            frame_id=self.frame_id,
            child_frame_id=f"marker_{tag_id}",
            ts=ts,
        )
        return (optical_t_marker.inverse(), float(best_err))


# -- rerun mirror --------------------------------------------------------------


class RerunSink:
    """Mirror annotated frames into the running dimos rerun viewer.

    RerunBridgeModule (dimos/visualization/rerun/bridge.py) serves a gRPC
    proxy at ``rerun+http://<rerun_host or listen_host>:9877/proxy``
    (constants.RERUN_GRPC_PORT); joining it from another process is
    ``rr.init(app_id) + rr.connect_grpc(url)`` -- the same thing dimos's own
    ``rerun_init()`` does when it finds the port already in use. Timeline name
    matches the bridge's (``dimos_time``) so frames line up with the run's own
    streams. Never raises past connect(): if no bridge/viewer is up, ``ok``
    stays False and the overlay is cv2-window-only.
    """

    ENTITY = "benchmark/start_end_tag_overlay"

    def __init__(self, fps: float = 5.0) -> None:
        self.ok = False
        self._min_dt = 1.0 / fps
        self._last_log = -math.inf
        self._rr = None

    def connect(self) -> bool:
        import socket

        try:
            from dimos.core.global_config import global_config
            from dimos.visualization.rerun.constants import RERUN_GRPC_PORT

            host = global_config.rerun_host or global_config.listen_host or "127.0.0.1"
            if host == "0.0.0.0":
                host = "127.0.0.1"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex((host, RERUN_GRPC_PORT)) != 0:
                    print(
                        f"overlay: no rerun viewer/bridge at {host}:{RERUN_GRPC_PORT} -- "
                        "cv2 window only (start the dimos stack first for the rerun mirror)"
                    )
                    return False
            import rerun as rr

            rr.init("dimos")  # same app id as dimos's rerun_init()
            rr.connect_grpc(url=f"rerun+http://{host}:{RERUN_GRPC_PORT}/proxy")
            self._rr = rr
            self.ok = True
            print(
                f"overlay: mirroring annotated frames to rerun ({host}:{RERUN_GRPC_PORT}, "
                f"entity {self.ENTITY!r}, ~{1.0 / self._min_dt:.0f}fps)"
            )
            return True
        except Exception as e:
            print(f"overlay: rerun mirror unavailable ({e}) -- cv2 window only")
            return False

    def log(self, annotated_bgr: np.ndarray, ts: float) -> None:
        if not self.ok or (ts - self._last_log) < self._min_dt:
            return
        self._last_log = ts
        try:
            rr = self._rr
            rr.set_time("dimos_time", timestamp=float(ts))  # bridge's own timeline
            rgb = cv2.cvtColor(annotated_bgr, cv2.COLOR_BGR2RGB)
            img = rr.Image(rgb)
            try:  # jpeg-compress if this SDK supports it (keeps the viewer light)
                img = img.compress(jpeg_quality=75)
            except Exception:
                pass
            rr.log(self.ENTITY, img)
        except Exception as e:
            self.ok = False
            print(f"overlay: rerun mirror failed mid-run ({e}) -- continuing cv2-only")


# -- run backends -------------------------------------------------------------


class LiveBenchRunner:
    """START -> `bench.py start` child process (its MetricsLogger is the one
    and only logger). STOP -> SIGINT to the child (the exact signal
    `bench.py stop` sends), wait for it to score + append, read back the row."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.child: subprocess.Popen | None = None

    def start(self, tag_id: int) -> None:
        existing = bench._read_active_state()
        if existing and bench._pid_alive(existing.get("pid", -1)):
            raise RuntimeError(
                f"a bench run is already active (pid={existing.get('pid')}, "
                f"route={existing.get('route')}) -- `bench.py stop` it first"
            )
        a = self.args
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "bench.py"),
            "start",
            "--mode", a.mode,
            "--route", a.route,
            "--notes", a.notes,
            "--holdout-tag", str(tag_id),  # the adopted (or overridden) start/end tag
            "--holdout-window", str(a.holdout_window),
            "--holdout-marker-length-m", str(a.holdout_marker_length_m),
            "--holdout-aruco-dictionary", a.holdout_aruco_dictionary,
        ]
        if a.dimos_log:
            cmd += ["--dimos-log", a.dimos_log]
        # Own process group: terminal Ctrl+C hits the overlay only, so stop()
        # stays the child's single SIGINT source (a second one mid-scoring
        # would corrupt the finalize path).
        self.child = subprocess.Popen(cmd, start_new_session=True)
        print(f"overlay: launched bench run (pid={self.child.pid})")

    def poll_dead(self) -> bool:
        return self.child is not None and self.child.poll() is not None

    def stop(self) -> dict[str, Any] | None:
        if self.child is None:
            return None
        if self.child.poll() is None:
            self.child.send_signal(signal.SIGINT)
        try:
            rc = self.child.wait(timeout=90)
        except subprocess.TimeoutExpired:
            print("overlay: timed out waiting for the bench run to finish scoring")
            return None
        finally:
            self.child = None
        if rc != 0:
            print(f"overlay: bench run exited rc={rc} -- see its output above")
            return None
        if not bench.BENCHMARKS_CSV.exists():
            return None
        with bench.BENCHMARKS_CSV.open() as f:
            rows = list(csv.DictReader(f))
        return rows[-1] if rows else None


class SelftestRunner:
    """Selftest twin: collects the overlay's own PnP samples as tag_sighting
    records (screen == metric, literally the same numbers) plus the harness's
    known-true odom stream, then scores via bench.py's own
    compute_run_metrics/append_csv_row/regenerate_report -- pointed at a TEMP
    results dir, never the real trial/results/."""

    def __init__(self, args: argparse.Namespace, tmp_dir: Path) -> None:
        self.args = args
        self.tmp_dir = tmp_dir
        self.active = False
        self.tag_id: int | None = None
        self.records: list[dict] = []
        self._start_ts: float | None = None
        self._end_ts: float | None = None

    def start(self, tag_id: int) -> None:
        self.active, self.records = True, []
        self.tag_id = tag_id
        self._start_ts = self._end_ts = None

    def record_sighting(self, ts: float, tag_t_camera: Any, reproj: float) -> None:
        if not self.active:
            return
        t, r = tag_t_camera.translation, tag_t_camera.rotation
        self._start_ts = self._start_ts if self._start_ts is not None else ts
        self._end_ts = ts
        self.records.append(
            {
                "type": "tag_sighting",
                "ts": ts,
                "logged_at": ts,
                "frame_id": f"marker_{self.tag_id}",
                "child_frame_id": "camera_optical",
                "marker_id": self.tag_id,
                "translation": [t.x, t.y, t.z],
                "rotation": [r.x, r.y, r.z, r.w],
                "reprojection_error_px": round(reproj, 4),
            }
        )

    def record_odom(self, ts: float, xyz: tuple[float, float, float]) -> None:
        if not self.active:
            return
        self.records.append(
            {
                "type": "odom_pose",
                "ts": ts,
                "logged_at": ts,
                "frame_id": "world",
                "child_frame_id": "base_link",
                "translation": list(xyz),
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        )

    def poll_dead(self) -> bool:
        return False

    def stop(self) -> dict[str, Any] | None:
        self.active = False
        import json

        run_id = f"selftest-overlay__odom__{time.strftime('%Y%m%d-%H%M%S')}"
        log_path = self.tmp_dir / f"{run_id}.jsonl"
        with log_path.open("w") as f:
            for rec in sorted(self.records, key=lambda r: r["ts"]):
                f.write(json.dumps(rec) + "\n")

        # Re-point bench's results paths at the temp dir for this process only
        # (append_csv_row/regenerate_report read these module globals) -- the
        # real trial/results/ is never touched.
        results = self.tmp_dir / "results"
        bench.TRIAL_DIR = self.tmp_dir
        bench.RESULTS_DIR = results
        bench.BENCHMARKS_CSV = results / "benchmarks.csv"
        bench.RESULTS_MD = results / "RESULTS.md"

        duration = (self._end_ts or 0.0) - (self._start_ts or 0.0)
        row = bench.compute_run_metrics(
            log_path,
            "odom",
            "selftest-overlay",
            "holdout_overlay --selftest",
            run_id,
            duration,
            holdout_tag=self.tag_id,
            holdout_window=self.args.holdout_window,
        )
        bench.append_csv_row(row)
        bench.regenerate_report()
        return row


# -- the window / state machine ----------------------------------------------


ADOPT_WINDOW_FRAMES = 10  # a tag is "stable" when seen in >=8 of the last 10 frames
ADOPT_MIN_SEEN = 8


class OverlayApp:
    def __init__(
        self,
        args: argparse.Namespace,
        tracker: TagTracker,
        runner: LiveBenchRunner | SelftestRunner,
        map_ids: set[int],
        record_dir: Path | None = None,
        rerun_sink: RerunSink | None = None,
    ) -> None:
        self.args = args
        self.tracker = tracker
        self.runner = runner
        self.map_ids = map_ids
        self.override_id: int | None = args.holdout_tag  # optional pin, skips adoption
        self.record_dir = record_dir
        self.rerun_sink = rerun_sink
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)
        self._rec_idx = 0

        self.state = STATE_WAITING
        self.frozen_corners: np.ndarray | None = None
        self.ref_t: tuple[float, float, float] | None = None
        self.ref_q: tuple[float, float, float, float] | None = None
        # (ts, tag_id, corners, tag_T_camera, reproj) for the ACTIVE tag
        self.last_det: tuple[float, int, np.ndarray, Any, float] | None = None
        self.last_delta_m: float | None = None
        self.last_delta_deg: float | None = None
        self.last_zone: str | None = None
        self._green_since: float | None = None
        self.stop_armed = False
        self.banner: tuple[str, float] | None = None
        self.button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.last_row: dict[str, Any] | None = None
        self.on_sample: Callable[[float, Any, float], None] | None = None
        self._ts = 0.0

        # -- adoption state (see _update_adoption) --
        self._seen_window: Any = deque(maxlen=ADOPT_WINDOW_FRAMES)  # per-frame {id: area_px}
        self.adopted_id: int | None = None
        self.mapped_only = False  # stable tags exist but every one is in the map
        self.run_tag_id: int | None = None  # frozen for the duration of RUNNING

    @property
    def active_tag_id(self) -> int | None:
        """The tag the overlay is working: frozen during a run, else the
        override, else whatever WAITING has adopted so far."""
        if self.state == STATE_RUNNING:
            return self.run_tag_id
        if self.override_id is not None:
            return self.override_id
        return self.adopted_id

    def _update_adoption(self, detections: dict[int, np.ndarray]) -> None:
        """Adopt the most prominent stable unmapped tag (WAITING, no override):
        stable = seen in >=ADOPT_MIN_SEEN of the last ADOPT_WINDOW_FRAMES
        frames; prominence = current image area. A previously adopted tag is
        kept while it stays stable (no flip-flopping between two similar
        tags). Sets self.mapped_only when stable tags exist but every one of
        them is in the localization map."""
        self._seen_window.append({mid: float(cv2.contourArea(cs)) for mid, cs in detections.items()})
        counts: dict[int, int] = {}
        for frame_seen in self._seen_window:
            for mid in frame_seen:
                counts[mid] = counts.get(mid, 0) + 1
        stable = {mid for mid, n in counts.items() if n >= ADOPT_MIN_SEEN}
        candidates = stable - self.map_ids
        self.mapped_only = bool(stable) and not candidates
        if self.adopted_id in candidates:
            return  # sticky while still a stable candidate
        if candidates:
            latest_area = self._seen_window[-1]
            self.adopted_id = max(candidates, key=lambda mid: latest_area.get(mid, 0.0))
        else:
            self.adopted_id = None

    # -- events ---------------------------------------------------------
    def on_mouse(self, event: int, x: int, y: int, flags: int, param: Any) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        bx, by, bw, bh = self.button_rect
        if not (bx <= x <= bx + bw and by <= y <= by + bh):
            return
        if self.state == STATE_WAITING:
            self.do_start()
        elif self.state == STATE_RUNNING:
            self.do_stop()

    def _det_fresh(self) -> bool:
        return (
            self.last_det is not None
            and (self._ts - self.last_det[0]) <= DET_FRESH_S
            and self.last_det[1] == self.active_tag_id
        )

    def do_start(self) -> None:
        if not self.tracker.ready:
            self.banner = ("no camera intrinsics yet -- waiting for camera_info", self._ts + 3)
            return
        if self.mapped_only and self.override_id is None:
            self.banner = (MAPPED_ONLY_MSG, self._ts + 4)
            return
        tag_id = self.active_tag_id
        if tag_id is None or not self._det_fresh():
            self.banner = (
                "no start/end tag in view -- park facing a tag that is not in the map",
                self._ts + 3,
            )
            return
        _, _, corners, tag_t_camera, _ = self.last_det
        try:
            self.runner.start(tag_id)
        except Exception as e:  # bench refused (active run) / spawn failure
            self.banner = (f"could not start run: {e}", self._ts + 6)
            print(f"overlay: could not start run: {e}")
            return
        self.run_tag_id = tag_id
        self.frozen_corners = corners.copy()
        t, r = tag_t_camera.translation, tag_t_camera.rotation
        self.ref_t = (t.x, t.y, t.z)
        self.ref_q = (r.x, r.y, r.z, r.w)
        self._green_since = None
        self.stop_armed = False
        self.state = STATE_RUNNING
        adopted = " (adopted)" if self.override_id is None else " (override)"
        print(
            f"overlay: START -- reference frozen (start/end tag: ID {tag_id}{adopted}, "
            f"range {math.dist((0.0, 0.0, 0.0), self.ref_t):.3f}m). Drive the loop."
        )

    def do_stop(self) -> None:
        print("overlay: STOP -- ending the bench run and scoring...")
        row = self.runner.stop()
        self.last_row = row
        self._print_row(row)
        self.state = STATE_WAITING
        self.frozen_corners = None
        self.ref_t = self.ref_q = None
        self.run_tag_id = None
        self.last_zone = self.last_delta_m = self.last_delta_deg = None
        self._green_since = None
        self.stop_armed = False
        self.banner = ("run recorded -- numbers on the console", self._ts + 4)

    @staticmethod
    def _print_row(row: dict[str, Any] | None) -> None:
        if not row:
            print("overlay: no scored row found -- check the bench output above")
            return

        def fmt(key: str, digits: int = 3, unit: str = "") -> str:
            v = row.get(key)
            if v in (None, "", "None"):
                return "n/a"
            try:
                return f"{float(v):.{digits}f}{unit}"
            except (TypeError, ValueError):
                return str(v)

        print(f"overlay: run scored -- run_id={row.get('run_id')} route={row.get('route')} mode={row.get('mode')}")
        print(
            f"overlay:   loop-closure {fmt('loop_closure_error_odom_m', 3, 'm')} odom -> "
            f"{fmt('loop_closure_error_corrected_m', 3, 'm')} corrected | "
            f"ATE-proxy {fmt('ate_proxy_rmse_m', 3, 'm')}"
        )
        print(
            f"overlay:   start/end tag closure {fmt('holdout_closure_error_m', 4, 'm')} / "
            f"{fmt('holdout_closure_error_deg', 2, 'deg')} "
            f"(claim={row.get('holdout_claim_source') or 'n/a'}, "
            f"reproj mean {fmt('holdout_reproj_mean_px', 2, 'px')})"
        )
        print(f"overlay:   row appended -> {bench.BENCHMARKS_CSV}")

    # -- per-frame ------------------------------------------------------
    def process_frame(self, bgr: np.ndarray, ts: float) -> np.ndarray:
        self._ts = ts
        img = bgr.copy()
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        detections = self.tracker.detect_all(gray)
        if self.state == STATE_WAITING:
            self._update_adoption(detections)
        tag_id = self.active_tag_id
        if tag_id is not None and tag_id in detections:
            solved = self.tracker.solve(detections[tag_id], tag_id, ts)
            if solved is not None:
                self.last_det = (ts, tag_id, detections[tag_id], *solved)

        if self.state == STATE_RUNNING and self.runner.poll_dead():
            print("overlay: bench run process exited on its own -- see its output above")
            self.state = STATE_WAITING
            self.frozen_corners = None
            self.ref_t = self.ref_q = None
            self.run_tag_id = None
            self.banner = ("bench run exited -- see console", ts + 6)

        if self.state == STATE_WAITING:
            self._draw_waiting(img, ts)
        else:
            self._draw_running(img, ts)

        if self.banner and ts < self.banner[1]:
            self._text(img, self.banner[0], (12, 62), 0.55, (60, 200, 255))

        if self.record_dir is not None:
            cv2.imwrite(str(self.record_dir / f"frame_{self._rec_idx:05d}.png"), img)
            self._rec_idx += 1
        if self.rerun_sink is not None:
            self.rerun_sink.log(img, ts)
        return img

    def _draw_waiting(self, img: np.ndarray, ts: float) -> None:
        self._text(
            img,
            "WAITING -- park facing any tag that is NOT in the map (~1-1.5m), then START",
            (12, 28),
            0.55,
            COLOR_NEUTRAL,
        )
        guard = self.mapped_only and self.override_id is None
        fresh = self._det_fresh()
        if fresh:
            _, tag_id, corners, tag_t_camera, reproj = self.last_det
            cv2.polylines(img, [corners.astype(np.int32)], True, COLOR_NEUTRAL, 2)
            source = "override" if self.override_id is not None else "adopted"
            label = f"start/end tag: ID {tag_id} ({source})"
            t = tag_t_camera.translation
            label += f" | range {math.dist((0, 0, 0), (t.x, t.y, t.z)):.2f}m | reproj {reproj:.1f}px"
            x, y = corners.min(axis=0)
            self._text(img, label, (int(x), max(16, int(y) - 8)), 0.5, COLOR_NEUTRAL)
        elif guard:
            self._text(
                img,
                MAPPED_ONLY_MSG,
                (12, img.shape[0] - BTN_H - 2 * BTN_MARGIN - 6),
                0.55,
                ZONE_COLORS["red"],
                thickness=2,
            )
        else:
            what = (
                f"start/end tag {self.override_id} not in view"
                if self.override_id is not None
                else "no unmapped tag in view -- looking for a start/end tag to adopt"
            )
            self._text(
                img,
                what,
                (12, img.shape[0] - BTN_H - 2 * BTN_MARGIN - 6),
                0.55,
                ZONE_COLORS["red"],
            )
        enabled = fresh and self.tracker.ready and not guard
        self._draw_button(img, "START", COLOR_BTN_GREEN if enabled else COLOR_BTN_GRAY)

    def _draw_running(self, img: np.ndarray, ts: float) -> None:
        self._text(
            img,
            "RUNNING -- drive the loop; return until the box is green, then STOP",
            (12, 28),
            0.55,
            COLOR_NEUTRAL,
        )
        if self.frozen_corners is not None:
            cv2.polylines(img, [self.frozen_corners.astype(np.int32)], True, COLOR_FROZEN, 1)
            fx, fy = self.frozen_corners.min(axis=0)
            self._text(img, "start reference", (int(fx), max(16, int(fy) - 8)), 0.45, COLOR_FROZEN)

        fresh = self._det_fresh()
        if fresh:
            _, _, corners, tag_t_camera, reproj = self.last_det
            t, r = tag_t_camera.translation, tag_t_camera.rotation
            # Same delta math as bench.py's referee: euclidean distance between
            # tag_T_camera translations + _quat_angle_deg between rotations.
            self.last_delta_m = math.dist(self.ref_t, (t.x, t.y, t.z))
            self.last_delta_deg = bench._quat_angle_deg(self.ref_q, (r.x, r.y, r.z, r.w))
            self.last_zone = _zone(self.last_delta_m, self.args.green_m, self.args.yellow_m)
            color = ZONE_COLORS[self.last_zone]
            cv2.polylines(img, [corners.astype(np.int32)], True, color, 2)
            self._text(
                img,
                f"delta vs start: {self.last_delta_m * 100:.1f} cm / {self.last_delta_deg:.1f} deg",
                (12, 96),
                0.8,
                color,
                thickness=2,
            )
            if self.last_zone == "green":
                if self._green_since is None:
                    self._green_since = ts
            else:
                self._green_since = None
            if self.on_sample is not None:
                self.on_sample(ts, tag_t_camera, reproj)
        else:
            self.last_zone = None
            self._green_since = None
            self._text(
                img,
                f"start/end tag {self.run_tag_id} not in view",
                (12, 96),
                0.7,
                ZONE_COLORS["red"],
                thickness=2,
            )

        self.stop_armed = (
            self._green_since is not None and (ts - self._green_since) >= self.args.green_hold_s
        )
        if self.stop_armed:
            self._text(
                img,
                f"back at start (<{self.args.green_m * 100:.0f}cm) -- STOP when done",
                (12, 126),
                0.55,
                ZONE_COLORS["green"],
            )
        self._draw_button(
            img,
            "STOP",
            COLOR_BTN_GREEN if self.stop_armed else COLOR_BTN_GRAY,
            pulse_ts=ts if self.stop_armed else None,
        )

    # -- drawing helpers --------------------------------------------------
    def _draw_button(self, img: np.ndarray, label: str, fill: tuple, pulse_ts: float | None = None) -> None:
        h = img.shape[0]
        x, y = BTN_MARGIN, h - BTN_H - BTN_MARGIN
        self.button_rect = (x, y, BTN_W, BTN_H)
        cv2.rectangle(img, (x, y), (x + BTN_W, y + BTN_H), fill, -1)
        cv2.rectangle(img, (x, y), (x + BTN_W, y + BTN_H), (240, 240, 240), 1)
        if pulse_ts is not None:
            thick = 2 + int(round(2.5 * (1 + math.sin(2 * math.pi * 2.0 * pulse_ts)) / 2))
            cv2.rectangle(
                img, (x - 4, y - 4), (x + BTN_W + 4, y + BTN_H + 4), ZONE_COLORS["green"], thick
            )
        tsize = cv2.getTextSize(label, FONT, 0.8, 2)[0]
        self._text(
            img,
            label,
            (x + (BTN_W - tsize[0]) // 2, y + (BTN_H + tsize[1]) // 2),
            0.8,
            (255, 255, 255),
            thickness=2,
        )

    @staticmethod
    def _text(
        img: np.ndarray,
        s: str,
        org: tuple[int, int],
        scale: float,
        color: tuple,
        thickness: int = 1,
    ) -> None:
        cv2.putText(img, s, org, FONT, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(img, s, org, FONT, scale, color, thickness, cv2.LINE_AA)


# -- live mode ---------------------------------------------------------------


def run_live(args: argparse.Namespace) -> None:
    # Transport attach: the exact pattern metrics_logger.py uses for its
    # holdout referee -- make_transport("camera_info"/"color_image", ...)
    # .subscribe(cb). Imported here (not module level) so --selftest never
    # needs a transport config.
    import threading

    from dimos.core.global_config import global_config
    from dimos.core.transport_factory import make_transport
    from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
    from dimos.msgs.sensor_msgs.Image import Image

    tracker = TagTracker(
        args.holdout_marker_length_m,
        args.holdout_aruco_dictionary,
        reproj_gate_px=args.reproj_gate_px,
    )
    if args.holdout_tag is not None:
        map_ids: set[int] = set()
        print(f"overlay: start/end tag pinned by --holdout-tag {args.holdout_tag} (no adoption)")
    else:
        map_path = Path(args.marker_map)
        if not map_path.exists():
            raise SystemExit(
                f"overlay: marker map not found: {map_path} -- needed to know which visible "
                "tags are mapped (auto-adoption picks an UNMAPPED tag). Pass --marker-map, "
                "or pin an id with --holdout-tag."
            )
        map_ids = set(load_marker_map(map_path))  # the module's own loader
        print(
            f"overlay: auto-adopting the start/end tag -- {len(map_ids)} mapped id(s) "
            f"excluded ({sorted(map_ids)}) per {map_path}"
        )
    runner = LiveBenchRunner(args)
    sink = RerunSink() if args.to_rerun else None
    if sink is not None:
        sink.connect()  # falls back cleanly (cv2-only) if no viewer is up
    app = OverlayApp(
        args,
        tracker,
        runner,
        map_ids,
        record_dir=Path(args.record) if args.record else None,
        rerun_sink=sink,
    )

    lock = threading.Lock()
    latest: dict[str, Any] = {"image": None, "info": None}

    def on_info(info: CameraInfo) -> None:
        with lock:
            latest["info"] = info
        cam_mtx, dist = camera_info_to_cv_matrices(info)
        tracker.set_intrinsics(cam_mtx, dist, info.distortion_model)

    def on_image(image: Image) -> None:
        with lock:
            latest["image"] = image

    camera_info_t = make_transport("camera_info", CameraInfo)
    camera_info_t.subscribe(on_info)
    color_image_t = make_transport("color_image", Image)
    color_image_t.subscribe(on_image)

    watching = (
        f"tag {args.holdout_tag} (override)"
        if args.holdout_tag is not None
        else "auto-adopting an unmapped tag"
    )
    print(
        f"overlay: transport={global_config.transport} -- {watching} "
        f"({args.holdout_aruco_dictionary}, {args.holdout_marker_length_m}m). "
        f"Window: {WINDOW_TITLE!r}"
    )
    cv2.namedWindow(WINDOW_TITLE)
    cv2.setMouseCallback(WINDOW_TITLE, app.on_mouse)

    placeholder = np.zeros((480, 640, 3), np.uint8)
    try:
        while True:
            with lock:
                image, info = latest["image"], latest["info"]
            now = time.time()
            if image is None:
                canvas = placeholder.copy()
                app._text(canvas, "waiting for color_image frames...", (12, 28), 0.6, COLOR_NEUTRAL)
                app._ts = now
                cv2.imshow(WINDOW_TITLE, canvas)
            else:
                # Same dims guard as metrics_logger's referee.
                if info is not None and info.width and info.height and (
                    image.width != info.width or image.height != info.height
                ):
                    pass
                else:
                    if info is not None:
                        tracker.frame_id = camera_optical_frame_id(image, info)
                    annotated = app.process_frame(image.to_opencv(), image.ts or now)
                    cv2.imshow(WINDOW_TITLE, annotated)
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                app.do_start() if app.state == STATE_WAITING else app.do_stop()
    except KeyboardInterrupt:
        print("overlay: Ctrl+C")
    finally:
        if app.state == STATE_RUNNING:
            print("overlay: quitting mid-run -- stopping + scoring first (every run is recorded)")
            app.do_stop()
        for t in (camera_info_t, color_image_t):
            try:
                t.stop()
            except Exception:
                pass
        cv2.destroyAllWindows()


# -- selftest -----------------------------------------------------------------

SELFTEST_TAG_ID = 7  # the UNMAPPED tag -- adoption must pick this one
SELFTEST_MAPPED_ID = 0  # the mapped tag, also visible -- adoption must skip it
SELFTEST_DICT = "DICT_4X4_50"
SELFTEST_TAG_M = 0.10
SELFTEST_DIST_M = 0.45  # close enough that the ~120px tag carries real perspective
SELFTEST_FPS = 30.0
_ST_W, _ST_H = 640, 480

# Where each tag sits in the wall frame W (tag B at the origin the camera
# looks at; tag A off to the side and a bit deeper, so it is stably visible
# but clearly LESS prominent -- smaller image area -- than B).
SELFTEST_SCENE = [
    (SELFTEST_TAG_ID, (0.0, 0.0, 0.0)),
    (SELFTEST_MAPPED_ID, (0.20, 0.0, -0.12)),
]


def _selftest_intrinsics() -> np.ndarray:
    fx = (_ST_W / 2.0) / math.tan(math.radians(60.0 / 2.0))  # 60 deg HFOV pinhole
    return np.array([[fx, 0, _ST_W / 2.0], [0, fx, _ST_H / 2.0], [0, 0, 1]], dtype=np.float64)


# Tag yawed in place so the view is never frontal, and the camera kept close
# (SELFTEST_DIST_M) so the rendered tag is ~120px wide: both are needed for
# solvePnP's planar IPPE solutions to be well-separated -- a small far tag
# with integer-quantized detector corners makes the two candidates chaotic
# (verified empirically while building this selftest; real 720p camera frames
# don't have this problem at benchmark ranges).
SELFTEST_TILT_DEG = -25.0


def _render_tag_frame(cx_m: float, K: np.ndarray) -> np.ndarray:
    """Render real DICT_4X4_50 tag bitmaps homography-warped for a camera at
    (cx_m, 0, SELFTEST_DIST_M) in the wall frame, looking at the origin (tag
    B's center), every tag yawed SELFTEST_TILT_DEG in place. The tilt + the
    chosen drive direction keep tag B's view obliquity in ~25-42deg, well away
    from the frontal degeneracy where solvePnP's two planar (IPPE) solutions
    collapse and flip. Same rendering approach as the demo harness, kept
    self-contained -- the detector runs on these pixels exactly as on a real
    frame."""
    import cv2.aruco as aruco

    tag_px, quiet = 160, 0.25
    dic = aruco.getPredefinedDictionary(getattr(aruco, SELFTEST_DICT))
    n = tag_px + 2 * int(round(tag_px * quiet))

    # Camera look-at (toward the origin) in wall frame W.
    th = math.radians(SELFTEST_TILT_DEG)
    R_wm = np.array([[math.cos(th), 0, math.sin(th)], [0, 1, 0], [-math.sin(th), 0, math.cos(th)]])
    c_w = np.array([cx_m, 0.0, SELFTEST_DIST_M])
    z_c = -c_w / np.linalg.norm(c_w)  # optical forward: toward tag B
    x_c = np.cross((0.0, -1.0, 0.0), z_c)
    x_c /= np.linalg.norm(x_c)
    y_c = np.cross(z_c, x_c)
    R_cw = np.stack([x_c, y_c, z_c])

    frame = np.full((_ST_H, _ST_W), 190, np.float64)
    hh = SELFTEST_TAG_M * (1 + 2 * quiet) / 2.0
    obj = np.array([[-hh, hh, 0], [hh, hh, 0], [hh, -hh, 0], [-hh, -hh, 0]], np.float64)
    src = np.array([[0, 0], [n, 0], [n, n], [0, n]], np.float32)

    # Farther-from-camera first, so a nearer tag would win any overlap.
    scene = sorted(SELFTEST_SCENE, key=lambda m: -np.linalg.norm(np.array(m[1]) - c_w))
    for marker_id, center in scene:
        pattern = aruco.generateImageMarker(dic, marker_id, tag_px)
        pad = int(round(tag_px * quiet))
        canvas = np.full((n, n), 255, np.uint8)
        canvas[pad : pad + tag_px, pad : pad + tag_px] = pattern
        # camera<-marker: p_cam = (R_cw R_wm) p_m + R_cw (p_w - c_w)
        R = R_cw @ R_wm
        rvec, _ = cv2.Rodrigues(R)
        tvec = (R_cw @ (np.array(center) - c_w)).reshape(3, 1)
        px, _ = cv2.projectPoints(obj, rvec, tvec, K, np.zeros(5))
        dst = px.reshape(4, 2).astype(np.float32)
        Hmat = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(
            canvas.astype(np.float64), Hmat, (_ST_W, _ST_H), flags=cv2.INTER_LINEAR, borderValue=-1.0
        )
        mask = warped >= 0
        frame[mask] = warped[mask]

    frame = cv2.GaussianBlur(frame, (0, 0), 0.5)
    return np.clip(frame, 0, 255).astype(np.uint8)


def run_selftest(args: argparse.Namespace) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="holdout_overlay_selftest_"))
    print(f"selftest: temp dir {tmp} (results land here -- the real trial/results/ is untouched)")

    args.holdout_tag = None  # adoption mode -- the point of the selftest
    args.holdout_aruco_dictionary = SELFTEST_DICT
    args.holdout_marker_length_m = SELFTEST_TAG_M

    # Localization map with ONLY the mapped tag, written + read back through
    # the module's own load_marker_map (the same loader run_live uses).
    map_yaml = tmp / "selftest_map.yaml"
    map_yaml.write_text(
        f"markers:\n  {SELFTEST_MAPPED_ID}:\n"
        "    translation: [0.0, 0.0, 0.0]\n    rotation: [0.0, 0.0, 0.0, 1.0]\n"
    )
    map_ids = set(load_marker_map(map_yaml))
    assert map_ids == {SELFTEST_MAPPED_ID}, f"map loader returned {map_ids}"
    print(f"selftest: marker map loaded via load_marker_map -- mapped ids {sorted(map_ids)}")

    K = _selftest_intrinsics()
    tracker = TagTracker(SELFTEST_TAG_M, SELFTEST_DICT, reproj_gate_px=args.reproj_gate_px)
    tracker.set_intrinsics(K, np.zeros((5, 1)), None)
    sink = RerunSink() if args.to_rerun else None
    if sink is not None:
        sink.connect()  # normally no viewer during selftest -> exercises the clean fallback

    # -- Phase 0: all-mapped refusal ------------------------------------
    # Same two-tag scene, but a map that contains BOTH ids: the guard must
    # show the red banner and refuse START.
    guard_app = OverlayApp(args, tracker, SelftestRunner(args, tmp), {SELFTEST_TAG_ID, SELFTEST_MAPPED_ID})
    for i in range(15):
        gray = _render_tag_frame(0.0, K)
        guard_app.process_frame(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), i / SELFTEST_FPS)
    assert guard_app.mapped_only, "guard: stable tags all mapped -> mapped_only should be True"
    assert guard_app.adopted_id is None, "guard: nothing should be adopted"
    bx, by, bw, bh = guard_app.button_rect
    guard_app.on_mouse(cv2.EVENT_LBUTTONDOWN, bx + bw // 2, by + bh // 2, 0, None)  # START
    assert guard_app.state == STATE_WAITING, "guard: START must be refused"
    assert guard_app.banner and guard_app.banner[0] == MAPPED_ONLY_MSG
    print(
        "selftest: all-mapped guard ok -- both visible tags in map -> red banner "
        f"({MAPPED_ONLY_MSG!r}), START refused"
    )

    # -- Main walk: adoption + full run ----------------------------------
    runner = SelftestRunner(args, tmp)
    app = OverlayApp(
        args,
        tracker,
        runner,
        map_ids,
        record_dir=Path(args.record) if args.record else None,
        rerun_sink=sink,
    )
    app.on_sample = runner.record_sighting

    # Camera lateral offset schedule (m): park -> START -> drive away (delta
    # grows through green->yellow->red) -> return (red->yellow->green) -> hold
    # green >1s so STOP arms -> STOP.
    schedule: list[float] = []
    schedule += [0.0] * 30                                        # WAITING park
    schedule += list(np.linspace(0.0, 0.15, 60))                  # away
    schedule += list(np.linspace(0.15, 0.0, 60))                  # return
    schedule += [0.0] * 45                                        # hold green 1.5s
    start_click_frame = 29

    zones: list[str | None] = []
    for i, cx in enumerate(schedule):
        ts = i / SELFTEST_FPS
        gray = _render_tag_frame(cx, K)
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        app.process_frame(bgr, ts)
        if runner.active:
            runner.record_odom(ts, (cx, 0.0, 0.02))
        zones.append(app.last_zone if app.state == STATE_RUNNING else None)

        if i == start_click_frame:
            assert app.state == STATE_WAITING, "expected WAITING before the START click"
            # Adoption: both tags are stably visible; the UNMAPPED one (B) is
            # bigger on screen and must be the one adopted.
            assert app.adopted_id == SELFTEST_TAG_ID, (
                f"adopted {app.adopted_id}, expected unmapped tag {SELFTEST_TAG_ID}"
            )
            assert not app.mapped_only
            assert app._det_fresh(), "adopted start/end tag should be detected while parked"
            print(
                f"selftest: adoption ok -- tags {SELFTEST_TAG_ID} (unmapped) + "
                f"{SELFTEST_MAPPED_ID} (mapped) visible; adopted ID {app.adopted_id}"
            )
            bx, by, bw, bh = app.button_rect
            app.on_mouse(cv2.EVENT_LBUTTONDOWN, bx + bw // 2, by + bh // 2, 0, None)  # START
            assert app.state == STATE_RUNNING, "START click should enter RUNNING"
            assert app.frozen_corners is not None and app.ref_t is not None
            assert app.run_tag_id == SELFTEST_TAG_ID
            assert runner.tag_id == SELFTEST_TAG_ID, "bench must be started with the ADOPTED id"
            print(
                f"selftest: frame {i} -- START clicked, adopted id {runner.tag_id} passed to the "
                f"bench start path, reference frozen "
                f"(range {math.dist((0.0, 0.0, 0.0), app.ref_t):.3f}m)"
            )

    # zone walk: after the last red there must be a yellow, then a green (the
    # return sequence), and the run must end green.
    seen = [z for z in zones if z]
    assert "red" in seen and "yellow" in seen and "green" in seen, f"zones seen: {set(seen)}"
    last_red = max(i for i, z in enumerate(zones) if z == "red")
    yellow_after = next((i for i in range(last_red, len(zones)) if zones[i] == "yellow"), None)
    assert yellow_after is not None, "no yellow after the last red (return leg)"
    green_after = next((i for i in range(yellow_after, len(zones)) if zones[i] == "green"), None)
    assert green_after is not None, "no green after the return-leg yellow"
    assert seen[-1] == "green", f"run should end green, ended {seen[-1]}"
    print(
        f"selftest: zone walk ok -- red(last @{last_red}) -> yellow(@{yellow_after}) "
        f"-> green(@{green_after}), final delta {app.last_delta_m * 100:.2f}cm"
    )
    assert app.stop_armed, "STOP should be armed (green sustained > green-hold-s)"
    print("selftest: STOP armed (green + pulsing border) after sustained <2cm")

    bx, by, bw, bh = app.button_rect
    app.on_mouse(cv2.EVENT_LBUTTONDOWN, bx + bw // 2, by + bh // 2, 0, None)  # STOP
    assert app.state == STATE_WAITING, "STOP click should return to WAITING"
    row = app.last_row
    assert row is not None, "STOP should have produced a scored row"
    assert row["holdout_tag"] == SELFTEST_TAG_ID, (
        f"row scored against tag {row['holdout_tag']}, expected adopted {SELFTEST_TAG_ID}"
    )
    err_m = row["holdout_closure_error_m"]
    assert err_m is not None and err_m < 0.02, f"start/end tag closure too large: {err_m}"

    with bench.BENCHMARKS_CSV.open() as f:
        csv_rows = list(csv.DictReader(f))
    assert len(csv_rows) == 1 and csv_rows[0]["run_id"] == row["run_id"]
    assert bench.RESULTS_MD.exists()
    print(f"selftest: metrics row landed -- {bench.BENCHMARKS_CSV} (1 row) + {bench.RESULTS_MD}")
    print(
        f"selftest: referee agrees with screen -- start/end tag closure "
        f"{err_m}m / {row['holdout_closure_error_deg']}deg "
        f"(claim={row['holdout_claim_source']}, sightings "
        f"{row['holdout_readings_start']}+{row['holdout_readings_end']}, "
        f"reproj mean {row['holdout_reproj_mean_px']}px)"
    )
    if args.record:
        print(f"selftest: annotated frames saved to {args.record}")
    print("SELFTEST PASS")


# -- main ----------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark run controller: live camera window with a frozen start/end tag "
        "outline and Start/Stop control, wrapping bench.py. The start/end tag (the reference "
        "tag bench.py/metrics_logger.py address via --holdout-tag) is AUTO-ADOPTED: the most "
        "prominent stable visible tag whose ID is not in --marker-map. --holdout-tag remains "
        "as an optional override."
    )
    ap.add_argument("--mode", default="marker", choices=bench.MODES, help="bench.py --mode passthrough")
    ap.add_argument("--route", default="drift-recovery", help="bench.py --route passthrough")
    ap.add_argument("--notes", default="", help="bench.py --notes passthrough")
    ap.add_argument("--dimos-log", default=None, help="bench.py --dimos-log passthrough")
    ap.add_argument(
        "--holdout-tag",
        type=int,
        default=None,
        help="OPTIONAL override: pin the start/end tag to this ID and skip auto-adoption "
        "(same flag name as bench.py). Default: adopt the most prominent stable visible "
        "tag whose ID is not in --marker-map.",
    )
    ap.add_argument(
        "--marker-map",
        default=DEFAULT_MARKER_MAP,
        help="localization-map YAML (loaded with the module's own load_marker_map) used to "
        "know which visible tags are mapped -- auto-adoption only picks UNMAPPED tags "
        f"(default {DEFAULT_MARKER_MAP})",
    )
    ap.add_argument("--holdout-window", type=int, default=bench.HOLDOUT_DEFAULT_WINDOW,
                    help="bench.py --holdout-window passthrough")
    ap.add_argument("--holdout-marker-length-m", type=float, default=0.10,
                    help="physical edge length (m) of the start/end tag (bench.py passthrough)")
    ap.add_argument("--holdout-aruco-dictionary", default="DICT_APRILTAG_36h11",
                    help="dictionary the start/end tag was printed from (bench.py passthrough)")
    ap.add_argument("--green-m", type=float, default=0.02,
                    help="delta below this is GREEN / arms STOP (default 0.02 = 2cm)")
    ap.add_argument("--yellow-m", type=float, default=0.05,
                    help="delta below this (and above --green-m) is YELLOW (default 0.05 = 5cm)")
    ap.add_argument("--green-hold-s", type=float, default=1.0,
                    help="seconds of sustained green before STOP arms (default 1.0)")
    ap.add_argument("--reproj-gate-px", type=float, default=3.0,
                    help="drop start/end tag detections whose best PnP fit reprojects worse "
                    "than this (same 3px gate VisualRelocalizationModule uses; default 3.0)")
    ap.add_argument("--record", default=None, metavar="DIR",
                    help="save every annotated frame as numbered PNGs into DIR (for GIF-making)")
    ap.add_argument("--to-rerun", action=argparse.BooleanOptionalAction, default=True,
                    help="mirror annotated frames into the running dimos rerun viewer "
                    "(entity benchmark/start_end_tag_overlay, ~5fps); falls back to "
                    "cv2-window-only if no viewer is reachable (default: on)")
    ap.add_argument("--selftest", action="store_true",
                    help="no robot/camera: walk the full state machine on rendered fixture "
                    "frames and land a metrics row in a temp results dir")
    args = ap.parse_args()

    if args.selftest:
        run_selftest(args)
        return
    run_live(args)


if __name__ == "__main__":
    main()
