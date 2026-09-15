#!/usr/bin/env bash
# Serialize every GPU user on this box. usage: gpu-lock.sh <cmd...>
# Waits for the lock; refuses to start while a foreign compute process holds a GPU.
set -euo pipefail
LOCK=${GPU_LOCK_FILE:-/home/ubuntu/sirius-wt/.gpu.lock}
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
flock 9
for i in $(seq 1 120); do
  busy=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l)
  [ "$busy" = 0 ] && break
  [ "$i" = 1 ] && echo "[gpu-lock] $busy GPU process(es) alive, waiting" >&2
  sleep 30
done
[ "$busy" = 0 ] || { echo "[gpu-lock] GPUs still busy after 60 min, giving up" >&2; exit 3; }
# Run the job as a child with the lock descriptor CLOSED: an exec would hand fd 9 to every
# descendant, and a daemon the build leaves behind (sccache) then holds the lock for everyone.
"$@" 9>&-
exit $?
