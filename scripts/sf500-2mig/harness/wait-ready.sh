#!/usr/bin/env bash
# Wait until the cluster can actually run a query, then set the FE-side statement timeout.
#   usage: wait-ready.sh <worktree-root> <num_cns> [fe_query_timeout_s]
# exit 0 = <num_cns> CNs Alive AND a real scan succeeded; 1 otherwise.
#
# Two steps, both learned the hard way (stools/README.md, stools/restart-2cn.sh):
#   1. Alive=true in SHOW COMPUTE NODES precedes the FE being able to PLACE a fragment; for a
#      short window it still answers "No available backends", which reads as an engine failure.
#      Probe with a real FILES() scan of nation until it succeeds.
#   2. The FE's own query_timeout (default 300 s) aborts server-side no matter what client-side
#      timeout the sweep uses. q05 was once recorded "refused, 300104 ms" and later ran in 20 s.
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
WT=${1:?worktree root}; N=${2:?num cns}; FE_TIMEOUT=${3:-1800}
# shellcheck source=./cluster-env.sh
source "$HARNESS/cluster-env.sh" "$WT"

M="$MYSQL_BIN --host $FE_HOST --port $FE_PORT --user root --batch --connect-timeout=5"

# Alive column located by HEADER NAME, not index: SHOW COMPUTE NODES gained columns between
# StarRocks versions and a fixed $9 silently counts the wrong thing.
alive_count() {
    $M -e "SHOW COMPUTE NODES;" 2>/dev/null \
      | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} c && $c=="true"{n++} END{print n+0}'
}
n=0; waited=0
for i in $(seq 1 "${ALIVE_TRIES:-60}"); do
    n=$(alive_count); waited=$((i * 3))
    [ "$n" = "$N" ] && break
    sleep 3
done
echo "alive=$n/$N after ${waited}s"
$M -e "SHOW COMPUTE NODES;" 2>&1 | head -n $((N + 4))

# Stale registrations from an arm with a different NUM_CNS stay in the FE meta store (the FE dir
# is shared between the base and fusion worktrees) and the scheduler may still target them.
# Off by default: ALTER SYSTEM is destructive and is not exercised by any dry run.
if [ "${DROP_DEAD_CNS:-0}" = 1 ]; then
    $M -e "SHOW COMPUTE NODES;" 2>/dev/null \
      | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++){if($i=="Alive")a=i; if($i=="IP")ip=i; if($i=="HeartbeatPort")hp=i} next} a && $a!="true"{print $ip":"$hp}' \
      | while read -r hostport; do
            [ -n "$hostport" ] && { echo "dropping dead compute node $hostport"; $M -e "ALTER SYSTEM DROP COMPUTE NODE \"$hostport\";" 2>&1 | head -2; }
        done
fi

echo "== SET GLOBAL query_timeout=$FE_TIMEOUT"
$M -e "SET GLOBAL query_timeout=$FE_TIMEOUT;" 2>&1 | head -3

probe="SELECT count(*) FROM FILES(\"path\"=\"file://$TPCH_DATA/nation/*.parquet\",\"format\"=\"parquet\");"
ready=0
for i in $(seq 1 "${PROBE_TRIES:-45}"); do
    if $M -e "$probe" >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
done
echo "scheduler_ready=$ready after $((i * 2))s (probe: nation scan on $TPCH_DATA)"
[ "$ready" = 1 ] || $M -e "$probe" 2>&1 | head -3

echo "== SHOW COMPUTE NODE BLACKLIST"
$M -e "SHOW COMPUTE NODE BLACKLIST;" 2>&1 | head -10

[ "$n" = "$N" ] && [ "$ready" = 1 ]
