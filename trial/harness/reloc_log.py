#!/usr/bin/env python3
"""One parser for RelocalizationModule's accept / reject / census log lines.

Mirrors the approach in dimos ``mapping/relocalization/eval_module.py::_parse_line``
(key-anchored regexes, ANSI stripped, console + main.jsonl through one path) and
adds what the harness needs on top of eval_module's HealthLine: the console
time-of-day (every harness clock join keys off it), ``n_pts``, ``time_cost_s`` and
``reloc_t_m``.

TWO WIRE FORMATS, both live on disk here, so both parse:

  CURRENT (dimos 708332fe7, structlog kwargs; console kwargs render ALPHABETICALLY)
    `08:44:54.015 [inf][...module.py] relocalize accepted fitness=0.984 n_pts=51034
     published_t_m=[-0.079, -0.025, 0.067] reloc_t_m=[0.078, 0.025, -0.067]
     source=ransac tf_from=world tf_to=map time_cost_s=1.7`
    jsonl twin: {"event": "relocalize accepted", "fitness": 0.984, ...}
    census: `relocalize candidates counts={'ransac': 34, 'fiducial': 2}`

  LEGACY (<= bf5d7de66, one f-string; every capture in out/ predates the switch)
    `22:11:50.589 [inf][...module.py] relocalize: fitness=0.756 time_cost=13.0s
     n_pts=89003 reloc_t=[..] TF 'world' -> 'map' published_t=[..] source=ransac`
    census: `relocalize candidates: ransac=34 fiducial=2`

Legacy support is not politeness -- it is the archive. Dropping it would make every
recorded capture in ``out/`` unreadable, and those logs are the trial's evidence.

WHY key-anchored and not one positional regex: the legacy line put ``source=`` LAST
while a positional regex here expected it FIRST, so it never matched and every
accept silently fell back to ``ransac`` -- the bug that made "fiducial won 0" an
artifact of the parser rather than a measurement. Anchor on the key, scan the whole
line, never assume field order.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

# dimos colorizes the console only when stdout is a tty, so a pty-captured log
# carries SGR codes between key and `=`. Strip before matching.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Console lines open with the CLI's time-of-day (UTC in every capture we hold).
_TOD_RE = re.compile(r"^(\d\d):(\d\d):(\d\d(?:\.\d+)?)")
# jsonl carries an ISO-8601 UTC timestamp instead.
_ISO_TOD_RE = re.compile(r"T(\d\d):(\d\d):(\d\d(?:\.\d+)?)")

_EVENT_ACCEPT = "relocalize accepted"
_EVENT_REJECT = "relocalize rejected"
_EVENT_CENSUS = "relocalize candidates"
# Legacy accept lines are the only `relocalize:` lines that carry a fitness (the
# prior-enabled / skipped / rejected lines have their own shapes).
_LEGACY_ACCEPT = re.compile(r"relocalize:\s*fitness=")

# `_m` / `_s` suffixes are the current names, bare are legacy; values may contain
# spaces (`[1.0, 2.0]`, `{'ransac': 34}`), so each field gets its own key anchor.
_FITNESS_RE = re.compile(r"\bfitness=([-\d.eE+]+)")
_TCOST_RE = re.compile(r"\btime_cost(?:_s)?=([-\d.eE+]+)")
_NPTS_RE = re.compile(r"\bn_pts=(\d+)")
_RELOC_T_RE = re.compile(r"\breloc_t(?:_m)?=\[([^\]]+)\]")
_PUB_T_RE = re.compile(r"\bpublished_t(?:_m)?=\[([^\]]+)\]")
_SOURCE_RE = re.compile(r"\bsource=([A-Za-z_]\w*)")
_COUNTS_RE = re.compile(r"\bcounts=\{([^}]*)\}")  # current: a python dict repr
_COUNT_ENTRY_RE = re.compile(r"'(\w+)':\s*(\d+)")
_LEGACY_COUNT_RE = re.compile(r"\b([a-z_]+)=(\d+)")  # legacy: `ransac=34 fiducial=2`


@dataclass(frozen=True)
class Accept:
    """One `relocalize accepted` record. ``source`` is None on the single-source
    path (module.py tags a winner only when the multi-prior judge ran) -- callers
    pick their own label for that, they are not the same thing as a ransac WIN."""

    tod_s: float | None  # seconds since midnight, from the log clock (UTC in captures)
    fitness: float
    time_cost_s: float | None
    n_pts: int | None
    reloc_t_m: list[float] | None  # map_T_world translation
    published_t_m: list[float] | None  # world_T_map translation (the TF join key)
    source: str | None


def _floats(csv: str) -> list[float]:
    return [float(x) for x in csv.split(",")]


def _tod(text: str) -> float | None:
    m = _TOD_RE.match(text) or _ISO_TOD_RE.search(text)
    if m is None:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _kind(text: str, obj: dict[str, Any] | None) -> str | None:
    """Which record this line is, in either format. jsonl states it in `event`;
    the console renders the event verbatim into the message."""
    if obj is not None:
        event = obj.get("event", "")
        if isinstance(event, str) and event.startswith(_EVENT_ACCEPT):
            return "accept"
        if isinstance(event, str) and event.startswith(_EVENT_REJECT):
            return "reject"
        if isinstance(event, str) and event.startswith(_EVENT_CENSUS):
            return "census"
        text = event if isinstance(event, str) else ""
    if _EVENT_ACCEPT in text or _LEGACY_ACCEPT.search(text):
        return "accept"
    if _EVENT_REJECT in text:
        return "reject"
    if _EVENT_CENSUS in text:
        return "census"
    return None


def _records(log_text: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """``(kind, line_text, json_obj_or_None)`` per relocalize line. The key regexes
    scan ``line_text`` verbatim -- for jsonl that is the whole JSON object, which is
    what a LEGACY jsonl run needs: its key=value pairs live INSIDE the ``event``
    string, and JSON escaping leaves `key=value` untouched."""
    out: list[tuple[str, str, dict[str, Any] | None]] = []
    for raw in log_text.splitlines():
        text = _ANSI_RE.sub("", raw).strip()
        obj: dict[str, Any] | None = None
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            obj = parsed
        kind = _kind(text, obj)
        if kind is not None:
            out.append((kind, text, obj))
    return out


def _field(obj: dict[str, Any] | None, key: str, text: str, pattern: re.Pattern[str]) -> Any:
    """The jsonl value if this record has one, else the console match. Current jsonl
    types match their console twins, so downstream never learns which file it read."""
    if obj is not None and obj.get(key) is not None:
        return obj[key]
    m = pattern.search(text)
    return m.group(1) if m is not None else None


def parse_accepts(log_text: str) -> list[Accept]:
    """Every accepted relocalize in the log, in file order."""
    out: list[Accept] = []
    for kind, text, obj in _records(log_text):
        if kind != "accept":
            continue
        fitness = _field(obj, "fitness", text, _FITNESS_RE)
        if fitness is None:  # not an accept after all (e.g. a truncated line)
            continue
        tcost = _field(obj, "time_cost_s", text, _TCOST_RE)
        npts = _field(obj, "n_pts", text, _NPTS_RE)
        reloc_t = _field(obj, "reloc_t_m", text, _RELOC_T_RE)
        pub_t = _field(obj, "published_t_m", text, _PUB_T_RE)
        src = _field(obj, "source", text, _SOURCE_RE)
        out.append(
            Accept(
                tod_s=_tod(text if obj is None else str(obj.get("timestamp", ""))),
                fitness=float(fitness),
                time_cost_s=None if tcost is None else float(tcost),
                n_pts=None if npts is None else int(npts),
                reloc_t_m=reloc_t if isinstance(reloc_t, list) else _as_floats(reloc_t),
                published_t_m=pub_t if isinstance(pub_t, list) else _as_floats(pub_t),
                source=None if src is None else str(src),
            )
        )
    return out


def _as_floats(csv: str | None) -> list[float] | None:
    return None if csv is None else _floats(csv)


def count_rejects(log_text: str) -> int:
    """Accepts below the fitness threshold -- the denominator's other half."""
    return sum(1 for kind, _t, _o in _records(log_text) if kind == "reject")


def parse_census(log_text: str) -> list[dict[str, int]]:
    """One dict of per-source PROPOSAL counts per judge cycle. A source missing here
    proposed nothing that cycle -- distinct from proposing and losing the judge."""
    out: list[dict[str, int]] = []
    for kind, text, obj in _records(log_text):
        if kind != "census":
            continue
        if obj is not None and isinstance(obj.get("counts"), dict):
            out.append({str(k): int(v) for k, v in obj["counts"].items()})
            continue
        m = _COUNTS_RE.search(text)
        if m is not None:
            out.append({k: int(n) for k, n in _COUNT_ENTRY_RE.findall(m.group(1))})
            continue
        # Legacy: `relocalize candidates: ransac=34 fiducial=2`
        tail = text.split(_EVENT_CENSUS, 1)[1]
        counts = {k: int(n) for k, n in _LEGACY_COUNT_RE.findall(tail)}
        if counts:
            out.append(counts)
    return out


def module_started(log_text: str) -> bool:
    """Did RelocalizationModule reach the end of start()? The event was capitalized
    before the structlog switch, so match case-insensitively."""
    return "elocalization module started" in log_text
