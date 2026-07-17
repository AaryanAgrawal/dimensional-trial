#!/usr/bin/env python3
"""hk_building_all_around: lesh's no-markers PGO check, quantified.

His test at this scale is visual (raw vs PGO map layers in rerun). This adds
the number behind the eyeball: per keyframe, |optimized - local| translation
= the drift correction PGO itself found necessary at that point of the walk.
A drifting odom shows a growing profile; PGO's loop closures anchor the ends.
No markers exist here, so this measures PGO's own opinion of the drift — a
consistency signal, NOT independent truth (markers remain the only ruler).

Run: cd dimos && uv run python ../trial/harness/allaround_profile.py [recording]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).parent
sys.path.insert(0, str(HARNESS))
from prep import build_graph  # noqa: E402

from dimos.memory2.store.sqlite import SqliteStore  # noqa: E402


def main() -> int:
    recording = sys.argv[1] if len(sys.argv) > 1 else "hk_building_all_around"
    dimos_root = Path(__file__).resolve().parents[2] / "dimos"
    store = SqliteStore(path=str(dimos_root / "data" / f"{recording}.db"), must_exist=True)
    with store:
        graph, pgo_s = build_graph(store, "lidar")

    kf = graph.keyframes
    t0 = kf[0].ts
    ts = np.array([k.ts - t0 for k in kf])
    raw = np.array([[k.local.translation.x, k.local.translation.y, k.local.translation.z]
                    for k in kf])
    opt = np.array([[k.optimized.translation.x, k.optimized.translation.y,
                     k.optimized.translation.z] for k in kf])
    corr = np.linalg.norm(opt - raw, axis=1)
    path_len = float(np.linalg.norm(np.diff(opt[:, :2], axis=0), axis=1).sum())

    stats = {
        "recording": recording, "keyframes": len(kf), "loops": len(graph.loops),
        "pgo_seconds": pgo_s, "duration_s": float(ts[-1]),
        "path_length_m_pgo": path_len,
        "correction_median_m": float(np.median(corr)),
        "correction_p90_m": float(np.percentile(corr, 90)),
        "correction_max_m": float(corr.max()),
        "note": "correction = PGO's own opinion of odom drift; consistency, not truth",
        "unix": time.time(),
    }
    print(json.dumps(stats, indent=1))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].plot(opt[:, 0], opt[:, 1], lw=1, label="PGO trajectory")
    axes[0].plot(raw[:, 0], raw[:, 1], lw=1, alpha=0.6, label="raw odom")
    axes[0].set_aspect("equal"); axes[0].legend(fontsize=8); axes[0].grid(alpha=0.3)
    axes[0].set_title(f"{recording}: {path_len:.0f} m walk")
    axes[1].plot(ts, corr, lw=1)
    axes[1].set_xlabel("time [s]"); axes[1].set_ylabel("|PGO correction| [m]")
    axes[1].set_title(f"drift correction PGO applied ({len(graph.loops)} loops)")
    axes[1].grid(alpha=0.3)
    fig.suptitle("replay · no markers here: PGO self-consistency, not independent truth", fontsize=9)
    out_dir = HARNESS.parent / "results" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{recording}_pgo_profile.png", dpi=150, bbox_inches="tight")
    with open(HARNESS / "out" / "markers" / f"{recording}.profile.json", "w") as f:
        json.dump(stats, f, indent=1)
    print(f"wrote {out_dir}/{recording}_pgo_profile.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
