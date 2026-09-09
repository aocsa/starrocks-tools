#!/usr/bin/env bash
# Run one verification arm end to end: preflight, cluster up, readiness, queries, cluster down,
# evidence collection.
#   usage: capture-arm.sh <worktree> <num_cns> <tag> <timeout_s> <runs_after_warmup> q01 q05 ...
#
# env overrides:
#   ASYNC=0            unset SIRIUS_CN_ASYNC_SENDER_DISPATCH (default 1 = set)
#   FUSION=<mode>      export SIRIUS_CN_FRAGMENT_FUSION=<mode>   (off | leaf | leaf-any | ...)
#   TRANSLATE_ONLY=1   export SIRIUS_CN_TRANSLATE_ONLY=1
#   FE_SETUP_SQL="..." run once after the FE is ready
#   EXTRA_ENV="A=1 B=2"  exported before launch
#   OUT_BASE           default /home/ubuntu/sirius-wt/arms
#   GPU_MEM STAGING HOST_MEM   default 84GiB / 8GiB / 160GiB (2x RTX PRO 6000, 97887 MiB each)
#   SIRIUS_QUERY_WATCHDOG_SECS default = <timeout_s>
#   READY_STRICT=0     run the queries even if the readiness probe failed
#
# Quent is on, fragment dumps at 1 CN, engine logs copied at the end, cnlog_extract at the end.
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
WT=${1:?worktree}; N=${2:?num cns}; TAG=${3:?tag}; TO=${4:?timeout_s}; RUNS=${5:?runs after warmup}; shift 5

