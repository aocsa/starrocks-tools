# Source (don't execute) before build-engine.sh / build-cn.sh / run-e2e.sh.
# Same knobs as /home/ubuntu/sirius-wt/env.sh, plus the Sirius worktree that holds the e2e.

export PATH="${HOME}/.pixi/bin:${PATH}"
export TOOLS_DIR="${TOOLS_DIR:-/home/ubuntu/sirius-wt/tools}"
export TMPDIR="${TMPDIR:-/opt/dlami/nvme/tmp}"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-amazon-corretto}"
export CONDA_OVERRIDE_CUDA="${CONDA_OVERRIDE_CUDA:-13}"
export SIRIUS_WT="${SIRIUS_WT:-/home/ubuntu/sirius/.claude/worktrees/arrow-shuffle-nixl-gpu-8a2a50}"
export GPU_LOCK_FILE="${GPU_LOCK_FILE:-/home/ubuntu/sirius-wt/.gpu.lock}"
export GPU_DEVICES="${GPU_DEVICES:-0,1}"
export SIRIUS_EXCHANGE_STAGING_BYTES="${SIRIUS_EXCHANGE_STAGING_BYTES:-1GiB}"
export UCX_TLS="${UCX_TLS:-cuda_copy,cuda_ipc,tcp,self}"
mkdir -p "$TMPDIR" 2>/dev/null || true
