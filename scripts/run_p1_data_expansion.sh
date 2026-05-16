#!/usr/bin/env bash
# P1 data expansion pipeline.
#
# Usage:
#   scripts/run_p1_data_expansion.sh [new_inputs_csv] [trace_cap_M] [trace_par] [grid_par] [sim_M] [warmup_M]
#
# Default expansion adds four graph inputs:
#   kron17,kron19,urand17,urand19
#
# The script is restartable:
#   - existing graphs are skipped
#   - existing traces are skipped
#   - existing ChampSim JSONs are skipped
#
# It writes the expanded train/test JSONL and ShareGPT files back to data/dataset/.

set -euo pipefail

NEW_INPUTS="${1:-kron17,kron19,urand17,urand19}"
TRACE_CAP_M="${2:-50}"
TRACE_PAR="${3:-4}"
GRID_PAR="${4:-8}"
SIM_M="${5:-30}"
WARMUP_M="${6:-1}"

KERNELS="bfs,pr,sssp,bc,cc,tc"
BASE_INPUTS="kron18,kron20,urand18,urand20"
ALL_INPUTS="${BASE_INPUTS},${NEW_INPUTS}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== P1 data expansion ==="
echo "new inputs:  ${NEW_INPUTS}"
echo "all inputs:  ${ALL_INPUTS}"
echo "trace cap:   ${TRACE_CAP_M}M"
echo "trace par:   ${TRACE_PAR}"
echo "grid par:    ${GRID_PAR}"
echo "sim/warmup:  ${SIM_M}M / ${WARMUP_M}M"
echo

echo "[1/5] Generating missing GAP input graphs..."
scripts/gen_gap_inputs.sh "$NEW_INPUTS"

echo
echo "[2/5] Generating traces for new inputs..."
scripts/gen_all_gap_traces.sh "$TRACE_CAP_M" "$TRACE_PAR" "$NEW_INPUTS" "$KERNELS"

echo
echo "[3/5] Running ChampSim grid for all inputs..."
scripts/run_w3_grid.sh "$GRID_PAR" "$SIM_M" "$WARMUP_M" "$ALL_INPUTS" "$KERNELS"

echo
echo "[4/5] Rebuilding expanded dataset..."
python3 scripts/build_dataset.py \
  --grid-dir data/w3_grid \
  --inputs "$ALL_INPUTS" \
  --kernels "$KERNELS" \
  --output-dir data/dataset

echo
echo "[5/5] Converting expanded dataset to LLaMA-Factory ShareGPT format..."
python3 training/convert_to_sharegpt.py \
  --train data/dataset/train.jsonl \
  --test data/dataset/test.jsonl \
  --output-dir data/dataset

echo
echo "=== P1 data expansion complete ==="
