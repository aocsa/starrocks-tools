#!/usr/bin/env bash
# demo/q1q6-integration-plus-fixes at SF1000: 4-CN all 22 queries (as asked), then 1-CN on the six former failures (fusion effect).
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad; WT=/home/prestouser/aocsa/sirius-stacks-wt/demo-plus
B=$SP/demoplus; export OUT_BASE=$B/arms; mkdir -p $B/arms
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python; CMP=/home/prestouser/aocsa/sirius-stacks-wt/perf/bench/rtxpro6000-2gpu/tools/compare.py
echo "##### demo-plus @ $(git -C $WT rev-parse --short HEAD) $(date -u +%T)"
bash $SP/fix/capture-arm.sh $WT 4 dp-cn4 600 2 $(seq -f 'q%02g' 1 22) 2>&1 | grep -E '^##|^alive|^q[0-9]+ r|REFUSING'
$P $CMP $B/arms/dp-cn4/runs $SP/oracle/tpch_sf1000 2>&1 | tail -n 24 > $B/arms/dp-cn4/compare.txt
bash $SP/fix/capture-arm.sh $WT 1 dp-cn1-six 600 2 q05 q08 q09 q17 q18 q21 2>&1 | grep -E '^##|^alive|^q[0-9]+ r|REFUSING'
$P $CMP $B/arms/dp-cn1-six/runs $SP/oracle/tpch_sf1000 2>&1 | tail -n 8 > $B/arms/dp-cn1-six/compare.txt
for a in dp-cn4 dp-cn1-six; do python3 $SP/perf/sf1000/quent_bp.py $(ls -d $B/arms/$a/quent/.cn*/*/) --runs-csv $B/arms/$a/runs/runs.csv --json $B/arms/$a/quent.json > $B/arms/$a/quent.txt 2>&1; done
python3 $SP/perf/sf1000/results_table.py perf-cn4=$SP/perf/sf1000/cn4/runs/runs.csv demoplus-cn4=$B/arms/dp-cn4/runs/runs.csv > $B/results-cn4.md
python3 $SP/perf/sf1000/results_table.py perf-cn1=$SP/perf/sf1000/cn1/runs/runs.csv standalone=$SP/perf/sf1000/standalone-failed/runs/runs.csv demoplus-cn1=$B/arms/dp-cn1-six/runs/runs.csv > $B/results-six.md
echo "##### ARMS DONE $(date -u +%T)"
