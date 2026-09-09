#!/usr/bin/env bash
# D-MCN16-2mig: the DISK twin of R5-MCN16-2mig. One variable vs that arm: memory.disk is configured
# (600 GiB per CN on the nvme) via the CN_SIRIUS_CONFIG_DIR launcher; everything else identical
# (wp0 build, 2 CNs on 2 MIG instances, L-B 36/8 HOST 40, optimized w2, engine-default operator_params).
# Queries: the four host-full failures of R5-MCN16-2mig. 1 cold + 2 warm, oracle every run.
# Caller holds gpu-lock.
set -u
source /home/ubuntu/sirius-wt/env.sh
H=/home/ubuntu/sirius-wt/harness; A=/home/ubuntu/sirius-wt/arms; E=/home/ubuntu/sirius-wt/all22/evidence.sh
P=/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python
WT=/home/ubuntu/sirius-wt/wp0; O=/home/ubuntu/starrocks-tools-wt/sf500-2-mig-gpus/oracle/tpch_sf500
KIT=/home/ubuntu/sirius-wt/demo/experimental/starrocks/benchmarks/tpch/queries
export TPCH_SF=500 TPCH_DATA=/home/ubuntu/tpch_parquet_sf500
CFG=${CFG:-$A/DISK-cn-cfg}; DISK_ROOT=/opt/dlami/nvme/spill
TAG=${TAG:-D-MCN16-2mig}; TO=300; RUNS=${RUNS:-2}; QUERIES="${QUERIES:-q05 q08 q09 q21}"
export SIRIUS_QUERY_WATCHDOG_SECS=$((TO - 60)) RESTART_ON_FAIL=1
export FE_SETUP_SQL="SET GLOBAL cbo_cte_reuse_rate = 1.15"
F='^##|^q[0-9]+ r|alive=|scheduler_ready|FE setup|REFUSING|SKIPPING|RESTART|CN[0-9] gpu='
mkdir -p $A/$TAG
say(){ echo "[DISK $(date -u +%H:%M:%S)] $*"; }
{ echo "tag=$TAG wt=$WT commit=$(git -C $WT rev-parse --short HEAD) branch=$(git -C $WT branch --show-current)"
  echo "gpus: $(nvidia-smi -L | tr -s ' ' | paste -sd';')"
  echo "env: NUM_CNS=2 GPU_DEVICES=0,1 GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB optimized w2 TPCH_SF=500"
  echo "DISK: capacity_bytes=600GiB per CN, downgrade_root_dirs=$DISK_ROOT/cn{0,1} via CN_SIRIUS_CONFIG_DIR=$CFG"
  echo "operator_params: engine default (min(device/40, pool/40)) -- one variable vs R5-MCN16-2mig"
  echo "queries: $QUERIES"
  echo "cn_sha256=$(sha256sum $WT/experimental/starrocks/target/release/sirius-starrocks-cn|cut -c1-16) engine_sha256=$(sha256sum $WT/build/release/extension/sirius/sirius.duckdb_extension|cut -c1-16)"; } > $A/$TAG/config.txt
cp $CFG/cn0.yaml $CFG/cn1.yaml $A/$TAG/ 2>/dev/null
say "disk free before: $(df -h $DISK_ROOT | tail -1 | awk '{print $4}')"
CN_SIRIUS_CONFIG_DIR=$CFG EXTRA_ENV="GPU_DEVICES=0,1 SIRIUS_EXCHANGE_OPTIMIZED=1 SIRIUS_CN_NIXL_TRANSFER_WINDOW=2" \
  GPU_MEM=36GiB STAGING=8GiB HOST_MEM=40GiB QD=$KIT \
  bash $H/capture-arm.sh $WT 2 $TAG $TO $RUNS $QUERIES 2>&1 | grep -E "$F" | cut -c1-170
$P $H/compare.py $A/$TAG/runs $O > $A/$TAG/compare.txt 2>&1; tail -1 $A/$TAG/compare.txt
bash $E $A/$TAG > /dev/null 2>&1
say "spill evidence:"
printf '  %-34s %s\n' "no viable downgrade target" "$(grep -hcF 'no viable downgrade target' $A/$TAG/engine-.cn*.log | paste -sd+ | bc 2>/dev/null || echo 0)"
printf '  %-34s %s\n' "[downgrade] lines" "$(grep -hcF '[downgrade]' $A/$TAG/engine-.cn*.log | paste -sd+ | bc 2>/dev/null || echo 0)"
printf '  %-34s %s\n' "to_disk non-zero requests" "$(grep -hoE 'to_disk: [1-9][0-9]*/[0-9]+' $A/$TAG/engine-.cn*.log | wc -l)"
printf '  %-34s %s\n' "tier=DISK batches" "$(grep -hcF 'tier=DISK' $A/$TAG/engine-.cn*.log | paste -sd+ | bc 2>/dev/null || echo 0)"
say "peak spill dir usage: $(du -sh $DISK_ROOT 2>/dev/null | cut -f1)  disk free now: $(df -h $DISK_ROOT | tail -1 | awk '{print $4}')"
say "results: $(awk -F, 'NR>1{print $1,$4}' $A/$TAG/runs/runs.csv | sort -u | tr '\n' ' ')"
say "DONE $A/$TAG"
