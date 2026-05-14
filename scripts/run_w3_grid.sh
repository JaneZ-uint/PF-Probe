#!/usr/bin/env bash
# W3b Step 4 — Full prefetcher × degree × trace grid.
#
# 13 configs × 24 traces = 312 ChampSim runs.
# Each run: 1M warmup + 30M simulation instructions.
# Output: data/w3_grid/<kernel>_<input>_<config>.json
#
# Usage: scripts/run_w3_grid.sh [parallelism] [sim_M] [warmup_M]
#
# Defaults: parallelism=8, sim_M=30, warmup_M=1
# Skips already-existing JSON files (idempotent / restartable).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${REPO_ROOT}/champsim/bin"
TRACE_DIR="${REPO_ROOT}/traces/gap"
OUT_DIR="${REPO_ROOT}/data/w3_grid"

PAR="${1:-8}"
SIM_M="${2:-30}"
WARMUP_M="${3:-1}"

WARMUP_INST=$((WARMUP_M * 1000000))
SIM_INST=$((SIM_M * 1000000))

KERNELS=(bfs pr sssp bc cc tc)
INPUTS=(kron18 kron20 urand18 urand20)
CONFIGS=(no ip_stride_d1 ip_stride_d2 ip_stride_d3 stream_d1 stream_d2 stream_d3 sms_d1 sms_d2 sms_d3 sandbox_d1 sandbox_d2 sandbox_d3)

mkdir -p "$OUT_DIR"

# Build job list
JOBS=()
for kernel in "${KERNELS[@]}"; do
  for input in "${INPUTS[@]}"; do
    trace="${TRACE_DIR}/${kernel}_${input}.trace.xz"
    if [[ ! -f "$trace" ]]; then
      echo "[warn] trace not found: $trace" >&2
      continue
    fi
    for config in "${CONFIGS[@]}"; do
      out_json="${OUT_DIR}/${kernel}_${input}_${config}.json"
      if [[ -f "$out_json" ]]; then
        continue  # skip completed
      fi
      binary="${BIN_DIR}/champsim_${config}"
      if [[ ! -x "$binary" ]]; then
        echo "[warn] binary not found: $binary" >&2
        continue
      fi
      JOBS+=("${binary}|${trace}|${out_json}|${kernel}_${input}_${config}")
    done
  done
done

TOTAL=${#JOBS[@]}
echo "=== W3 grid: ${TOTAL} jobs to run (par=${PAR}, warmup=${WARMUP_M}M, sim=${SIM_M}M) ==="

if [[ $TOTAL -eq 0 ]]; then
  echo "Nothing to do — all JSONs already exist."
  exit 0
fi

# Worker function
run_one() {
  local spec="$1"
  IFS='|' read -r binary trace out_json label <<< "$spec"
  local start
  start=$(date +%s)
  if "$binary" \
      --warmup-instructions "$WARMUP_INST" \
      --simulation-instructions "$SIM_INST" \
      --json "$out_json" \
      "$trace" > /dev/null 2>&1; then
    local end
    end=$(date +%s)
    echo "[done] ${label} ($((end-start))s)"
  else
    local end
    end=$(date +%s)
    echo "[FAIL] ${label} ($((end-start))s)" >&2
  fi
}
export -f run_one
export WARMUP_INST SIM_INST

START=$(date +%s)

printf '%s\n' "${JOBS[@]}" | xargs -I{} -P "$PAR" bash -c 'run_one "$@"' _ {}

END=$(date +%s)
ELAPSED=$((END-START))

# Summary
DONE=$(find "$OUT_DIR" -name '*.json' | wc -l)
echo
echo "========================================="
echo "Grid complete: ${DONE}/312 JSONs in ${ELAPSED}s ($(( ELAPSED / 60 ))m$(( ELAPSED % 60 ))s)"
echo "========================================="

# Quick sanity: check a few JSONs have per_pc_load_latency
echo
echo "Spot-check per_pc_load_latency presence:"
for f in $(ls "$OUT_DIR"/*.json 2>/dev/null | shuf | head -5); do
  name=$(basename "$f" .json)
  pc_count=$(python3 -c "
import json
with open('$f') as fh: d = json.load(fh)
ppl = d[0]['roi'].get('cpu0_L1D', {}).get('per_pc_load_latency', {})
print(len(ppl))
" 2>/dev/null || echo "ERR")
  echo "  ${name}: ${pc_count} PCs"
done
