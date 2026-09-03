#!/usr/bin/env bash
# usage: start-cluster.sh <worktree-root> <logfile>   -- launches cluster8.sh detached, returns immediately
WT=${1:?worktree root}; LOG=${2:?logfile}
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
source "$SP/cluster-env.sh" "$WT"
cd "$WT/experimental/starrocks"
setsid nohup bash benchmarks/cluster8.sh > "$LOG" 2>&1 < /dev/null &
echo "cluster launcher pid=$! log=$LOG"
