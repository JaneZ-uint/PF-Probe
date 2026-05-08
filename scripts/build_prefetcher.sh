#!/usr/bin/env bash
# Build a ChampSim binary with a chosen L2C prefetcher.
#
# Usage:  scripts/build_prefetcher.sh <prefetcher_name>
#
# Produces champsim/bin/champsim_<prefetcher_name>.
# Generates champsim/.csconfig/config_<prefetcher_name>.json on the fly,
# leaving the canonical champsim/champsim_config.json untouched.
#
# All other cache levels keep their default prefetcher ("no").

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <prefetcher_name>" >&2
  exit 1
fi

PREF="$1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHAMPSIM_DIR="${REPO_ROOT}/champsim"
BASE_CFG="${CHAMPSIM_DIR}/champsim_config.json"
DERIVED_CFG="${CHAMPSIM_DIR}/.csconfig/config_${PREF}.json"

if [[ ! -f "$BASE_CFG" ]]; then
  echo "error: $BASE_CFG not found" >&2
  exit 2
fi

# Sanity-check the prefetcher exists (skip for "no", which always exists).
if [[ "$PREF" != "no" && ! -d "${CHAMPSIM_DIR}/prefetcher/${PREF}" ]]; then
  echo "error: prefetcher directory ${CHAMPSIM_DIR}/prefetcher/${PREF} does not exist" >&2
  exit 3
fi

mkdir -p "$(dirname "$DERIVED_CFG")"

python3 - "$BASE_CFG" "$DERIVED_CFG" "$PREF" <<'PY'
import json, sys
src, dst, pref = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src) as f:
    cfg = json.load(f)
cfg["L2C"]["prefetcher"] = pref
cfg["executable_name"] = f"champsim_{pref}"
with open(dst, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"derived: L2C.prefetcher={pref}, executable_name=champsim_{pref}")
PY

cd "$CHAMPSIM_DIR"
./config.sh "$DERIVED_CFG"
make -j"$(nproc)"
echo
echo "=== built: ${CHAMPSIM_DIR}/bin/champsim_${PREF} ==="
ls -lh "${CHAMPSIM_DIR}/bin/champsim_${PREF}"
