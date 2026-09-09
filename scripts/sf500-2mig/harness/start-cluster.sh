#!/usr/bin/env bash
# usage: start-cluster.sh <worktree-root> <logfile>  -- launches cluster8.sh detached, returns immediately.
# Caller waits for Alive (capture-arm.sh does).
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
WT=${1:?worktree root}; LOG=${2:?logfile}
# shellcheck source=./cluster-env.sh
source "$HARNESS/cluster-env.sh" "$WT"

[ -x "$SR_DIR/target/release/sirius-starrocks-cn" ] || {
    echo "no CN binary at $SR_DIR/target/release/sirius-starrocks-cn -- run: pixi run cn-build in $SR_DIR" >&2; exit 1; }
[ -x "$SR_DIR/starrocks/output/fe/bin/start_fe.sh" ] || {
    echo "no packaged FE at $SR_DIR/starrocks/output/fe/bin/start_fe.sh" >&2; exit 1; }

mkdir -p "$(dirname "$LOG")"
cd "$SR_DIR" || exit 1
# Absolute path on purpose: stop-cluster.sh matches the launcher by worktree path, and
# `bash benchmarks/cluster8.sh` (relative, as the GB200 harness ran it) is not distinguishable
# between worktrees in /proc cmdline.
if [ "${PIN_MODE:-0}" = 1 ]; then
    # feat/pin-table-cn's kit: CNs take a full --sirius-config YAML (the only way to reach the
    # pin-compression keys; mutually exclusive with cluster8.sh's carve-out flags). One YAML per
    # CN, generated per arm from GPU_MEM / HOST_MEM; up.sh reads STAGING and NUM_CNS.
    [ -x "$SR_DIR/benchmarks/pinned/up.sh" ] || { echo "PIN_MODE=1 but no $SR_DIR/benchmarks/pinned/up.sh" >&2; exit 1; }
    export CONFIG_DIR=${CONFIG_DIR:-$(dirname "$LOG")/cn-config}
    NUM_CNS=$NUM_CNS GPU_MEM=$GPU_MEM HOST_MEM=$HOST_MEM OUT_DIR=$CONFIG_DIR \
        ENABLE_PIN_COMPRESSION=${ENABLE_PIN_COMPRESSION:-1} bash "$SR_DIR/benchmarks/pinned/gen-config.sh" || exit 1
    setsid nohup bash "$SR_DIR/benchmarks/pinned/up.sh" > "$LOG" 2>&1 < /dev/null &
    echo "cluster launcher (pinned kit up.sh) pid=$! wt=$WT NUM_CNS=$NUM_CNS GPU_MEM=$GPU_MEM STAGING=$STAGING HOST_MEM=$HOST_MEM configs=$CONFIG_DIR log=$LOG"
else
    setsid nohup bash "$SR_DIR/benchmarks/cluster8.sh" > "$LOG" 2>&1 < /dev/null &
    echo "cluster launcher pid=$! wt=$WT NUM_CNS=$NUM_CNS GPU_MEM=$GPU_MEM STAGING=$STAGING HOST_MEM=$HOST_MEM log=$LOG"
fi
