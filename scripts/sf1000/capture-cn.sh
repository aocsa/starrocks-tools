#!/usr/bin/env bash
# Quent-instrumented CN capture at SF1000. usage: capture-cn.sh <num_cns> <tag> <timeout_s> <runs_after_warmup> q01 q03 ...
# Cluster: 100GiB GPU pool, 16GiB staging, 160GiB host per CN (the perf plan's retime configuration), watchdog 300 s,
# Quent on with per-fragment labels, async sender dispatch on. Telemetry dirs are wiped before launch so the newest
# session per CN is this arm's; after a clean SIGTERM the sessions are copied to <out>/quent/cn<i>/.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; SR=$WT/experimental/starrocks; DATA=/scratch/sirius/datasets/tpch_sf1000
N=$1; TAG=$2; TO=$3; RUNS=$4; shift 4
OUT=$SP/perf/sf1000/$TAG; mkdir -p $OUT/quent
source $SP/cluster-env.sh $WT
export NUM_CNS=$N GPU_MEM=100GiB HOST_MEM=160GiB STAGING=16GiB SIRIUS_EXCHANGE_STAGING_BYTES=16GiB SIRIUS_QUERY_WATCHDOG_SECS=300
export SIRIUS_CN_ENABLE_QUENT=1 SIRIUS_CN_ASYNC_SENDER_DISPATCH=1
[ "$N" = 1 ] && { export SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump; mkdir -p $OUT/dump; rm -f $OUT/dump/*; }
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
echo "##### CAPTURE $TAG NUM_CNS=$N queries: $* $(date -u +%T)"
pgrep -f "$SR/target/release/sirius-starrocks-cn" >/dev/null && { echo "REFUSING: CNs from this worktree still running"; exit 2; }
rm -rf $SR/.cn*/telemetry/*
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "$N" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo
bash $SP/perf/sf1000/run-queries.sh $TAG $DATA $OUT/runs $TO $RUNS "$@"
echo "== stopping (SIGTERM, wait) $(date -u +%T)"; bash $SP/stop-cluster.sh $WT
for d in $SR/.cn*; do cn=$(basename $d); [ -d $d/telemetry ] || continue; for s in $d/telemetry/*/; do mkdir -p $OUT/quent/$cn; cp -r $s $OUT/quent/$cn/; done; done
grep -a "/\* $TAG " $SR/starrocks/output/fe/log/fe.audit.log 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' > $OUT/fe.audit.$TAG.log
echo "audit lines with marker: $(wc -l < $OUT/fe.audit.$TAG.log); quent sessions: $(ls -d $OUT/quent/*/*/ 2>/dev/null | wc -l)"
echo "##### CAPTURE $TAG DONE $(date -u +%T)"
