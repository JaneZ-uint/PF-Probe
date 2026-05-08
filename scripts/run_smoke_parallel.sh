#!/usr/bin/env bash
# Parallel smoke grid: run 4 traces concurrently, 5 prefetchers each (serial
# within a trace to avoid contention on a single trace decoder).
#
# Wall-clock target: ~25-30 minutes on the 22-core host instead of ~100
# minutes serial. Each champsim process is single-threaded; running 4 in
# parallel uses 4 cores, peak memory ≤ 2 GB.
#
# Usage: scripts/run_smoke_parallel.sh [warmup_M] [sim_M]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

WARMUP_M="${1:-1}"
SIM_M="${2:-5}"

PREFETCHERS=(no ip_stride stream sms sandbox)
TRACES=(mcf lbm omnetpp bwaves)

LOGDIR="${REPO_ROOT}/data/w2_smoke/logs"
mkdir -p "$LOGDIR"

# Worker: run all 5 prefetchers on one trace, sequentially.
trace_worker() {
  local trace="$1"
  for pref in "${PREFETCHERS[@]}"; do
    "${SCRIPT_DIR}/run_smoke.sh" "$pref" "$trace" "$WARMUP_M" "$SIM_M" \
      >> "${LOGDIR}/${trace}.log" 2>&1
  done
  echo "[trace-done] ${trace}"
}

START=$(date +%s)
pids=()
for trace in "${TRACES[@]}"; do
  case "$trace" in
    mcf)     fn="605.mcf_s-1554B.champsimtrace.xz" ;;
    lbm)     fn="619.lbm_s-2677B.champsimtrace.xz" ;;
    omnetpp) fn="620.omnetpp_s-874B.champsimtrace.xz" ;;
    bwaves)  fn="603.bwaves_s-3699B.champsimtrace.xz" ;;
  esac
  if [[ ! -f "${REPO_ROOT}/traces/${fn}" ]]; then
    echo "[skip] trace ${trace} not present"
    continue
  fi
  trace_worker "$trace" &
  pids+=($!)
  echo "[launched] trace=${trace} pid=$!"
done

# Wait for all workers.
fail=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    fail=$((fail+1))
  fi
done
END=$(date +%s)
echo
echo "=== smoke grid done in $((END-START))s, failures=${fail} ==="

# IPC summary.
python3 - <<'PY'
import json, glob, os
out_dir = 'data/w2_smoke'
combos = {}
for p in sorted(glob.glob(os.path.join(out_dir, '*.json'))):
    name = os.path.basename(p)[:-5]
    if name.endswith('_smoke'):
        continue
    try:
        d = json.load(open(p))
        ipc = d[0]['roi']['cores'][0]['instructions'] / d[0]['roi']['cores'][0]['cycles']
    except Exception:
        ipc = None
    combos[name] = ipc
traces = ['mcf', 'lbm', 'omnetpp', 'bwaves']
prefs  = ['no', 'ip_stride', 'stream', 'sms', 'sandbox']
print(f"\n{'prefetcher':<12s}", *(f"{t:>10s}" for t in traces))
for pref in prefs:
    cells = []
    for t in traces:
        v = combos.get(f"{t}_{pref}")
        cells.append(f"{v:>10.4f}" if isinstance(v, float) else f"{'-':>10s}")
    print(f"{pref:<12s}", *cells)
PY
