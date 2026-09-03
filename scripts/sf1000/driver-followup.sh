#!/usr/bin/env bash
# After driver-rest: (a) standalone run of the queries that FAILED on 1 CN (planning/pipelining contrast), (b) q15 determinism check
# on 1 CN with the canonical float-sum sort re-enabled (SIRIUS_CANONICAL_FLOAT_SUMS=1), (c) copy the engine logs next to each arm.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad; B=$SP/perf/sf1000
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf; SR=$WT/experimental/starrocks
until grep -q 'REST DONE' $B/driver-rest.log 2>/dev/null; do sleep 20; done
FAILED=$(python3 - <<'PY'
import csv,collections
r=list(csv.DictReader(open('/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000/cn1/runs/runs.csv')))
st=collections.defaultdict(set)
for x in r: st[x['query']].add(x['status'])
print(' '.join(q for q in sorted(st) if st[q]!={'pass'} and q!='q16'))   # q16 is a translator gap (multi_distinct_count), not a capacity question
PY
)
echo "##### standalone for the CN-failed queries: $FAILED $(date -u +%T)"
# keep the earlier standalone Quent sessions: run into a separate out dir
sed -e 's#OUT=$SP/perf/sf1000/standalone; mkdir -p $OUT/quent; rm -rf $OUT/quent/\*#OUT=$SP/perf/sf1000/standalone-failed; mkdir -p $OUT/quent#' -e 's#sirius-standalone-quent.yaml#sirius-standalone-quent-failed.yaml#' $B/capture-standalone.sh > $B/capture-standalone-failed.sh
sed 's#/standalone/quent#/standalone-failed/quent#' $B/sirius-standalone-quent.yaml > $B/sirius-standalone-quent-failed.yaml
bash $B/capture-standalone-failed.sh 900 1 $FAILED 2>&1 | tee $B/capture-standalone-failed.log | grep -E '^##|^q[0-9]+ r|sessions'
echo "##### q15 determinism: 1 CN, SIRIUS_CANONICAL_FLOAT_SUMS=1, 6 runs $(date -u +%T)"
SIRIUS_CANONICAL_FLOAT_SUMS=1 bash $B/capture-cn.sh 1 cn1-canon-q15 600 5 q15 2>&1 | tee $B/capture-cn1-canon-q15.log | grep -E '^##|^alive|^q[0-9]+ r'
echo "##### q15 control: 1 CN, gate off, 6 runs $(date -u +%T)"
bash $B/capture-cn.sh 1 cn1-nocanon-q15 600 5 q15 2>&1 | tee $B/capture-cn1-nocanon-q15.log | grep -E '^##|^alive|^q[0-9]+ r'
echo "##### engine log copies $(date -u +%T)"
for a in cn1 cn4 cn1-canon-q15 cn1-nocanon-q15; do for d in $SR/.cn*; do cn=$(basename $d); [ -f $d/log/sirius_2026-09-03.log ] && cp $d/log/sirius_2026-09-03.log $B/$a/engine-$cn.log; done; done
echo "##### FOLLOWUP DONE $(date -u +%T)"
