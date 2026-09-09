#!/usr/bin/env bash
# Same-box reference arms for PLAN-top3-2mig.md step 2 (mode-off only; no optimized-mode arm before the instrumented rebuild):
#   R5-P04-2mig    perf/exchange-04-07-06 222b646a, 2 CNs on 2 MIG instances, L-A 40/4, HOST 40
#   R5-MCNoff-2mig perf/multi-cn-ingress-packing-transfer 2e0cbf51, SIRIUS_EXCHANGE_OPTIMIZED unset, L-B 36/8, HOST 40
#   R5-I2-1mig     all22/integration 95bec853, ONE CN on MIG ordinal 0, 40/4, HOST 40
#   R5-SA-1mig     standalone DuckDB CLI + integration extension on MIG ordinal 0, 40 GiB YAML (arms-SA-mig.sh)
# perf and mcn carry the GPU_DEVICES launcher patch UNCOMMITTED (cluster8.sh only; binaries untouched) -> dirty=1 in config.txt.
# Caller holds gpu-lock. SF500 kit, oracle every run, same protocol as arms-S500-mig.sh.
set -u
source /home/ubuntu/sirius-wt/env.sh
H=/home/ubuntu/sirius-wt/harness; A=/home/ubuntu/sirius-wt/arms; E=/home/ubuntu/sirius-wt/all22/evidence.sh
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python
T=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus; O=$T/oracle/tpch_sf500
KIT=/home/ubuntu/sirius-wt/demo/experimental/starrocks/benchmarks/tpch/queries
export TPCH_SF=500
ALL22="q01 q02 q03 q04 q05 q06 q07 q08 q09 q10 q11 q12 q13 q14 q15 q16 q17 q18 q19 q20 q21 q22"
F='^##|^q[0-9]+ r|alive=|scheduler_ready|FE setup|REFUSING|SKIPPING|RESTART|CN[0-9] gpu='
TO=300; RUNS=2
export SIRIUS_QUERY_WATCHDOG_SECS=$((TO - 60)) RESTART_ON_FAIL=1
export FE_SETUP_SQL="SET GLOBAL cbo_cte_reuse_rate = 1.15"
GPUS="$(nvidia-smi -L | tr -s ' ' | paste -sd';')"
say(){ echo "[R5 $(date -u +%H:%M:%S)] $*"; }
run_arm(){ # tag worktree ncn gpu_devices gpu_mem staging host_mem extra_env
  local tag=$1 wt=$2 n=$3 gd=$4 gm=$5 st=$6 hm=$7 extra=$8
  mkdir -p $A/$tag
  { echo "tag=$tag wt=$wt commit=$(git -C $wt rev-parse --short HEAD) branch=$(git -C $wt branch --show-current) dirty=$(git -C $wt status --short | grep -vc '^??') dirty_files=$(git -C $wt status --short | grep -v '^??' | awk '{print $2}' | paste -sd,)"
    echo "box=g7e.4xlarge MIG: $GPUS"
    echo "env: NUM_CNS=$n GPU_DEVICES=$gd GPU_MEM=$gm STAGING=$st HOST_MEM=$hm DISK=none EXTRA_ENV=$extra TPCH_SF=$TPCH_SF QD=$KIT ASYNC=1"
    echo "FE_SETUP_SQL=$FE_SETUP_SQL"; echo "TO=$TO RUNS=$RUNS watchdog=$SIRIUS_QUERY_WATCHDOG_SECS restart_on_fail=1 oracle=$O"
    echo "cn_sha256=$(sha256sum $wt/experimental/starrocks/target/release/sirius-starrocks-cn | cut -c1-16) engine_sha256=$(sha256sum $wt/build/release/extension/sirius/sirius.duckdb_extension | cut -c1-16)"
    echo "same-box reference arm (PLAN-top3-2mig.md step 2), mode-off"; } > $A/$tag/config.txt
  say "arm $tag ($(basename $wt), N=$n gpus=$gd $gm/$st/$hm, ${extra:-<no extra env>})"
  EXTRA_ENV="GPU_DEVICES=$gd $extra" GPU_MEM=$gm STAGING=$st HOST_MEM=$hm QD=$KIT \
    bash $H/capture-arm.sh $wt $n $tag $TO $RUNS $ALL22 2>&1 | grep -E "$F" | cut -c1-170
  $P $H/compare.py $A/$tag/runs $O > $A/$tag/compare.txt 2>&1; tail -1 $A/$tag/compare.txt
  bash $E $A/$tag | head -2
}
run_arm R5-P04-2mig    /home/ubuntu/sirius-wt/perf 2 0,1 40GiB 4GiB 40GiB ""
run_arm R5-MCNoff-2mig /home/ubuntu/sirius-wt/mcn  2 0,1 36GiB 8GiB 40GiB ""
run_arm R5-I2-1mig     /home/ubuntu/sirius-wt/demo 1 0   40GiB 4GiB 40GiB ""
say "arm R5-SA-1mig (standalone, MIG ordinal 0)"
( cd /home/ubuntu/sirius-wt/demo && pixi run bash -c "CUDA_VISIBLE_DEVICES=0 bash $T/scripts/sf500-2mig/arms-SA-mig.sh R5-SA-1mig" ) 2>&1 | grep -E '^q[0-9]+ r|queries match|DONE'
$P $H/results_table.py S500-I2-2cn-2mig=$A/S500-I2-2cn-2mig/runs/runs.csv R5-P04-2mig=$A/R5-P04-2mig/runs/runs.csv R5-MCNoff-2mig=$A/R5-MCNoff-2mig/runs/runs.csv R5-I2-1mig=$A/R5-I2-1mig/runs/runs.csv R5-SA-1mig=$A/R5-SA-1mig/runs/runs.csv > $A/R5-results.md
say "DONE table $A/R5-results.md"
