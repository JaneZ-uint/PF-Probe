#!/usr/bin/env bash
# Run a single ChampSim smoke job for the given prefetcher × trace.
#
# Usage: scripts/run_smoke.sh <prefetcher_name> <trace_basename> [warmup_M] [sim_M]
#   warmup_M default 1, sim_M default 5 (i.e. 1M warmup + 5M simulation).
#
# Reads traces from traces/<trace_basename> and writes JSON to
# data/w2_smoke/<trace_short>_<prefetcher>.json
#
# trace_basename can be either the full filename
# (e.g. "605.mcf_s-1554B.champsimtrace.xz") or a short alias from
# {mcf, lbm, omnetpp, bwaves}. The latter is resolved via the table below.

set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "usage: $0 <prefetcher_name> <trace_basename_or_alias> [warmup_M] [sim_M]" >&2
  exit 1
fi

PREF="$1"
TRACE_ARG="$2"
WARMUP_M="${3:-1}"
SIM_M="${4:-5}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${REPO_ROOT}/champsim/bin/champsim_${PREF}"
TRACE_DIR="${REPO_ROOT}/traces"
OUT_DIR="${REPO_ROOT}/data/w2_smoke"
mkdir -p "$OUT_DIR"

declare -A ALIAS=(
  [mcf]="605.mcf_s-1554B.champsimtrace.xz"
  [lbm]="619.lbm_s-2677B.champsimtrace.xz"
  [omnetpp]="620.omnetpp_s-874B.champsimtrace.xz"
  [bwaves]="603.bwaves_s-3699B.champsimtrace.xz"
)

if [[ -n "${ALIAS[$TRACE_ARG]:-}" ]]; then
  TRACE_FILE="${ALIAS[$TRACE_ARG]}"
  TRACE_SHORT="$TRACE_ARG"
else
  TRACE_FILE="$TRACE_ARG"
  # Strip .champsimtrace.xz suffix and SPEC numeric prefix for short name.
  TRACE_SHORT="${TRACE_FILE%.champsimtrace.xz}"
  TRACE_SHORT="${TRACE_SHORT##*[0-9].}"
  TRACE_SHORT="${TRACE_SHORT%%_s-*}"
fi

TRACE_PATH="${TRACE_DIR}/${TRACE_FILE}"
if [[ ! -f "$TRACE_PATH" ]]; then
  echo "error: trace not found: $TRACE_PATH" >&2
  exit 2
fi
if [[ ! -x "$BIN" ]]; then
  echo "error: champsim binary not found or not executable: $BIN" >&2
  echo "       run scripts/build_prefetcher.sh ${PREF} first" >&2
  exit 3
fi

OUT_JSON="${OUT_DIR}/${TRACE_SHORT}_${PREF}.json"
WARMUP_INST=$((WARMUP_M * 1000000))
SIM_INST=$((SIM_M * 1000000))

echo "[run] ${PREF} on ${TRACE_SHORT}: ${WARMUP_M}M warmup + ${SIM_M}M sim → ${OUT_JSON}"
START=$(date +%s)
"$BIN" \
  --warmup-instructions "$WARMUP_INST" \
  --simulation-instructions "$SIM_INST" \
  --json "$OUT_JSON" \
  "$TRACE_PATH" \
  > "${OUT_JSON%.json}.stdout" 2>&1
END=$(date +%s)
echo "[done] ${PREF} on ${TRACE_SHORT} in $((END-START))s"
