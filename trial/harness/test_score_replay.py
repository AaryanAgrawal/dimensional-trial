#!/usr/bin/env python3
"""Unit tests for score_replay.py's PURE scoring/reporting logic -- no replay, no
PGO graph build, no DB. The truth oracle (_load_graph -> PGO.correction_at) is the
one thing stubbed: it is score_replay's INPUT (the silver truth), not logic under
test, so a known transform stands in for it. Everything graded is score_replay's
own: the geodesic angle, the 1 m / 15 deg success gate, the submap-size strata
bins, and the 0-accept print guard.

Deterministic: scipy Rotation by fixed angles + numpy default_rng; no wall-clock,
no randomness. Run:
  uv run --project /home/dimos/dimensional-trial/dimos \
      python -m pytest trial/harness/test_score_replay.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent))  # score_replay lives here
import score_replay  # noqa: E402


def _rt(rot: np.ndarray, t: tuple[float, float, float]) -> np.ndarray:
    """A 4x4 rigid transform from a rotation and translation."""
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = t
    return m


# --------------------------------------------------------------------------- #
# _geodesic_deg: known rotations -> known degrees
# --------------------------------------------------------------------------- #
def test_geodesic_identical_rotations_is_zero() -> None:
    """Ra == Rb -> 0 deg; the clip guard must not push an exactly-1 cosine off 0."""
    r = Rotation.from_euler("xyz", [11.0, -22.0, 33.0], degrees=True).as_matrix()
    assert abs(score_replay._geodesic_deg(r, r) - 0.0) < 1e-9


def test_geodesic_pure_yaw_recovers_the_angle() -> None:
    """I vs Rz(30 deg) -> exactly 30 deg (angle of Ra^T Rb)."""
    rz = Rotation.from_euler("z", 30.0, degrees=True).as_matrix()
    assert abs(score_replay._geodesic_deg(np.eye(3), rz) - 30.0) < 1e-9


def test_geodesic_arbitrary_axis_recovers_the_angle() -> None:
    """A 47 deg turn about an arbitrary (normalised) axis reads back 47 deg."""
    axis = np.array([1.0, -2.0, 0.5])
    axis = axis / np.linalg.norm(axis)
    rb = Rotation.from_rotvec(np.radians(47.0) * axis).as_matrix()
    assert abs(score_replay._geodesic_deg(np.eye(3), rb) - 47.0) < 1e-9


def test_geodesic_opposite_is_180_clip_guards_acos() -> None:
    """Rz(180) -> 180 deg; trace = -1 pushes cosine to exactly -1, clip must hold."""
    rz = Rotation.from_euler("z", 180.0, degrees=True).as_matrix()
    assert abs(score_replay._geodesic_deg(np.eye(3), rz) - 180.0) < 1e-9


# --------------------------------------------------------------------------- #
# _score_fix: the success gate err_t < 1.0 m AND err_r < 15 deg
# --------------------------------------------------------------------------- #
def _fix_from_est(est: np.ndarray) -> dict:
    """A captured fix whose recovered map_T_world equals `est` (world_map_fix is
    world_T_map = inv(est), the shape score_replay inverts back)."""
    return {"world_map_fix": np.linalg.inv(est).tolist()}


def test_score_fix_zero_error_succeeds() -> None:
    truth = np.eye(4)
    s = score_replay._score_fix(_fix_from_est(np.eye(4)), truth)
    assert s["err_t_m"] == 0.0 and s["err_r_deg"] == 0.0 and s["success"] is True


def test_score_fix_just_inside_both_gates_succeeds() -> None:
    """0.9 m + 14 deg -> both strictly under -> success."""
    truth = np.eye(4)
    est = _rt(Rotation.from_euler("z", 14.0, degrees=True).as_matrix(), (0.9, 0.0, 0.0))
    s = score_replay._score_fix(_fix_from_est(est), truth)
    assert abs(s["err_t_m"] - 0.9) < 1e-9
    assert abs(s["err_r_deg"] - 14.0) < 1e-9
    assert s["success"] is True


def test_score_fix_translation_exactly_at_gate_fails() -> None:
    """err_t == 1.0 m is NOT < 1.0 -> fail even with zero rotation error."""
    truth = np.eye(4)
    est = _rt(np.eye(3), (1.0, 0.0, 0.0))
    s = score_replay._score_fix(_fix_from_est(est), truth)
    assert abs(s["err_t_m"] - 1.0) < 1e-9 and s["success"] is False


def test_score_fix_rotation_exactly_at_gate_fails() -> None:
    """err_r == 15 deg is NOT < 15 -> fail even with zero translation error."""
    truth = np.eye(4)
    est = _rt(Rotation.from_euler("z", 15.0, degrees=True).as_matrix(), (0.0, 0.0, 0.0))
    s = score_replay._score_fix(_fix_from_est(est), truth)
    assert abs(s["err_r_deg"] - 15.0) < 1e-9 and s["success"] is False


def test_score_fix_translation_over_gate_fails() -> None:
    truth = np.eye(4)
    est = _rt(np.eye(3), (1.5, 0.0, 0.0))
    s = score_replay._score_fix(_fix_from_est(est), truth)
    assert s["err_t_m"] > 1.0 and s["success"] is False


def test_score_fix_no_rotation_captured_is_undetermined() -> None:
    """world_map_fix None -> translation-only via reloc_t; err_r and success left
    None (undetermined), never a fabricated pass/fail."""
    truth = _rt(np.eye(3), (2.0, 0.0, 0.0))
    fix = {"world_map_fix": None, "reloc_t": [2.3, 0.0, 0.0]}  # map_T_world translation
    s = score_replay._score_fix(fix, truth)
    assert abs(s["err_t_m"] - 0.3) < 1e-9
    assert s["err_r_deg"] is None and s["success"] is None


# --------------------------------------------------------------------------- #
# _summarize: None-safe over an empty band (the 0-accept case)
# --------------------------------------------------------------------------- #
def test_summarize_empty_band_is_none_safe() -> None:
    """No rows -> every derived number is None, counts are 0, nothing raises."""
    s = score_replay._summarize([])
    assert s["n"] == 0 and s["n_judged"] == 0 and s["n_success"] == 0
    assert s["success_rate"] is None
    assert s["median_err_t_m_on_success"] is None
    assert s["min_err_t_m"] is None


def test_summarize_counts_and_medians() -> None:
    """Two judged (one success), one undetermined -> rate over judged only; median
    err on the success rows only."""
    rows = [
        {"err_t_m": 0.2, "err_r_deg": 3.0, "success": True},
        {"err_t_m": 4.0, "err_r_deg": 40.0, "success": False},
        {"err_t_m": 0.9, "err_r_deg": None, "success": None},  # undetermined
    ]
    s = score_replay._summarize(rows)
    assert s["n"] == 3 and s["n_judged"] == 2 and s["n_success"] == 1
    assert s["success_rate"] == 0.5
    assert s["median_err_t_m_on_success"] == 0.2
    assert s["min_err_t_m"] == 0.2


# --------------------------------------------------------------------------- #
# NPTS_BINS: the submap-size strata partition (contiguous, disjoint, complete)
# --------------------------------------------------------------------------- #
def _band_index(n_pts: float) -> int:
    """The single band n_pts falls in, by score_replay's own lo <= n < hi rule."""
    bins = score_replay.NPTS_BINS
    hits = [i for i, (lo, hi) in enumerate(zip(bins[:-1], bins[1:])) if lo <= n_pts < hi]
    assert len(hits) == 1, f"n_pts={n_pts} landed in {len(hits)} bands, want exactly 1"
    return hits[0]


