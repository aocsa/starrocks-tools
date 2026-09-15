#!/usr/bin/env bash
# Release-build sirius-starrocks-cn linked to libsirius + libnixl.
# Do not set RUSTFLAGS. Do not `pixi run cargo` from experimental/starrocks.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$HERE/env.sh"
cd "$SIRIUS_WT/experimental/starrocks"
export TOOLS_DIR
# shellcheck source=/dev/null
source "$SIRIUS_WT/experimental/starrocks/scripts/cn-env.sh"
CN_PIXI=/home/ubuntu/sirius-wt/demo/experimental/starrocks/.pixi/envs/cn
export PATH="/usr/bin:${CN_PIXI}/bin:${PATH}"
export THRIFT="${THRIFT:-${CN_PIXI}/bin/thrift}"
cargo build --release -p sirius-starrocks-cn
test -x target/release/sirius-starrocks-cn
echo "cn: $SIRIUS_WT/experimental/starrocks/target/release/sirius-starrocks-cn"
