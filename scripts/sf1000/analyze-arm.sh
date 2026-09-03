#!/usr/bin/env bash
# usage: analyze-arm.sh <arm: cn1|cn4|standalone>  -- runs the extractors over one arm's evidence
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad; B=$SP/perf/sf1000; A=$1
SR=/home/prestouser/aocsa/sirius-stacks-wt/perf/experimental/starrocks
P=/home/prestouser/aocsa/sirius-stacks/.pixi/envs/default/bin/python
if [ "$A" != standalone ]; then
  python3 $B/cnlog_extract.py $B/$A/cluster.log $B/$A/runs/runs.csv --engine "$B/$A/engine-*.log" --json $B/$A-cnlog.json > $B/$A-cnlog.txt 2>&1
  EX=$B/survey/explain; [ "$A" = cn4 ] && [ -d $B/survey/explain4 ] && EX=$B/survey/explain4; python3 $B/card_compare.py $EX $B/$A-cnlog.json > $B/card-compare-$A.txt 2>&1
  SESS=$(ls -d $B/$A/quent/.cn*/*/ 2>/dev/null)
else
  SESS=$(ls -d $B/standalone/quent/*/ 2>/dev/null)
fi
python3 $B/quent_bp.py $SESS --runs $B/$A-cnlog.json --json $B/$A-quent.json > $B/$A-quent.txt 2>&1
: > $B/$A-ops.txt; for s in $SESS; do python3 $SP/perf/quent_summary.py $s "$A $(basename $(dirname $s))/$(basename $s | cut -c1-13)" >> $B/$A-ops.txt 2>&1; done
echo "$A: cnlog=$(wc -l < $B/$A-cnlog.txt 2>/dev/null) quent=$(wc -l < $B/$A-quent.txt) ops=$(wc -l < $B/$A-ops.txt) sessions=$(echo $SESS | wc -w)"
