#!/usr/bin/env bash
# Overnight IK-2005 40-record re-run.
#
# Step 1 gates step 2: a quick validity check re-runs two unchanged records on
# frame 0909 and compares them against the archived non-convergence re-run.  If
# it passes, the full 12-frame / 40-record matrix launches (0909 headline frame
# first); if it fails, the long run is held.
#
# Frame-level resume: run_ik2005_all_frames.py skips a frame whose archive
# already exists in the out-dir, so re-running this script after an interrupt
# only re-does the in-progress frame.
set -uo pipefail
cd "$(dirname "$0")/.."

# Use the active environment's interpreter by default; override with PYTHON=...
PY="${PYTHON:-python}"
OUT_DIR=output/ik2005_40record
CODES=0909,0303,0306,0606,0612,0918,1212,1224,1515,1530,1818,1836

echo "=== $(date) IK-2005 40-record overnight run ==="
echo "=== $(date) step 1/2: validity check (0909 vs archive) ==="
if ! "$PY" scripts/ik2005_validity_check.py; then
  echo "=== $(date) VALIDITY CHECK FAILED — holding, not launching the long run ==="
  exit 1
fi

echo "=== $(date) step 2/2: full 40-record run, frames: ${CODES} ==="
if ! "$PY" scripts/run_ik2005_all_frames.py --out-dir "$OUT_DIR" --codes "$CODES"; then
  echo "=== $(date) FULL RUN FAILED ==="
  exit 1
fi
echo "=== $(date) DONE ==="
