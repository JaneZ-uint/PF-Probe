#!/usr/bin/env bash
# Build all 13 ChampSim binaries for the W3b prefetcher × degree grid.
#
# Produces:
#   champsim_no
#   champsim_{ip_stride,stream,sms,sandbox}_d{1,2,3}
#
# Usage:  scripts/build_all_degrees.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_ROOT/scripts/build_prefetcher.sh"

FAILED=0

echo "=== Building champsim_no ==="
"$BUILD" no || { echo "FAILED: no"; FAILED=$((FAILED+1)); }

for pref in ip_stride stream sms sandbox; do
  for deg in 1 2 3; do
    echo
    echo "=== Building champsim_${pref}_d${deg} ==="
    "$BUILD" "$pref" "$deg" || { echo "FAILED: ${pref}_d${deg}"; FAILED=$((FAILED+1)); }
  done
done

echo
echo "========================================="
echo "Build summary:"
echo "========================================="
ls -lh "$REPO_ROOT/champsim/bin"/champsim_* 2>/dev/null || true
echo
if [[ $FAILED -gt 0 ]]; then
  echo "WARNING: $FAILED build(s) failed!"
  exit 1
else
  echo "All 13 binaries built successfully."
fi
