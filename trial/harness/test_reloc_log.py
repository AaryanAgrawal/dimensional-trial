#!/usr/bin/env python3
"""Unit tests for reloc_log.py -- the harness's RelocalizationModule log parser.

Constructed known-truth lines (I wrote the answer, so the asserts are exact
literals), one invariant per test. All three wire formats are graded because all
three are on disk: the QUIET default the module logs today, the VERBOSE/CURRENT
structlog rendering (`verbose_eval_logging=True`), and the LEGACY f-string every
archived capture in out/ is written in.

Deterministic: fixed strings, no RNG, no clock. Run:
  uv run --project /home/dimos/dimensional-trial/dimos \
      python -m pytest trial/harness/test_reloc_log.py -q
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reloc_log  # noqa: E402

# Verbatim from out/hk_village3_bp_runs/run2_lidar_fiducial.log (kwargs alphabetical).
CURRENT_ACCEPT = (
    "08:44:54.015 [inf][pping/relocalization/module.py] relocalize accepted "
    "fitness=0.984 n_pts=51034 published_t_m=[-0.079, -0.025, 0.067] "
    "reloc_t_m=[0.078, 0.025, -0.067] source=ransac tf_from=world tf_to=map "
    "time_cost_s=1.7"
)
# Verbatim shape from out/results_dimos/survey2_heldout.replay_run.log -- note
# source= sits LAST, the field order the old positional regex assumed away.
LEGACY_ACCEPT = (
    "22:11:50.589 [inf][pping/relocalization/module.py] relocalize: fitness=0.756 "
    "time_cost=13.0s n_pts=89003 reloc_t=[-2.467, -8.645, -0.086] "
    "TF 'world' -> 'map' published_t=[-8.849, -1.587, 0.073] source=fiducial"
)
# The quiet default (verbose_eval_logging=False): four kwargs, no pose fields.
# Alphabetical again -- fitness, margin, source, time_cost_s.
QUIET_ACCEPT = (
    "08:44:54.015 [inf][pping/relocalization/module.py] relocalize accepted "
    "fitness=0.873 margin=0.178 source=fiducial time_cost_s=1.4"
)
CURRENT_JSONL = (
    '{"source": "fiducial", "fitness": 0.87, "time_cost_s": 2.0, "n_pts": 100, '
    '"reloc_t_m": [1.0, 2.0, 3.0], "published_t_m": [-1.0, -2.0, -3.0], '
    '"tf_from": "world", "tf_to": "map", "event": "relocalize accepted", '
    '"level": "info", "timestamp": "2026-07-22T08:44:54.015000Z"}'
)


def test_current_console_accept_parses_every_field() -> None:
    """A current-format console accept yields all seven fields, units intact."""
    (acc,) = reloc_log.parse_accepts(CURRENT_ACCEPT)
    assert acc.tod_s == 8 * 3600 + 44 * 60 + 54.015
    assert acc.fitness == 0.984
    assert acc.time_cost_s == 1.7
    assert acc.n_pts == 51034
    assert acc.published_t_m == [-0.079, -0.025, 0.067]
    assert acc.reloc_t_m == [0.078, 0.025, -0.067]
    assert acc.source == "ransac"


def test_legacy_console_accept_reports_the_trailing_source() -> None:
    """The regression that made `fiducial won 0` an artifact: source= is the LAST
    field on a legacy line, and must be read as fiducial, never defaulted."""
    (acc,) = reloc_log.parse_accepts(LEGACY_ACCEPT)
    assert acc.source == "fiducial"
    assert acc.fitness == 0.756
    assert acc.time_cost_s == 13.0  # `time_cost=13.0s` -- the unit suffix is not a digit
    assert acc.published_t_m == [-8.849, -1.587, 0.073]


def test_quiet_accept_parses_with_a_margin_and_no_position() -> None:
    """A quiet-mode accept is a record, not a parse failure: fitness/source/margin/
    time_cost_s survive, and the absent pose fields read as None -- never dropped,
    never 0, so a caller that needs a position can tell it was not logged."""
    (acc,) = reloc_log.parse_accepts(QUIET_ACCEPT)
    assert acc.tod_s == 8 * 3600 + 44 * 60 + 54.015
    assert acc.fitness == 0.873
    assert acc.source == "fiducial"
    assert acc.margin == 0.178
    assert acc.time_cost_s == 1.4
    assert acc.published_t_m is None
    assert acc.reloc_t_m is None
    assert acc.n_pts is None


def test_quiet_accept_without_a_rival_source_reports_margin_none() -> None:
    """margin= is omitted when one source held the finalists. None means 'no rival
    to subtract', which is not the 0.0 a tie would print."""
    line = QUIET_ACCEPT.replace("margin=0.178 ", "")
    (acc,) = reloc_log.parse_accepts(line)
    assert acc.margin is None
    assert acc.fitness == 0.873


