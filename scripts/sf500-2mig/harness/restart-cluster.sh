#!/usr/bin/env bash
# Full cluster restart between queries: stop, start (cluster8.sh or, with PIN_MODE=1, the pinned kit's up.sh), wait until
# a real scan works, re-apply FE_SETUP_SQL, re-pin when PIN=1. run-queries.sh calls this after a query with a failed or
# timed-out run when RESTART_ON_FAIL=1: a CN that predates fail-fast and the lease bookkeeping fixes strands fragments and
# leases on a failure, and every later measurement on that cluster is invalid (the pin kit's bench.sh restarts for the
# same reason).
#   usage: restart-cluster.sh <worktree-root> <out_dir> <num_cns> <timeout_s> <reason>
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
WT=${1:?worktree root}; OUT=${2:?out dir}; N=${3:?num cns}; TO=${4:?timeout_s}; REASON=${5:-unspecified}
# shellcheck source=./cluster-env.sh
source "$HARNESS/cluster-env.sh" "$WT"
M="$MYSQL_BIN --host $FE_HOST --port $FE_PORT --user root --batch --connect-timeout=5"
k=$(ls "$OUT"/cluster.r*.log 2>/dev/null | wc -l); k=$((k + 1))
echo "== RESTART #$k ($REASON) $(date -u +%T)" | tee -a "$OUT/restarts.log"
bash "$HARNESS/stop-cluster.sh" "$WT" | tail -1
# The CN's GPU context must be gone before the next one binds the device.
for _ in $(seq 1 30); do nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -q . || break; sleep 2; done
bash "$HARNESS/start-cluster.sh" "$WT" "$OUT/cluster.r$k.log"
bash "$HARNESS/wait-ready.sh" "$WT" "$N" $((TO + 60)) 2>&1 | tee -a "$OUT/restarts.log" | grep -E 'alive=|scheduler_ready'
ready_rc=${PIPESTATUS[0]}
[ -n "${FE_SETUP_SQL:-}" ] && { echo "== FE setup: $FE_SETUP_SQL"; $M -e "$FE_SETUP_SQL" 2>&1 | head -3; }
pin_rc=0
if [ "${PIN:-0}" = 1 ] && [ "$ready_rc" = 0 ]; then
    bash "$HARNESS/pin-cluster.sh" "$WT" "$N" "$OUT/pin.txt"; pin_rc=$?
fi
echo "== restart #$k done ready_rc=$ready_rc pin_rc=$pin_rc $(date -u +%T)" | tee -a "$OUT/restarts.log"
[ "$ready_rc" = 0 ] && [ "$pin_rc" = 0 ]
