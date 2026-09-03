#!/usr/bin/env bash
# RESTART_CMD for bench.sh --cold-restart: stop, wipe nothing, relaunch, return (bench.sh waits for Alive)
WT=${1:?worktree root}; LOG=${2:?logfile}
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
bash "$SP/stop-cluster.sh" "$WT" >/dev/null
sleep 3
bash "$SP/start-cluster.sh" "$WT" "$LOG"
sleep 25
