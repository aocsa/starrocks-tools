#!/usr/bin/env bash
# A/B: async sender dispatch off vs on, 4 CN SF1000, 5 warm runs each (1 discarded), q01 q06. Same binary (perf worktree).
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; DS=/scratch/sirius/datasets/tpch_sf1000
export GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB SIRIUS_EXCHANGE_STAGING_BYTES=16GiB NUM_CNS=4
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
for mode in off on off on; do
  OUT=$SP/perf/ab/$mode-$(date -u +%H%M%S); mkdir -p $OUT
  if [ $mode = on ]; then export SIRIUS_CN_ASYNC_SENDER_DISPATCH=1; else unset SIRIUS_CN_ASYNC_SENDER_DISPATCH; fi
  bash $SP/start-cluster.sh $WT $OUT/cluster.log >/dev/null
  for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "4" ] && break; sleep 3; done
  echo "##### AB mode=$mode alive=$n $(date -u +%H:%M:%S)"
  (cd $WT/experimental/starrocks && MIN_BACKENDS=4 QUERY_TIMEOUT=600 TPCH_DATA=$DS bash benchmarks/tpch/bench.sh $OUT/out.csv 5 q01 q06 2>&1 | grep -E 'warm pass|REFUSED|WEDGE')
  bash $SP/stop-cluster.sh $WT >/dev/null; sleep 3
done
echo "AB DONE $(date -u +%H:%M:%S)"
