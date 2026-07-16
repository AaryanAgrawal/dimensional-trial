#!/usr/bin/env bash
# One-command envelope sweep: reuses the existing venv -> marker_loc_demo/envelope_sweep.py -> out/envelope/.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "no .venv found -- run ./run.sh first (or create the venv per README)." >&2
  exit 1
fi

rm -rf out/envelope
mkdir -p out/envelope
./.venv/bin/python -m marker_loc_demo.envelope_sweep "$@"

echo
echo "wrote: $(pwd)/out/envelope/ENVELOPE.md"
cat out/envelope/ENVELOPE.md
