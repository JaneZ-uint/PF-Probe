#!/usr/bin/env bash
# Run all 5 prefetcher binaries x all available traces, sequentially.
# Output: data/w2_smoke/<trace>_<prefetcher>.json
#
# Sequential (not parallel) to avoid host cache contention skewing IPC.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PREFETCHERS=(no ip_stride stream sms sandbox)
TRACES=(mcf lbm omnetpp bwaves)
WARMUP_M=1
SIM_M=5

START=$(date +%s)
for trace in "${TRACES[@]}"; do
  # Skip traces that didn't get downloaded.
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
  for pref in "${PREFETCHERS[@]}"; do
    "${SCRIPT_DIR}/run_smoke.sh" "$pref" "$trace" "$WARMUP_M" "$SIM_M"
  done
done
END=$(date +%s)
echo
echo "=== smoke grid done in $((END-START))s ==="

# Print IPC summary (all combos that have JSON files).
python3 - <<'PY'
import json, os, glob
out_dir = os.path.join(os.path.dirname(os.path.abspath('.')), 'data', 'w2_smoke')
out_dir = 'data/w2_smoke'
combos = {}
for p in sorted(glob.glob(os.path.join(out_dir, '*.json'))):
    name = os.path.basename(p)[:-5]
    try:
        d = json.load(open(p))
        ipc = d[0]['roi']['cores'][0]['instructions'] / d[0]['roi']['cores'][0]['cycles']
    except Exception as e:
        ipc = None
    combos[name] = ipc

# pivot
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