def test_verbose_accept_carries_no_margin() -> None:
    """The verbose accept drops margin= (its finalist table already has it), so the
    field is None there too -- reading margin never silently means 'tied'."""
    (acc,) = reloc_log.parse_accepts(CURRENT_ACCEPT)
    assert acc.margin is None


def test_quiet_reject_is_counted_and_never_an_accept() -> None:
    """The trimmed reject line (source/fitness/threshold, no time_cost_s or n_pts)
    still lands in the denominator and still stays out of parse_accepts."""
    text = "\n".join([
        "08:45:00.000 [war][...module.py] relocalize rejected fitness=0.31 "
        "source=fiducial threshold=0.45",
        QUIET_ACCEPT,
    ])
    assert reloc_log.count_rejects(text) == 1
    assert len(reloc_log.parse_accepts(text)) == 1


def test_quiet_run_has_no_census_rather_than_a_zero_census() -> None:
    """A quiet log emits no `relocalize candidates` at all, so the census is empty.
    Callers must read [] as 'not logged', not as 'no source proposed'."""
    assert reloc_log.parse_census(QUIET_ACCEPT) == []


def test_jsonl_and_console_twins_agree() -> None:
    """The same structlog call read from main.jsonl or the console gives one record."""
    (acc,) = reloc_log.parse_accepts(CURRENT_JSONL)
    assert acc.source == "fiducial"
    assert acc.fitness == 0.87
    assert acc.n_pts == 100
    assert acc.published_t_m == [-1.0, -2.0, -3.0]
    assert acc.tod_s == 8 * 3600 + 44 * 60 + 54.015


def test_missing_source_is_none_not_ransac() -> None:
    """No source= means the single-source path ran -- absence of a judge, not a
    RANSAC win. The parser reports None and lets the caller label it."""
    line = CURRENT_ACCEPT.replace("source=ransac ", "")
    (acc,) = reloc_log.parse_accepts(line)
    assert acc.source is None


def test_ansi_colored_line_parses() -> None:
    """A pty-captured log carries SGR codes between key and `=`; strip, then match."""
    colored = CURRENT_ACCEPT.replace("fitness=", "\x1b[36mfitness\x1b[0m=")
    (acc,) = reloc_log.parse_accepts(colored)
    assert acc.fitness == 0.984


def test_census_parses_current_legacy_and_jsonl() -> None:
    """Proposal counts survive all three renderings, per cycle, in file order."""
    text = "\n".join([
        "08:44:54.015 [inf][...priors.py] relocalize candidates counts={'ransac': 34, 'fiducial': 2}",
        "22:11:49.281 [inf][...priors.py] relocalize candidates: ransac=34",
        '{"counts": {"fiducial": 4}, "event": "relocalize candidates", "level": "info"}',
    ])
    assert reloc_log.parse_census(text) == [
        {"ransac": 34, "fiducial": 2},
        {"ransac": 34},
        {"fiducial": 4},
    ]


def test_rejects_counted_in_both_formats_and_never_as_accepts() -> None:
    """Rejects feed the denominator only; they must not leak into parse_accepts."""
    text = "\n".join([
        "08:45:00.000 [inf][...module.py] relocalize rejected fitness=0.21 threshold=0.45",
        "22:11:55.000 [inf][...module.py] relocalize rejected: fitness=0.19 < threshold=0.45",
        CURRENT_ACCEPT,
    ])
    assert reloc_log.count_rejects(text) == 2
    assert len(reloc_log.parse_accepts(text)) == 1


def test_module_started_matches_both_capitalizations() -> None:
    """The event was capitalized before the structlog switch; both mean started."""
    assert reloc_log.module_started("Relocalization module started map_file='x'")
    assert reloc_log.module_started("relocalization module started map_file=x")
    assert not reloc_log.module_started("Relocalization module disabled")


def test_non_relocalize_lines_are_ignored() -> None:
    """Another module's log, a banner, or a blank line yields nothing."""
    text = "\n".join([
        "08:44:19.281 [inf][...module_coordinator.py] Building the blueprint",
        "",
        '{"event": "something else", "level": "info"}',
        "not json {",
    ])
    assert reloc_log.parse_accepts(text) == []
    assert reloc_log.parse_census(text) == []
    assert reloc_log.count_rejects(text) == 0
