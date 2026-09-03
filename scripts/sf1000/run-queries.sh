#!/usr/bin/env bash
# Run bench-kit TPC-H queries against the FE on 9030 with a /* marker */ so the FE audit log ties QueryId to (query, run).
# usage: run-queries.sh <tag> <tpch_data> <out_dir> <timeout_s> <runs_after_warmup> q01 q02 ...
# writes <out_dir>/runs.csv (query,run,phase,status,ms,rows,start_utc) and <out_dir>/<q>.r<i>.out (mysql --batch)
TAG=$1; DATA=$2; OUT=$3; TO=$4; RUNS=$5; shift 5
QD=/home/prestouser/aocsa/sirius-stacks-wt/perf/experimental/starrocks/benchmarks/tpch/queries
mkdir -p $OUT; echo "query,run,phase,status,ms,rows,start_utc" > $OUT/runs.csv
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
for q in "$@"; do
  sql=$(sed "s#__TPCH_DATA__#$DATA#g" $QD/$q.sql)
  for i in $(seq 0 $RUNS); do
    phase=warm; [ $i = 0 ] && phase=cold
    start=$(date -u +%FT%T.%3N); t0=$(date +%s%N)
    if timeout $TO $M -e "/* $TAG $q r$i */ $sql" > $OUT/$q.r$i.out 2> $OUT/$q.r$i.err; then st=pass; else st=fail; rc=$?; fi
    ms=$(( ($(date +%s%N) - t0) / 1000000 )); rows=$(( $(wc -l < $OUT/$q.r$i.out) - 1 )); [ $rows -lt 0 ] && rows=0
    [ $st = fail ] && [ $ms -ge $((TO*1000 - 500)) ] && st=timeout
    echo "$q,$i,$phase,$st,$ms,$rows,$start" >> $OUT/runs.csv
    echo "$q r$i $phase $st ${ms}ms rows=$rows $( [ $st != pass ] && head -c 160 $OUT/$q.r$i.err | tr '\n' ' ')"
    [ $st != pass ] && break   # a failing run skips the rest of that query (bench.sh semantics)
  done
done
