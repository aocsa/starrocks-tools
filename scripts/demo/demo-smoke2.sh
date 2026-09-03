#!/usr/bin/env bash
# Rerun of the cluster2-equivalent smoke on the demo worktree. Fix vs demo-step3.sh: source scripts/cn-env.sh so the CN finds
# libnixl.so (cluster8.sh does this; the first smoke launch did not and both CNs died at exec). Also dumps the fragments each CN
# receives (SIRIUS_CN_DUMP_FRAGMENTS) so the 1-file layout's FileOrFiles.start/length byte ranges are visible.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/demo; SR=$WT/experimental/starrocks
OUT=$SP/step3-demo; mkdir -p $OUT/dump-cn1 $OUT/dump-cn2; rm -f $OUT/dump-cn1/* $OUT/dump-cn2/*
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
D=/scratch/prestouser/aocsa/demo-q1q6/tpch_sf1_f64_1file
echo "##### SMOKE2 cluster2-equivalent (FE + 2 CNs on GPU 0, 8GiB each, staging 1280MiB) $(date -u +%T)"
if $M -e 'select 1' >/dev/null 2>&1; then echo "REFUSING: something already answers on 9030"; exit 2; fi
cd $SR
export TOOLS_DIR=/home/prestouser/aocsa/tools
source scripts/cn-env.sh || { echo "cn-env.sh failed"; exit 3; }
export SIRIUS_EXCHANGE_STAGING_BYTES=1280MiB SIRIUS_QUERY_WATCHDOG_SECS=60 CUDA_VISIBLE_DEVICES=0 OUT
setsid nohup bash -c 'starrocks/output/fe/bin/start_fe.sh --logconsole & SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump-cn1 target/release/sirius-starrocks-cn --gpu-device 0 --gpu-memory-limit 8GiB --host-memory-limit 12GiB --engine-dir .cn1 & SIRIUS_CN_DUMP_FRAGMENTS=$OUT/dump-cn2 target/release/sirius-starrocks-cn --gpu-device 0 --heartbeat-port 9052 --thrift-port 9062 --brpc-port 8062 --http-port 8042 --starlet-port 9072 --gpu-memory-limit 8GiB --host-memory-limit 12GiB --engine-dir .cn2 & wait' > $OUT/cluster2.log 2>&1 < /dev/null &
LP=$!
echo "launcher pid=$LP"
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "2" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"
nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader | sort
$M -e "WITH lineitem AS (SELECT * FROM FILES(\"path\"=\"file://$D/lineitem/*.parquet\",\"format\"=\"parquet\")) SELECT sum(l_extendedprice * l_discount) AS revenue FROM lineitem WHERE l_shipdate >= date '1997-01-01' AND l_shipdate < date '1998-01-01' AND l_discount BETWEEN 0.03 - 0.01 AND 0.03 + 0.01 AND l_quantity < 24;" 2>&1
$M -e "SELECT count(*) FROM FILES(\"path\"=\"file://$D/lineitem/*.parquet\",\"format\"=\"parquet\");" 2>&1
echo "== q06 (bench-kit SQL) through the 2-CN smoke:"
$M -e "$(sed 's#FILES(\"path\"=\"file://[^\"]*/lineitem/\*\.parquet\"#FILES(\"path\"=\"file://'$D'/lineitem/*.parquet\"#' benchmarks/tpch/queries/q06.sql)" 2>&1 | head -5
echo "== nixl lines:"
sed 's/\x1b\[[0-9;]*m//g' $OUT/cluster2.log | grep -a -E 'nixl bandwidth canary|transmitted batches via nixl' | grep -o 'peer=.*\|stream_id=.*' | sort | uniq -c | head -8
echo "== negative markers (expect 0):"
sed 's/\x1b\[[0-9;]*m//g' $OUT/cluster2.log | grep -a -c -E 'needs the nixl transport tier|input stream row count unknown|fail_stalled_query|errorCode=62' 
echo "== fragment dumps: FileOrFiles start/length per CN"
for d in dump-cn1 dump-cn2; do echo "-- $d: $(ls $OUT/$d | wc -l) files"; grep -a -h -o -E 'FileOrFiles[^}]*' $OUT/$d/fragment-*.txt 2>/dev/null | grep -o -E '(path|start|length|file_size|size)[^,]*' | sort | uniq -c | head -12; done
echo "== stopping (SIGTERM to the launcher's process group, wait up to 45 s)"
kill -TERM -- -$LP 2>/dev/null
for i in $(seq 1 45); do pgrep -f "target/release/sirius-starrocks-cn --gpu-device 0 --gpu-memory-limit 8GiB" >/dev/null || break; sleep 1; done
pgrep -f "target/release/sirius-starrocks-cn --gpu-device 0 --gpu-memory-limit 8GiB" >/dev/null && { echo "CNs still up after 45 s, SIGKILL"; kill -KILL -- -$LP 2>/dev/null; }
pkill -KILL -f 'java .*starrocks/output/fe' 2>/dev/null; sleep 2
echo "SMOKE2 DONE $(date -u +%T)"
