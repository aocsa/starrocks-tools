#!/usr/bin/env bash
# Translate-only survey of all 22 TPC-H queries on the SF1000 paths with ONE CN (SIRIUS_CN_TRANSLATE_ONLY=1): the CN accepts and
# translates every fragment without executing it, so each query only costs an FE plan + translation; the client times out.
# Also captures EXPLAIN COSTS and EXPLAIN VERBOSE per query (FE-side plans and cardinality estimates).
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; OUT=$SP/perf/sf1000/survey; DATA=/scratch/sirius/datasets/tpch_sf1000
QD=$WT/experimental/starrocks/benchmarks/tpch/queries
mkdir -p $OUT/explain $OUT/dump; rm -f $OUT/dump/*
source $SP/cluster-env.sh $WT
export NUM_CNS=1 GPU_MEM=64GiB HOST_MEM=128GiB STAGING=8GiB SIRIUS_CN_TRANSLATE_ONLY=1 SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
echo "##### SURVEY start $(date -u +%T)"
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "1" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"
for q in $(seq -f 'q%02g' 1 22); do
  sql=$(sed "s#__TPCH_DATA__#$DATA#g" $QD/$q.sql)
  $M -e "EXPLAIN COSTS $sql" > $OUT/explain/$q.costs.txt 2>&1
  $M -e "EXPLAIN VERBOSE $sql" > $OUT/explain/$q.verbose.txt 2>&1
  before=$(wc -l < $OUT/cluster.log)
  timeout 20 $M -e "/* survey $q */ $sql" > $OUT/$q.out 2> $OUT/$q.err; rc=$?
  sleep 1
  tail -n +$((before+1)) $OUT/cluster.log | sed 's/\x1b\[[0-9;]*m//g' | grep -a -iE 'translate|unsupported|error|fragment' | grep -av 'AuditLog\|INFO (' > $OUT/$q.cnlog
  nt=$(grep -c 'accepting untranslatable' $OUT/$q.cnlog); ok=$(grep -c -iE 'translated fragment|translate_fragment_logged.*close' $OUT/$q.cnlog)
  fe_err=$(head -c 200 $OUT/$q.err | tr '\n' ' ')
  echo "$q rc=$rc untranslatable_fragments=$nt translate_events=$ok explain_costs_lines=$(wc -l < $OUT/explain/$q.costs.txt) fe_err=${fe_err:0:120}"
done
echo "##### stopping $(date -u +%T)"; bash $SP/stop-cluster.sh $WT
echo "##### SURVEY DONE $(date -u +%T)"