OUT=${OUT_BASE:-/home/ubuntu/sirius-wt/arms}/$TAG
mkdir -p "$OUT/quent"
# shellcheck source=./cluster-env.sh
source "$HARNESS/cluster-env.sh" "$WT"
DATA=$TPCH_DATA
export NUM_CNS=$N
export GPU_MEM=${GPU_MEM:-84GiB} HOST_MEM=${HOST_MEM:-160GiB} STAGING=${STAGING:-8GiB}
export SIRIUS_EXCHANGE_STAGING_BYTES=$STAGING
# The engine watchdog must not fire BEFORE the client timeout, or a slow-but-correct query is
# recorded as an engine kill. The GB200 harness pinned 300 s against a 600 s client timeout.
export SIRIUS_QUERY_WATCHDOG_SECS=${SIRIUS_QUERY_WATCHDOG_SECS:-$TO}
export SIRIUS_CN_ENABLE_QUENT=1
export SIRIUS_LOG_BACKEND=${SIRIUS_LOG_BACKEND:-spdlog}
if [ "${ASYNC:-1}" = 1 ]; then export SIRIUS_CN_ASYNC_SENDER_DISPATCH=1; else unset SIRIUS_CN_ASYNC_SENDER_DISPATCH; fi
[ -n "${FUSION:-}" ] && export SIRIUS_CN_FRAGMENT_FUSION=$FUSION
[ "${TRANSLATE_ONLY:-0}" = 1 ] && export SIRIUS_CN_TRANSLATE_ONLY=1
for kv in ${EXTRA_ENV:-}; do export "$kv"; done
[ "$N" = 1 ] && { export SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump; mkdir -p "$OUT/dump"; rm -f "$OUT/dump"/*; }

M="$MYSQL_BIN --host $FE_HOST --port $FE_PORT --user root --batch --connect-timeout=5"
DAY=$(date -u +%F)   # this box runs UTC, so spdlog's daily-rotated sirius_<local date>.log matches

echo "##### ARM $TAG wt=$(basename "$WT")@$(git -C "$WT" rev-parse --short HEAD 2>/dev/null) NUM_CNS=$N GPU_MEM=$GPU_MEM STAGING=$STAGING HOST_MEM=$HOST_MEM ASYNC=${ASYNC:-1} FUSION=${FUSION:-<unset>} TRANSLATE_ONLY=${TRANSLATE_ONLY:-0} EXTRA_ENV=${EXTRA_ENV:-} watchdog=$SIRIUS_QUERY_WATCHDOG_SECS data=$DATA queries: $* $(date -u +%T)"

# ---- preflight -------------------------------------------------------------------------
pgrep -f "^[^ ]*/sirius-starrocks-cn " >/dev/null && { echo "REFUSING: a CN is already running"; exit 2; }
( exec 3<>/dev/tcp/$FE_HOST/$FE_PORT ) 2>/dev/null && { echo "REFUSING: something answers on $FE_PORT"; exit 2; }
[ -x "$MYSQL_BIN" ] || { echo "REFUSING: no mysql client at '$MYSQL_BIN'"; exit 2; }
[ -x "$JAVA_HOME/bin/java" ] || { echo "REFUSING: no java at $JAVA_HOME/bin/java"; exit 2; }
[ -d "$DATA/nation" ] || { echo "REFUSING: no dataset at $DATA"; exit 2; }
gpus=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
[ -n "${GPU_DEVICES:-}${MIG_DEVICES:-}" ] || [ "$gpus" -ge "$N" ] || { echo "REFUSING: asked for $N CNs but only $gpus GPUs are visible (set GPU_DEVICES=0,0 to share one)"; exit 2; }

# Fresh telemetry and engine log per arm, so the extractors cannot mix two arms.
rm -rf "$SR_DIR"/.cn*/telemetry/*
for d in "$SR_DIR"/.cn*; do [ -f "$d/log/sirius_$DAY.log" ] && : > "$d/log/sirius_$DAY.log"; done

# ---- up --------------------------------------------------------------------------------
bash "$HARNESS/start-cluster.sh" "$WT" "$OUT/cluster.log"
bash "$HARNESS/wait-ready.sh" "$WT" "$N" $((TO + 60)) 2>&1 | tee "$OUT/ready.txt"
ready_rc=${PIPESTATUS[0]}
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' '; echo

[ -n "${FE_SETUP_SQL:-}" ] && { echo "== FE setup: $FE_SETUP_SQL"; $M -e "$FE_SETUP_SQL" 2>&1 | head -3; }

# ---- pins (feat/pin-table-cn kit) ------------------------------------------------------
# PIN=1 pins lineitem + orders on every alive CN through the kit's pin-all.sh (host or gpu tier per
# PIN_TIER, the kit's column subsets). Pins are in-process state and the FE's brpc channel can
# refuse for a few seconds after bring-up, so the kit retries; a CN that still reports ok=0 makes
# the arm skip its queries rather than measure a half-pinned cluster.
pin_rc=0
if [ "${PIN:-0}" = 1 ] && [ "$ready_rc" = 0 ]; then
    : > "$OUT/pin.txt"
    bash "$HARNESS/pin-cluster.sh" "$WT" "$N" "$OUT/pin.txt"; pin_rc=$?
fi
# run-queries.sh's restart-on-failure hook needs the worktree and CN count (RESTART_ON_FAIL=1 enables it).
export RQ_WT=$WT RQ_N=$N HARNESS

# ---- queries ---------------------------------------------------------------------------
if [ "$ready_rc" != 0 ] && [ "${READY_STRICT:-1}" = 1 ]; then
    echo "SKIPPING QUERIES: cluster never became ready (see $OUT/ready.txt); set READY_STRICT=0 to run anyway"
elif [ "$pin_rc" != 0 ]; then
    echo "SKIPPING QUERIES: pins did not land on every CN (see $OUT/pin.txt)"
elif [ $# -gt 0 ]; then
    bash "$HARNESS/run-queries.sh" "$TAG" "$DATA" "$OUT/runs" "$TO" "$RUNS" "$@"
fi

# ---- down + evidence -------------------------------------------------------------------
echo "== stopping (SIGTERM, wait) $(date -u +%T)"; bash "$HARNESS/stop-cluster.sh" "$WT"
for d in "$SR_DIR"/.cn*; do
    cn=$(basename "$d")
    [ -f "$d/log/sirius_$DAY.log" ] && cp "$d/log/sirius_$DAY.log" "$OUT/engine-$cn.log"
    [ -d "$d/telemetry" ] || continue
    for s in "$d"/telemetry/*/; do mkdir -p "$OUT/quent/$cn"; cp -r "$s" "$OUT/quent/$cn/" 2>/dev/null; done
done
[ -f "$OUT/runs/runs.csv" ] && "$PY" "$HARNESS/cnlog_extract.py" "$OUT/cluster.log" "$OUT/runs/runs.csv" \
    --engine "$OUT/engine-*.log" --json "$OUT/cnlog.json" > "$OUT/cnlog.txt" 2>&1
echo "##### ARM $TAG DONE rc_ready=$ready_rc out=$OUT $(date -u +%T)"
