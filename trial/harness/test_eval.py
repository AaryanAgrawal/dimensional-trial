#!/usr/bin/env python3
"""Unit tests for eval.py's PURE orchestration logic -- no replay, no subprocess.

Two things are graded, both offline:
  * --list builds the held-out pair matrix and RUNS NOTHING -- proven by hard-failing
    if eval.py ever calls subprocess.run in the --list path.
  * _marker_json converts run A's gated marker YAML to the module's JSON contract
    (sorted-by-int-id, float-coerced, schema=map_T_tag), idempotently.

Deterministic: fixed inputs, no wall-clock, no randomness. Run:
  uv run --project /home/dimos/dimensional-trial/dimos \
      python -m pytest trial/harness/test_eval.py -q
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))  # eval.py lives here
eval_harness = importlib.import_module("eval")  # `eval` shadows no stdlib module


def _fake_pair(tmp_path: Path) -> dict:
    """A pair whose premap is ABSENT (so --list marks it WILL SKIP) and whose marker
    YAML is None -> _build_matrix writes nothing and spawns nothing."""
    return {
        "scene": "unit_scene",
        "tag": "unit_tag",
        "premap_recording": "rec_A",
        "replay_recording": "rec_B",
        "premap": tmp_path / "absent_premap.lcm",
        "marker_map_yaml": None,
        "markers_map_b": None,
    }


def test_list_builds_matrix_and_spawns_no_subprocess(tmp_path, monkeypatch, capsys) -> None:
    """`eval.py --list` prints the pair matrix and returns cleanly (exit 0) WITHOUT
    ever shelling out. subprocess.run is replaced with a bomb: if the --list path
    spawns anything, the test fails loudly."""
    def _no_spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("--list must not spawn a subprocess")

    monkeypatch.setattr(eval_harness, "PAIRS", [_fake_pair(tmp_path)])
    monkeypatch.setattr(eval_harness.subprocess, "run", _no_spawn)
    monkeypatch.setattr(sys, "argv", ["eval.py", "--list"])

    eval_harness.main()  # returns None on the --list path == exit 0; no SystemExit

    out = capsys.readouterr().out
    assert "Held-out pair matrix: 1 pair(s)" in out
    assert "tag=unit_tag" in out and "scene=unit_scene" in out
    assert "WILL SKIP: premap absent" in out  # premap path does not exist
    assert "rec_A" in out and "rec_B" in out


def test_list_only_matched_pair_when_only_filter_used(tmp_path, monkeypatch, capsys) -> None:
    """--only filters the matrix by substring before --list; a non-matching filter
    raises SystemExit (matched no pair) rather than silently listing everything."""
    monkeypatch.setattr(eval_harness, "PAIRS", [_fake_pair(tmp_path)])
    monkeypatch.setattr(eval_harness.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no spawn")))
    monkeypatch.setattr(sys, "argv", ["eval.py", "--only", "no_such_scene", "--list"])

    raised = False
    try:
        eval_harness.main()
    except SystemExit as e:
        raised = True
        assert "matched no pair" in str(e)
    assert raised, "a --only filter that matches nothing must SystemExit"


def test_marker_json_contract_sorted_floats_schema(tmp_path) -> None:
    """_marker_json emits {"meta":{schema:map_T_tag}, "markers":{id:{translation,
    rotation}}} with ids sorted numerically (not lexically: 2 before 10) and all
    values coerced to float."""
    yaml_path = tmp_path / "survey.marker_map.yaml"
    yaml_path.write_text(
        "markers:\n"
        "  10:\n"
        "    translation: [1, 2, 3]\n"
        "    rotation: [1, 0, 0, 0]\n"
        "  2:\n"
        "    translation: [4, 5, 6]\n"
        "    rotation: [0, 1, 0, 0]\n"
    )

    out_path = eval_harness._marker_json(yaml_path)
    assert out_path == yaml_path.with_suffix(".json")

    doc = json.loads(out_path.read_text())
    assert doc["meta"]["schema"] == "map_T_tag"
    assert doc["meta"]["source_yaml"] == "survey.marker_map.yaml"
    assert list(doc["markers"].keys()) == ["2", "10"]  # numeric sort, not "10" < "2"
    assert doc["markers"]["2"]["translation"] == [4.0, 5.0, 6.0]
    assert all(isinstance(x, float) for x in doc["markers"]["10"]["translation"])
    assert doc["markers"]["10"]["rotation"] == [1.0, 0.0, 0.0, 0.0]


def test_marker_json_absent_yaml_returns_none(tmp_path) -> None:
    assert eval_harness._marker_json(tmp_path / "nope.yaml") is None


def test_marker_json_is_idempotent(tmp_path) -> None:
    """Running twice on the same YAML yields byte-identical JSON (sorted ids, no
    timestamps) -- the determinism the harness docstring promises."""
    yaml_path = tmp_path / "m.yaml"
    yaml_path.write_text(
        "markers:\n  7:\n    translation: [0.1, 0.2, 0.3]\n    rotation: [1, 0, 0, 0]\n"
    )
    first = eval_harness._marker_json(yaml_path).read_text()
    second = eval_harness._marker_json(yaml_path).read_text()
    assert first == second


def test_build_matrix_overrides_and_presence_flags(tmp_path, monkeypatch) -> None:
    """_build_matrix records premap presence and the exact reloc override surface.
    With a present marker JSON both overrides appear (marker_map_file first, then
    use_fiducial_prior=true); an absent premap is flagged, not raised."""
    yaml_path = tmp_path / "s.marker_map.yaml"
    yaml_path.write_text(
        "markers:\n  3:\n    translation: [1, 1, 1]\n    rotation: [1, 0, 0, 0]\n"
    )
    pair = _fake_pair(tmp_path)
    pair["marker_map_yaml"] = yaml_path
    monkeypatch.setattr(eval_harness, "PAIRS", [pair])

    specs = eval_harness._build_matrix()
    assert len(specs) == 1
    s = specs[0]
    assert s["premap_present"] is False  # absent path, recorded not raised
    assert s["marker_present"] is True
    assert s["overrides"] == [
        f"{eval_harness.MODULE}.marker_map_file={yaml_path.with_suffix('.json')}",
        f"{eval_harness.MODULE}.use_fiducial_prior=true",
    ]
