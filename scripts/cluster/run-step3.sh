#!/usr/bin/env bash
# usage: run-step3.sh <worktree> <dataset-dir> <tag>   (cluster must NOT be running; this script starts and stops it)
# Produces $SP/step3/<tag>/{cluster.log,warm/,cold/,summary.txt}
set -u
WT=${1:?}; DS=${2:?}; TAG=${3:?}
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
OUT=$SP/step3/$TAG; mkdir -p $OUT/warm $OUT/cold
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
TOOLS=$WT/experimental/starrocks/tools; [ -f $TOOLS/compare.py ] || TOOLS=/home/prestouser/aocsa/sirius-stacks-wt/sot/bench/rtxpro6000-2gpu/tools
QD=$WT/experimental/starrocks/benchmarks/tpch/queries
ORACLE=$SP/oracle/$(basename $DS)
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
{
echo "== $TAG  worktree=$WT  dataset=$DS  $(date -u)"
echo "== env: NUM_CNS=$NUM_CNS GPU_MEM=$GPU_MEM STAGING=$STAGING SIRIUS_EXCHANGE_STAGING_BYTES=$SIRIUS_EXCHANGE_STAGING_BYTES SIRIUS_QUERY_WATCHDOG_SECS=$SIRIUS_QUERY_WATCHDOG_SECS CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "$NUM_CNS" ] && break; sleep 3; done
echo "== alive compute nodes after $((i*3))s: $n"
$M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++){if($i=="Alive")c=i; if($i=="HeartbeatPort")h=i}; next} {print "   cn heartbeat="$h" alive="$c}'
echo "== nvidia-smi:"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo
echo "== one CN pid per GPU:"; nvidia-smi --query-compute-apps=gpu_uuid,pid --format=csv,noheader 2>/dev/null | sort | uniq -c
echo "== blacklist:"; $M -e "SHOW COMPUTE NODE BLACKLIST;" 2>&1 | head -3; $M -e "SHOW BACKEND BLACKLIST;" 2>&1 | head -3
echo "== EXPLAIN q01:"; Q=$(sed "s|__TPCH_DATA__|$DS|g" $QD/q01.sql); $M -e "EXPLAIN $Q" 2>&1 | grep -E -i 'AGGREGATE|EXCHANGE|SORT|PARTITION|new_planner' | sed 's/^[[:space:]]*//'
echo "== EXPLAIN q06:"; Q=$(sed "s|__TPCH_DATA__|$DS|g" $QD/q06.sql); $M -e "EXPLAIN $Q" 2>&1 | grep -E -i 'AGGREGATE|EXCHANGE|PARTITION' | sed 's/^[[:space:]]*//'
echo "== WARM bench:"; cd $WT/experimental/starrocks && MIN_BACKENDS=$NUM_CNS QUERY_TIMEOUT=${QUERY_TIMEOUT:-120} TPCH_DATA=$DS bash benchmarks/tpch/bench.sh $OUT/warm/out.csv 3 ${QUERIES:-q01 q06} 2>&1 | grep -E 'pass|REFUSED|WEDGE|cluster:|complete'
echo "== WARM compare:"; $P $TOOLS/compare.py $OUT/warm $ORACLE 2>&1 | tail -4
echo "== COLD-RESTART bench:"; MIN_BACKENDS=$NUM_CNS QUERY_TIMEOUT=${QUERY_TIMEOUT:-120} COLD_TIMEOUT=${COLD_TIMEOUT:-180} TPCH_DATA=$DS RESTART_CMD="bash $SP/restart-cluster.sh $WT $OUT/cluster.log" bash benchmarks/tpch/bench.sh --cold-restart $OUT/cold/out.csv 1 ${QUERIES:-q01 q06} 2>&1 | grep -E 'pass|REFUSED|WEDGE|cluster:|complete|restarting'
echo "== COLD compare:"; $P $TOOLS/compare.py $OUT/cold $ORACLE 2>&1 | tail -4
echo "== log checks (cluster.log, current process generation):"
grep -a -E 'nixl bandwidth canary' $OUT/cluster.log | sed 's/\x1b\[[0-9;]*m//g' | grep -o 'peer=[^ ]* gbps="[^"]*"' | sort | uniq -c | head -20
grep -a -E 'transmitted batches via nixl' $OUT/cluster.log | sed 's/\x1b\[[0-9;]*m//g' | grep -o 'stream_id=.*' | sort | uniq -c | head -12
echo "negative markers: needs-transport-tier=$(grep -a -c 'needs the nixl transport tier' $OUT/cluster.log) row-count-unknown=$(grep -a -c 'input stream row count unknown' $OUT/cluster.log) fail_stalled=$(grep -a -c 'fail_stalled_query' $OUT/cluster.log) errorCode62=$(grep -a -c 'errorCode=62' $OUT/cluster.log) ERROR-lines=$(grep -a -c ' ERROR ' $OUT/cluster.log)"
echo "== cn-distribution (rows):"; CNDIST=$WT/experimental/starrocks/scripts/cn-distribution.py; [ -f $CNDIST ] || CNDIST=/home/prestouser/aocsa/sirius-stacks-wt/sot/experimental/starrocks/scripts/cn-distribution.py; $P $CNDIST --dir $WT/experimental/starrocks --prefix .cn --metric rows 2>&1 | sed -n '/WORK DISTRIBUTION/,/TOTAL/p'
bash $SP/stop-cluster.sh $WT
echo "== done $(date -u)"
} 2>&1 | tee $OUT/summary.txt
