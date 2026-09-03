#!/usr/bin/env bash
# Generalised SF1000 capture for the fix verification arms.
# usage: capture-arm.sh <worktree> <num_cns> <tag> <timeout_s> <runs_after_warmup> q01 ...
# env overrides: ASYNC=0 (unset SIRIUS_CN_ASYNC_SENDER_DISPATCH), FUSION=<mode> (SIRIUS_CN_FRAGMENT_FUSION), TRANSLATE_ONLY=1,
#                FE_SETUP_SQL="SET GLOBAL ...;" (run once after the FE is alive), EXTRA_ENV="VAR=val VAR2=val" (exported before launch),
#                OUT_BASE (default scratchpad/fix/arms). Quent on, fragment dumps at 1 CN, engine logs copied at the end.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=$1; N=$2; TAG=$3; TO=$4; RUNS=$5; shift 5
SR=$WT/experimental/starrocks; DATA=/scratch/sirius/datasets/tpch_sf1000
OUT=${OUT_BASE:-$SP/fix/arms}/$TAG; mkdir -p $OUT/quent
source $SP/cluster-env.sh $WT
export NUM_CNS=$N GPU_MEM=100GiB HOST_MEM=160GiB STAGING=16GiB SIRIUS_EXCHANGE_STAGING_BYTES=16GiB SIRIUS_QUERY_WATCHDOG_SECS=300
export SIRIUS_CN_ENABLE_QUENT=1
if [ "${ASYNC:-1}" = 1 ]; then export SIRIUS_CN_ASYNC_SENDER_DISPATCH=1; else unset SIRIUS_CN_ASYNC_SENDER_DISPATCH; fi
[ -n "${FUSION:-}" ] && export SIRIUS_CN_FRAGMENT_FUSION=$FUSION
[ "${TRANSLATE_ONLY:-0}" = 1 ] && export SIRIUS_CN_TRANSLATE_ONLY=1
for kv in ${EXTRA_ENV:-}; do export "$kv"; done
[ "$N" = 1 ] && { export SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump; mkdir -p $OUT/dump; rm -f $OUT/dump/*; }
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
echo "##### ARM $TAG wt=$(basename $WT)@$(git -C $WT rev-parse --short HEAD) NUM_CNS=$N ASYNC=${ASYNC:-1} FUSION=${FUSION:-<unset>} TRANSLATE_ONLY=${TRANSLATE_ONLY:-0} EXTRA_ENV=${EXTRA_ENV:-} queries: $* $(date -u +%T)"
pgrep -f "sirius-starrocks-cn --gpu-device" >/dev/null && { echo "REFUSING: a CN is already running"; exit 2; }
( exec 3<>/dev/tcp/127.0.0.1/9030 ) 2>/dev/null && { echo "REFUSING: something answers on 9030"; exit 2; }
rm -rf $SR/.cn*/telemetry/*
for d in $SR/.cn*; do [ -f $d/log/sirius_$(date -u +%F).log ] && : > $d/log/sirius_$(date -u +%F).log; done   # fresh engine logs per arm
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "$N" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo
[ -n "${FE_SETUP_SQL:-}" ] && { echo "== FE setup: $FE_SETUP_SQL"; $M -e "$FE_SETUP_SQL" 2>&1 | head -3; }
[ $# -gt 0 ] && bash $SP/perf/sf1000/run-queries.sh $TAG $DATA $OUT/runs $TO $RUNS "$@"
echo "== stopping (SIGTERM, wait) $(date -u +%T)"; bash $SP/stop-cluster.sh $WT
for d in $SR/.cn*; do cn=$(basename $d); [ -d $d/telemetry ] || continue; for s in $d/telemetry/*/; do mkdir -p $OUT/quent/$cn; cp -r $s $OUT/quent/$cn/ 2>/dev/null; done; [ -f $d/log/sirius_$(date -u +%F).log ] && cp $d/log/sirius_$(date -u +%F).log $OUT/engine-$cn.log; done
[ -f $OUT/runs/runs.csv ] && python3 $SP/perf/sf1000/cnlog_extract.py $OUT/cluster.log $OUT/runs/runs.csv --engine "$OUT/engine-*.log" --json $OUT/cnlog.json > $OUT/cnlog.txt 2>&1
echo "##### ARM $TAG DONE $(date -u +%T)"
