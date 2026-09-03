# source this before cluster8.sh / bench.sh. usage: source cluster-env.sh <worktree-root>
WT=${1:?worktree root}
CLONE=/home/prestouser/aocsa/sirius-stacks
export TOOLS_DIR=/home/prestouser/aocsa/tools
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-arm64
export PATH="$CLONE/experimental/starrocks/.pixi/envs/client/bin:$JAVA_HOME/bin:$PATH"
unset CUDA_VISIBLE_DEVICES
export NUM_CNS=${NUM_CNS:-4}
export GPU_MEM=${GPU_MEM:-64GiB}
export HOST_MEM=${HOST_MEM:-128GiB}
export STAGING=${STAGING:-8GiB}
export SIRIUS_EXCHANGE_STAGING_BYTES=${SIRIUS_EXCHANGE_STAGING_BYTES:-$STAGING}
export SIRIUS_QUERY_WATCHDOG_SECS=${SIRIUS_QUERY_WATCHDOG_SECS:-60}
export RUST_LOG=${RUST_LOG:-sirius_starrocks_cn=info,info}
export SR_DIR=$WT/experimental/starrocks
