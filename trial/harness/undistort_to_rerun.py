# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Live sidecar: undistort the Go2 fisheye front camera into a RUNNING rerun.

A separate process that subscribes to the live raw camera topic with dimos'
real pubsub (``subscribe_pubsub_uri`` — the same call ``cameracalibrate`` uses,
cameracalibrate.py:1455), fisheye-undistorts each frame with a precomputed
OpenCV map, and logs it into the ALREADY-running rerun viewer over its gRPC
proxy. It never restarts the pipeline: it is a subscriber + a rerun client, so
it coexists with the running blueprint on its own entity path.

Cannot fully run without (1) a live camera publisher on the bus and (2) a rerun
viewer already serving its gRPC proxy (the dimos bridge). Use ``--self-check``
to validate the undistort map offline, with neither dependency present.
"""

from __future__ import annotations

import argparse
import threading
import time
from typing import Any

import cv2
import numpy as np
import rerun as rr

from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.fiducial.marker_pose import is_fisheye_model

# The go2 fisheye intrinsics shipped with dimos; equidistant/Kannala-Brandt model.
DEFAULT_CAMERA_INFO = (
    "/home/dimos/dimensional-trial/dimos/dimos/robot/unitree/go2/front_camera_720.yaml"
)
# Raw rgb8 front camera; pubsub URI format is "<proto>:<topic>" (registry.py).
DEFAULT_TOPIC = "lcm:/color_image"
# The dimos rerun bridge serves its gRPC proxy here (constants.py RERUN_GRPC_PORT
# = 9877; bridge.py builds "rerun+http://{host}:9877/proxy").
DEFAULT_RERUN_URL = "rerun+http://127.0.0.1:9877/proxy"
DEFAULT_ENTITY = "world/undistorted_image"


def build_undistort_maps(
    info: CameraInfo,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Precompute the (map1, map2) remap tables + the undistorted camera matrix.

    Built ONCE from fixed intrinsics; ``cv2.remap`` then runs per frame. Keeps
    ``P == K`` so the undistorted pixels stay in the same pinhole-K convention
    the fiducial corner path already uses (marker_pose.py:88).
    """
    k = info.get_K_matrix()
    size = (info.width, info.height)  # (w, h) as OpenCV expects

    if is_fisheye_model(info.distortion_model):
        # Fisheye (equidistant) takes exactly 4 distortion coeffs (k1..k4).
        # https://docs.opencv.org/4.x/db/d58/group__calib3d__fisheye.html
        d = np.array(info.D, dtype=np.float64).reshape(-1, 1)[:4]
        map1, map2 = cv2.fisheye.initUndistortRectifyMap(  # https://docs.opencv.org/4.x/db/d58/group__calib3d__fisheye.html
            k, d, np.eye(3), k, size, cv2.CV_16SC2
        )
    else:
        # plumb_bob / radtan fallback so the sidecar also works on pinhole rigs.
        d = np.array(info.D, dtype=np.float64).reshape(-1, 1)
        map1, map2 = cv2.initUndistortRectifyMap(  # https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
            k, d, np.eye(3), k, size, cv2.CV_16SC2
        )
    return map1, map2, k


def undistort_image(msg: Image, map1: np.ndarray, map2: np.ndarray) -> Image:
    """Remap one frame, preserving format/frame_id/ts so ``to_rerun`` picks the
    right color model and the bridge timeline stays aligned."""
    remapped = cv2.remap(msg.data, map1, map2, interpolation=cv2.INTER_LINEAR)
    return Image(data=remapped, format=msg.format, frame_id=msg.frame_id, ts=msg.ts)


