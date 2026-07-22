#!/usr/bin/env python3
"""Re-lane a recording so the UNMODIFIED dimos replay pipeline consumes a chosen
lidar stream, without touching dimos code.

Why this exists: `dimos --replay run <blueprint>` builds its map from whatever
ReplayConnection.lidar_stream() picks, and that is hardwired to
`_stream_name("go2_lidar", "lidar")` (robot/unitree/go2/connection.py) -- there
is NO -o override, env var, or config field to point it at another stream. A
mid360 recording carries the livox/FAST-LIO lane as `fastlio_lidar`, which the
replay can therefore never reach. Rather than re-implement a data path or edit
dimos, we select the lane at the DATA layer: copy the .db and rename the chosen
stream's tables to `lidar`. dimos then does ALL the localizing on those clouds,
100% unmodified.

This is pure stream selection, not a pipeline step: each dimos stream is its own
set of sqlite tables (`<s>`, `<s>_blob`, `<s>_rtree` + shadows) plus one
name->config row in `_streams` (config is name-agnostic, verified). Renaming
moves the recorded bytes verbatim -- no cloud is re-transformed, re-posed, or
re-serialized. The go2 lane is dropped from the copy; the source recording is
untouched (run the go2 lane straight off it).

odom/color_image are kept as-is: the RelocalizationModule consumes only
global_map (from VoxelGridMapper) + the premap and never reads odom, and both
lanes share one epoch recording clock, so the go2 odom still anchors the
driver's wall<->recording mapping correctly. (VoxelGridMapper.add_frame
voxelizes cloud points verbatim in frame_id='world' -- it never applies the
per-message pose -- so the fastlio clouds, already world-frame, map correctly
despite their placeholder poses.)

Run: python relane_db.py <src_db_stem> --lidar-from fastlio_lidar --out-stem <stem>.fastlio
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

DIMOS_ROOT = Path(__file__).resolve().parents[2] / "dimos"
DATA_DIR = DIMOS_ROOT / "data"

# The per-stream sqlite objects, by suffix. The _rtree virtual table's shadow
# tables (_rtree_rowid/_node/_parent) are renamed/dropped automatically by
# sqlite (>=3.25) when the rtree itself is, so they are not listed explicitly.
_STREAM_TABLES = ("", "_blob", "_rtree")


def _drop_stream(cur: sqlite3.Cursor, name: str) -> None:
    """Remove a stream and its registry row. DROP on the rtree virtual table
    also drops its shadow tables."""
    for suf in _STREAM_TABLES:
        cur.execute(f'DROP TABLE IF EXISTS "{name}{suf}"')
    cur.execute("DELETE FROM _streams WHERE name = ?", (name,))
    cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (name,))


def _rename_stream(cur: sqlite3.Cursor, src: str, dst: str) -> None:
    """Rename every table of stream `src` to `dst`, and its registry/sequence
    rows. `_streams.config` carries no stream name (verified), so only the
    primary-key `name` moves."""
    for suf in _STREAM_TABLES:
        cur.execute(f'ALTER TABLE "{src}{suf}" RENAME TO "{dst}{suf}"')
    cur.execute("UPDATE _streams SET name = ? WHERE name = ?", (dst, src))
    cur.execute("UPDATE sqlite_sequence SET name = ? WHERE name = ?", (dst, src))


def relane(src_stem: str, lidar_from: str, out_stem: str) -> Path:
    src = DATA_DIR / f"{src_stem}.db"
    dst = DATA_DIR / f"{out_stem}.db"
    if not src.exists():
        raise FileNotFoundError(src)
    if lidar_from == "lidar":
        raise ValueError("--lidar-from is already 'lidar'; nothing to re-lane")

    # Follow symlinks (data/*.db may point at external media) so the copy is a
    # real independent file, then work on it.
    shutil.copyfile(src.resolve(), dst)

    con = sqlite3.connect(str(dst))
    try:
        cur = con.cursor()
        have = {r[0] for r in cur.execute("SELECT name FROM _streams")}
        if lidar_from not in have:
            raise KeyError(f"{lidar_from!r} not in {sorted(have)}")
        if "lidar" in have:
            _drop_stream(cur, "lidar")  # discard the go2 lane in the copy
        _rename_stream(cur, lidar_from, "lidar")
        con.commit()
    finally:
        con.close()
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src_stem", help="source recording stem under dimos/data")
    ap.add_argument("--lidar-from", required=True,
                    help="stream to expose as `lidar` (e.g. fastlio_lidar)")
    ap.add_argument("--out-stem", required=True, help="output recording stem")
    a = ap.parse_args()
    out = relane(a.src_stem, a.lidar_from, a.out_stem)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
