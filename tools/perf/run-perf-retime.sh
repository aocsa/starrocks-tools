#!/usr/bin/env bash
# Re-time with the patched build (perf worktree): SR 1/2/4 CN at SF100 and SF1000 (q01 q06), flag-on control,
# then extra queries q03 q12 q14 at SF100: standalone (perf build == baseline for standalone), SR 1 CN, SR 4 CN.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
EXT=$WT/build/release/extension/sirius/sirius.duckdb_extension
QD=$WT/experimental/starrocks/benchmarks/tpch/queries
CMP=$WT/bench/rtxpro6000-2gpu/tools/compare.py
export GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB QUERY_TIMEOUT=600 COLD_TIMEOUT=900 SIRIUS_EXCHANGE_STAGING_BYTES=16GiB
for sf in 100 1000; do
  DS=/scratch/sirius/datasets/tpch_sf$sf
  for n in 1 2 4; do
    echo "##### PATCHED SR SF$sf NUM_CNS=$n (SIRIUS_CANONICAL_FLOAT_SUMS unset) $(date -u +%H:%M:%S)"
    NUM_CNS=$n bash $SP/run-step3.sh $WT $DS patched-sf${sf}-${n}cn 2>&1 | grep -E '^== (alive|WARM|COLD)|pass|REFUSED|WEDGE|MATCH|DIFFER|NO-|negative'
  done
done
echo "##### CONTROL SR SF100 NUM_CNS=1 SIRIUS_CANONICAL_FLOAT_SUMS=1 $(date -u +%H:%M:%S)"
SIRIUS_CANONICAL_FLOAT_SUMS=1 NUM_CNS=1 bash $SP/run-step3.sh $WT /scratch/sirius/datasets/tpch_sf100 control-sf100-1cn 2>&1 | grep -E '^== (alive|WARM)|warm pass|MATCH|DIFFER'
# extra queries, SF100
DS=/scratch/sirius/datasets/tpch_sf100; OUT=$SP/perf/standalone/dec-sf100-extra; mkdir -p $OUT
echo "##### STANDALONE SF100 extra q03 q12 q14 $(date -u +%H:%M:%S)"
SIRIUS_CONFIG_FILE=$SP/perf/sirius-1gpu-100.yaml CUDA_VISIBLE_DEVICES=0 timeout 1200 $P $SP/perf/standalone_run.py $EXT $QD $DS $OUT 3 q03 q12 q14 2>&1 | grep -v WARN
$P $CMP $OUT $SP/oracle/tpch_sf100 2>&1 | tail -5
for n in 1 4; do
  echo "##### PATCHED SR SF100 NUM_CNS=$n extra q03 q12 q14 $(date -u +%H:%M:%S)"
  NUM_CNS=$n QUERIES="q03 q12 q14" bash $SP/run-step3.sh $WT $DS patched-sf100-${n}cn-extra 2>&1 | grep -E '^== (alive|WARM|COLD)|pass|REFUSED|WEDGE|MATCH|DIFFER|NO-|negative'
done
echo "RETIME DONE $(date -u +%H:%M:%S)"
