"""Derive a marker map (map_T_tag survey) for a recording, via the real dimos path.

Same derivation the replay gate uses (test_unitree_go2_fiducial_relocalization_replay.py
marker_map_file fixture): PGO the lidar stream, then corrected_marker_transforms
(dimos/mapping/loop_closure/eval.py) for the final smoothed pose of each detection
track lifted into the PGO-corrected frame -- the frame `dimos map global --pgo
--export` writes its premap in, so the entries are map_T_tag against that premap.

Difference from the fixture: the IPPE mirror-flip gate is ON (ambiguity_ratio_min
2.0, the live detector's value in MarkerDetectionStreamModuleConfig). eval.py's
default is 1.0 = gate off, which lets a mirror-ambiguous view bake a flipped
map_T_tag into the survey that every later fiducial fix inherits.

    uv run python /home/dimos/dimensional-trial/trial/harness/derive_marker_map.py \
        --recording hk_village3

Writes data/replay_gate/<recording>.markers.yaml and prints one line per tag.
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from dimos.mapping.loop_closure.eval import corrected_marker_transforms
from dimos.mapping.loop_closure.pgo import PGO
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.robot.unitree.go2.connection import _camera_info_static
from dimos.utils.data import get_data_dir

app = typer.Typer(add_completion=False)


def _dist_m(a: Transform, b: Transform) -> float:
    da, db = a.translation, b.translation
    return float(((da.x - db.x) ** 2 + (da.y - db.y) ** 2 + (da.z - db.z) ** 2) ** 0.5)


@app.command()
def main(
    recording: str = typer.Option("hk_village3"),
    marker_length_m: float = typer.Option(0.10),  # hk_village3's printed tag edge
    ambiguity_ratio_min: float = typer.Option(2.0),  # live detector's value; 1.0 = gate off
    out: Path | None = typer.Option(None),
) -> None:
    db_path = get_data_dir() / f"{recording}.db"
    if not db_path.exists():
        raise SystemExit(f"recording not local: {db_path}")
    out_path = out or (get_data_dir() / "replay_gate" / f"{recording}.markers.yaml")

    store = SqliteStore(path=str(db_path), must_exist=True)
    with store:
        graph = store.streams.lidar.transform(PGO()).last().data
        by_marker = corrected_marker_transforms(
            store,
            graph,
            camera_info=_camera_info_static(),
            marker_size=marker_length_m,
            # Knob values are eval.py's CLI defaults, matching the replay gate.
            marker_max_speed=0.5,
            marker_max_rot_rate=50.0,
            marker_quality_window=0.1,
            marker_smoothing=7.5,
            ambiguity_ratio_min=ambiguity_ratio_min,
        )

    if not by_marker:
        raise SystemExit(f"no markers detected in {recording}")

    entries: dict[int, dict[str, list[float]]] = {}
    for marker_id, tracks in sorted(by_marker.items()):
        # Medoid track (min summed distance to the others): robust to one bad track.
        best = min(tracks, key=lambda t: sum(_dist_m(t, other) for other in tracks))
        spread_m = max((_dist_m(best, other) for other in tracks), default=0.0)
        entries[marker_id] = {
            "translation": [best.translation.x, best.translation.y, best.translation.z],
            "rotation": [best.rotation.x, best.rotation.y, best.rotation.z, best.rotation.w],
        }
        print(
            f"tag {marker_id}: tracks={len(tracks)} "
            f"xyz_m=({best.translation.x:.3f}, {best.translation.y:.3f}, {best.translation.z:.3f}) "
            f"max_track_spread_m={spread_m:.3f}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump({"markers": entries}))
    print(f"ambiguity_ratio_min={ambiguity_ratio_min} tags={len(entries)} -> {out_path}")


if __name__ == "__main__":
    app()
