#!/usr/bin/env bash
# Stop the cluster8.sh launcher, the FE and the CNs started from a given worktree.
#   usage: stop-cluster.sh <worktree-root>
# SIGTERM first and wait: the CN's SHUTDOWN_GRACE is 15 s and the arena high-water line plus
# the Quent session only flush on a clean teardown. SIGKILL only after the grace window.
WT=${1:?worktree root}
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
GRACE=${STOP_GRACE_SECS:-60}

SR=$WT/experimental/starrocks
pat_launch="$SR/benchmarks/cluster8.sh"
pat_launch_pin="$SR/benchmarks/pinned/up.sh"
pat_cn="$SR/target/release/sirius-starrocks-cn"
pat_fe="$SR/starrocks/output/fe"
# The fusion worktree's output/fe is a symlink to the base worktree's, so the FE's own
# cmdline may carry either spelling.
pat_fe_real=$(realpath -m "$pat_fe" 2>/dev/null)

find_pids() {
    { pgrep -f "$pat_launch"; pgrep -f "$pat_launch_pin"; pgrep -f "$pat_cn"; pgrep -f "$pat_fe"
      [ -n "$pat_fe_real" ] && [ "$pat_fe_real" != "$pat_fe" ] && pgrep -f "$pat_fe_real"
    } 2>/dev/null | sort -u
}

pids=$(find_pids)
[ -n "$pids" ] && kill $pids 2>/dev/null
waited=0
for i in $(seq 1 "$GRACE"); do
    pids=$(find_pids); waited=$i
    [ -z "$pids" ] && break
    sleep 1
done
[ -n "$pids" ] && { echo "still alive after ${waited}s, SIGKILL: $(echo $pids | tr '\n' ' ')"; kill -9 $pids 2>/dev/null; sleep 2; }

# An FE from an earlier, differently-spelled path would still hold 9030 and block the next arm.
stray=$(pgrep -af "StarRocksFE|sirius-starrocks-cn" 2>/dev/null | grep -v pgrep | head -5)
[ -n "$stray" ] && echo "WARNING stray processes remain (not from $WT): $stray"
echo "stopped (waited ${waited}s)"
