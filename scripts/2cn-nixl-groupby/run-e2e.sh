#!/usr/bin/env bash
# Launch isolated FE + 2 MIG CNs and run the FILES() GROUP BY e2e under gpu-lock.
# Equivalent to, from SIRIUS_WT:
#   /home/ubuntu/sirius-wt/all22/gpu-lock.sh experimental/starrocks/tests/2cn_files_group_by.sh
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$HERE/env.sh"
E2E_SH=${E2E_SH:-$SIRIUS_WT/experimental/starrocks/tests/2cn_files_group_by.sh}
if [ ! -x "$E2E_SH" ]; then
  echo "no e2e script at $E2E_SH (set SIRIUS_WT or E2E_SH)" >&2
  exit 1
fi
cd "$SIRIUS_WT"
exec "$HERE/gpu-lock.sh" "$E2E_SH"
