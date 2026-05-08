#!/usr/bin/env bash
# Download a small subset of SPEC2017 ChampSim simpoint traces.
# Per plan §3, we use 4 memory-intensive benchmarks: mcf, lbm, omnetpp, bwaves.
# We grab one simpoint per benchmark to start; can extend later.
#
# Usage: ./scripts/download_traces.sh [target_dir]
#   target_dir defaults to ./traces/
#
# Source: https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/
# Provided by Daniel Jiménez (Texas A&M).

set -euo pipefail

TARGET_DIR="${1:-traces}"
BASE_URL="https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu"

# One simpoint per benchmark — chosen as the longest-running phase per simpoint
# weights table (see weights-and-simpoints-speccpu.tar.gz on the same site if
# you want to pick differently).
TRACES=(
  "605.mcf_s-1554B.champsimtrace.xz"
  "619.lbm_s-2677B.champsimtrace.xz"
  "620.omnetpp_s-874B.champsimtrace.xz"
  "603.bwaves_s-3699B.champsimtrace.xz"
)

mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

for t in "${TRACES[@]}"; do
  if [[ -s "$t" ]]; then
    echo "[skip] $t already present ($(du -h "$t" | cut -f1))"
    continue
  fi
  echo "[get ] $t"
  # -C - resumes a partial download
  curl -fL -C - -o "$t" "$BASE_URL/$t"
  echo "[done] $t ($(du -h "$t" | cut -f1))"
done

echo
echo "=== Trace inventory ==="
ls -lh *.xz 2>/dev/null || true
echo
echo "Total:"
du -sh . 2>/dev/null