def test_npts_bins_partition_boundaries() -> None:
    """Every n_pts lands in exactly one band; the bin EDGES (60k/80k/100k) belong to
    the upper band (half-open [lo, hi)). This is the strata contract score_replay's
    by_submap_size table is built on."""
    assert score_replay.NPTS_BINS[0] == 0 and score_replay.NPTS_BINS[-1] == np.inf
    assert list(score_replay.NPTS_BINS) == sorted(score_replay.NPTS_BINS)  # ascending
    assert _band_index(0) == 0
    assert _band_index(59_999) == 0
    assert _band_index(60_000) == 1  # edge -> upper band
    assert _band_index(79_999) == 1
    assert _band_index(80_000) == 2
    assert _band_index(99_999) == 2
    assert _band_index(100_000) == 3
    assert _band_index(250_000) == 3  # into the inf band


# --------------------------------------------------------------------------- #
# main(): bucketing loop + 0-accept print guard, with the PGO oracle stubbed
# --------------------------------------------------------------------------- #
class _FakeCorrection:
    def __init__(self, mat: np.ndarray) -> None:
        self._mat = mat

    def to_matrix(self) -> np.ndarray:
        return self._mat


class _FakeGraph:
    """Stands in for the recording's PGO graph: correction_at(ts) -> a fixed
    map_T_world (identity here, so a captured est == truth reads as zero error)."""

    def correction_at(self, ts: float) -> _FakeCorrection:
        return _FakeCorrection(np.eye(4))


