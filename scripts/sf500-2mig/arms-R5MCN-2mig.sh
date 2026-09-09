#!/usr/bin/env bash
# R5-MCN16-2mig: the optimized-mode reference (plan-2mig step 4), full 22 on the instrumented wp0 build.
# 2 CNs on 2 MIG instances (GPU_DEVICES=0,1), L-B 36/8 HOST 40, optimized exchange window 2. Checksum
# and opcounters OFF (timing-representative); the default reconcile/downgrade/arena/frame INFO stays on.
# 1 cold + 2 warm, 300s, restart after every failure, oracle every run at 1e-6.
set -u
source /home/ubuntu/sirius-wt/env.sh
H=/home/ubuntu/sirius-wt/harness; A=/home/ubuntu/sirius-wt/arms; E=/home/ubuntu/sirius-wt/all22/evidence.sh
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python
WT=/home/ubuntu/sirius-wt/wp0; O=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus/oracle/tpch_sf500
KIT=/home/ubuntu/sirius-wt/demo/experimental/starrocks/benchmarks/tpch/queries
export TPCH_SF=500 TPCH_DATA=/home/ubuntu/tpch_parquet_sf500
ALL22="q01 q02 q03 q04 q05 q06 q07 q08 q09 q10 q11 q12 q13 q14 q15 q16 q17 q18 q19 q20 q21 q22"
TAG=R5-MCN16-2mig; TO=300; RUNS=2
export SIRIUS_QUERY_WATCHDOG_SECS=$((TO - 60)) RESTART_ON_FAIL=1
export FE_SETUP_SQL="SET GLOBAL cbo_cte_reuse_rate = 1.15"
F='^##|^q[0-9]+ r|alive=|scheduler_ready|FE setup|REFUSING|SKIPPING|RESTART|CN[0-9] gpu='
mkdir -p $A/$TAG
{ echo "tag=$TAG wt=$WT commit=$(git -C $WT rev-parse --short HEAD) branch=$(git -C $WT branch --show-current)"
  echo "gpus: $(nvidia-smi -L | tr -s ' ' | paste -sd';')"
  echo "env: NUM_CNS=2 GPU_DEVICES=0,1 GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB TPCH_SF=500 optimized window 2 (checksum/opcounters OFF)"
  echo "cn_sha256=$(sha256sum $WT/experimental/starrocks/target/release/sirius-starrocks-cn|cut -c1-16) engine_sha256=$(sha256sum $WT/build/release/extension/sirius/sirius.duckdb_extension|cut -c1-16)"
  echo "optimized-mode reference on the WP0 instrumented build (plan-2mig step 4)"; } > $A/$TAG/config.txt
say(){ echo "[R5MCN $(date -u +%H:%M:%S)] $*"; }
say "start: 22 queries, wp0 $(git -C $WT rev-parse --short HEAD), L-B 36/8 HOST 40, opt w2"
EXTRA_ENV="GPU_DEVICES=0,1 SIRIUS_EXCHANGE_OPTIMIZED=1 SIRIUS_CN_NIXL_TRANSFER_WINDOW=2" \
  GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB QD=$KIT \
  bash $H/capture-arm.sh $WT 2 $TAG $TO $RUNS $ALL22 2>&1 | grep -E "$F" | cut -c1-170
$P $H/compare.py $A/$TAG/runs $O > $A/$TAG/compare.txt 2>&1; tail -1 $A/$TAG/compare.txt
bash $E $A/$TAG > /dev/null 2>&1
say "failing/wrong: $(awk -F, 'NR>1&&$4!="pass"{print $1}' $A/$TAG/runs/runs.csv | sort -u | tr '\n' ' ')"
say "restarts: $(grep -c '^== RESTART' $A/$TAG/restarts.log 2>/dev/null)"
say "DONE $A/$TAG"
