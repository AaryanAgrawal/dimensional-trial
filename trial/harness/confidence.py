#!/usr/bin/env python3
"""Confidence-quality analytics for relocalization results.

The question this module answers is the one lesh's #2137 next-steps named and
nobody has measured: does the published fitness/confidence actually PREDICT
whether a relocalization answer is correct? Pure numpy — no dimos imports —
so it is unit-testable without a robot, a recording, or open3d.

Inputs everywhere: two aligned arrays over N relocalization attempts,
  confidence[i]  — the score published with attempt i (e.g. wall-ICP fitness)
  success[i]     — bool, attempt i was actually correct (err < 1 m AND < 15°,
                   judged against truth the caller chose and must label)

Industry grounding (all primary-sourced in WORKSPACE.md §7):
- Selective prediction / risk-coverage (El-Yaniv & Wiener, JMLR 2010): an
  accept-gate is a selective classifier; sweeping the gate maps the full
  coverage-vs-risk tradeoff. dimos's fitness_threshold (code 0.45, docs 0.6,
  provenance unknown) is one undocumented point on this curve — the curve
  makes the choice a measurement instead of folklore.
- Calibration / reliability + ECE (Guo et al., ICML 2017): production AMRs
  act on score thresholds (Omron: localization score <70% -> operator
  action); a threshold is only meaningful if the score is calibrated.
- AUROC: threshold-free ranking quality of the score as a success predictor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "OperatingPoint",
    "RiskCoverageCurve",
    "auroc",
    "expected_calibration_error",
    "operating_point",
    "reliability_bins",
    "risk_coverage_curve",
]


@dataclass
class OperatingPoint:
    """What accepting at ``threshold`` would have done on this data.

    All rates are fractions in [0, 1]. ``n_*`` are raw counts so small-N
    results can't hide behind a rate. ``false_accept_rate`` is the one that
    drives a robot into a wall: of everything the gate ACCEPTED, how much was
    actually wrong (risk, in selective-prediction terms). ``miss_rate``: of
    everything actually correct, how much the gate threw away (wasted
    compute + slower time-to-fix).
    """

    threshold: float
    coverage: float  # fraction of attempts accepted
    risk: float  # fraction of ACCEPTED attempts that were wrong
    miss_rate: float  # fraction of CORRECT attempts rejected
    n_accepted: int
    n_false_accepts: int
    n_missed: int
    n_total: int


@dataclass
class RiskCoverageCurve:
    """Risk-coverage curve: one OperatingPoint per distinct threshold.

    ``points`` is ordered by descending threshold (strictest gate first,
    coverage rises along the list). ``aurc`` is the area under the
    risk-coverage curve (lower = better; 0 means every accepted answer was
    correct at every coverage level), integrated over coverage by the
    trapezoid rule.
    """

    points: list[OperatingPoint] = field(default_factory=list)
    aurc: float = float("nan")

    def best_threshold(self, max_risk: float) -> OperatingPoint | None:
        """Loosest gate (max coverage) whose measured risk <= max_risk.

        None if no threshold meets the risk bar — itself a finding: the
        confidence signal cannot be gated to that safety level on this data.
        """
        ok = [p for p in self.points if p.risk <= max_risk and p.n_accepted > 0]
        return max(ok, key=lambda p: p.coverage) if ok else None


def _validate(confidence: np.ndarray, success: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    confidence = np.asarray(confidence, dtype=np.float64)
    success = np.asarray(success, dtype=bool)
    if confidence.shape != success.shape or confidence.ndim != 1:
        raise ValueError(
            f"confidence {confidence.shape} and success {success.shape} must be equal-length 1-D"
        )
    if confidence.size == 0:
        raise ValueError("empty input: no relocalization attempts to analyze")
    if np.any(~np.isfinite(confidence)):
        raise ValueError("confidence contains NaN/inf — upstream must filter failed attempts")
    return confidence, success


def operating_point(
    confidence: np.ndarray, success: np.ndarray, threshold: float
) -> OperatingPoint:
    """Evaluate one accept-gate (accept iff confidence >= threshold)."""
    confidence, success = _validate(confidence, success)
    accepted = confidence >= threshold
    n_acc = int(accepted.sum())
    n_false = int((accepted & ~success).sum())
    n_correct = int(success.sum())
    n_missed = int((~accepted & success).sum())
    return OperatingPoint(
        threshold=float(threshold),
        coverage=n_acc / confidence.size,
        risk=(n_false / n_acc) if n_acc else 0.0,
        miss_rate=(n_missed / n_correct) if n_correct else 0.0,
        n_accepted=n_acc,
        n_false_accepts=n_false,
        n_missed=n_missed,
        n_total=int(confidence.size),
    )


def risk_coverage_curve(confidence: np.ndarray, success: np.ndarray) -> RiskCoverageCurve:
    """Sweep the accept-gate over every distinct confidence value."""
    confidence, success = _validate(confidence, success)
    thresholds = np.unique(confidence)[::-1]  # strictest (highest) first
    points = [operating_point(confidence, success, float(t)) for t in thresholds]
    # AURC over coverage; prepend the (0 coverage, 0 risk) origin so a curve
    # with a single point still integrates.
    cov = np.array([0.0] + [p.coverage for p in points])
    risk = np.array([points[0].risk if points else 0.0] + [p.risk for p in points])
    aurc = float(np.trapezoid(risk, cov)) if cov[-1] > 0 else float("nan")
    return RiskCoverageCurve(points=points, aurc=aurc)


def auroc(confidence: np.ndarray, success: np.ndarray) -> float:
    """AUROC of confidence as a ranker of success, via the Mann-Whitney U
    statistic (ties get half credit). NaN when only one class is present —
    an honest "cannot be measured on this data", never a fake 0.5.
    """
    confidence, success = _validate(confidence, success)
    pos = confidence[success]
    neg = confidence[~success]
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    # rankdata via argsort double-argsort, average ranks for ties
    allc = np.concatenate([pos, neg])
    order = np.argsort(allc, kind="mergesort")
    ranks = np.empty_like(allc)
    ranks[order] = np.arange(1, allc.size + 1, dtype=np.float64)
    # average tied ranks
    sorted_vals = allc[order]
    i = 0
    while i < sorted_vals.size:
        j = i
        while j + 1 < sorted_vals.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = ranks[order[i : j + 1]].mean()
        i = j + 1
    u = ranks[: pos.size].sum() - pos.size * (pos.size + 1) / 2.0
    return float(u / (pos.size * neg.size))


def reliability_bins(
    confidence: np.ndarray,
    success: np.ndarray,
    n_bins: int = 10,
    lo: float = 0.0,
    hi: float = 1.0,
) -> list[dict[str, float]]:
    """Equal-width reliability bins over [lo, hi].

    Each dict: bin_lo, bin_hi, mean_confidence, empirical_success, count.
    Empty bins are omitted (a bin with no data is no evidence, not zero).
    """
    confidence, success = _validate(confidence, success)
    edges = np.linspace(lo, hi, n_bins + 1)
    out: list[dict[str, float]] = []
    for k in range(n_bins):
        in_bin = (confidence >= edges[k]) & (
            (confidence < edges[k + 1]) if k < n_bins - 1 else (confidence <= edges[k + 1])
        )
        n = int(in_bin.sum())
        if n == 0:
            continue
        out.append(
            {
                "bin_lo": float(edges[k]),
                "bin_hi": float(edges[k + 1]),
                "mean_confidence": float(confidence[in_bin].mean()),
                "empirical_success": float(success[in_bin].mean()),
                "count": float(n),
            }
        )
    return out


def expected_calibration_error(
    confidence: np.ndarray,
    success: np.ndarray,
    n_bins: int = 10,
    lo: float = 0.0,
    hi: float = 1.0,
) -> float:
    """ECE (Guo et al. 2017): count-weighted mean |confidence - accuracy|
    over reliability bins. Caveat for fitness scores: ICP fitness is an
    inlier ratio, not a probability — a high ECE says "do not read fitness
    0.7 as 70% sure", which is exactly the claim worth testing before anyone
    treats the universal confidence reading as a probability.
    """
    bins = reliability_bins(confidence, success, n_bins=n_bins, lo=lo, hi=hi)
    if not bins:
        return float("nan")
    total = sum(b["count"] for b in bins)
    return float(
        sum(abs(b["mean_confidence"] - b["empirical_success"]) * b["count"] for b in bins) / total
    )
