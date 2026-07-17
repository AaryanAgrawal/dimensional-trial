#!/usr/bin/env python3
"""Unit tests for confidence.py — hand-computed expectations, no randomness."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from confidence import (
    auroc,
    expected_calibration_error,
    operating_point,
    reliability_bins,
    risk_coverage_curve,
)

# 8 attempts, hand-checkable: high-confidence ones mostly correct, one
# high-confidence bust (the dangerous case), low-confidence mixed.
CONF = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])
SUCC = np.array([True, True, False, True, True, False, True, False])


def test_operating_point_at_0_55() -> None:
    # accepted: 0.9 0.8 0.7 0.6 -> 4; false accepts: 0.7 -> 1
    p = operating_point(CONF, SUCC, 0.55)
    assert p.n_accepted == 4
    assert p.coverage == pytest.approx(0.5)
    assert p.n_false_accepts == 1
    assert p.risk == pytest.approx(0.25)
    # correct total = 5, missed correct: 0.5, 0.3 -> 2
    assert p.n_missed == 2
    assert p.miss_rate == pytest.approx(2 / 5)


def test_operating_point_accept_all_and_none() -> None:
    all_in = operating_point(CONF, SUCC, 0.0)
    assert all_in.coverage == 1.0
    assert all_in.risk == pytest.approx(3 / 8)  # global failure rate
    none_in = operating_point(CONF, SUCC, 1.1)
    assert none_in.n_accepted == 0
    assert none_in.risk == 0.0
    assert none_in.miss_rate == 1.0


def test_risk_coverage_monotone_coverage_and_best_threshold() -> None:
    rc = risk_coverage_curve(CONF, SUCC)
    covs = [p.coverage for p in rc.points]
    assert covs == sorted(covs)  # strictest gate first -> coverage rises
    assert len(rc.points) == 8  # one per distinct confidence
    # risk <= 0.25: candidates are thresholds 0.9 (risk 0), 0.8 (0), 0.6
    # (0.25), 0.5 (0.2); loosest = 0.5 with coverage 5/8. (0.7 has risk 1/3.)
    best = rc.best_threshold(max_risk=0.25)
    assert best is not None
    assert best.threshold == pytest.approx(0.5)
    assert best.coverage == pytest.approx(5 / 8)
    # impossible risk bar -> None, not a made-up threshold
    perfect = rc.best_threshold(max_risk=-0.01)
    assert perfect is None
    assert 0.0 <= rc.aurc <= 1.0


def test_auroc_perfect_inverted_and_known_value() -> None:
    c = np.array([0.1, 0.2, 0.8, 0.9])
    s = np.array([False, False, True, True])
    assert auroc(c, s) == pytest.approx(1.0)
    assert auroc(c, ~s) == pytest.approx(0.0)
    # hand value for the shared fixture: pos ranks among 8 values
    # pairs: pos={0.9,.8,.6,.5,.3} neg={0.7,.4,.2}; wins: count conf_pos>conf_neg
    # 0.9>all3, 0.8>all3, 0.6>{0.4,0.2}, 0.5>{0.4,0.2}, 0.3>{0.2} = 11 of 15
    assert auroc(CONF, SUCC) == pytest.approx(11 / 15)


def test_auroc_single_class_is_nan() -> None:
    assert np.isnan(auroc(np.array([0.5, 0.6]), np.array([True, True])))


def test_auroc_ties_get_half_credit() -> None:
    c = np.array([0.5, 0.5])
    s = np.array([True, False])
    assert auroc(c, s) == pytest.approx(0.5)


def test_reliability_bins_and_ece() -> None:
    # two populated bins: [0.0,0.5) holds 0.4,0.3,0.2 (1 of 3 correct);
    # [0.5,1.0] holds the rest (4 of 5 correct)
    bins = reliability_bins(CONF, SUCC, n_bins=2)
    assert len(bins) == 2
    lo, hi = bins
    assert lo["count"] == 3 and lo["empirical_success"] == pytest.approx(1 / 3)
    assert hi["count"] == 5 and hi["empirical_success"] == pytest.approx(4 / 5)
    ece = expected_calibration_error(CONF, SUCC, n_bins=2)
    exp = (abs(0.3 - 1 / 3) * 3 + abs(0.7 - 0.8) * 5) / 8
    assert ece == pytest.approx(exp)


def test_validation_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        operating_point(np.array([]), np.array([]), 0.5)
    with pytest.raises(ValueError):
        operating_point(np.array([0.5, np.nan]), np.array([True, False]), 0.5)
    with pytest.raises(ValueError):
        auroc(np.array([0.5]), np.array([True, False]))
