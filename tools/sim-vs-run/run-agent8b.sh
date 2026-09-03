#!/usr/bin/env bash
# agent 8: one WARM arm through the lead's harness pieces (cluster-env.sh, start-cluster.sh, bench.sh, compare.py)
# with a GRACEFUL CN stop (SIGTERM, wait <= 90 s) so the Quent WARM generation flushes (07-local-experiments.md section 1, capture hygiene).
# usage: run-agent8.sh <worktree> <dataset> <tag> <runs>    env: NUM_CNS GPU_MEM STAGING HOST_MEM [SIRIUS_CN_ASYNC_SENDER_DISPATCH]
set -u
WT=${1:?}; DS=${2:?}; TAG=${3:?}; RUNS=${4:-5}
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
OUT=$SP/step3/agent8-$TAG; mkdir -p $OUT
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
TOOLS=/home/prestouser/aocsa/sirius-stacks-wt/sot/bench/rtxpro6000-2gpu/tools
ORACLE=$SP/oracle/$(basename $DS)
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
CNBIN="$WT/experimental/starrocks/target/release/sirius-starrocks-cn"
{
echo "== $TAG  worktree=$WT  dataset=$DS  runs=$RUNS  $(date -u)"
echo "== env: NUM_CNS=$NUM_CNS GPU_MEM=$GPU_MEM STAGING=$STAGING HOST_MEM=$HOST_MEM SIRIUS_EXCHANGE_STAGING_BYTES=$SIRIUS_EXCHANGE_STAGING_BYTES SIRIUS_CN_ASYNC_SENDER_DISPATCH=${SIRIUS_CN_ASYNC_SENDER_DISPATCH:-<unset>} CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"
echo "== nvidia-smi before:"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "$NUM_CNS" ] && break; sleep 3; done
echo "== alive compute nodes after $((i*3))s: $n"
echo "== WARM bench:"; cd $WT/experimental/starrocks && MIN_BACKENDS=$NUM_CNS QUERY_TIMEOUT=${QUERY_TIMEOUT:-600} TPCH_DATA=$DS bash benchmarks/tpch/bench.sh $OUT/out.csv $RUNS ${QUERIES:-q01 q06} 2>&1 | grep -E 'pass|REFUSED|WEDGE|cluster:|complete'
echo "== compare:"; $P $TOOLS/compare.py $OUT $ORACLE 2>&1 | tail -4
echo "== log checks (cluster.log):"
grep -a -E 'nixl bandwidth canary' $OUT/cluster.log | sed 's/\x1b\[[0-9;]*m//g' | grep -o 'peer=[^ ]* gbps="[^"]*"' | sort | uniq -c | head -20
grep -a -E 'transmitted batches via nixl' $OUT/cluster.log | sed 's/\x1b\[[0-9;]*m//g' | grep -o 'stream_id=.*' | sort | uniq -c | head -12
echo "negative markers: needs-transport-tier=$(grep -a -c 'needs the nixl transport tier' $OUT/cluster.log) row-count-unknown=$(grep -a -c 'input stream row count unknown' $OUT/cluster.log) fail_stalled=$(grep -a -c 'fail_stalled_query' $OUT/cluster.log) errorCode62=$(grep -a -c 'errorCode=62' $OUT/cluster.log) ERROR-lines=$(grep -a -c ' ERROR ' $OUT/cluster.log)"
echo "== graceful CN stop (ONE SIGTERM, wait per pid, FE untouched until CNs exit) $(date -u)"
pids=$(pgrep -f "sirius-starrocks-cn" | tr "\n" " "); echo "CN pids: $pids"; for p in $pids; do tr "\0" " " < /proc/$p/cmdline | cut -c1-160; echo; done; kill -TERM $pids
for i in $(seq 1 90); do alive=0; for p in $pids; do kill -0 $p 2>/dev/null && alive=1; done; [ $alive = 0 ] && break; sleep 1; done
echo "CN processes gone after ${i}s (SIGKILL fallback below if any remain)"; sed "s/\x1b\[[0-9;]*m//g" $OUT/cluster.log | grep -aE "shutdown signal received|engine thread shutting down|forcing process exit" | cut -c1-150 | sort | uniq -c
bash $SP/stop-cluster.sh $WT
echo "== newest Quent session per CN:"
for c in $(seq 0 $((NUM_CNS-1))); do d=$WT/experimental/starrocks/.cn$c/telemetry; s=$(ls -1t $d | head -1); echo "cn$c $d/$s q_init=$(cat $d/$s/query/*.ndjson 2>/dev/null | grep -c '"Init"') q_exit=$(cat $d/$s/query/*.ndjson 2>/dev/null | grep -c '"state":"Exit"') tasks=$(cat $d/$s/task/*.ndjson 2>/dev/null | grep -c '"Created"') engine_bytes=$(cat $d/$s/engine/*.ndjson 2>/dev/null | wc -c)"; done
echo "== done $(date -u)"
} 2>&1 | tee $OUT/summary.txt
