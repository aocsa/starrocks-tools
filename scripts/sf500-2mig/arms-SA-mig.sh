#!/usr/bin/env bash
# Standalone Sirius (the repo's DuckDB CLI with the Sirius extension, ONE process, ONE MIG instance) on the same SF500 parquet and the
# same 22 query texts as the CN arms (the CN kit with FILES() rewritten to read_parquet, so the DuckDB oracle applies verbatim).
# One process per query (a failure cannot take the others down), 1 cold + 2 warm iterations, 300 s per iteration budget,
# results as TSV for harness/compare.py, timings from `.timer on` into the harness runs.csv layout.
#   usage: arms-SA.sh <tag> [q01 q02 ...]      (caller holds gpu-lock; GPU 0)
set -u
source /home/ubuntu/sirius-wt/env.sh
TAG=${1:-R5-SA-1mig}; shift || true
QUERIES=${*:-"q01 q02 q03 q04 q05 q06 q07 q08 q09 q10 q11 q12 q13 q14 q15 q16 q17 q18 q19 q20 q21 q22"}
WT=/home/ubuntu/sirius-wt/demo; A=/home/ubuntu/sirius-wt/arms; H=/home/ubuntu/sirius-wt/harness
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python; O=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus/oracle/tpch_sf500
DATA=/home/ubuntu/tpch_parquet_sf500
DUCKDB=$WT/build/release/duckdb; CFG=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus/scripts/sf500-2mig/sirius-standalone-mig48.yaml
RUNS=2; TO=300
OUT=$A/$TAG; mkdir -p $OUT/runs $OUT/queries $OUT/logs
say(){ echo "[SA $(date -u +%H:%M:%S)] $*"; }
export SIRIUS_CONFIG_FILE=$CFG CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0} SIRIUS_LOG_DIR=$OUT/logs
{ echo "tag=$TAG wt=$WT commit=$(git -C $WT rev-parse --short HEAD) branch=$(git -C $WT branch --show-current) mode=standalone-duckdb-cli"
  echo "duckdb=$DUCKDB config=$CFG data=$DATA kit=CN-kit $WT/experimental/starrocks/benchmarks/tpch/queries (FILES()->read_parquet, __TPCH_SF__=500)"
  echo "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES RUNS=$RUNS TO_per_iteration=$TO oracle=$O"; } > $OUT/config.txt
say "$(head -1 $OUT/config.txt)"

# Query texts: the CN kit's SQL verbatim (same constants the oracle was generated from; the standalone kit under
# test/tpch_performance uses different TPC-H parameters, e.g. q06 1997/0.03 vs 1994/0.06), with StarRocks' FILES() table
# function rewritten to DuckDB's read_parquet and the SF placeholder substituted.
CNKIT=$WT/experimental/starrocks/benchmarks/tpch/queries
for f in $CNKIT/q*.sql; do
  sed -E "s#FILES\(\"path\"=\"file://__TPCH_DATA__/([a-z]+)/\*\.parquet\",\"format\"=\"parquet\"\)#read_parquet('$DATA/\1/*.parquet')#g; s/__TPCH_SF__/500/g" $f > $OUT/queries/$(basename $f)
done
views(){ :; }
echo "query,run,phase,status,ms,rows,start_utc" > $OUT/runs/runs.csv
for q in $QUERIES; do
  sql=$OUT/queries/$q.sql; [ -f $sql ] || { say "$q: no query file"; continue; }
  script=$OUT/logs/$q.duckdb.sql
  { echo ".bail off"; views; echo ".headers on"; echo ".mode tabs"; echo ".timer on"
    for i in $(seq 0 $RUNS); do echo ".print __RUN__ $i"; echo ".output $OUT/runs/$q.r$i.out"; sed 's/;\s*$//' $sql; echo ";"; echo ".output stdout"; echo ".print __END__ $i"; done
  } > $script
  start=$(date -u +%FT%T.%3N)
  timeout $((TO * (RUNS + 1) + 60)) $DUCKDB -f $script > $OUT/logs/$q.stdout 2> $OUT/logs/$q.stderr; rc=$?
  # One "Run Time (s): real X" per iteration (views ran before .timer on); map them to runs in order.
  mapfile -t times < <(grep -oP 'Run Time \(s\): real \K[0-9]+\.[0-9]+' $OUT/logs/$q.stdout)
  for i in $(seq 0 $RUNS); do
    phase=warm; [ $i = 0 ] && phase=cold
    out=$OUT/runs/$q.r$i.out
    rows=0; st=fail; ms=0
    if [ -s $out ] && ! grep -qiE '^(Error|.*Exception|.*out_of_memory|.*bad_alloc)' $out; then rows=$(( $(wc -l < $out) - 1 )); [ $rows -lt 0 ] && rows=0; st=pass; fi
    [ -n "${times[$i]:-}" ] && ms=$(python3 -c "print(int(float('${times[$i]}')*1000))")
    [ $st = fail ] && { err=$(grep -m1 -iE 'Error|Exception|out_of_memory|bad_alloc|CUDA|Killed' $OUT/logs/$q.stderr $OUT/logs/$q.stdout $out 2>/dev/null | head -1 | cut -d: -f2- | cut -c1-200); echo "ERROR: ${err:-no output (rc=$rc)}" > $OUT/runs/$q.r$i.err; }
    [ $rc = 124 ] && [ $st = fail ] && st=timeout
    echo "$q,$i,$phase,$st,$ms,$rows,$start" >> $OUT/runs/runs.csv
    echo "$q r$i $phase $st ${ms}ms rows=$rows $( [ $st != pass ] && cat $OUT/runs/$q.r$i.err | cut -c1-140)"
  done
done
$P $H/compare.py $OUT/runs $O > $OUT/compare.txt 2>&1; tail -1 $OUT/compare.txt
say "DONE $OUT"
