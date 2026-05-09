#!/usr/bin/env bash
# Quick champsim_no sweep across all GAP traces — 5M sim window each, par=4.
# Output: data/gap_smoke/<trace>_no.json with per-trace IPC + per-PC AMAT.
# Used for filling the W3b Step 2 inventory table in notebooks/02-...

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/data/gap_smoke"
mkdir -p "$OUT_DIR"

ls "${REPO_ROOT}/traces/gap/"*.trace.xz | xargs -I{} -P 4 bash -c '
  trace="$1"
  name=$(basename "$trace" .trace.xz)
  out="'"$OUT_DIR"'/${name}_no.json"
  if [[ -f "$out" ]]; then echo "[skip] $name"; exit 0; fi
  "'"$REPO_ROOT"'/champsim/bin/champsim_no" \
    --warmup-instructions 1000000 \
    --simulation-instructions 5000000 \
    --json "$out" "$trace" >/dev/null 2>&1 \
    && echo "[done] $name"
' _ {}
