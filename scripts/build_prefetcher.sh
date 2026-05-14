#!/usr/bin/env bash
# Build a ChampSim binary with a chosen L2C prefetcher and optional degree.
#
# Usage:  scripts/build_prefetcher.sh <prefetcher_name> [degree]
#
# Without degree:  produces champsim/bin/champsim_<prefetcher_name>
# With degree 1-3: produces champsim/bin/champsim_<prefetcher_name>_d<degree>
#
# Degree mapping (compile-time -D flag per prefetcher):
#   degree | ip_stride         | stream        | sms (PHT cap)      | sandbox
#   1      | IP_STRIDE_DEGREE=1| STREAM_DEGREE=2| SMS_PHT_REPLAY_CAP=8 | SANDBOX_DEGREE=2
#   2      | IP_STRIDE_DEGREE=2| STREAM_DEGREE=4| SMS_PHT_REPLAY_CAP=16| SANDBOX_DEGREE=4
#   3      | IP_STRIDE_DEGREE=3| STREAM_DEGREE=6| SMS_PHT_REPLAY_CAP=24| SANDBOX_DEGREE=6

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prefetcher_name> [degree]" >&2
  exit 1
fi

PREF="$1"
DEGREE="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHAMPSIM_DIR="${REPO_ROOT}/champsim"
BASE_CFG="${CHAMPSIM_DIR}/champsim_config.json"

if [[ ! -f "$BASE_CFG" ]]; then
  echo "error: $BASE_CFG not found" >&2
  exit 2
fi

# Sanity-check the prefetcher exists (skip for "no").
if [[ "$PREF" != "no" && ! -d "${CHAMPSIM_DIR}/prefetcher/${PREF}" ]]; then
  echo "error: prefetcher directory ${CHAMPSIM_DIR}/prefetcher/${PREF} does not exist" >&2
  exit 3
fi

# Build executable name and degree-specific -D flag.
DEGREE_FLAG=""
if [[ -n "$DEGREE" ]]; then
  if [[ "$DEGREE" != "1" && "$DEGREE" != "2" && "$DEGREE" != "3" ]]; then
    echo "error: degree must be 1, 2, or 3" >&2
    exit 4
  fi
  if [[ "$PREF" == "no" ]]; then
    echo "error: degree is not applicable to 'no' prefetcher" >&2
    exit 4
  fi
  EXE_NAME="champsim_${PREF}_d${DEGREE}"
  case "$PREF" in
    ip_stride)
      declare -A _map=([1]=1 [2]=2 [3]=3)
      DEGREE_FLAG="-DIP_STRIDE_DEGREE=${_map[$DEGREE]}"
      ;;
    stream)
      declare -A _map=([1]=2 [2]=4 [3]=6)
      DEGREE_FLAG="-DSTREAM_DEGREE=${_map[$DEGREE]}"
      ;;
    sms)
      declare -A _map=([1]=8 [2]=16 [3]=24)
      DEGREE_FLAG="-DSMS_PHT_REPLAY_CAP=${_map[$DEGREE]}"
      ;;
    sandbox)
      declare -A _map=([1]=2 [2]=4 [3]=6)
      DEGREE_FLAG="-DSANDBOX_DEGREE=${_map[$DEGREE]}"
      ;;
    *)
      echo "error: unknown prefetcher '$PREF' for degree mapping" >&2
      exit 5
      ;;
  esac
else
  EXE_NAME="champsim_${PREF}"
fi

DERIVED_CFG="${CHAMPSIM_DIR}/.csconfig/config_${EXE_NAME}.json"
mkdir -p "$(dirname "$DERIVED_CFG")"

python3 - "$BASE_CFG" "$DERIVED_CFG" "$PREF" "$EXE_NAME" <<'PY'
import json, sys
src, dst, pref, exe = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src) as f:
    cfg = json.load(f)
cfg["L2C"]["prefetcher"] = pref
cfg["executable_name"] = exe
with open(dst, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"derived: L2C.prefetcher={pref}, executable_name={exe}")
PY

cd "$CHAMPSIM_DIR"
./config.sh "$DERIVED_CFG"

if [[ -n "$DEGREE_FLAG" ]]; then
  # Force recompilation of the prefetcher .o — Make doesn't track CPPFLAGS
  # changes in its dependency graph, so the stale .o would be reused.
  PREF_OBJ=".csconfig/modules/prefetcher/${PREF}/${PREF}.o"
  rm -f "$PREF_OBJ"
  echo "=== degree flag: $DEGREE_FLAG (removed $PREF_OBJ to force rebuild) ==="
  make -j"$(nproc)" CPPFLAGS="$DEGREE_FLAG"
else
  make -j"$(nproc)"
fi

echo
echo "=== built: ${CHAMPSIM_DIR}/bin/${EXE_NAME} ==="
ls -lh "${CHAMPSIM_DIR}/bin/${EXE_NAME}"
