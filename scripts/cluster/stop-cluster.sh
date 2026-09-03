#!/usr/bin/env bash
# stop FE + CNs launched from a given worktree (only processes whose cmdline contains that path).
# SIGTERM, then wait up to 45 s for a clean exit (engine teardown flushes Quent), then SIGKILL.
WT=${1:?worktree root}
pat_cn="$WT/experimental/starrocks/target/release/sirius-starrocks-cn"; pat_fe="$WT/experimental/starrocks/starrocks/output/fe"
pids=$(pgrep -f "$pat_cn"; pgrep -f "$pat_fe")
[ -n "$pids" ] && kill $pids 2>/dev/null
for i in $(seq 1 45); do pids=$(pgrep -f "$pat_cn"; pgrep -f "$pat_fe"); [ -z "$pids" ] && break; sleep 1; done
[ -n "$pids" ] && { echo "still alive after ${i}s, SIGKILL: $pids"; kill -9 $pids 2>/dev/null; }
pgrep -af "sirius-starrocks-cn|StarRocksFE" | grep -v pgrep | head -3; echo "stopped (waited ${i}s)"
