#!/usr/bin/env bash
# One-command run: fresh venv (if missing) -> full synthetic demo -> out/.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "creating venv..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

rm -rf out
mkdir -p out
./.venv/bin/python -m marker_loc_demo.main "$@"

echo
echo "wrote: $(pwd)/out/metrics.json"
cat out/metrics.json