def _self_check(info: CameraInfo) -> int:
    """Build the map and undistort a synthetic frame — no bus, no rerun."""
    map1, map2, new_k = build_undistort_maps(info)
    # Deterministic synthetic frame: a per-column gradient, no rng/clock.
    col = np.linspace(0, 255, info.width, dtype=np.uint8)
    frame = np.repeat(col[None, :, None], info.height, axis=0)
    frame = np.repeat(frame, 3, axis=2)
    synthetic = Image.from_numpy(frame, ts=0.0)
    out = undistort_image(synthetic, map1, map2)
    ok = out.data.shape == (info.height, info.width, 3)
    print(
        f"self-check: fisheye={is_fisheye_model(info.distortion_model)} "
        f"map1={map1.shape} in={synthetic.data.shape} out={out.data.shape} "
        f"new_fx={new_k[0, 0]:.3f} rerun={out.to_rerun() is not None} "
        f"{'OK' if ok else 'FAIL'}"
    )
    return 0 if ok else 1


def run_sidecar(args: argparse.Namespace) -> int:
    """Subscribe live, undistort, and log into the running viewer forever."""
    from dimos.protocol.pubsub.registry import subscribe_pubsub_uri

    info = CameraInfo.from_yaml(args.camera_info)
    map1, map2, new_k = build_undistort_maps(info)

    # rr.init with the bridge's app_id, then connect into its gRPC proxy — the
    # exact client path dimos itself uses (init.py:45 then init.py:71). App-id
    # alone shares the VIEWER; pass --recording-id to share the bridge's
    # timeline/entity-tree (the bridge uses a random recording_id by default).
    init_kwargs: dict[str, Any] = {}
    if args.recording_id:
        init_kwargs["recording_id"] = args.recording_id
    rr.init(args.app_id, **init_kwargs)
    rr.connect_grpc(url=args.rerun_url)

    if args.log_pinhole:
        # Static camera model so overlays in this 2D view line up with the
        # undistorted (pinhole-K) pixels.
        rr.log(
            args.entity,
            rr.Pinhole(image_from_camera=new_k, resolution=[info.width, info.height]),
            static=True,
        )

    count = [0]
    lock = threading.Lock()

    def _on_image(msg: Any) -> None:
        if not isinstance(msg, Image):
            return
        out = undistort_image(msg, map1, map2)
        with lock:
            rr.set_time("log_time", timestamp=out.ts)
            rr.log(args.entity, out.to_rerun())
            count[0] += 1

    transport, unsub = subscribe_pubsub_uri(args.topic, _on_image, msg_type=Image)
    print(
        f"undistort sidecar live: topic={args.topic} entity={args.entity} "
        f"-> {args.rerun_url} (app_id={args.app_id}). Ctrl-C to stop."
    )
    try:
        while True:
            time.sleep(1.0)
            with lock:
                n = count[0]
            print(f"frames undistorted+logged: {n}")
    except KeyboardInterrupt:
        print(f"stopping; {count[0]} frames logged")
    finally:
        unsub()
        transport.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Live sidecar: undistort the Go2 fisheye front camera into a running rerun."
    )
    p.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help='pubsub URI "<proto>:<topic>" for raw rgb8 frames (default: %(default)s)',
    )
    p.add_argument(
        "--camera-info",
        default=DEFAULT_CAMERA_INFO,
        help="CameraInfo YAML with intrinsics (default: the go2 720p fisheye)",
    )
    p.add_argument(
        "--entity",
        default=DEFAULT_ENTITY,
        help="rerun entity path to log the undistorted image to (default: %(default)s)",
    )
    p.add_argument(
        "--rerun-url",
        default=DEFAULT_RERUN_URL,
        help="gRPC proxy of the RUNNING viewer (default: %(default)s)",
    )
    p.add_argument(
        "--app-id",
        default="dimos",
        help="rerun app id; must match the running viewer (default: %(default)s)",
    )
    p.add_argument(
        "--recording-id",
        default=None,
        help="share the bridge's recording/timeline (default: sibling recording in same viewer)",
    )
    p.add_argument(
        "--no-pinhole",
        dest="log_pinhole",
        action="store_false",
        help="do not log an rr.Pinhole from the undistorted K",
    )
    p.add_argument(
        "--self-check",
        action="store_true",
        help="build the map + undistort a synthetic frame, then exit (no bus, no rerun)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.self_check:
        return _self_check(CameraInfo.from_yaml(args.camera_info))
    return run_sidecar(args)


if __name__ == "__main__":
    raise SystemExit(main())