def _write_replay(path: Path, fixes: list[dict]) -> None:
    path.write_text(json.dumps({
        "meta": {"recording_first_ts": 1000.0, "premap": "fake_premap", "n_rejects": 3},
        "fixes": fixes,
    }))


def _make_fix(n_pts: int, ts: float) -> dict:
    """A perfect fix (est == identity == truth) at a given submap size + timestamp."""
    return {
        "ts": ts, "n_pts": n_pts, "fitness": 0.8, "source": "ransac",
        "rot_source": "tf", "world_map_fix": np.eye(4).tolist(), "reloc_t": [0.0, 0.0, 0.0],
    }


def test_main_buckets_fixes_into_strata(tmp_path, monkeypatch, capsys) -> None:
    """One fix per band (50k/70k/90k/120k) -> by_submap_size reports n=1 in each of
    the four strata, and the overall success count is 4 (all perfect)."""
    monkeypatch.setattr(score_replay, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(score_replay, "_load_graph", lambda rec, stream: (_FakeGraph(), "stub"))
    fixes = [
        _make_fix(50_000, 1000.0), _make_fix(70_000, 1001.0),
        _make_fix(90_000, 1002.0), _make_fix(120_000, 1003.0),
    ]
    _write_replay(tmp_path / "vt.replay.json", fixes)
    monkeypatch.setattr(sys, "argv", ["score_replay.py", "vt"])

    try:
        score_replay.main()
    finally:
        out = capsys.readouterr().out

    report = json.loads((tmp_path / "vt.replay_score.json").read_text())
    assert [b["n"] for b in report["by_submap_size"]] == [1, 1, 1, 1]
    assert report["overall"]["n_success"] == 4 and report["overall"]["n"] == 4
    assert report["n_with_rotation"] == 4
    # first accept is at ts == recording_first_ts -> 0.0 s since drive start
    assert report["first_accept_t_since_drive_start_s"] == 0.0
    assert "first_accept=0.0s" in out


def test_main_zero_accept_guard_does_not_crash_print(tmp_path, monkeypatch, capsys) -> None:
    """No fixes -> first_accept is None; the print guard renders it as '-' instead of
    crashing on f'{None:.1f}'. Every stratum is empty and reports n=0."""
    monkeypatch.setattr(score_replay, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(score_replay, "_load_graph", lambda rec, stream: (_FakeGraph(), "stub"))
    _write_replay(tmp_path / "empty.replay.json", [])
    monkeypatch.setattr(sys, "argv", ["score_replay.py", "empty"])

    try:
        score_replay.main()  # must not raise
    finally:
        out = capsys.readouterr().out

    report = json.loads((tmp_path / "empty.replay_score.json").read_text())
    assert report["first_accept_t_since_drive_start_s"] is None
    assert report["n_accepts"] == 0
    assert [b["n"] for b in report["by_submap_size"]] == [0, 0, 0, 0]
    assert "first_accept=-" in out  # the guard fired
