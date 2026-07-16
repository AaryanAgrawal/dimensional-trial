# A1 -- holdout fixture verification, fresh.
#
# Recomputes the holdout-referee metrics from the synthetic JSONL fixtures at
# trial/scripts/out/fixtures/holdout-referee_{odom,marker}.jsonl THROUGH
# bench.py's own computation path (compute_run_metrics -> report.py's
# load_jsonl/by_type + bench's _holdout_referee_metrics), and asserts the
# known truth those fixtures encode:
#   odom-claim run:   holdout closure error 0.2959 m / 10.0 deg (claim = odom_pose)
#   marker-claim run: holdout closure error 0.0014 m /  1.0 deg (claim = corrected_pose)
#
# Pure stdlib + bench.py/report.py -- no dimos import, runs anywhere:
#   cd dimos && uv run pytest ../trial/scripts/tests/test_holdout_fixture_metrics.py
#
# Added by the verification battery (2026-07-15); tests only, touches no
# component under test.

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = SCRIPTS_DIR / "out" / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
import bench  # noqa: E402

HOLDOUT_TAG = 42  # the marker_id embedded in both fixtures' tag_sighting records


def _metrics(fixture: str, mode: str) -> dict:
    return bench.compute_run_metrics(
        FIXTURES_DIR / fixture,
        mode,
        "holdout-referee",
        "fixture verification",
        f"fixture__{mode}",
        21.0,
        holdout_tag=HOLDOUT_TAG,
    )


def test_odom_fixture_known_numbers() -> None:
    row = _metrics("holdout-referee_odom.jsonl", "odom")
    assert row["holdout_closure_error_m"] == 0.2959, row
    assert row["holdout_closure_error_deg"] == 10.0, row
    # no corrected_pose in this fixture -> the claim must fall back to odom
    assert row["holdout_claim_source"] == "odom_pose", row
    assert row["holdout_readings_start"] == 10 and row["holdout_readings_end"] == 10, row
    assert row["holdout_reason"] is None, row


def test_marker_fixture_known_numbers() -> None:
    row = _metrics("holdout-referee_marker.jsonl", "marker")
    assert row["holdout_closure_error_m"] == 0.0014, row
    assert row["holdout_closure_error_deg"] == 1.0, row
    # corrected_pose present -> it must be the claim source, not odom
    assert row["holdout_claim_source"] == "corrected_pose", row
    assert row["holdout_readings_start"] == 10 and row["holdout_readings_end"] == 10, row
    assert row["holdout_reason"] is None, row


def test_marker_beats_odom_on_same_referee() -> None:
    # The whole point of the referee: same physical fixture motion, the
    # corrected (marker) claim closes ~200x tighter than dead-reckoning.
    odom = _metrics("holdout-referee_odom.jsonl", "odom")
    marker = _metrics("holdout-referee_marker.jsonl", "marker")
    assert marker["holdout_closure_error_m"] < odom["holdout_closure_error_m"] / 100
