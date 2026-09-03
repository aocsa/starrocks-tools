#!/usr/bin/env bash
# V1a + V1b on the fix-1 worktree (1 CN, GPU 0 via cluster8.sh's CN0). Run only when no cluster is up and fix 1 is committed+built.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad; WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast
bash $SP/fix/capture-arm.sh $WT 1 V1a 120 0 q05 q08 q09 q17 q18 q21 2>&1 | grep -E '^##|^alive|^q[0-9]+ r'
bash $SP/fix/capture-arm.sh $WT 1 V1b 600 2 q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22 2>&1 | grep -E '^##|^alive|^q[0-9]+ r'
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
$P $WT/bench/rtxpro6000-2gpu/tools/compare.py $SP/fix/arms/V1b/runs $SP/oracle/tpch_sf1000 2>&1 | tail -n 17 > $SP/fix/arms/V1b/compare.txt
python3 $SP/fix/check-V1.py $SP/fix/arms/V1a $SP/fix/arms/V1b $SP/perf/sf1000/compare-cn1.txt | tee $SP/fix/arms/V1-check.txt
python3 $SP/perf/sf1000/results_table.py baseline=$SP/perf/sf1000/cn1/runs/runs.csv V1b=$SP/fix/arms/V1b/runs/runs.csv > $SP/fix/arms/V1b/results-vs-baseline.md
echo "##### V1 DONE $(date -u +%T)"
