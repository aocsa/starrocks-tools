#!/usr/bin/env bash
# Print the config.txt header for one arm: every flag and resolved bound in absolute bytes, binary hashes, GPU identity.
#   usage: config-header.sh <worktree> <num_cns> <tag>     (reads the same env capture-arm.sh reads)
# Bounds: batch B = BATCH_BYTES if set else min(clamp(device/40, 512MiB, 5GiB), GPU_MEM/40) (sirius_config.cpp:42-60,:573-591);
#         mcn frame cap = STAGING/4 - 8 MiB (sirius_ffi.cpp:1104,:1176-1182); RX max_batch = STAGING/4.
WT=${1:?worktree}; N=${2:?num cns}; TAG=${3:?tag}
to_bytes(){ python3 - "$1" <<'PY'
import re,sys
s=sys.argv[1].strip(); m=re.match(r'^([0-9.]+)\s*([KMGT]?)(i?)B?$',s)
if not m: print(int(float(s))); sys.exit()
n,u,i=m.groups(); mult={'':1,'K':1000,'M':1000**2,'G':1000**3,'T':1000**4}[u]
if i: mult={'':1,'K':1024,'M':1024**2,'G':1024**3,'T':1024**4}[u]
print(int(float(n)*mult))
PY
}
gm=$(to_bytes "${GPU_MEM:-84GiB}"); st=$(to_bytes "${STAGING:-8GiB}"); hm=$(to_bytes "${HOST_MEM:-160GiB}")
# CUDA device total as the engine sees it: the MIG instance's framebuffer when MIG is on (the first
# "| gpu gi ci dev | used / total |" row of the MIG table), else the card's.
dev=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i 0 2>/dev/null | head -1)
mig_total=$(nvidia-smi 2>/dev/null | awk '/MIG devices:/{m=1} m && /^\|  *[0-9]+ +[0-9]+ +[0-9]+ +[0-9]+ +\|/{if (match($0, /[0-9]+MiB \/ [0-9]+MiB/)) {split(substr($0,RSTART,RLENGTH),a,"/ "); gsub(/MiB/,"",a[2]); print a[2]; exit}}')
device_bytes=$(( ${mig_total:-${dev:-0}} * 1024 * 1024 ))
if [ -n "${BATCH_BYTES:-}" ]; then B=$(to_bytes "$BATCH_BYTES"); Bsrc=pinned; else
  d40=$(( device_bytes / 40 )); [ $d40 -lt $((512*1024*1024)) ] && d40=$((512*1024*1024)); [ $d40 -gt $((5*1024*1024*1024)) ] && d40=$((5*1024*1024*1024))
  p40=$(( gm / 40 )); B=$(( d40 < p40 ? d40 : p40 )); Bsrc="engine default min(device/40, pool/40)"; fi
cap=$(( st / 4 - 8 * 1024 * 1024 ))
echo "tag=$TAG wt=$WT commit=$(git -C "$WT" rev-parse --short HEAD) branch=$(git -C "$WT" branch --show-current) dirty=$(git -C "$WT" status --short | grep -vc '^??') dirty_files=$(git -C "$WT" status --short | grep -v '^??' | awk '{print $2}' | paste -sd,)"
echo "gpus: $(nvidia-smi -L | tr -s ' ' | paste -sd';')"
echo "layout: NUM_CNS=$N GPU_DEVICES=${GPU_DEVICES:-<one per gpu>} MIG_DEVICES=${MIG_DEVICES:-} GPU_MEM=${GPU_MEM:-84GiB}=${gm}B STAGING=${STAGING:-8GiB}=${st}B HOST_MEM=${HOST_MEM:-160GiB}=${hm}B device_total=${device_bytes}B"
echo "bounds: batch_B=${B}B ($Bsrc) max_build=$((2*B))B frame_cap_optimized=${cap}B rx_max_batch=$((st/4))B cap_violated_by_default_batch=$([ $B -gt $cap ] && echo yes || echo no)"
echo "launch: CN_SIRIUS_CONFIG_DIR=${CN_SIRIUS_CONFIG_DIR:-<flags>} DISK_ROOT=${DISK_ROOT:-none} DISK_BYTES=${DISK_BYTES:-0} BATCH_BYTES=${BATCH_BYTES:-<default>}"
echo "exchange: SIRIUS_EXCHANGE_OPTIMIZED=${SIRIUS_EXCHANGE_OPTIMIZED:-unset} WINDOW=${SIRIUS_CN_NIXL_TRANSFER_WINDOW:-default} ASYNC=${ASYNC:-1} FUSION=${FUSION:-<default leaf>} STREAMING_RECEIVER=${SIRIUS_CN_STREAMING_RECEIVER:-unset} HWM=${SIRIUS_EXCHANGE_INPUT_HWM_BYTES:-unset} INGRESS_BUDGET=${SIRIUS_INGRESS_BUDGET_BYTES:-unset} CHECKSUM=${SIRIUS_EXCHANGE_CHECKSUM:-unset} STRICT=${SIRIUS_EXCHANGE_STRICT:-unset} EXTRA_ENV=${EXTRA_ENV:-}"
echo "protocol: TO=${TO:-} RUNS=${RUNS:-} watchdog=${SIRIUS_QUERY_WATCHDOG_SECS:-} restart_on_fail=${RESTART_ON_FAIL:-0} break_on_fail=${BREAK_ON_FAIL:-0} FE_SETUP_SQL=${FE_SETUP_SQL:-} TPCH_SF=${TPCH_SF:-} data=${TPCH_DATA:-} oracle=${ORACLE:-} QD=${QD:-}"
echo "binaries: cn_sha256=$(sha256sum "$WT/experimental/starrocks/target/release/sirius-starrocks-cn" | cut -c1-16) engine_sha256=$(sha256sum "$WT/build/release/extension/sirius/sirius.duckdb_extension" | cut -c1-16) fe=$(readlink -f "$WT/experimental/starrocks/starrocks/output/fe")"
echo "dataset: $(readlink -f "${TPCH_DATA:-/dev/null}") lineitem_files=$(ls "${TPCH_DATA:-/nonexistent}/lineitem" 2>/dev/null | wc -l) metadata_sha256=$(sha256sum "${TPCH_DATA:-/nonexistent}/metadata.json" 2>/dev/null | cut -c1-16)"
