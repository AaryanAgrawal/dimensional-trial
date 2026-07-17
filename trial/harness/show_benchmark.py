#!/usr/bin/env python3
"""THE relocalization benchmark, visualized: odom vs PGO vs the reloc module.

One physical referee tag per recording. Each system produces a cloud of
"where is the tag" placements; the tightness of each system's OWN cloud is
its error — no shared truth, no circularity:

  odom   — tag position from raw odometry pose at each sighting
  pgo    — same sightings, poses run through PGO's correction
  module — per benchmark section, the reloc module's estimated pose places
           the tag sightings its submap window contained (odom-bridged
           within the window, exactly like a live fix would be)

Outputs: per-recording placement scatters + a cross-recording bar chart,
PNGs in trial/results/figures/, one summary table on stdout.

Run: cd dimos && uv run python ../trial/harness/show_benchmark.py hk_village1 hk_village3 ...
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from markers import detect_all  # noqa: E402
from prep import pose7_to_mat  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2  # noqa: E402

FIGURES = HARNESS.parent / "results" / "figures"
REFEREE = 10  # villages' referee tag (benchmark_setup.yaml)


def placements(recording: str) -> dict[str, np.ndarray]:
    """Three placement clouds for the referee tag, in each system's frame."""
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    with open(HARNESS / "out" / "prepared" / f"{recording}.pkl", "rb") as f:
        prep = pickle.load(f)
    graph = pickle.loads(prep["pose_graph_bytes"])

    store = SqliteStore(path=str(dimos_root / "data" / f"{recording}.db"), must_exist=True)
    with store:
        rows = [r for r in detect_all(store, graph) if r["marker_id"] == REFEREE]
        lidar_poses = {}
        for idx, obs in enumerate(store.stream(prep["lidar_stream"], PointCloud2)):
            if obs.pose_tuple is not None:
                lidar_poses[idx] = pose7_to_mat(obs.pose_tuple)

    odom = np.array([r["T_world_tag_raw"][:3, 3] for r in rows])
    pgo = np.array([r["T_map_tag_corr"][:3, 3] for r in rows])

    # Module placements: sections' T_est applied to in-window sightings.
    with open(HARNESS / "out" / "results" / f"{recording}.ransac.json") as f:
        bench = json.load(f)
    module = []
    for res in bench["results"]:
        if res["status"] != "ok" or "T_est" not in res:
            continue
        s = next(s for s in prep["sections"] if s["frame_idx"] == res["frame_idx"])
        P = lidar_poses.get(res["frame_idx"])
        if P is None:
            continue
        T_est = np.asarray(res["T_est"])
        Pinv = np.linalg.inv(P)
        for r in rows:
            if s["ts"] - s["window_s"] <= r["ts"] <= s["ts"]:
                body_T_tag = Pinv @ r["T_world_tag_raw"]  # odom-bridged in window
                module.append((T_est @ body_T_tag)[:3, 3])
    return {"odom": odom, "pgo": pgo, "module": np.array(module)}


def spread(cloud: np.ndarray) -> float:
    """Median distance from the cloud's own centroid [m]."""
    if len(cloud) < 3:
        return float("nan")
    return float(np.median(np.linalg.norm(cloud - cloud.mean(0), axis=1)))


def main() -> int:
    recordings = sys.argv[1:] or ["hk_village1", "hk_village3", "hk_village5", "hk_village6"]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(parents=True, exist_ok=True)
    all_stats: dict[str, dict[str, float]] = {}
    colors = {"odom": "tab:gray", "pgo": "tab:blue", "module": "tab:red"}

    for rec in recordings:
        cl = placements(rec)
        all_stats[rec] = {k: spread(v) for k, v in cl.items()}
        n = {k: len(v) for k, v in cl.items()}
        print(f"{rec}: " + "  ".join(
            f"{k}={all_stats[rec][k]:.3f}m(n={n[k]})" for k in ("odom", "pgo", "module")))

        fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), sharex=True, sharey=True)
        for ax, k in zip(axes, ("odom", "pgo", "module")):
            c = cl[k]
            if len(c):
                cc = c - c.mean(0)  # center each system's own cloud
                ax.scatter(cc[:, 0], cc[:, 1], s=12, alpha=0.6, color=colors[k])
            ax.add_patch(plt.Circle((0, 0), 0.5, fill=False, ls=":", lw=1, alpha=0.5))
            ax.set_title(f"{k}  median dev {all_stats[rec][k]:.2f} m  (n={n[k]})")
            ax.set_aspect("equal")
            ax.grid(alpha=0.3)
            ax.set_xlabel("x − centroid [m]")
        axes[0].set_ylabel("y − centroid [m]")
        fig.suptitle(
            f"{rec} — ONE physical tag (id {REFEREE}), each system's own placements "
            f"(centered; dotted circle = 0.5 m) · replay · self-consistency, no shared truth",
            fontsize=9.5)
        p = FIGURES / f"benchmark_{rec}.png"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  figure: {p}")

    # Cross-recording bar chart
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(recordings))
    w = 0.26
    for i, k in enumerate(("odom", "pgo", "module")):
        vals = [all_stats[r][k] for r in recordings]
        bars = ax.bar(x + (i - 1) * w, vals, w, label=k, color=colors[k], alpha=0.85)
        for b, v in zip(bars, vals):
            if np.isfinite(v):
                ax.annotate(f"{v:.2f}", (b.get_x() + b.get_width() / 2, v),
                            ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x, [r.replace("hk_", "") for r in recordings])
    ax.set_ylabel("median tag-placement deviation from own centroid [m]")
    ax.set_title("The relocalization benchmark — one referee tag, three systems\n"
                 "(lower = system places the same physical tag more consistently)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.text(0.01, 0.01, "replay · self-consistency per system (no shared truth) · "
             "module = RANSAC→judge per section", fontsize=6, alpha=0.7)
    p = FIGURES / "benchmark_odom_pgo_module.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    print(f"headline figure: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
