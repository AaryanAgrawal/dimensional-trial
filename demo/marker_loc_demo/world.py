"""The synthetic world: a ~10x8m room with a wall-mounted AprilTag marker map.

`marker_map.yaml` is a first draft of the config format the real
`MarkerLocalizationModule` would load (id -> known 6-DOF pose in `world`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import yaml

from . import transforms as tf

ROOM_WIDTH_X = 10.0  # meters, wall at x=0 and x=ROOM_WIDTH_X
ROOM_DEPTH_Y = 8.0  # meters, wall at y=0 and y=ROOM_DEPTH_Y
ROOM_HEIGHT_Z = 3.0  # ceiling height, for reference only (tags are wall-mounted)

DEFAULT_TAG_SIZE_M = 0.15

WALL_NORMALS = {
    "x=0": np.array([1.0, 0.0, 0.0]),  # west wall, faces +X into the room
    "x=10": np.array([-1.0, 0.0, 0.0]),  # east wall, faces -X into the room
    "y=0": np.array([0.0, 1.0, 0.0]),  # south wall, faces +Y into the room
    "y=8": np.array([0.0, -1.0, 0.0]),  # north wall, faces -Y into the room
}


@dataclass
class MarkerSpec:
    id: int
    size_m: float
    translation: np.ndarray  # (3,) world xyz, meters
    R_world_marker: np.ndarray  # (3,3)
    wall: str

    @property
    def T_world_marker(self) -> np.ndarray:
        return tf.make_T(self.R_world_marker, self.translation)


def _wall_point(wall: str, along: float, height: float) -> np.ndarray:
    if wall == "x=0":
        return np.array([0.0, along, height])
    if wall == "x=10":
        return np.array([ROOM_WIDTH_X, along, height])
    if wall == "y=0":
        return np.array([along, 0.0, height])
    if wall == "y=8":
        return np.array([along, ROOM_DEPTH_Y, height])
    raise ValueError(wall)


def build_marker_map() -> list[MarkerSpec]:
    """Hand-placed layout: 15 tags spread across the 4 walls, varied height,
    15cm each, positioned so the trajectory's loop (see `trajectory.py`)
    passes each one at a workable range and angle.
    """
    markers: list[MarkerSpec] = []
    next_id = 0

    def add(wall: str, along: float, height: float) -> None:
        nonlocal next_id
        R = tf.wall_marker_rotation(WALL_NORMALS[wall])
        t = _wall_point(wall, along, height)
        markers.append(MarkerSpec(id=next_id, size_m=DEFAULT_TAG_SIZE_M, translation=t, R_world_marker=R, wall=wall))
        next_id += 1

    # x=0 / x=10 walls run along Y (length ROOM_DEPTH_Y=8)
    for along, h in [(1.0, 1.1), (3.0, 1.6), (5.0, 1.1), (7.0, 1.6)]:
        add("x=0", along, h)
    for along, h in [(1.0, 1.6), (3.0, 1.1), (5.0, 1.6), (7.0, 1.1)]:
        add("x=10", along, h)
    # y=0 / y=8 walls run along X (length ROOM_WIDTH_X=10)
    for along, h in [(1.5, 1.3), (5.0, 1.5), (8.5, 1.3)]:
        add("y=0", along, h)
    for along, h in [(1.5, 1.4), (3.5, 1.6), (5.0, 1.2), (8.5, 1.4)]:
        add("y=8", along, h)

    return markers


def save_marker_map(markers: list[MarkerSpec], path: str) -> None:
    doc = {
        "dictionary": "DICT_APRILTAG_36h11",
        "default_size_m": DEFAULT_TAG_SIZE_M,
        "frame": "world",
        "notes": (
            "world frame: X-east, Y-north, Z-up, origin at a room corner. "
            "rotation is world_R_marker as a quaternion; marker-local convention "
            "is X-right, Y-up, Z=normal (points away from the wall into the room), "
            "matching dimos/perception/fiducial/marker_pose.py object points."
        ),
        "markers": [
            {
                "id": m.id,
                "size_m": m.size_m,
                "wall": m.wall,
                "translation_m": {
                    "x": round(float(m.translation[0]), 4),
                    "y": round(float(m.translation[1]), 4),
                    "z": round(float(m.translation[2]), 4),
                },
                "quaternion_wxyz": {
                    k: round(float(v), 6)
                    for k, v in zip("wxyz", tf.quat_from_matrix(m.R_world_marker))
                },
            }
            for m in markers
        ],
    }
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False)


def load_marker_map(path: str) -> list[MarkerSpec]:
    with open(path) as f:
        doc = yaml.safe_load(f)
    markers = []
    for m in doc["markers"]:
        t = np.array([m["translation_m"]["x"], m["translation_m"]["y"], m["translation_m"]["z"]])
        q = np.array([m["quaternion_wxyz"][k] for k in "wxyz"])
        R = tf.matrix_from_quat(q)
        markers.append(MarkerSpec(id=m["id"], size_m=m["size_m"], translation=t, R_world_marker=R, wall=m["wall"]))
    return markers
