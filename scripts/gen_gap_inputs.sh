#!/usr/bin/env bash
# Generate GAP input graphs used by the trace pipeline.
#
# Usage: scripts/gen_gap_inputs.sh [inputs_csv]
#   inputs_csv: comma-separated names like kron17,kron19,urand17,urand19
#
# Name convention:
#   kron<N>  -> GAP converter -g <N>
#   urand<N> -> GAP converter -u <N>
#
# For each input, this creates:
#   traces/gap/inputs/<name>.sg   (unweighted, for bfs/pr/bc/cc/tc)
#   traces/gap/inputs/<name>.wsg  (weighted, for sssp)

set -euo pipefail

INPUTS_CSV="${1:-kron17,kron19,urand17,urand19}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONVERTER="${REPO_ROOT}/vendor/gapbs/converter"
OUT_DIR="${REPO_ROOT}/traces/gap/inputs"

[[ -x "$CONVERTER" ]] || { echo "error: missing executable $CONVERTER" >&2; exit 2; }
mkdir -p "$OUT_DIR"

IFS=',' read -r -a INPUTS <<< "$INPUTS_CSV"

for input in "${INPUTS[@]}"; do
  if [[ "$input" =~ ^kron([0-9]+)$ ]]; then
    flag="-g"
    scale="${BASH_REMATCH[1]}"
  elif [[ "$input" =~ ^urand([0-9]+)$ ]]; then
    flag="-u"
    scale="${BASH_REMATCH[1]}"
  else
    echo "error: unsupported input name '$input' (expected kron<N> or urand<N>)" >&2
    exit 2
  fi

  sg="${OUT_DIR}/${input}.sg"
  wsg="${OUT_DIR}/${input}.wsg"

  if [[ -f "$sg" ]]; then
    echo "[skip] $sg"
  else
    echo "[gen ] $sg"
    "$CONVERTER" "$flag" "$scale" -s -b "$sg"
  fi

  if [[ -f "$wsg" ]]; then
    echo "[skip] $wsg"
  else
    echo "[gen ] $wsg"
    "$CONVERTER" "$flag" "$scale" -s -w -b "$wsg"
  fi
done
