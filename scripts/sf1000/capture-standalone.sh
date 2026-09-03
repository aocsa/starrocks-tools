#!/usr/bin/env bash
# usage: capture-standalone.sh <timeout_s> <runs_after_warmup> q01 ...   (GPU 0, Quent on, 100GiB pool)
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; CLONE=/home/prestouser/aocsa/sirius-stacks
TO=$1; RUNS=$2; shift 2
OUT=$SP/perf/sf1000/standalone; mkdir -p $OUT/quent; rm -rf $OUT/quent/*
EXT=$WT/build/release/extension/sirius/sirius.duckdb_extension; QD=$WT/experimental/starrocks/benchmarks/tpch/queries
P=$CLONE/.pixi/envs/duckdb-python/bin/python; [ -x $P ] || P=$(ls $CLONE/.pixi/envs/*/bin/python | head -1)
echo "##### STANDALONE SF1000 queries: $* python=$P $(date -u +%T)"
pgrep -f 'sirius-starrocks-cn --gpu-device 0' >/dev/null && { echo "REFUSING: a CN is on GPU 0"; exit 2; }
SIRIUS_CONFIG_FILE=$SP/perf/sf1000/sirius-standalone-quent.yaml CUDA_VISIBLE_DEVICES=0 $P $SP/perf/sf1000/standalone_run_labeled.py $EXT $QD /scratch/sirius/datasets/tpch_sf1000 $OUT/runs $RUNS $TO sf1000-standalone "$@" 2>&1 | grep -v WARN
echo "quent sessions: $(ls -d $OUT/quent/*/ 2>/dev/null | wc -l)"
echo "##### STANDALONE DONE $(date -u +%T)"
