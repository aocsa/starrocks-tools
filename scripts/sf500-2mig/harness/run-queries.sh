#!/usr/bin/env bash
# Run TPC-H queries against the FE on 9030 with a /* marker */ so the FE audit log and the CN
# cluster log tie a QueryId back to (query, run).
#   usage: run-queries.sh <tag> <tpch_data> <out_dir> <timeout_s> <runs_after_warmup> q01 q02 ...
# writes <out_dir>/runs.csv (query,run,phase,status,ms,rows,start_utc) and
#        <out_dir>/<q>.r<i>.out / .err (mysql --batch)
# status: pass | fail | timeout | wrong (exit 0 but the run differs from the oracle: VALUES-DIFFER / ROWS-DIFFER / EMPTY)
# QD (query directory), MYSQL_BIN, ORACLE, SR_DIR, PY come from cluster-env.sh; all overridable by env.
#
# Every run is compared against the oracle immediately (ORACLE unset = no compare, status stays pass),
# and every query gets ALL its runs even after a failure (BREAK_ON_FAIL=1 restores the old
# break-on-first-failure), so a failing query yields RUNS+1 samples of its failure class.
# ENGINE_LOG_SLICES=1 (default when SR_DIR is set) copies, after each run, the bytes each CN appended
# to its engine log during that run into <out_dir>/../engine-slices/<q>.r<i>.<cn>.log: a wrong answer
# or a killed arm keeps its engine evidence (the arm-end copy is all-or-nothing).
# RESTART_ON_FAIL=1 (with RQ_WT RQ_N exported by capture-arm.sh): after a query with a failed or
# timed-out run, restart the cluster before the next query. A WRONG run never triggers a restart:
# the cluster state that produced it is evidence.
TAG=$1; DATA=$2; OUT=$3; TO=$4; RUNS=$5; shift 5
QD=${QD:-/home/ubuntu/sirius-wt/base/experimental/starrocks/benchmarks/tpch/queries}
MYSQL_BIN=${MYSQL_BIN:-$(command -v mysql || echo /home/ubuntu/sirius-wt/base/experimental/starrocks/.pixi/envs/default/bin/mysql)}
FE_HOST=${FE_HOST:-127.0.0.1}; FE_PORT=${FE_PORT:-9030}
HARNESS=${HARNESS:-/home/ubuntu/sirius-wt/harness}
PY=${PY:-python3}
[ -x "$MYSQL_BIN" ] || { echo "no mysql client at $MYSQL_BIN (source cluster-env.sh first)" >&2; exit 1; }
[ -d "$QD" ] || { echo "no query dir $QD" >&2; exit 1; }
[ -n "${ORACLE:-}" ] && [ ! -d "$ORACLE" ] && { echo "ORACLE=$ORACLE is not a directory; per-run compare disabled" >&2; ORACLE=; }

mkdir -p "$OUT"; echo "query,run,phase,status,ms,rows,start_utc" > "$OUT/runs.csv"
M="$MYSQL_BIN --host $FE_HOST --port $FE_PORT --user root --batch --connect-timeout=5"
DAY=$(date -u +%F)
SLICES=$(dirname "$OUT")/engine-slices
slices_on=0; [ "${ENGINE_LOG_SLICES:-1}" = 1 ] && [ -n "${SR_DIR:-}" ] && [ -d "$SR_DIR" ] && slices_on=1 && mkdir -p "$SLICES"
declare -A off
mark_logs(){ for d in "$SR_DIR"/.cn*; do f=$d/log/sirius_$DAY.log; off[$(basename $d)]=$( [ -f "$f" ] && stat -c %s "$f" || echo 0 ); done; }
slice_logs(){ # q rI
  for d in "$SR_DIR"/.cn*; do cn=$(basename $d); f=$d/log/sirius_$DAY.log; [ -f "$f" ] || continue
    start=${off[$cn]:-0}; now=$(stat -c %s "$f"); [ "$now" -lt "$start" ] && start=0   # rotated/truncated by a restart
    tail -c +$((start + 1)) "$f" | head -c $((now - start)) > "$SLICES/$1.$2.$cn.log"; done; }

need_restart=0
for q in "$@"; do
  [ -f "$QD/$q.sql" ] || { echo "$q: no $QD/$q.sql -- skipped"; continue; }
  if [ "$need_restart" = 1 ] && [ "${RESTART_ON_FAIL:-0}" = 1 ]; then
    bash "$HARNESS/restart-cluster.sh" "$RQ_WT" "$(dirname "$OUT")" "$RQ_N" "$TO" "after $prev_q" \
      || echo "RESTART FAILED before $q (see $(dirname "$OUT")/restarts.log); continuing"
    need_restart=0
  fi
  prev_q=$q
  sql=$(sed -e "s#__TPCH_DATA__#$DATA#g" -e "s#__TPCH_SF__#${TPCH_SF:-1}#g" "$QD/$q.sql")
  for i in $(seq 0 "$RUNS"); do
    phase=warm; [ "$i" = 0 ] && phase=cold
    [ $slices_on = 1 ] && mark_logs
    start=$(date -u +%FT%T.%3N); t0=$(date +%s%N)
    if timeout "$TO" $M -e "/* $TAG $q r$i */ $sql" > "$OUT/$q.r$i.out" 2> "$OUT/$q.r$i.err"; then st=pass; else st=fail; fi
    ms=$(( ($(date +%s%N) - t0) / 1000000 )); rows=$(( $(wc -l < "$OUT/$q.r$i.out") - 1 )); [ $rows -lt 0 ] && rows=0
    [ $st = fail ] && [ $ms -ge $((TO * 1000 - 500)) ] && st=timeout
    verdict=
    if [ $st = pass ] && [ -n "${ORACLE:-}" ]; then
      verdict=$("$PY" "$HARNESS/compare.py" --run "$q.r$i" "$OUT" "$ORACLE" 2>&1 | head -1)
      case "$verdict" in MATCH*) ;; NO-ORACLE*) ;; *) st=wrong; echo "$verdict" > "$OUT/$q.r$i.err";; esac
    fi
    [ $slices_on = 1 ] && slice_logs "$q" "r$i"
    echo "$q,$i,$phase,$st,$ms,$rows,$start" >> "$OUT/runs.csv"
    echo "$q r$i $phase $st ${ms}ms rows=$rows ${verdict:+oracle=$verdict }$( [ $st != pass ] && [ $st != wrong ] && head -c 160 "$OUT/$q.r$i.err" | tr '\n' ' ')"
    if [ $st = fail ] || [ $st = timeout ]; then need_restart=1; [ "${BREAK_ON_FAIL:-0}" = 1 ] && break; fi
  done
done
