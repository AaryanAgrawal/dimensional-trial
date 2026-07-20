#!/usr/bin/env python3
"""Grade the REAL dimos relocalization pipeline under `dimos --replay run`.

This is the dimos-NATIVE benchmark driver: it runs the shipped blueprint on a
recording and captures what the shipped RelocalizationModule actually publishes.
It re-implements NO dimos data-path step (that was the old harness's sin -- a
body-frame re-anchor dimos never does, which manufactured a phantom gravity-gate
bug). Here dimos does the localizing; we only listen.

What we capture, and why each channel (verified against source Jul 2026):
  - RelocalizationModule (mapping/relocalization/module.py) publishes its
    world->map answer to the TF tree via `self.tf.publish(tf.now())`, and logs
    one `relocalize:` line per accept (fitness, time_cost, n_pts, reloc_t,
    published_t, source). The log line has NO rotation and NO recording ts.
  - The TF tree rides LCM topic `dimos/tf` as TFMessage (protocol/tf/tf.py
    LCMTF). We subscribe there for the FULL world->map transform (rotation the
    log omits). In a reloc-only run (no marker_map_file) the visual module idles
    (start() no-ops), so every world->map on the tree is the lidar module's --
    a clean capture. NOTE: `world_map_fix` is the visual module's Out / the lidar
    module's In (a fiducial PRIOR), NOT where the answer is published -- so it is
    empty here. Subscribing to it, as first planned, would capture nothing.
  - `tf.now()` restamps the published TF to WALL time, and the log stamps wall
    time too; neither is the recording ts that `PoseGraph.correction_at(t)`
    needs. We recover recording ts from a light recording-timestamped stream
    (`odom`, PoseStamped, msg.ts preserved through replay) -> a wall<->recording
    clock (speed=1.0, replay.py pins one anchor), then map each accept's wall
    time back, minus its logged compute time (the submap predates the publish
    by time_cost). Residual ts uncertainty ~1-2 s -> <~0.08 m of truth motion,
    at/below the PGO silver floor; it never flips a success verdict. Stated, not
    hidden.

Output: out/results_dimos/<rec>.replay.json = {meta, fixes:[{ts, world_map_fix
(4x4 world->map), fitness, n_pts, source, ...}]}. (The task named a bare list;
we wrap it with provenance meta -- reproducibility is a house non-negotiable --
keeping the required keys on every fix element.)

Run (village3, reloc-only):
  uv run --project /home/dimos/dimensional-trial/dimos \
      python ../trial/harness/replay_bench.py hk_village3 \
      --premap /abs/path/hk_village3.pc2.lcm

LCM bus is EXCLUSIVE: one replay at a time (a second Coordinator crashes). The
driver refuses to start if another `dimos --replay` is live.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import numpy as np

from dimos.core.transport import LCMTransport
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.tf2_msgs.TFMessage import TFMessage

DIMOS_ROOT = Path(__file__).resolve().parents[2] / "dimos"
TRIAL_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(__file__).parent / "out" / "results_dimos"

BLUEPRINT = "unitree-go2-fiducial-relocalization"
FRAME_WORLD = "world"
FRAME_MAP = "map"
WARMUP_BUDGET_S = 90.0  # module deploy + teardown headroom on top of drive length

# `07:51:58.887 [inf][...module.py] relocalize: fitness=0.980 time_cost=2.0s
#  n_pts=55828 reloc_t=[..] TF 'world' -> 'map' published_t=[..] source=ransac`
_ACCEPT_RE = re.compile(
    r"(?P<h>\d\d):(?P<m>\d\d):(?P<s>\d\d)\.(?P<ms>\d+)\s.*?"
    r"relocalize: fitness=(?P<fit>[0-9.]+) time_cost=(?P<tc>[0-9.]+)s "
    r"n_pts=(?P<npts>\d+) reloc_t=\[(?P<reloc>[^\]]+)\] "
    r"TF 'world' -> 'map' published_t=\[(?P<pub>[^\]]+)\]"
    r"(?: source=(?P<src>\S+))?"
)
# rejects share the timestamp+fitness shape; counted for the denominator only.
_REJECT_RE = re.compile(r"relocalize rejected: fitness=(?P<fit>[0-9.]+)")


def _git_rev(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _floats(csv: str) -> list[float]:
    return [float(x) for x in csv.split(",")]


def _preflight_bus_clear() -> None:
    """LCM is a shared bus; a second replay Coordinator crashes the first. Refuse
    to launch while any `dimos --replay` is alive (our own captures included)."""
    hits = subprocess.run(
        ["pgrep", "-af", "dimos --replay"], capture_output=True, text=True
    ).stdout.strip()
    # pgrep -af matches our own arg string too if it appears; filter to real CLIs.
    live = [ln for ln in hits.splitlines() if "run " in ln and "replay_bench" not in ln]
    if live:
        raise RuntimeError(
            "another `dimos --replay` is live -- the LCM bus is exclusive; wait or "
            f"kill it first:\n  " + "\n  ".join(live)
        )


def _recording_window(rec: str) -> tuple[float, float]:
    """(first_ts, last_ts) in recording seconds, across the driven streams. The
    replay anchor (replay.py) pins to the earliest first_ts; first-accept time is
    reported relative to it."""
    db = DIMOS_ROOT / "data" / f"{rec}.db"
    store = SqliteStore(path=str(db), must_exist=True)
    firsts, lasts = [], []
    with store:
        for name in store.list_streams():
            try:
                firsts.append(float(store.stream(name).first().ts))
                lasts.append(float(store.stream(name).last().ts))
            except (LookupError, ValueError):
                continue
    return min(firsts), max(lasts)


class _Capture:
    """In-process LCM listeners. Callbacks fire on the LCM handle thread; list
    .append is atomic under the GIL, so no lock is needed. Every sample carries
    the wall time it was seen (time.time()) -- our single clock for everything."""

    def __init__(self) -> None:
        self.odom: list[tuple[float, float]] = []          # (wall_recv, rec_ts)
        self.world_map: list[tuple[float, np.ndarray, tuple[float, float, float]]] = []
        self._odom_t = LCMTransport("/odom", PoseStamped)
        # The tf tree rides LCM channel "/tf" (empirically; LCMTF's "dimos/tf"
        # normalizes to this). RelocalizationModule's world->map republishes here
        # every PUBLISH_INTERVAL alongside world->base_link etc.
        self._tf_t = LCMTransport("/tf", TFMessage)
        self._unsub: list = []

    def start(self) -> None:
        self._unsub.append(self._odom_t.subscribe(self._on_odom))
        self._unsub.append(self._tf_t.subscribe(self._on_tf))

    def _on_odom(self, msg: PoseStamped) -> None:
        self.odom.append((time.time(), float(msg.ts)))

    def _on_tf(self, msg: TFMessage) -> None:
        w = time.time()
        for tf in msg.transforms:
            if tf.frame_id == FRAME_WORLD and tf.child_frame_id == FRAME_MAP:
                T = tf.to_matrix()
                self.world_map.append((w, T, tuple(float(x) for x in T[:3, 3])))

    def stop(self) -> None:
        for u in self._unsub:
            u()
        self._odom_t.stop()
        self._tf_t.stop()


def _run_replay(rec: str, premap: Path, log_path: Path, max_wall_s: float,
                extra_overrides: list[str]) -> tuple[list[str], float]:
    """Launch the shipped blueprint on the recording; return (argv, launch_wall).
    Own session (start_new_session) so teardown kills the whole worker pool by the
    exact pgid. Waits for natural exit (loop=False -> replay ends at data end),
    hard-capped at max_wall_s."""
    argv = [
        "uv", "run", "--project", str(DIMOS_ROOT),
        "dimos", "--replay", f"--replay-db={rec}", "run", BLUEPRINT,
        "-o", f"relocalizationmodule.map_file={premap}", *extra_overrides,
    ]
    launch_wall = time.time()
    with log_path.open("wb") as fh:
        proc = subprocess.Popen(
            argv, stdout=fh, stderr=subprocess.STDOUT,
            cwd=str(DIMOS_ROOT), start_new_session=True,
        )
    try:
        deadline = launch_wall + max_wall_s
        while proc.poll() is None and time.time() < deadline:
            time.sleep(1.0)
        if proc.poll() is None:
            print(f"[replay_bench] wall cap {max_wall_s:.0f}s hit; stopping replay")
    finally:
        _teardown(proc)
    return argv, launch_wall


def _teardown(proc: subprocess.Popen) -> None:
    """SIGTERM the process group, escalate to SIGKILL past the budget. Idempotent
    and exception-safe: this is the ONE place the worker pool is reaped."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig, budget in ((signal.SIGTERM, 30.0), (signal.SIGKILL, 10.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=budget)
            return
        except subprocess.TimeoutExpired:
            continue


