#!/usr/bin/env bash
# SF500 TPC-H on the g7e.4xlarge box with the RTX PRO 6000 split into two MIG 2g.48gb instances (48,512 MiB each):
# one CN per MIG instance via cluster8.sh MIG_DEVICES; sizes kept at 40/4/40 so it compares 1:1 with S500-I2-2cn-1gpu.
# Original single-GPU header follows.
# SF500 TPC-H on the single-GPU g7e.4xlarge box (1x RTX PRO 6000 96 GiB, 16 vCPU, 124 GiB RAM): 2 CNs sharing GPU 0
# via cluster8.sh's GPU_DEVICES override, integration tree (demo), 40 GiB pool + 4 GiB staging per CN (2x44 + 2 CUDA
# contexts < 95.6 GiB), 40 GiB host pool per CN (FE heap 8 GiB + page cache in the rest). Same protocol as rerun-all.sh:
# 1 cold + 2 warm, 300 s, restart after every failure, oracle 1e-6. The SF500 oracle does not exist on this box, so it is
# generated AFTER the arm (a 16-thread DuckDB run beside the bench would steal the 8 cores). Caller holds gpu-lock.
set -u
source /home/ubuntu/sirius-wt/env.sh
H=/home/ubuntu/sirius-wt/harness; A=/home/ubuntu/sirius-wt/arms; L=/home/ubuntu/sirius-wt/logs
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python
WT=/home/ubuntu/sirius-wt/s500-mig
KIT=$WT/experimental/starrocks/benchmarks/tpch/queries
export TPCH_SF=500
DATA=/home/ubuntu/tpch_parquet_sf$TPCH_SF; O=/home/ubuntu/starrocks-tools-wt/sf500-single-gpu/oracle/tpch_sf$TPCH_SF
ALL22="q01 q02 q03 q04 q05 q06 q07 q08 q09 q10 q11 q12 q13 q14 q15 q16 q17 q18 q19 q20 q21 q22"
F='^##|^q[0-9]+ r|alive=|scheduler_ready|FE setup|REFUSING|SKIPPING|RESTART|CN[0-9] gpu='
TO=300; RUNS=2
export SIRIUS_QUERY_WATCHDOG_SECS=$((TO - 60)) RESTART_ON_FAIL=1
export FE_SETUP_SQL="SET GLOBAL cbo_cte_reuse_rate = 1.15"
GM=40GiB ST=4GiB HM=40GiB; EXTRA="GPU_DEVICES=0,1"   # CUDA enumerates the two MIG 2g.48gb instances as ordinals 0 and 1; the engine cannot resolve MIG-<uuid> through NVML
say(){ echo "[S500mig $(date -u +%H:%M:%S)] $*"; }

until grep -qE '\] done\. datasets:' $L/regen-sf500.log 2>/dev/null && [ -d $DATA/lineitem ]; do
  grep -qE 'FATAL' $L/regen-sf500.log 2>/dev/null && { say "dataset regeneration FAILED"; tail -5 $L/regen-sf500.log; exit 1; }
  sleep 30
done
say "dataset: $(ls $DATA | wc -l) tables, $(du -sh $DATA/ | cut -f1); verify: $(grep -E "RESULT:" $L/regen-sf500.log | tail -1) (SF500 lineitem FAIL is the known verifier-constant bug: generator 3,000,028,242 rows, all other tables OK)"
say "gpu: $(nvidia-smi --query-gpu=name,uuid,memory.used --format=csv,noheader)"

run_arm(){ # tag queries...
  local tag=$1; shift
  mkdir -p $A/$tag
  { echo "tag=$tag wt=$WT commit=$(git -C $WT rev-parse --short HEAD) branch=$(git -C $WT branch --show-current) dirty=$(git -C $WT status --short | grep -vc '^??')"
    echo "box=g7e.4xlarge 1x RTX PRO 6000 $(nvidia-smi --query-gpu=uuid --format=csv,noheader) 16 vCPU 124GiB"
    echo "env: NUM_CNS=2 GPU_MEM=$GM STAGING=$ST HOST_MEM=$HM EXTRA_ENV=$EXTRA TPCH_SF=$TPCH_SF QD=$KIT"
    echo "FE_SETUP_SQL=$FE_SETUP_SQL"; echo "TO=$TO RUNS=$RUNS watchdog=$SIRIUS_QUERY_WATCHDOG_SECS restart_on_fail=1 oracle=$O"; } > $A/$tag/config.txt
  say "arm $tag: $*"
  EXTRA_ENV="$EXTRA" GPU_MEM=$GM STAGING=$ST HOST_MEM=$HM QD=$KIT \
    bash $H/capture-arm.sh $WT 2 $tag $TO $RUNS "$@" 2>&1 | grep -E "$F" | cut -c1-170
  echo "restarts: $(grep -c '^== RESTART' $A/$tag/restarts.log 2>/dev/null)"
}

# Smoke first: proves two CNs come up on one card and answer a scan before the 22-query run is committed to.
run_arm S500-smoke-2cn-2mig q06
grep -q 'q06,0,cold,pass' $A/S500-smoke-2cn-2mig/runs/runs.csv 2>/dev/null || { say "SMOKE FAILED, stopping"; exit 1; }

run_arm S500-I2-2cn-2mig $ALL22

say "oracle: reusing $O ($(ls $O/q*.tsv | wc -l) answers)"
for t in S500-I2-2cn-2mig; do
  $P $H/compare.py $A/$t/runs $O > $A/$t/compare.txt 2>&1; echo "$t: $(tail -1 $A/$t/compare.txt)"
  bash /home/ubuntu/sirius-wt/all22/evidence.sh $A/$t > /dev/null
done
$P $H/results_table.py S500-I2-2cn-2mig=$A/S500-I2-2cn-2mig/runs/runs.csv > $A/S500-mig-results.md
say "DONE table $A/S500-mig-results.md"
