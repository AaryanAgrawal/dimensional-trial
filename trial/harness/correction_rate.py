#!/usr/bin/env python3
"""How much is relocalization actually CORRECTING, per metre driven?

Reads what the REAL dimos pipeline already published (out/results_dimos/*.replay.json,
captured by replay_bench.py off the TF tree) plus the recording's own `odom` stream
for distance travelled. Nothing is recomputed from point clouds here -- this only
differences fixes dimos itself emitted.

Per consecutive accepted-fix pair k-1 -> k:
  d_corr_m   = |t_k - t_{k-1}| of the published world_T_map translation
  d_yaw_deg  = yaw of R_{k-1}^T R_k  (world_T_map rotation; from the 4x4, not the log)
  dt_s       = recording-time gap
  d_drive_m  = arclength of odom world_T_base between the two fix timestamps

The headline is d_corr_m / d_drive_m. The FIRST fix is dropped (acquisition, not drift).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy.stats import pearsonr, spearmanr

sys.path.insert(0, "/home/dimos/dimensional-trial/dimos")
sys.path.insert(0, "/home/dimos/dimensional-trial/trial/harness")
from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped  # noqa: E402
from reloc_log import parse_accepts  # noqa: E402

DATA = Path("/home/dimos/dimensional-trial/dimos/data")
CACHE = Path("/home/dimos/dimensional-trial/trial/harness/out/odom_arclen")
CACHE.mkdir(parents=True, exist_ok=True)


def arclen(recording: str) -> tuple[np.ndarray, np.ndarray]:
    """(ts, cumulative_distance_m) of the recorded odom track, cached to npz."""
    db = recording
    npz = CACHE / f"{db}.npz"
    if npz.exists():
        z = np.load(npz)
        return z["ts"], z["s"]
    rows = []
    store = SqliteStore(path=str(DATA / f"{db}.db"), must_exist=True)
    with store:
        for obs in store.stream("odom", PoseStamped):
            p = obs.data.position
            rows.append((float(obs.ts), p.x, p.y, p.z))
    arr = np.array(rows)
    arr = arr[np.argsort(arr[:, 0])]
    ts, pos = arr[:, 0], arr[:, 1:4]
    step = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(step)])
    np.savez(npz, ts=ts, s=s)
    return ts, s


def yaw_deg(R: np.ndarray) -> float:
    return float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))


def pairs(path: Path) -> dict:
    d = json.loads(path.read_text())
    meta, fixes = d["meta"], d["fixes"]
    fixes = sorted(fixes, key=lambda f: f["ts"])
    ts_o, s_o = arclen(meta["recording"])
    rows = []
    for a, b in zip(fixes, fixes[1:]):
        Ta, Tb = np.array(a["world_map_fix"]), np.array(b["world_map_fix"])
        dR = Ta[:3, :3].T @ Tb[:3, :3]
        rows.append(
            dict(
                ts=b["ts"],
                d_corr_m=float(np.linalg.norm(Tb[:3, 3] - Ta[:3, 3])),
                d_yaw_deg=abs(yaw_deg(dR)),
                dt_s=float(b["ts"] - a["ts"]),
                d_drive_m=float(np.interp(b["ts"], ts_o, s_o) - np.interp(a["ts"], ts_o, s_o)),
                source=b.get("source"),
                fitness=b["fitness"],
                time_cost_s=b.get("time_cost_s"),
            )
        )
    return dict(meta=meta, n_fixes=len(fixes), rows=rows)


def q(v):
    v = np.asarray(v, float)
    if v.size == 0:
        return None
    return dict(
        n=int(v.size),
        min=round(float(v.min()), 4),
        med=round(float(np.median(v)), 4),
        p90=round(float(np.percentile(v, 90)), 4),
        max=round(float(v.max()), 4),
    )


def report(tag: str, path: Path, drop_first: int = 1) -> dict:
    r = pairs(path)
    rows = r["rows"][drop_first:]  # first PAIR involves the acquisition fix
    if not rows:
        return dict(tag=tag, note="no pairs after dropping acquisition")
    corr = np.array([x["d_corr_m"] for x in rows])
    yaw = np.array([x["d_yaw_deg"] for x in rows])
    dt = np.array([x["dt_s"] for x in rows])
    drv = np.array([x["d_drive_m"] for x in rows])
    ok = drv > 1e-6
    rate = corr[ok] / drv[ok]
    out = dict(
        tag=tag,
        recording=r["meta"]["recording"],
        n_fixes=r["n_fixes"],
        n_pairs=len(rows),
        drive_seconds=round(r["meta"]["drive_seconds"], 1),
        total_drive_m=round(float(drv.sum()), 2),
        total_corr_m=round(float(corr.sum()), 3),
        aggregate_rate_m_per_m=round(float(corr.sum() / drv.sum()), 4) if drv.sum() > 0 else None,
        d_corr_m=q(corr),
        d_yaw_deg=q(yaw),
        dt_s=q(dt),
        d_drive_m=q(drv),
        rate_m_per_m=q(rate),
        sorted_corr_m=[round(x, 3) for x in sorted(corr)],
        sorted_yaw_deg=[round(x, 3) for x in sorted(yaw)],
    )
    if len(rows) >= 4:
        out["corr_vs_drive_pearson"] = [round(v, 3) for v in pearsonr(corr, drv)]
        out["corr_vs_drive_spearman"] = [round(v, 3) for v in spearmanr(corr, drv)]
        out["corr_vs_dt_pearson"] = [round(v, 3) for v in pearsonr(corr, dt)]
        out["corr_vs_dt_spearman"] = [round(v, 3) for v in spearmanr(corr, dt)]
    by_src: dict[str, list[float]] = {}
    for x in rows:
        by_src.setdefault(str(x["source"]), []).append(x["d_corr_m"])
    out["by_source_d_corr_m"] = {k: q(v) for k, v in by_src.items()}
    out["rows"] = rows
    return out


def log_pairs(path: Path, recording: str | None, start_re: str) -> dict:
    """Same differencing off a raw run log. The accept LINE carries no rotation and no
    recording ts (replay_bench.py's docstring), so this yields translation only, and the
    clock is the log's wall time-of-day. `dimos --replay` runs at speed 1.0, so the
    module-started line anchors log-tod -> recording-ts; the submap predates the publish
    by its logged time_cost_s. Anchor error is common to both endpoints of a pair, so it
    cancels in d_drive_m (validated below against the exact-ts hk_village3 replay)."""
    text = path.read_text()
    accepts = [a for a in parse_accepts(text) if a.published_t_m is not None]
    m = re.search(rf"^(\d\d):(\d\d):(\d\d\.\d+).*{start_re}", text, re.M)
    if m is None:
        raise RuntimeError(f"no module-start anchor in {path}")
    t0_tod = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    ts_o = s_o = None
    if recording is not None:
        ts_o, s_o = arclen(recording)
    rows = []
    for a, b in zip(accepts, accepts[1:]):
        ta = a.tod_s - t0_tod - (a.time_cost_s or 0.0)
        tb = b.tod_s - t0_tod - (b.time_cost_s or 0.0)
        drive = None
        if ts_o is not None:
            drive = float(
                np.interp(ts_o[0] + tb, ts_o, s_o) - np.interp(ts_o[0] + ta, ts_o, s_o)
            )
        rows.append(
            dict(
                t_since_start_s=round(tb, 2),
                d_corr_m=float(np.linalg.norm(np.array(b.published_t_m) - np.array(a.published_t_m))),
                d_yaw_deg=None,  # the accept log line carries no rotation
                dt_s=round(tb - ta, 3),
                d_drive_m=drive,
                source=b.source,
                fitness=b.fitness,
                time_cost_s=b.time_cost_s,
            )
        )
    return dict(meta=dict(recording=recording, log=str(path)), n_fixes=len(accepts), rows=rows)


def report_log(tag: str, path: Path, recording: str | None, start_re: str, drop_first: int = 1) -> dict:
    r = log_pairs(path, recording, start_re)
    rows = r["rows"][drop_first:]
    corr = np.array([x["d_corr_m"] for x in rows])
    dt = np.array([x["dt_s"] for x in rows])
    out = dict(
        tag=tag,
        recording=recording,
        n_fixes=r["n_fixes"],
        n_pairs=len(rows),
        d_corr_m=q(corr),
        dt_s=q(dt),
        sorted_corr_m=[round(x, 3) for x in sorted(corr)],
        rows=rows,
    )
    if recording is not None:
        drv = np.array([x["d_drive_m"] for x in rows])
        ok = drv > 1e-6
        out["total_drive_m"] = round(float(drv.sum()), 2)
        out["total_corr_m"] = round(float(corr.sum()), 3)
        out["aggregate_rate_m_per_m"] = round(float(corr.sum() / drv.sum()), 4)
        out["d_drive_m"] = q(drv)
        out["rate_m_per_m"] = q(corr[ok] / drv[ok])
        if len(rows) >= 4:
            out["corr_vs_drive_pearson"] = [round(v, 3) for v in pearsonr(corr, drv)]
            out["corr_vs_drive_spearman"] = [round(v, 3) for v in spearmanr(corr, drv)]
            out["corr_vs_dt_pearson"] = [round(v, 3) for v in pearsonr(corr, dt)]
            out["corr_vs_dt_spearman"] = [round(v, 3) for v in spearmanr(corr, dt)]
    by_src: dict[str, list[float]] = {}
    for x in rows:
        by_src.setdefault(str(x["source"]), []).append(x["d_corr_m"])
    out["by_source_d_corr_m"] = {k: q(v) for k, v in by_src.items()}
    return out


if __name__ == "__main__":
    base = Path("/home/dimos/dimensional-trial/trial/harness/out/results_dimos")
    targets = [
        ("hk_village3_reloc_only", base / "hk_village3.replay.json"),
        ("hk_village3_consistency", base / "hk_village3.consistency_check.replay.json"),
        ("survey1_self", base / "sf_office_go2_20260718_survey1.replay.json"),
        ("survey1_self_gated", base / "sf_office_gated.replay.json"),
        ("survey2_heldout", base / "survey2_heldout.replay.json"),
        ("survey2_det_run1", base / "survey2_det_run1.replay.json"),
        ("survey2_det_run2", base / "survey2_det_run2.replay.json"),
        ("survey2_det_run3", base / "survey2_det_run3.replay.json"),
    ]
    res = [report(t, p) for t, p in targets if p.exists()]

    bp = Path("/home/dimos/dimensional-trial/trial/harness/out/hk_village3_bp_runs")
    ev = Path("/home/dimos/dimensional-trial/trial/harness/out/eval")
    log_targets = [
        ("bp_run1_lidar", bp / "run1_lidar.log", "hk_village3"),
        ("bp_run2_lidar_fiducial", bp / "run2_lidar_fiducial.log", "hk_village3"),
        ("bp_run3_fiducial", bp / "run3_fiducial.log", "hk_village3"),
        # Live captures: no `--replay-db` recorded anywhere in the artifacts, so the
        # driven distance has no attributable odom stream. Translation + dt only.
        ("eval_live_colored", ev / "survey2_live_vs_survey1_colored.replay_run.log", None),
        ("eval_live_run1", ev / "survey2_live_vs_survey1_run1.replay_run.log", None),
    ]
    res += [
        report_log(t, p, rec, "relocalization module started")
        for t, p, rec in log_targets
        if p.exists()
    ]
    print(json.dumps(res, indent=1))
