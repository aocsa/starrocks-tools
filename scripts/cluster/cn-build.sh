#!/usr/bin/env bash
# usage: pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn bash cn-build.sh <worktree>/experimental/starrocks [cargo args...]
set -euo pipefail
cd "$1"; shift
export TOOLS_DIR=/home/prestouser/aocsa/tools
SHIMS=$TOOLS_DIR/toolchain-shims
export PATH="$SHIMS:$PATH"
export RUSTFLAGS="-C link-arg=-L$SHIMS -C link-arg=-lnvidia-ml"
source scripts/cn-env.sh
echo "NIXL_PREFIX=$NIXL_PREFIX NIXL_NO_STUBS_FALLBACK=$NIXL_NO_STUBS_FALLBACK CC=$CC"
exec cargo "$@"
