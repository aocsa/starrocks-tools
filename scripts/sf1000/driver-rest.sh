#!/usr/bin/env bash
# After the 1-CN sweep: 4-CN capture and standalone capture of the queries that passed on 1 CN, then the DuckDB oracle at
# SF1000 for that set (CPU only, after the GPU arms so it does not perturb them) and the compares.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; CLONE=/home/prestouser/aocsa/sirius-stacks; B=$SP/perf/sf1000
DATA=/scratch/sirius/datasets/tpch_sf1000; QD=$WT/experimental/starrocks/benchmarks/tpch/queries
until grep -q 'CAPTURE cn1 DONE' $B/capture-cn1.log 2>/dev/null; do sleep 15; done
PASS=$(python3 - <<'PY'
import csv,collections
r=list(csv.DictReader(open('/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000/cn1/runs/runs.csv')))
st=collections.defaultdict(set)
for x in r: st[x['query']].add(x['status'])
print(' '.join(q for q in sorted(st) if st[q]=={'pass'}))
PY
)
echo "##### passing on 1 CN: $PASS  $(date -u +%T)"
echo "##### 4-CN capture $(date -u +%T)"
bash $B/capture-cn.sh 4 cn4 600 2 $PASS 2>&1 | tee $B/capture-cn4.log | grep -E '^##|^alive|^q[0-9]+ r|audit'
echo "##### standalone capture $(date -u +%T)"
bash $B/capture-standalone.sh 900 2 $PASS 2>&1 | tee $B/capture-standalone.log | grep -E '^##|^q[0-9]+ r|sessions'
echo "##### oracle SF1000 for the passing set $(date -u +%T)"
mkdir -p /scratch/prestouser/aocsa/duckdb-oracle-tmp
P=$CLONE/.pixi/envs/default/bin/python
ORACLE_THREADS=48 ORACLE_MEM=380GB ORACLE_TMP=/scratch/prestouser/aocsa/duckdb-oracle-tmp $P $WT/bench/rtxpro6000-2gpu/tools/oracle.py $QD $DATA $SP/oracle/tpch_sf1000 $PASS 2>&1 | grep -v WARN | tail -n 30
echo "##### compares $(date -u +%T)"
for arm in cn1 cn4 standalone; do echo "== $arm"; $P $WT/bench/rtxpro6000-2gpu/tools/compare.py $B/$arm/runs $SP/oracle/tpch_sf1000 2>&1 | tail -n 26; done
echo "##### REST DONE $(date -u +%T)"
