#!/usr/bin/env bash
# pixi-build the Sirius engine in SIRIUS_WT and point libsirius.so at the extension.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$HERE/env.sh"
cd "$SIRIUS_WT"
git submodule update --init --recursive
pixi run make
ln -sfn sirius.duckdb_extension build/release/extension/sirius/libsirius.so
test -e build/release/extension/sirius/libsirius.so
echo "engine: $SIRIUS_WT/build/release/extension/sirius/libsirius.so"
