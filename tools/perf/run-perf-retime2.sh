#!/usr/bin/env bash
# Re-time with async sender dispatch (perf CN binary rebuilt): SR 2/4 CN at SF1000 and SF100 (q01 q06),
# A/B control with SIRIUS_CN_ASYNC_SENDER_DISPATCH=0 at 4 CN SF1000, extras q03 q12 q14 at 4 CN SF100.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf
export GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB QUERY_TIMEOUT=600 COLD_TIMEOUT=900 SIRIUS_EXCHANGE_STAGING_BYTES=16GiB
F='^== (alive|WARM|COLD)|pass|REFUSED|WEDGE|MATCH|DIFFER|NO-|negative'
for sf in 1000 100; do
  DS=/scratch/sirius/datasets/tpch_sf$sf
  for n in 2 4; do
    echo "##### ASYNC SR SF$sf NUM_CNS=$n $(date -u +%H:%M:%S)"
    SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 NUM_CNS=$n bash $SP/run-step3.sh $WT $DS async-sf${sf}-${n}cn 2>&1 | grep -E "$F"
  done
done
echo "##### DEFAULT (async off) CONTROL SR SF1000 NUM_CNS=4 $(date -u +%H:%M:%S)"
NUM_CNS=4 bash $SP/run-step3.sh $WT /scratch/sirius/datasets/tpch_sf1000 asyncoff-sf1000-4cn 2>&1 | grep -E "$F"
echo "##### ASYNC SR SF100 NUM_CNS=4 extra q03 q12 q14 $(date -u +%H:%M:%S)"
SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 NUM_CNS=4 QUERIES="q03 q12 q14" bash $SP/run-step3.sh $WT /scratch/sirius/datasets/tpch_sf100 async-sf100-4cn-extra 2>&1 | grep -E "$F"
echo "##### ASYNC SR SF1000 NUM_CNS=1 $(date -u +%H:%M:%S)"
SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 NUM_CNS=1 bash $SP/run-step3.sh $WT /scratch/sirius/datasets/tpch_sf1000 async-sf1000-1cn 2>&1 | grep -E "$F"
echo "RETIME2 DONE $(date -u +%H:%M:%S)"
