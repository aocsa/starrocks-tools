#!/usr/bin/env bash
# standalone (GPU 0) for SF10/SF100/SF1000, then StarRocks 1/2/4 CN arms per SF. Decimal datasets.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
SOT=/home/prestouser/aocsa/sirius-stacks-wt/sot
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
EXT=$SOT/build/release/extension/sirius/sirius.duckdb_extension
QD=$SOT/experimental/starrocks/benchmarks/tpch/queries
CMP=$SOT/bench/rtxpro6000-2gpu/tools/compare.py
cd $SOT
for sf in 10 100 1000; do
  DS=/scratch/sirius/datasets/tpch_sf$sf
  case $sf in 10) CFG=$SP/perf/sirius-1gpu.yaml;; *) CFG=$SP/perf/sirius-1gpu-100.yaml;; esac
  OUT=$SP/perf/standalone/dec-sf$sf; mkdir -p $OUT
  echo "##### STANDALONE SF$sf cfg=$(basename $CFG) $(date -u +%H:%M:%S)"
  nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo
  SIRIUS_CONFIG_FILE=$CFG CUDA_VISIBLE_DEVICES=0 timeout 2400 $P $SP/perf/standalone_run.py $EXT $QD $DS $OUT 3 q01 q06 2>&1 | grep -v WARN
  [ -d $SP/oracle/$(basename $DS) ] && $P $CMP $OUT $SP/oracle/$(basename $DS) 2>&1 | tail -3 || echo "oracle for $(basename $DS) not ready yet"
done
for sf in 10 100 1000; do
  DS=/scratch/sirius/datasets/tpch_sf$sf
  case $sf in
    10)   export GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB QUERY_TIMEOUT=120 COLD_TIMEOUT=300;;
    *)    export GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB QUERY_TIMEOUT=600 COLD_TIMEOUT=900;;
  esac
  export SIRIUS_EXCHANGE_STAGING_BYTES=$STAGING
  for n in 1 2 4; do
    echo "##### SR SF$sf NUM_CNS=$n GPU_MEM=$GPU_MEM STAGING=$STAGING HOST_MEM=$HOST_MEM $(date -u +%H:%M:%S)"
    NUM_CNS=$n bash $SP/run-step3.sh $SOT $DS dec-sf${sf}-${n}cn 2>&1 | grep -E '^== (alive|WARM|COLD|nvidia)|pass|REFUSED|WEDGE|MATCH|DIFFER|NO-|negative|^\.cn|MiB'
  done
done
echo "CHAIN DONE $(date -u +%H:%M:%S)"
