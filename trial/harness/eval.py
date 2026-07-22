#!/usr/bin/env python3
"""Dimos-native HELD-OUT relocalization eval: grade the REAL `--replay` pipeline
on a DIFFERENT traversal than the one that built the map.

This is an ORCHESTRATOR, not a benchmark of its own -- it re-implements NO dimos
data-path step (the house rule: test real dimos, never a look-alike). For each
held-out PAIR it shells out to the two shipped drivers and only reads their JSON:
  replay_bench.py -> runs `dimos --replay run <blueprint>` on run B (the replay
                     recording) against run A's premap + marker survey, and
                     captures what the real RelocalizationModule publishes on /tf.
  score_replay.py -> grades those published world->map fixes against run B's own
                     PGO silver truth (1 m / 15 deg success bar).
then hands the captured fixes to `eval_module.run_offline_report`, which prints
the per-source table, Umeyama-aligns A's map frame to B's PGO-truth frame for a
real held-out med_err (or `-` with the reason), and writes json + a top-down
trajectory PNG. This file just picks the pairs, converts A's marker survey to the
module's JSON contract, serializes the runs (the LCM bus is EXCLUSIVE -- one
`dimos --replay` at a time; a second Coordinator crashes it), caches per pair, and
tabulates.

WHY HELD-OUT. The old matrix replayed the SAME recording its premap was built from
(train == test), so lidar-only absolute numbers were optimistic and only the A/B
prior delta was trustworthy. Here premap + marker map come from run A and the
replay is run B of the SAME scene, a genuinely unseen traversal -- the honest test
this trial has been building toward. `--list` prints the pair matrix and runs
nothing.

Determinism: no wall-clock / random anywhere -- given the same captured fixes, the
matrix, table, and eval.json are byte-reproducible (only git revs + the drivers'
own captures vary, and those carry their own provenance).

Run (bus is exclusive -> pairs run strictly serial; a cached capture replays nothing):
  uv run --project /home/dimos/dimensional-trial/dimos \
      python trial/harness/eval.py                 # whole matrix, cached
  ...  python trial/harness/eval.py --list         # print the pair matrix, run nothing
  ...  python trial/harness/eval.py --only sf_office --rerun   # one pair, no cache
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

HARNESS_DIR = Path(__file__).resolve().parent
TRIAL_ROOT = HARNESS_DIR.parent
REPO_ROOT = TRIAL_ROOT.parent
DIMOS_ROOT = REPO_ROOT / "dimos"
OUT_DIR = HARNESS_DIR / "out" / "results_dimos"
EVAL_DIR = HARNESS_DIR / "out" / "eval"
REPLAY_BENCH = HARNESS_DIR / "replay_bench.py"
SCORE_REPLAY = HARNESS_DIR / "score_replay.py"

# The flagship stack under the SHARED-CONTRACT name (Track A's rename target). Used
# here for display/provenance only: the actual blueprint is whatever replay_bench.py
# drives -- keep replay_bench's BLUEPRINT constant in lockstep with this rename.
BLUEPRINT = "unitree-go2-relocalization-fiducial"
MODULE = "relocalizationmodule"  # dimos -o override prefix for the reloc module config
SUCCESS_T_M = 1.0                # translation gate; must match score_replay.SUCCESS_T_M
SUCCESS_R_DEG = 15.0             # rotation gate;    must match score_replay.SUCCESS_R_DEG

# One entry per held-out PAIR: premap + marker survey from run A, replay run B of
# the SAME scene. `tag` is the shared output key (its capture reuses across the two
# drivers + the report). Marker map is A's gated YAML; _marker_json() converts it to
# the module's JSON contract for the prior-ON replay override.
PAIRS: list[dict[str, Any]] = [
    {
        "scene": "sf_office",
        "tag": "survey2_heldout",
        "premap_recording": "sf_office_go2_20260718_survey1",  # A: built the map
        "replay_recording": "sf_office_go2_20260720_survey2",  # B: the unseen traversal
        "premap": OUT_DIR.parent / "robotday_build/sf_office_go2_20260718_survey1.pc2.lcm",
        "marker_map_yaml": OUT_DIR.parent
        / "robotday_build_gated/sf_office_go2_20260718_survey1.marker_map.yaml",
        # Optional run-B marker survey (map_B_T_tag) that anchors the Umeyama
        # map_A->map_B alignment; absent today -> med_err prints `-` with the reason.
        "markers_map_b": None,
    },
]


def _git_rev(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _marker_json(yaml_path: Path) -> Path | None:
    """Convert A's gated marker YAML to the module's JSON contract
    ({"meta":..,"markers":{"<id>":{"translation":[..],"rotation":[..]}}}), written
    next to the YAML. Returns the .json path, or None if the YAML is absent. Idempotent
    and deterministic (sorted ids, no timestamps)."""
    if not yaml_path.exists():
        return None
    import yaml

    doc = yaml.safe_load(yaml_path.read_text())
    markers = {
        str(tag_id): {
            "translation": [float(x) for x in val["translation"]],
            "rotation": [float(x) for x in val["rotation"]],
        }
        for tag_id, val in sorted(doc.get("markers", {}).items(), key=lambda kv: int(kv[0]))
    }
    out = {
        "meta": {"source_yaml": yaml_path.name, "schema": "map_T_tag"},
        "markers": markers,
    }
    json_path = yaml_path.with_suffix(".json")
    json_path.write_text(json.dumps(out, indent=2))
    return json_path


def _build_matrix() -> list[dict[str, Any]]:
    """Resolve each pair to absolute paths + the exact replay override surface. Paths
    are recorded (present/absent), not required here -- existence is checked at run."""
    specs: list[dict[str, Any]] = []
    for pair in PAIRS:
        premap = Path(pair["premap"]).resolve()
        marker_yaml = Path(pair["marker_map_yaml"]).resolve() if pair["marker_map_yaml"] else None
        marker_json = (
            _marker_json(marker_yaml) if marker_yaml is not None and marker_yaml.exists() else None
        )
        overrides = [f"{MODULE}.use_fiducial_prior=true"]
        if marker_json is not None:
            overrides.insert(0, f"{MODULE}.marker_map_file={marker_json}")
        specs.append({
            "scene": pair["scene"],
            "tag": pair["tag"],
            "premap_recording": pair["premap_recording"],
            "replay_recording": pair["replay_recording"],
            "premap_abs": str(premap),
            "premap_present": premap.exists(),
            "marker_yaml_abs": str(marker_yaml) if marker_yaml else None,
            "marker_json_abs": str(marker_json) if marker_json else None,
            "marker_present": marker_json is not None,
            "markers_map_b_abs": str(pair["markers_map_b"]) if pair["markers_map_b"] else None,
            "overrides": overrides,
        })
    return specs


def _uv(script: Path, *args: str) -> list[str]:
    """The drivers import dimos, so run them in the dimos project env via uv."""
    return ["uv", "run", "--project", str(DIMOS_ROOT), "python", str(script), *args]


def _run(cmd: list[str]) -> int:
    """Run a driver with inherited stdout/stderr so the operator watches the live
    replay. Returns the exit code."""
    print("  $ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(REPO_ROOT)).returncode


def _replay_json(spec: dict[str, Any]) -> Path:
    return OUT_DIR / f"{spec['tag']}.replay.json"


def _score_json(spec: dict[str, Any]) -> Path:
    return OUT_DIR / f"{spec['tag']}.replay_score.json"


def _run_log(spec: dict[str, Any]) -> Path:
    return OUT_DIR / f"{spec['tag']}.replay_run.log"


def _capture(spec: dict[str, Any], rerun: bool) -> str:
    """Ensure run B's captured fixes exist for this pair. Cache hit (replay.json
    present, not --rerun) replays NOTHING; else shells out to the two real drivers,
    strictly serial (the caller loops one pair at a time; the LCM bus is exclusive).
    Returns a status string."""
    replay_json = _replay_json(spec)
    if not spec["premap_present"]:
        return f"skipped: premap missing ({spec['premap_abs']})"
    if not spec["marker_present"]:
        return f"skipped: no marker survey JSON ({spec['marker_yaml_abs']})"
    if replay_json.exists() and not rerun:
        return "cached"

    replay_args = [
        spec["replay_recording"], "--premap", spec["premap_abs"], "--tag", spec["tag"],
    ]
    for ov in spec["overrides"]:
        replay_args += ["-o", ov]
    rc_replay = _run(_uv(REPLAY_BENCH, *replay_args))
    if rc_replay != 0:
        print(f"  WARN: replay_bench exit {rc_replay} (report may still use a replay.json)")
    # Score against run B's own PGO truth (raw / unaligned; eval_module applies the
    # held-out map_A->map_B alignment on top). Best-effort: a missing score file just
    # leaves med_err at `-`.
    rc_score = _run(_uv(SCORE_REPLAY, spec["replay_recording"], "--tag", spec["tag"]))
    if rc_score != 0:
        print(f"  WARN: score_replay exit {rc_score} (report continues without per-fix truth)")
    return "ok" if replay_json.exists() else "error: no replay.json produced"


def _report(spec: dict[str, Any]) -> dict[str, Any] | None:
    """Run the shared eval_module report on this pair's captured fixes. Returns its
    stats dict, or None if the capture is missing."""
    from dimos.mapping.relocalization.eval_module import run_offline_report

    replay_json = _replay_json(spec)
    if not replay_json.exists():
        return None
    score_json = _score_json(spec)
    run_log = _run_log(spec)
    markers_b = Path(spec["markers_map_b_abs"]) if spec["markers_map_b_abs"] else None
    result = run_offline_report(
        replay_json=replay_json,
        recording_db=DIMOS_ROOT / "data" / f"{spec['replay_recording']}.db",
        marker_map=Path(spec["marker_yaml_abs"]),
        out_dir=EVAL_DIR,
        key=spec["tag"],
        title=f"{spec['scene']} {spec['premap_recording']}(A) -> {spec['replay_recording']}(B)",
        run_log=run_log if run_log.exists() else None,
        score_json=score_json if score_json.exists() else None,
        markers_map_b=markers_b,
    )
    stats: dict[str, Any] = result["stats"]
    return stats


def _list(specs: list[dict[str, Any]]) -> None:
    print(f"Held-out pair matrix: {len(specs)} pair(s)  "
          f"(blueprint={BLUEPRINT}, gate={SUCCESS_T_M} m / {SUCCESS_R_DEG} deg)\n")
    for i, s in enumerate(specs):
        skip = ""
        if not s["premap_present"]:
            skip = "   [WILL SKIP: premap absent]"
        elif not s["marker_present"]:
            skip = "   [WILL SKIP: marker survey absent]"
        marker = s["marker_json_abs"] or f"(ABSENT) {s['marker_yaml_abs']}"
        mb = s["markers_map_b_abs"] or "None (med_err held out until run B is surveyed)"
        print(f"[{i}] tag={s['tag']}  scene={s['scene']}{skip}")
        print(f"     A (premap/marker) = {s['premap_recording']}")
        print(f"     B (replay)        = {s['replay_recording']}")
        print(f"     premap            = {s['premap_abs']}{'' if s['premap_present'] else '   (ABSENT)'}")
        print(f"     marker (A, json)  = {marker}")
        print(f"     markers_map_b     = {mb}")
        print(f"     overrides         = {' '.join(s['overrides'])}")
    print("\nHELD-OUT: premap + marker survey are run A; the replay is run B of the same "
          "scene (never the same recording). med_err is Umeyama-aligned (map_A->map_B) "
          "when run B's markers are surveyed, else printed as `-`.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rerun", action="store_true",
                    help="ignore cached captures; re-run replay_bench + score_replay per pair")
    ap.add_argument("--only", metavar="SCENE", default=None,
                    help="run only pairs whose scene/recording name CONTAINS this substring")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="print the pair matrix and exit (run nothing)")
    a = ap.parse_args()

    specs = _build_matrix()
    if a.only:
        specs = [s for s in specs if a.only in s["scene"] or a.only in s["replay_recording"]]
        if not specs:
            raise SystemExit(f"--only {a.only!r} matched no pair in the matrix")

    if a.list_only:
        _list(specs)
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for s in specs:  # strictly serial: the LCM bus is exclusive
        print(f"\n[eval] {s['tag']}  A={s['premap_recording']} -> B={s['replay_recording']}")
        status = _capture(s, a.rerun)
        print(f"  capture: {status}")
        stats = _report(s) if not status.startswith("skipped") else None
        rows.append({"tag": s["tag"], "scene": s["scene"], "status": status, "stats": stats})

    out = {
        "provenance": {
            "blueprint": BLUEPRINT,
            "success_gate": {"err_t_m": SUCCESS_T_M, "err_r_deg": SUCCESS_R_DEG},
            "git_rev_trial": _git_rev(TRIAL_ROOT),
            "git_rev_dimos": _git_rev(DIMOS_ROOT),
            "harness": "trial/harness/eval.py",
            "drivers": ["trial/harness/replay_bench.py", "trial/harness/score_replay.py"],
            "eval_module": "dimos/mapping/relocalization/eval_module.py",
            "held_out": True,
        },
        "matrix": specs,
        "rows": rows,
    }
    out_path = EVAL_DIR / "eval.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n[eval] wrote {out_path}  ({len(rows)} pair(s))")
    if not any(r["stats"] for r in rows):
        print("[eval] no pair produced a report (captures missing?).", file=sys.stderr)


if __name__ == "__main__":
    main()
