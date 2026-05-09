#!/usr/bin/env bash
# Generate one ChampSim trace for a GAP kernel + pre-built input graph.
#
# Usage: scripts/gen_gap_trace.sh <kernel> <input_name> [cap_M]
#   kernel:       bfs | pr | sssp | bc | cc | tc
#   input_name:   kron18 | kron20 | urand18 | urand20  (must exist as .sg in traces/gap/inputs/)
#   cap_M:        instruction trace cap in millions, default 50
#
# Output: traces/gap/<kernel>_<input_name>.trace.xz
# Intermediate: writes a raw trace to disk first, then xz-compresses it
# (peak disk ~ cap_M * 64 MB = ~3 GB at default cap).

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <kernel> <input_name> [cap_M]" >&2
  exit 1
fi

KERNEL="$1"
INPUT="$2"
CAP_M="${3:-50}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN="${REPO_ROOT}/vendor/pin/pin"
TRACER="${REPO_ROOT}/champsim/tracer/pin/obj-intel64/champsim_tracer.so"
KERNEL_BIN="${REPO_ROOT}/vendor/gapbs/${KERNEL}"
# SSSP requires a weighted graph (.wsg); all other GAP kernels take unweighted (.sg).
case "$KERNEL" in
  sssp) INPUT_EXT="wsg" ;;
  *)    INPUT_EXT="sg" ;;
esac
INPUT_FILE="${REPO_ROOT}/traces/gap/inputs/${INPUT}.${INPUT_EXT}"
OUT_DIR="${REPO_ROOT}/traces/gap"
RAW_TRACE="${OUT_DIR}/${KERNEL}_${INPUT}.trace"
XZ_TRACE="${RAW_TRACE}.xz"

for f in "$PIN" "$TRACER" "$KERNEL_BIN" "$INPUT_FILE"; do
  [[ -e "$f" ]] || { echo "error: missing $f" >&2; exit 2; }
done

if [[ -f "$XZ_TRACE" ]]; then
  echo "[skip] $XZ_TRACE already present"
  exit 0
fi

mkdir -p "$OUT_DIR"

CAP_INST=$((CAP_M * 1000000))

echo "[trace] $KERNEL on $INPUT, cap=${CAP_M}M instructions"
START=$(date +%s)
# setarch -R disables ASLR so PIE binaries load at a fixed base, making the
# trace's runtime PCs align with `objdump -d` of the binary across runs.
# Without this, every trace would have a different load base and the PC →
# assembly mapping for the W3 dataset would break.
setarch "$(uname -m)" -R \
  "$PIN" -t "$TRACER" -o "$RAW_TRACE" -t "$CAP_INST" -- \
  "$KERNEL_BIN" -f "$INPUT_FILE" -n 1 \
  > "${OUT_DIR}/${KERNEL}_${INPUT}.kernelout" 2>&1
PIN_END=$(date +%s)
echo "[trace] pin done in $((PIN_END - START))s; raw size $(du -h "$RAW_TRACE" | cut -f1)"

# Compress + remove raw to keep disk pressure bounded.
# `-3` gives ~30× faster compression than the default `-6` with only ~3% size
# penalty on this workload (Pin trace data is highly compressible regardless).
xz -3 -T 4 -f "$RAW_TRACE"
echo "[xz   ] done in $(( $(date +%s) - PIN_END))s; xz size $(du -h "$XZ_TRACE" | cut -f1)"
echo "[done ] $XZ_TRACE"
