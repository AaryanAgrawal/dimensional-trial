# F -- bench.py lifecycle: start -> SIGINT (via `bench.py stop` from a second
# process) -> score -> CSV append -> RESULTS.md regeneration, exercised in a
# throwaway temp dir with a MOCK metrics_logger (same approach as bench.py's
# earlier pre-Monday verification), so:
#   - no dimos venv / transport stack is needed (the mock satisfies bench.py's
#     lazy `from metrics_logger import MetricsLogger`),
#   - the REAL trial/results/{benchmarks.csv,RESULTS.md} are never touched
#     (bench.py resolves every output path relative to its own file location,
#     so a temp-dir copy writes only inside the temp dir).
#
# The mock's "run" copies the holdout-referee_marker.jsonl fixture as the
# run's log, which makes the scored row's holdout numbers cross-checkable
# against A1's known truth (0.0014 m / 1.0 deg).
#
# Pure stdlib; run from anywhere:
#   cd dimos && uv run pytest ../trial/scripts/tests/test_bench_lifecycle.py
#
# Added by the verification battery (2026-07-15); tests only -- bench.py and
# report.py are COPIED into the temp dir, never modified.

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
FIXTURE = SCRIPTS_DIR / "out" / "fixtures" / "holdout-referee_marker.jsonl"

MOCK_METRICS_LOGGER = '''\
"""Mock of metrics_logger.MetricsLogger for bench.py lifecycle testing.

Same constructor/run/summary/close surface bench.py uses; no dimos import.
run() blocks until SIGINT (exactly like the real logger's Ctrl+C path) after
copying a known fixture as the run's log output.
"""
import shutil
import time
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixture.jsonl"


class MetricsLogger:
    def __init__(self, out_path, dimos_log_path=None, *, holdout_tag=None,
                 holdout_marker_length_m=0.10,
                 holdout_aruco_dictionary="DICT_APRILTAG_36h11"):
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE, self.out_path)
        self._holdout_tag = holdout_tag

    def run(self, duration=None):
        start = time.monotonic()
        try:
            while duration is None or (time.monotonic() - start) < duration:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("mock metrics_logger: SIGINT received, stopping...")

    def summary(self):
        return {"mock": True, "holdout_tag": self._holdout_tag}

    def close(self):
        pass
'''


@pytest.fixture()
def bench_tmp(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPTS_DIR / "bench.py", scripts / "bench.py")
    shutil.copy(SCRIPTS_DIR / "report.py", scripts / "report.py")
    shutil.copy(FIXTURE, scripts / "fixture.jsonl")
    (scripts / "metrics_logger.py").write_text(MOCK_METRICS_LOGGER)
    return scripts


def _wait_for(predicate, timeout_s: float = 15.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_start_stop_csv_results_cycle(bench_tmp: Path) -> None:
    bench = bench_tmp / "bench.py"
    state_file = bench_tmp / "out" / ".bench_active.json"
    results_dir = bench_tmp.parent / "results"

    start = subprocess.Popen(
        [
            sys.executable,
            str(bench),
            "start",
            "--mode",
            "marker",
            "--route",
            "holdout-referee",
            "--notes",
            "lifecycle test",
            "--holdout-tag",
            "42",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for(state_file.exists, what="active-run state file")

        # a second `start` while one is active must refuse
        second = subprocess.run(
            [sys.executable, str(bench), "start", "--mode", "odom", "--route", "x"],
            capture_output=True,
            text=True,
        )
        assert second.returncode != 0
        assert "already active" in second.stderr

        # `stop` from a second process: SIGINT -> finalize exactly once
        stop = subprocess.run(
            [sys.executable, str(bench), "stop"], capture_output=True, text=True, timeout=60
        )
        assert stop.returncode == 0, stop.stderr
        assert "run stopped, scored, and appended" in stop.stdout

        out, _ = start.communicate(timeout=30)
        assert start.returncode == 0, out
        assert "scoring run..." in out and "appended row" in out
    finally:
        if start.poll() is None:
            start.kill()

    # state cleared; CSV + RESULTS.md regenerated in the TEMP tree only
    assert not state_file.exists()
    rows = list(csv.DictReader((results_dir / "benchmarks.csv").open()))
    assert len(rows) == 1
    row = rows[0]
    assert row["route"] == "holdout-referee" and row["mode"] == "marker"
    assert int(row["odom_ticks"]) == 41 and int(row["corrected_ticks"]) == 41
    # cross-check with A1's known fixture truth
    assert row["holdout_closure_error_m"] == "0.0014"
    assert row["holdout_closure_error_deg"] == "1.0"
    assert row["holdout_claim_source"] == "corrected_pose"

    results_md = (results_dir / "RESULTS.md").read_text()
    assert "holdout-referee" in results_md and "0.001m / 1.00deg" in results_md

    # `stop` again with nothing active must report so, not crash
    idle_stop = subprocess.run(
        [sys.executable, str(bench), "stop"], capture_output=True, text=True
    )
    assert idle_stop.returncode != 0
    assert "no active run" in idle_stop.stderr


def test_report_verb_regenerates_from_csv(bench_tmp: Path) -> None:
    bench = bench_tmp / "bench.py"
    results_dir = bench_tmp.parent / "results"
    # no CSV yet -> placeholder RESULTS.md
    rep = subprocess.run(
        [sys.executable, str(bench), "report"], capture_output=True, text=True
    )
    assert rep.returncode == 0
    assert "No runs yet" in (results_dir / "RESULTS.md").read_text()
