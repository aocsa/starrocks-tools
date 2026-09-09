# Source (don't execute) before start-cluster.sh / capture-arm.sh.
#   usage: source cluster-env.sh <worktree-root>
#
# Box-local adaptation of starrocks-tools/scripts/cluster/cluster-env.sh for
# x86_64 / 48 cores / 499 GB RAM / 2x RTX PRO 6000 (97887 MiB each).
#
# Every variable exported here is read by code in this repo -- see README.md
# ("env vars and where they are read") before adding one.
WT=${1:?worktree root}
[ -d "$WT/experimental/starrocks" ] || { echo "not a sirius worktree: $WT" >&2; return 1 2>/dev/null || exit 1; }

export HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
export WT_BASE=${WT_BASE:-/home/ubuntu/sirius-wt/base}      # baseline build: pixi envs, FE, engine .so live here
export SR_DIR=$WT/experimental/starrocks

# TOOLS_DIR (nixl/UCX, needed by cn-env.sh), JAVA_HOME (FE), TMPDIR, pixi on PATH.
# shellcheck source=/home/ubuntu/sirius-wt/env.sh
source /home/ubuntu/sirius-wt/env.sh

# mysql comes from the starrocks pixi env; java must come from JAVA_HOME. Order matters: that
# pixi env also ships a JDK 17 (`java -version` = 17.0.18) and the FE needs 21, so $JAVA_HOME/bin
# goes FIRST. start_fe.sh honours JAVA_HOME when set; this keeps `java` on PATH consistent too.
export PATH="$JAVA_HOME/bin:$WT_BASE/experimental/starrocks/.pixi/envs/default/bin:$PATH"
# python with duckdb + pyarrow (oracle regeneration); the harness tools need stdlib only.
export PY=${PY:-$WT_BASE/.pixi/envs/default/bin/python}
[ -x "$PY" ] || PY=$(command -v python3)

# cluster8.sh assigns one CN per GPU and refuses if NUM_CNS > visible GPUs; a pinned
# CUDA_VISIBLE_DEVICES would hide the others.
unset CUDA_VISIBLE_DEVICES
export NUM_CNS=${NUM_CNS:-1}
# 97887 MiB per card: 84 GiB pool + 8 GiB staging (staging sits OUTSIDE --gpu-memory-limit)
# + CUDA context still fits. The GB200 box's 100GiB pool does not.
export GPU_MEM=${GPU_MEM:-84GiB}
export HOST_MEM=${HOST_MEM:-160GiB}
export STAGING=${STAGING:-8GiB}
export SIRIUS_EXCHANGE_STAGING_BYTES=${SIRIUS_EXCHANGE_STAGING_BYTES:-$STAGING}
# Port plan (cluster8.sh): FE 8030/9010/9020/9030; CN i gets 9100+10i .. 9100+10i+4.
export PORT_BASE=${PORT_BASE:-9100}
export PORT_STRIDE=${PORT_STRIDE:-10}

# Engine telemetry: the CN's FFI path installs a log sink only when a SIRIUS_LOG_* var is
# set. engine.rs sets SIRIUS_LOG_DIR=<engine-dir>/log itself; spdlog is the default backend
# but is pinned here so the arm scripts' engine-log glob cannot silently find nothing.
export SIRIUS_LOG_BACKEND=${SIRIUS_LOG_BACKEND:-spdlog}
export RUST_LOG=${RUST_LOG:-sirius_starrocks_cn=info,info}

# Data / queries / oracle on this box.
export TPCH_SF=${TPCH_SF:-1000}
export TPCH_DATA=${TPCH_DATA:-/home/ubuntu/tpch_parquet_sf$TPCH_SF}
# The worktree's own kit when it carries all 22 queries (dp/kit and later), else base's copy.
_wt_qd=$WT/experimental/starrocks/benchmarks/tpch/queries
if [ "$(ls "$_wt_qd"/q*.sql 2>/dev/null | wc -l)" -ge 22 ]; then
  export QD=${QD:-$_wt_qd}
else
  export QD=${QD:-$WT_BASE/experimental/starrocks/benchmarks/tpch/queries}
fi
export ORACLE=${ORACLE:-/home/ubuntu/starrocks-tools/oracle/tpch_sf$TPCH_SF}
export OUT_BASE=${OUT_BASE:-/home/ubuntu/sirius-wt/arms}

export MYSQL_BIN=${MYSQL_BIN:-$(command -v mysql)}
export FE_HOST=${FE_HOST:-127.0.0.1}
export FE_PORT=${FE_PORT:-9030}