def _clock_offset(odom: list[tuple[float, float]], win: tuple[float, float]) -> float:
    """recording_ts ~= wall + offset (replay speed=1.0). offset = median over odom
    samples inside the recording window; median rejects the transport-latency tail
    (a late-delivered sample only ever reads recording-EARLIER-than-wall)."""
    lo, hi = win[0] - 5.0, win[1] + 5.0
    deltas = [rec - wall for wall, rec in odom if lo <= rec <= hi]
    if not deltas:
        raise RuntimeError("no odom samples in the recording window -- cannot map the clock")
    return float(np.median(deltas))


def _match_world_map(
    published_t: list[float],
    world_map: list[tuple[float, np.ndarray, tuple[float, float, float]]],
) -> tuple[np.ndarray | None, float | None, str]:
    """Recover the full world->map matrix for a log accept by its translation.
    published_t (3 dp, from the log) == the TF's translation to full precision, so
    an exact 1 mm match is the common case; nearest-within-2cm is the fallback.
    Returns (T4x4 or None, earliest_wall or None, rot_source)."""
    pt = np.asarray(published_t)
    exact = [(w, T) for (w, T, t) in world_map if np.allclose(np.round(t, 3), pt, atol=1e-3)]
    if exact:
        return exact[0][1], min(w for w, _ in exact), "tf_exact"
    if world_map:
        w, T, t = min(world_map, key=lambda r: float(np.linalg.norm(np.asarray(r[2]) - pt)))
        if float(np.linalg.norm(np.asarray(t) - pt)) < 0.02:
            return T, w, "tf_nearest"
    return None, None, "none"


