#!/usr/bin/env bash
# THE RELOCALIZATION BENCHMARK — one command, whole suite (benchmark_setup.yaml v2).
# Usage: ./run_suite.sh [recording ...]   (default: the full 7-recording suite)
# Everything replay, deterministic seeds, full denominators. Outputs:
#   trial/harness/out/{prepared,results,markers}/ + trial/results/figures/ + .rrd files
set -uo pipefail
cd "$(dirname "$0")/../../dimos" || exit 1
H=../trial/harness
LOG=$H/out/suite_$(date +%Y%m%d_%H%M%S).log
mkdir -p $H/out
echo "suite run -> $LOG (dimos $(git rev-parse --short HEAD), trial $(git -C ../ rev-parse --short HEAD))" | tee -a $LOG

MARKER_RUNS_DEFAULT="hk_village1 hk_village3 hk_village5 hk_village6"
MID360="recording_go2_mid360_2026-05-29_4-45pm-PST"
PGO_ONLY_DEFAULT="go2_hongkong_office hk_building_all_around"
RUNS=${@:-"$MARKER_RUNS_DEFAULT $MID360 $PGO_ONLY_DEFAULT"}

for rec in $RUNS; do
  echo "=== $rec ===" | tee -a $LOG
  EXTRA=""
  NQ=24
  [ "$rec" = "hk_village3" ] && NQ=120
  [ "$rec" = "go2_hongkong_office" ] && NQ=40
  [ "$rec" = "$MID360" ] && { NQ=40; EXTRA="--lidar-pose-from-odom odom"; }

  uv run python $H/prep.py "$rec" --n-queries $NQ $EXTRA 2>&1 | grep -E "PGO:|premap:|sections:|wrote|Traceback|Error" | tee -a $LOG

  # Marker stage only where tags exist (see benchmark_setup.yaml)
  case "$rec" in
    hk_village*|$MID360)
      uv run python $H/markers.py "$rec" 2>&1 | grep -E "detections:|marker map|fiducial fixes|wrote|note|Traceback" | tee -a $LOG ;;
    *) echo "(no tags — PGO-truth-only run)" | tee -a $LOG ;;
  esac

  uv run python $H/run_bench.py "$rec" --config ransac 2>&1 | grep -E "^bench:|success_rate_all|median_err|median_dt|wrote|Traceback" | tee -a $LOG

  # Fiducial configs only where a decorrelated fixes file exists with content
  FIX=$H/out/markers/$rec.fixes.json
  if [ -s "$FIX" ] && [ "$(python3 -c "import json;print(len(json.load(open('$FIX'))))" 2>/dev/null)" != "0" ]; then
    for cfg in "ransac+fiducial" "fiducial+judge"; do
      uv run python $H/run_bench.py "$rec" --config "$cfg" --fiducial-fixes "$FIX" 2>&1 | grep -E "^bench:|success_rate_all|median_dt|wrote|Traceback" | tee -a $LOG
    done
  fi
done

echo "=== cross-recording + figures ===" | tee -a $LOG
uv run python $H/cross_recording_confidence.py 2>&1 | tail -5 | tee -a $LOG
uv run python $H/allaround_profile.py hk_building_all_around 2>&1 | grep -E "path_length|correction_median|wrote" | tee -a $LOG
uv run python $H/allaround_profile.py go2_hongkong_office 2>&1 | grep -E "path_length|correction_median|wrote" | tee -a $LOG
uv run python $H/make_rrd.py $MARKER_RUNS_DEFAULT 2>&1 | grep wrote | tee -a $LOG
echo "=== SUITE COMPLETE ===" | tee -a $LOG
