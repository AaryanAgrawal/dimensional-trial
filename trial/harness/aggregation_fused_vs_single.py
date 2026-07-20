#!/usr/bin/env python3
"""FUSED (jnav Huber aggregation) vs LATEST-SINGLE-BEST fiducial world->map fix
error, on the hk_village3 tag-10 replay.

THE POINT of the port: a single tag glimpse solves a world->map fix whose
translation error is per-detection tag-ORIENTATION error times the tag's lever
arm to the map origin (31.26 m on hk_village3 -> ~0.55 m of fix per degree). The
old FiducialPrior published the LATEST single glimpse's fix, so that per-glimpse
orientation noise passed straight through. This harness Huber-fuses each visit's
glimpses (apriltag_aggregation.robust_cluster_pose: medoid + IRLS translation +
Markley quaternion mean) and measures how much the fix error drops.

METHOD (replay, deterministic, no RNG): reuse live_fix_quality's cached UNGATED
tag-10 detection pass (out/rehearsal/live_fix_quality_cache.npz: per-frame
rvec/tvec + gate columns) and its fix chain --
    world_T_marker(t) = world_T_base(t) @ base_T_optical @ optical_T_marker(t)
    fix(t)            = world_T_marker(t) @ inv(map_T_marker)
Both methods run on the SAME gated set (the live gate: IPPE ambiguity ratio
>= 2.0 AND reproj <= 3.0 px -- the reverted FiducialPrior default), so the
comparison isolates FUSION, not the gate. Per time-clustered visit (5 s jnav
gap, >= 3 glimpses): FUSED = robust_cluster_pose over the visit; LATEST = the
visit's final glimpse fix (what the old prior would hold). Both scored against
the same reference.

REFERENCE (stated, not hidden): PGO-corrected pose, inv(correction_at(ts)) from
the prepared hk_village3 pose graph -- silver truth (~6 cm floor), NOT survey
truth, and village3's documented non-monotonic drift makes it wobble ~0.4 m
late-run (see live_fix_quality). No conclusion here rests on sub-0.5 m
differences; the effect measured is meters. A fused pose averages over a visit's
~5 s (< 0.1 m of drift at the measured ~0.013 m/s) and is scored at the visit's
last-glimpse time -- that drift offset is negligible against the effect.

Run: uv run --project /home/dimos/dimos-code python \\
     /home/dimos/dimensional-trial/trial/harness/aggregation_fused_vs_single.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pickle
import subprocess
import sys

import cv2
import matplotlib
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))

from prep import pose7_to_mat, transform_to_mat  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped  # noqa: E402
from dimos.perception.fiducial.apriltag_aggregation import (  # noqa: E402
    AggregationConfig,
    TagObservation,
    cluster_by_time,
    matrix_from_pose7,
    pose7_from_matrix,
    robust_cluster_pose,
)
from dimos.robot.unitree.go2.connection import BASE_TO_OPTICAL  # noqa: E402

SEED = 0  # printed per house rule; nothing below draws random numbers
TAG_ID = 10  # the one surveyed tag in hk_village3.marker_map.yaml
AMBIGUITY_MIN = 2.0  # reverted FiducialPrior default; matches the cache's pass_live gate
CLUSTER_GAP_S = 5.0  # jnav visit gap
MIN_OBS = 3  # jnav DEFAULT_MIN_OBSERVATIONS


class OdomTrack:
    """Recorded odom stream -> interpolated world_T_base at any ts (lerp position,
    slerp rotation). Reimplemented here because live_fix_quality can't be imported
    under this branch (it imports the removed visual_relocalization.detect_markers)."""

    def __init__(self, db_path: Path) -> None:
        rows = []
        store = SqliteStore(path=str(db_path), must_exist=True)
        with store:
            for obs in store.stream("odom", PoseStamped):
                p, q = obs.data.position, obs.data.orientation
                rows.append((float(obs.ts), p.x, p.y, p.z, q.x, q.y, q.z, q.w))
        arr = np.array(rows)
        arr = arr[np.argsort(arr[:, 0])]
        self.ts = arr[:, 0]
        self.pos = arr[:, 1:4]
        self.rot = Rotation.from_quat(arr[:, 4:8])
        self._slerp = Slerp(self.ts, self.rot)

    def world_T_base(self, t: float) -> np.ndarray:
        t = float(np.clip(t, self.ts[0], self.ts[-1]))
        i = int(np.clip(np.searchsorted(self.ts, t), 1, len(self.ts) - 1))
        w = (t - self.ts[i - 1]) / (self.ts[i] - self.ts[i - 1])
        matrix = np.eye(4)
        matrix[:3, 3] = self.pos[i - 1] * (1 - w) + self.pos[i] * w
        matrix[:3, :3] = self._slerp([t]).as_matrix()[0]
        return matrix


def _optical_T_marker(cache: dict[str, np.ndarray], i: int) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, :3] = cv2.Rodrigues(
        np.array([cache["rvec_0"][i], cache["rvec_1"][i], cache["rvec_2"][i]])
    )[0]
    matrix[:3, 3] = (cache["tvec_0"][i], cache["tvec_1"][i], cache["tvec_2"][i])
    return matrix


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rehearsal-dir", type=Path, default=HARNESS / "out" / "rehearsal")
    ap.add_argument("--recording", default="hk_village3")
    ap.add_argument(
        "--fig",
        type=Path,
        default=HARNESS.parent / "results" / "figures" / "aggregation_fused_vs_single.png",
    )
    a = ap.parse_args()

    dimos_root = HARNESS.resolve().parents[1] / "dimos"
    db = dimos_root / "data" / f"{a.recording}.db"
    revs = {}
    for name, path in (("trial", HARNESS.parent), ("dimos-code", Path("/home/dimos/dimos-code"))):
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        revs[name] = out.stdout.strip()
    print(
        f"aggregation_fused_vs_single: SEED={SEED} (no RNG) "
        f"git trial={revs['trial']} dimos-code={revs['dimos-code']} db={db}"
    )

    cache_npz = a.rehearsal_dir / "live_fix_quality_cache.npz"
    z = dict(np.load(cache_npz, allow_pickle=False))
    ts = z["ts"]
    order = np.argsort(ts)
    z = {k: v[order] for k, v in z.items() if k != "key"}
    ts = z["ts"]
    passing = z["pass_live"] > 0
    print(
        f"loaded {len(ts)} tag-{TAG_ID} detections ({int(passing.sum())} pass the live "
        f"gate: IPPE ambiguity >= {AMBIGUITY_MIN} AND reproj <= 3.0 px) from {cache_npz.name}"
    )

    # world_T_marker(t) per detection via odom + the static camera extrinsic.
    odom = OdomTrack(db)
    base_T_optical = transform_to_mat(BASE_TO_OPTICAL)
    world_T_marker = np.array(
        [odom.world_T_base(float(ts[i])) @ base_T_optical @ _optical_T_marker(z, i)
         for i in range(len(ts))]
    )

    mm = yaml.safe_load(
        (a.rehearsal_dir / f"{a.recording}.marker_map.yaml").read_text()
    )["markers"][TAG_ID]
    map_T_marker = pose7_to_mat((*mm["translation"], *mm["rotation"]))
    marker_T_map = np.linalg.inv(map_T_marker)
    lever_m = float(np.linalg.norm(marker_T_map[:3, 3]))
    print(f"tag {TAG_ID} lever arm |map_t_tag| = {lever_m:.2f} m "
          f"(~{lever_m * np.pi / 180.0:.3f} m of fix per deg of tag-orientation error)")

    # PGO silver reference: inv(correction_at(ts)) translation.
    with open(HARNESS / "out" / "prepared" / f"{a.recording}.pkl", "rb") as f:
        graph = pickle.loads(pickle.load(f)["pose_graph_bytes"])

    def ref_at(t: float) -> np.ndarray:
        return np.linalg.inv(transform_to_mat(graph.correction_at(float(t))))[:3, 3]

    # SINGLE per-frame fix (world->map translation) for every gated glimpse.
    gated = np.flatnonzero(passing)
    fix_single = np.array([(world_T_marker[i] @ marker_T_map)[:3, 3] for i in gated])
    ref_single = np.array([ref_at(ts[i]) for i in gated])
    single_err = np.linalg.norm(fix_single - ref_single, axis=1)

    # FUSED vs LATEST-SINGLE-BEST, per time-clustered visit.
    cfg = AggregationConfig()
    observations = [
        TagObservation(
            ts=float(ts[i]),
            marker_id=TAG_ID,
            pose=pose7_from_matrix(world_T_marker[i]),
            reproj_px=float(z["reproj_px"][i]),
            distance_m=float(z["range_m"][i]),
            view_angle_deg=float(z["view_deg"][i]),
        )
        for i in gated
    ]
    fused_err: list[float] = []
    latest_err: list[float] = []
    visit_n: list[int] = []
    for visit in cluster_by_time(observations, CLUSTER_GAP_S):
        if len(visit) < MIN_OBS:
            continue
        t_rep = max(o.ts for o in visit)
        ref_v = ref_at(t_rep)
        fused_pose = robust_cluster_pose(visit, cfg.rotation_weight_m_per_rad, cfg.huber_delta_m)
        fix_fused = (matrix_from_pose7(fused_pose) @ marker_T_map)[:3, 3]
        latest = max(visit, key=lambda o: o.ts)
        fix_latest = (matrix_from_pose7(latest.pose) @ marker_T_map)[:3, 3]
        fused_err.append(float(np.linalg.norm(fix_fused - ref_v)))
        latest_err.append(float(np.linalg.norm(fix_latest - ref_v)))
        visit_n.append(len(visit))

    fused = np.array(fused_err)
    latest = np.array(latest_err)
    print(f"\n{len(fused)} visits (glimpses/visit: {visit_n}); all vs the PGO silver reference:")
    print(f"  per-frame SINGLE fix error (all {len(single_err)} gated glimpses): "
          f"median {np.median(single_err):.2f}  p90 {np.percentile(single_err, 90):.2f} m")
    print(f"  LATEST-SINGLE-BEST per visit (old prior):  "
          f"median {np.median(latest):.2f}  p90 {np.percentile(latest, 90):.2f}  "
          f"mean {latest.mean():.2f} m")
    print(f"  FUSED per visit (jnav Huber aggregation):  "
          f"median {np.median(fused):.2f}  p90 {np.percentile(fused, 90):.2f}  "
          f"mean {fused.mean():.2f} m")
    red_med = 100.0 * (1 - np.median(fused) / np.median(latest))
    red_mean = 100.0 * (1 - fused.mean() / latest.mean())
    print(f"  -> fusion cuts the per-visit fix error median {np.median(latest):.2f} -> "
          f"{np.median(fused):.2f} m ({red_med:.0f}%), mean {latest.mean():.2f} -> "
          f"{fused.mean():.2f} m ({red_mean:.0f}%)")
    per_visit_win = int((fused < latest).sum())
    print(f"  fused beats latest-single in {per_visit_win}/{len(fused)} visits")

    _render(a.fig, single_err, latest, fused, visit_n, lever_m, revs, a.recording)
    print(f"\nwrote {a.fig}")
    return 0


def _render(
    fig_path: Path,
    single_err: np.ndarray,
    latest: np.ndarray,
    fused: np.ndarray,
    visit_n: list[int],
    lever_m: float,
    revs: dict[str, str],
    recording: str,
) -> None:
    fig, (ax_bar, ax_visit) = plt.subplots(1, 2, figsize=(12.5, 5.2))
    red = 100.0 * (1 - np.median(fused) / np.median(latest))
    fig.suptitle(
        f"Huber-fusing tag sightings cuts fiducial fix error "
        f"{np.median(latest):.1f} -> {np.median(fused):.1f} m ({red:.0f}%)  "
        f"[{recording} replay, real detections]",
        fontsize=12, fontweight="bold",
    )

    # Panel A: median fix error by method (p10-p90 whiskers).
    labels = ["single glimpse\n(per frame)", "LATEST single\n(old, per visit)",
              "FUSED Huber\n(this, per visit)"]
    data = [single_err, latest, fused]
    meds = [np.median(d) for d in data]
    lo = [np.median(d) - np.percentile(d, 10) for d in data]
    hi = [np.percentile(d, 90) - np.median(d) for d in data]
    colors = ["#b0b0b0", "#d1495b", "#2e7d32"]
    ax_bar.bar(labels, meds, yerr=[lo, hi], capsize=6, color=colors, edgecolor="black", linewidth=0.6)
    for i, m in enumerate(meds):
        ax_bar.text(i, m, f" {m:.2f} m", ha="center", va="bottom", fontweight="bold")
    ax_bar.set_ylabel("world->map fix translation error vs PGO silver (m)")
    ax_bar.set_title("median fix error (whiskers p10-p90)")
    ax_bar.grid(axis="y", alpha=0.3)

    # Panel B: paired per-visit latest vs fused.
    idx = np.arange(len(fused))
    width = 0.4
    ax_visit.bar(idx - width / 2, latest, width, label="LATEST single (old)", color="#d1495b")
    ax_visit.bar(idx + width / 2, fused, width, label="FUSED Huber (this)", color="#2e7d32")
    ax_visit.set_xticks(idx)
    ax_visit.set_xticklabels([f"v{i + 1}\nn={n}" for i, n in enumerate(visit_n)])
    ax_visit.set_ylabel("fix error vs PGO silver (m)")
    ax_visit.set_title("per visit: LATEST single vs FUSED")
    ax_visit.legend()
    ax_visit.grid(axis="y", alpha=0.3)

    fig.text(
        0.5, 0.005,
        f"hk_village3 tag-10 replay, {len(single_err)} gated glimpses over {len(fused)} visits | "
        f"lever arm {lever_m:.1f} m | gate: IPPE ambiguity>=2.0 & reproj<=3px | SEED=0 (no RNG) | "
        f"git trial={revs['trial']} dimos-code={revs['dimos-code']} | ref=inv(PGO correction), "
        f"silver ~6cm floor, wobbles ~0.4m late-run | "
        f"cmd: uv run --project /home/dimos/dimos-code python trial/harness/aggregation_fused_vs_single.py",
        ha="center", fontsize=6.5, color="#444",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
