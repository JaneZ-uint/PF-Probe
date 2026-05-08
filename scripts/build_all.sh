#!/usr/bin/env bash
# Build all five W2 prefetcher binaries (no, ip_stride, stream, sms, sandbox).
# Wraps scripts/build_prefetcher.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFETCHERS=(no ip_stride stream sms sandbox)

for p in "${PREFETCHERS[@]}"; do
  echo
  echo "########## building champsim_${p} ##########"
  "${SCRIPT_DIR}/build_prefetcher.sh" "$p"
done

echo
echo "=== summary ==="
ls -lh "${SCRIPT_DIR}/../champsim/bin/"champsim_* 2>/dev/null
