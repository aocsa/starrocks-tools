#!/usr/bin/env bash
# WP0 smoke (plan-2mig §2 / §6 step 4): prove the new telemetry is attributable in one grep, on the
# instrumented wp0 build. 2 CNs on 2 MIG instances (GPU_DEVICES=0,1), L-B 36/8 HOST 40, optimized
# exchange window 2, checksum + opcounters on (STRICT off: observe, don't fail). q03 (a clean shuffle
# -> relay/reload/reconcile lines) and q08 (the incident query -> downgrade/arena futility). 1 cold + 2
# warm each. Caller holds gpu-lock.
set -u
source /home/ubuntu/sirius-wt/env.sh
H=/home/ubuntu/sirius-wt/harness; A=/home/ubuntu/sirius-wt/arms
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python
WT=/home/ubuntu/sirius-wt/wp0; O=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus/oracle/tpch_sf500
KIT=/home/ubuntu/sirius-wt/demo/experimental/starrocks/benchmarks/tpch/queries
export TPCH_SF=500 TPCH_DATA=/home/ubuntu/tpch_parquet_sf500
TAG=M-dbg-2mig; TO=300; RUNS=2
export SIRIUS_QUERY_WATCHDOG_SECS=$((TO - 60)) RESTART_ON_FAIL=1
export FE_SETUP_SQL="SET GLOBAL cbo_cte_reuse_rate = 1.15"
mkdir -p $A/$TAG
{ echo "tag=$TAG wt=$WT commit=$(git -C $WT rev-parse --short HEAD) branch=$(git -C $WT branch --show-current)"
  echo "purpose=WP0 telemetry smoke on the instrumented build"
  echo "gpus: $(nvidia-smi -L | tr -s ' ' | paste -sd';')"
  echo "env: NUM_CNS=2 GPU_DEVICES=0,1 GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB TPCH_SF=500"
  echo "EXTRA_ENV: SIRIUS_EXCHANGE_OPTIMIZED=1 WINDOW=2 SIRIUS_EXCHANGE_CHECKSUM=1 SIRIUS_EXCHANGE_OPCOUNTERS=1 (STRICT off)"
  echo "cn_sha256=$(sha256sum $WT/experimental/starrocks/target/release/sirius-starrocks-cn|cut -c1-16) engine_sha256=$(sha256sum $WT/build/release/extension/sirius/sirius.duckdb_extension|cut -c1-16)"; } > $A/$TAG/config.txt
echo "[Mdbg $(date -u +%H:%M:%S)] gpus: $(nvidia-smi -L | grep MIG | wc -l) MIG instances"
EXTRA_ENV="GPU_DEVICES=0,1 SIRIUS_EXCHANGE_OPTIMIZED=1 SIRIUS_CN_NIXL_TRANSFER_WINDOW=2 SIRIUS_EXCHANGE_CHECKSUM=1 SIRIUS_EXCHANGE_OPCOUNTERS=1" \
  GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB QD=$KIT \
  bash $H/capture-arm.sh $WT 2 $TAG $TO $RUNS q03 q08 2>&1 | grep -E '^##|^q[0-9]+ r|alive=|scheduler_ready|REFUSING|SKIPPING|RESTART|CN[0-9] gpu=' | cut -c1-170
$P $H/compare.py $A/$TAG/runs $O > $A/$TAG/compare.txt 2>&1; tail -1 $A/$TAG/compare.txt
echo "[Mdbg $(date -u +%H:%M:%S)] telemetry grep over engine logs:"
for tag in '[exchange_reconcile]' '[exchange_relay]' '[exchange_reload]' '[exchange_frame]' '[exchange_checksum]' '[arena]' '[gpu_pool]' '[host_pool]' '[downgrade]' '[streaming_sink]' '[opcount]' 'MISMATCH'; do
  n=$(grep -rF -- "$tag" $A/$TAG/engine-.cn*.log 2>/dev/null | wc -l); printf '  %-24s %s\n' "$tag" "$n"
done
echo "[Mdbg $(date -u +%H:%M:%S)] DONE $A/$TAG"