def _parse_and_join(
    log_path: Path, cap: _Capture, win: tuple[float, float]
) -> tuple[list[dict], dict]:
    """Turn the run log + captures into scored-ready fix rows.

    Recording ts per accept comes from ONE clock -- the driver's time.time(),
    shared by the odom samples and the /tf captures -- so no log-timezone guess
    is needed (the CLI log's time-of-day sits a whole-hours offset from wall time;
    reported below, not used for timing). For a tf-matched accept:
        R = (tf_wall + offset) - time_cost
    where tf_wall is when its world->map first republished, offset the odom clock
    map (recording = wall + offset, replay speed=1.0), and time_cost the logged
    compute time -- subtracted so truth is sampled at the submap's drive-position,
    not the publish moment. Residual: the republish lags the accept 0..PUBLISH
    _INTERVAL, biasing R late ~1 s -> <~0.05 m of truth motion, at the PGO floor.
    Accepts that missed a republish (rare: the last before shutdown) fall back to
    the log time-of-day mapped through a constant calibrated on the matched ones."""
    text = log_path.read_text(errors="replace")
    offset = _clock_offset(cap.odom, win)  # recording_ts = wall + offset

    now = time.time()
    lt = time.localtime(now)
    midnight_local = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec + now % 1.0)

    raw = []
    for mobj in _ACCEPT_RE.finditer(text):
        g = mobj.groupdict()
        tod = int(g["h"]) * 3600 + int(g["m"]) * 60 + int(g["s"]) + int(g["ms"]) / 10 ** len(g["ms"])
        T, tf_wall, rot_source = _match_world_map(_floats(g["pub"]), cap.world_map)
        raw.append((g, tod, float(g["tc"]), T, tf_wall, rot_source))

    # Calibrate tod -> recording from tf-matched accepts: R = (tod - time_cost) + K.
    ks = [(tw + offset) - (tod - tc) for (_, tod, tc, _, tw, _) in raw if tw is not None]
    K = float(np.median(ks)) if ks else midnight_local + offset
    # Whole-hours gap between the CLI log clock and wall time (documents why we
    # anchor timing on /tf, not the log). Expected: a timezone multiple of 3600.
    clock_gaps = [tw - (midnight_local + tod) for (_, tod, _, _, tw, _) in raw if tw is not None]

    fixes: list[dict] = []
    for g, tod, tc, T, tf_wall, rot_source in raw:
        if tf_wall is not None:
            ts_rec, ts_source = (tf_wall + offset) - tc, "tf"
        else:
            ts_rec, ts_source = (tod - tc) + K, ("log_calibrated" if ks else "log_naive")
        fixes.append({
            "ts": ts_rec,
            "ts_source": ts_source,
            "wall_ts": tf_wall if tf_wall is not None else midnight_local + tod,
            "world_map_fix": T.tolist() if T is not None else None,
            "reloc_t": _floats(g["reloc"]),           # map_T_world translation (log)
            "published_t": _floats(g["pub"]),          # world_T_map translation (log)
            "fitness": float(g["fit"]),
            "n_pts": int(g["npts"]),
            "time_cost_s": tc,
            "source": g["src"] or "ransac",
            "rot_source": rot_source,
        })

    diag = {
        "clock_offset_s": offset,
        "n_odom_samples": len(cap.odom),
        "n_world_map_tf": len(cap.world_map),
        "n_accepts": len(fixes),
        "n_rejects": len(_REJECT_RE.findall(text)),
        "n_with_rotation": sum(1 for f in fixes if f["world_map_fix"] is not None),
        "log_vs_wall_clock_gap_s": float(np.median(clock_gaps)) if clock_gaps else None,
        "traceback_in_log": "Traceback (most recent call last)" in text,
        "module_started": "Relocalization module started" in text,
    }
    return fixes, diag


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--premap", required=True, help="ABS path to the .pc2.lcm premap")
    ap.add_argument("--max-wall-s", type=float, default=None,
                    help="hard cap; default = drive length + 90 s warmup/teardown")
    ap.add_argument("-o", dest="overrides", action="append", default=[],
                    metavar="k=v", help="extra dimos -o override (repeatable)")
    a = ap.parse_args()

    premap = Path(a.premap).resolve()
    if not premap.exists():
        raise FileNotFoundError(f"premap not found: {premap}")
    _preflight_bus_clear()

    win = _recording_window(a.recording)
    drive_s = win[1] - win[0]
    max_wall_s = a.max_wall_s if a.max_wall_s is not None else drive_s + WARMUP_BUDGET_S
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"{a.recording}.replay_run.log"

    extra = [arg for o in a.overrides for arg in ("-o", o)]
    print(f"[replay_bench] {a.recording}: drive={drive_s:.1f}s cap={max_wall_s:.0f}s premap={premap}")

    cap = _Capture()
    cap.start()
    try:
        argv, _launch_wall = _run_replay(a.recording, premap, log_path, max_wall_s, extra)
    finally:
        # Drain a beat so the last republished TF lands, then detach listeners.
        time.sleep(1.0)
        cap.stop()

    fixes, diag = _parse_and_join(log_path, cap, win)

    out = {
        "meta": {
            "recording": a.recording,
            "premap": str(premap),
            "blueprint": BLUEPRINT,
            "command": " ".join(argv),
            "recording_first_ts": win[0],
            "recording_last_ts": win[1],
            "drive_seconds": drive_s,
            "git_rev_dimos": _git_rev(DIMOS_ROOT),
            "git_rev_trial": _git_rev(TRIAL_ROOT),
            "created_unix": time.time(),
            "label": "replay",  # real recorded sensor data, not SIMULATED
            **diag,
        },
        "fixes": fixes,
    }
    out_path = OUT_DIR / f"{a.recording}.replay.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[replay_bench] wrote {out_path}")
    print(f"[replay_bench] accepts={diag['n_accepts']} rejects={diag['n_rejects']} "
          f"with_rotation={diag['n_with_rotation']} world_map_tf={diag['n_world_map_tf']} "
          f"log_gap_s={diag['log_vs_wall_clock_gap_s']} traceback={diag['traceback_in_log']}")


if __name__ == "__main__":
    main()
