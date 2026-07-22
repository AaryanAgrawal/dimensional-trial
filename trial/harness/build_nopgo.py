#!/usr/bin/env python
"""Export the RAW (pre-PGO) accumulated map that `dimos map global` builds but
never writes out.

`dimos map global --export` forces --pgo and only exports the PGO-corrected map
(map.py:670-673 writes `pgo_map`). The CLI *always* also builds the raw map at
map.py:581 (`global_map = _accumulate(kept, ...)` with graph=None) but discards
it. This script reproduces the CLI's raw path — the same spatial dedup, the same
world-frame registration decision — and calls the CLI's OWN accumulation
function `_accumulate` (graph=None) so the accumulation math is the real dimos
code, never a re-implementation. It then writes the cloud via the same
`PointCloud2.lcm_encode` the CLI export uses.

The go2 lidar is recorded already world-registered (frame_id == "world"), so the
raw map is exactly: accumulate the recorded clouds in the world frame with no
pose-graph optimization. We assert that frame so a future dataset that isn't
world-registered fails loudly instead of silently producing a wrong map.

Mirrors map.py main() defaults: voxel=0.05, block_count=2_000_000, pgo_tol=0.3.

Usage: build_nopgo.py <dataset> <out.pc2.lcm> [--carve] [--device CPU:0]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

# The real CLI accumulation function — same one `dimos map global` calls at
# map.py:581 for its raw map. We reuse it, we do not re-derive it.
from dimos.mapping.utils.cli.map import _accumulate
from dimos.memory2.cli.dataset import open_store, resolve_dataset
from dimos.memory2.tf import StreamTF
from dimos.memory2.type.observation import Observation
from dimos.memory2.utils.progress import progress
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("out", type=Path)
    ap.add_argument("--voxel", type=float, default=0.05)
    ap.add_argument("--block-count", type=int, default=2_000_000)
    ap.add_argument("--pgo-tol", type=float, default=0.3)  # map.py:320 default
    ap.add_argument("--device", default="CPU:0")
    ap.add_argument("--carve", action="store_true")
    args = ap.parse_args()

    db_path = resolve_dataset(args.dataset)
    store = open_store(db_path)
    lidar = store.stream("lidar", PointCloud2)
    total = lidar.count()
    print(lidar.summary())

    first_obs = next(iter(lidar), None)
    if first_obs is None:
        raise SystemExit("empty lidar stream")
    cloud_frame = first_obs.data.frame_id

    # go2 recordings are stored already world-registered. This script's raw-map
    # definition (accumulate verbatim in world, register=None) only holds then.
    if cloud_frame != "world":
        raise SystemExit(
            f"clouds are in {cloud_frame!r}, not 'world'; this raw-export path "
            "assumes world-registered clouds (register=None). Use `dimos map "
            "global` for tf-registered datasets."
        )
    tf_buf = StreamTF.from_store(store)  # unused for register (kept verbatim), parity with CLI
    _ = tf_buf
    print("clouds already in world frame 'world'; accumulating verbatim")

    # --- Spatial dedup: verbatim from map.py:493-537 (selection, not math). ---
    def _position(obs: Observation[Any]) -> tuple[float, float, float] | None:
        pose = obs.pose
        if pose is not None and not (pose.position.is_zero() or pose.orientation.is_zero()):
            return (pose.position.x, pose.position.y, pose.position.z)
        return None

    seen: dict[Any, tuple[Observation[Any], tuple[float, float, float]]] = {}
    for i, obs in enumerate(lidar):
        pos = _position(obs)
        if pos is None:
            continue
        if args.pgo_tol > 0:
            key: Any = (
                math.floor(pos[0] / args.pgo_tol),
                math.floor(pos[1] / args.pgo_tol),
                math.floor(pos[2] / args.pgo_tol),
            )
        else:
            key = i
        seen[key] = (obs, pos)

    n_kept = len(seen)
    pct = 100 * n_kept / total if total else 0
    print(f"dedup: kept [{n_kept}/{total}] frames ({pct:.1f}%) at tol={args.pgo_tol}m")
    kept = [obs for obs, _ in seen.values()]

    # --- Raw map: the CLI's own _accumulate, graph=None, register=None. ---
    with progress(n_kept, "reconstructing raw (no-pgo) map") as bar:
        global_map = _accumulate(
            kept,
            voxel=args.voxel,
            block_count=args.block_count,
            device=args.device,
            graph=None,  # <-- the only difference from the PGO build
            register=None,  # clouds already world-registered
            carve_columns=args.carve,
            progress_cb=bar,
        )

    if global_map is None:
        raise SystemExit("accumulation produced no cloud")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(global_map.lcm_encode())
    print(f"wrote {args.out} ({len(global_map.pointcloud.points)} points, carve={args.carve})")


if __name__ == "__main__":
    main()
