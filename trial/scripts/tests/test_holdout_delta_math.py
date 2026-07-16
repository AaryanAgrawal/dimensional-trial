# A3 -- holdout-referee delta math on constructed records.
#
# Builds synthetic runs from first principles (reference measurement A from
# the tag referee + return pose claim B with a KNOWN offset) and asserts
# bench.py's _holdout_referee_metrics reports exactly |claimed - measured|.
#
# The property under test is tolerance-cancellation (benchmark-spec.md §4):
# the referee measures the TRUE start->end displacement, so an IMPERFECT
# physical return (operator stops 30 cm off the tape mark) is measured
# correctly -- a truthful pose estimate scores ~0 error even though the loop
# didn't close physically, and only the *disagreement* between claim and
# referee shows up as error. No taped start==end assumption anywhere.
#
# Pure stdlib + bench.py -- no dimos import:
#   cd dimos && uv run pytest ../trial/scripts/tests/test_holdout_delta_math.py
#
# Added by the verification battery (2026-07-15); tests only.

from __future__ import annotations

import math
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import bench  # noqa: E402

IDENTITY_Q = [0.0, 0.0, 0.0, 1.0]
YAW_10_DEG_Q = [0.0, 0.0, math.sin(math.radians(5.0)), math.cos(math.radians(5.0))]


def _rec(kind: str, ts: float, xyz: tuple[float, float, float], q: list[float] | None = None, **extra: object) -> dict:
    return {
        "type": kind,
        "ts": ts,
        "translation": list(xyz),
        "rotation": list(q or IDENTITY_Q),
        **extra,
    }


def _run(
    measured_offset: tuple[float, float, float],
    claimed_offset: tuple[float, float, float],
    *,
    end_q_measured: list[float] | None = None,
    end_q_claimed: list[float] | None = None,
    window: int = 3,
    n_each_end: int = 3,
    gap_s: float = 60.0,
) -> dict:
    """A start cluster and an end cluster, for both the referee (tag_sighting,
    camera-in-tag-frame) and the claim (odom_pose). The tag sightings move by
    `measured_offset` between clusters; the odom claim moves by
    `claimed_offset`. Referee error must be |
    |claimed_offset| - |measured_offset| | (windows are exact medians here --
    every record in a cluster is identical)."""
    records: list[dict] = []
    for i in range(n_each_end):
        t = i * 0.5
        records.append(_rec("tag_sighting", t, (0.5, 0.0, 1.2), marker_id=42, reprojection_error_px=0.5))
        records.append(_rec("odom_pose", t, (0.0, 0.0, 0.0)))
    for i in range(n_each_end):
        t = gap_s + i * 0.5
        mx, my, mz = measured_offset
        cx, cy, cz = claimed_offset
        records.append(
            _rec(
                "tag_sighting",
                t,
                (0.5 + mx, 0.0 + my, 1.2 + mz),
                end_q_measured,
                marker_id=42,
                reprojection_error_px=0.5,
            )
        )
        records.append(_rec("odom_pose", t, (cx, cy, cz), end_q_claimed))

    odom = [r for r in records if r["type"] == "odom_pose"]
    corrected: list[dict] = []
    return bench._holdout_referee_metrics(records, odom, corrected, window=window)


def test_known_offset_yields_asserted_error() -> None:
    # Referee measures a 0.40 m true displacement; the claim says 0.10 m.
    # Error must be exactly |0.10 - 0.40| = 0.30 m.
    out = _run(measured_offset=(0.4, 0.0, 0.0), claimed_offset=(0.1, 0.0, 0.0))
    assert out["holdout_closure_error_m"] == 0.3, out
    assert out["holdout_closure_error_deg"] == 0.0, out
    assert out["holdout_claim_source"] == "odom_pose", out


def test_tolerance_cancellation_imperfect_return() -> None:
    # The operator returns 30 cm OFF the tape mark (imperfect physical
    # return). A truthful pose stream claims that same 30 cm. The referee
    # must score ~0 error: the imperfection cancels because both sides
    # measure the same real displacement.
    out = _run(measured_offset=(0.3, 0.0, 0.0), claimed_offset=(0.3, 0.0, 0.0))
    assert out["holdout_closure_error_m"] == 0.0, out
    assert out["holdout_closure_error_deg"] == 0.0, out


def test_imperfect_return_with_drifting_claim_still_caught() -> None:
    # Same 30 cm imperfect return, but the pose stream drifted and claims
    # only 5 cm -- the referee must still report the 25 cm disagreement.
    # (Tolerance cancels; drift does not.)
    out = _run(measured_offset=(0.3, 0.0, 0.0), claimed_offset=(0.05, 0.0, 0.0))
    assert out["holdout_closure_error_m"] == 0.25, out


def test_rotation_delta_measured() -> None:
    # Referee sees a 10-deg end-orientation change; the claim says 0 deg.
    # Angular closure error must be 10 deg (translation error 0).
    out = _run(
        measured_offset=(0.0, 0.0, 0.0),
        claimed_offset=(0.0, 0.0, 0.0),
        end_q_measured=YAW_10_DEG_Q,
    )
    assert out["holdout_closure_error_m"] == 0.0, out
    assert out["holdout_closure_error_deg"] == 10.0, out


def test_rotation_tolerance_cancellation() -> None:
    # Both referee and claim see the same 10-deg imperfect end heading ->
    # angular error ~0.
    out = _run(
        measured_offset=(0.0, 0.0, 0.0),
        claimed_offset=(0.0, 0.0, 0.0),
        end_q_measured=YAW_10_DEG_Q,
        end_q_claimed=YAW_10_DEG_Q,
    )
    assert out["holdout_closure_error_deg"] == 0.0, out
