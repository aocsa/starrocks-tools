#!/usr/bin/env bash
# Carve task Step 3 on the demo worktree: cluster2-equivalent smoke (2 CNs on GPU 0, 8GiB carve-outs, DEMO Q6 shape), then
# 4-CN arms: SF1 1file, SF1 multi, SF10 1file, SF10 multi (f64), warm 3 + cold-restart 1, oracle, log checks, cn-distribution.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/demo; SR=$WT/experimental/starrocks
OUT=$SP/step3-demo; mkdir -p $OUT
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
echo "##### SMOKE cluster2-equivalent (FE + 2 CNs on GPU 0, 8GiB each, staging 1280MiB) $(date -u +%T)"
( cd $SR && export SIRIUS_EXCHANGE_STAGING_BYTES=1280MiB SIRIUS_QUERY_WATCHDOG_SECS=60 CUDA_VISIBLE_DEVICES=0; \
  setsid nohup bash -c 'starrocks/output/fe/bin/start_fe.sh --logconsole & target/release/sirius-starrocks-cn --gpu-device 0 --gpu-memory-limit 8GiB --host-memory-limit 12GiB --engine-dir .cn1 & target/release/sirius-starrocks-cn --gpu-device 0 --heartbeat-port 9052 --thrift-port 9062 --brpc-port 8062 --http-port 8042 --starlet-port 9072 --gpu-memory-limit 8GiB --host-memory-limit 12GiB --engine-dir .cn2 & wait' > $OUT/cluster2.log 2>&1 < /dev/null & )
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "2" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"; nvidia-smi --query-compute-apps=gpu_uuid,pid --format=csv,noheader | sort | uniq -c
D=/scratch/prestouser/aocsa/demo-q1q6/tpch_sf1_f64_1file
$M -e "WITH lineitem AS (SELECT * FROM FILES(\"path\"=\"file://$D/lineitem/*.parquet\",\"format\"=\"parquet\")) SELECT sum(l_extendedprice * l_discount) AS revenue FROM lineitem WHERE l_shipdate >= date '1997-01-01' AND l_shipdate < date '1998-01-01' AND l_discount BETWEEN 0.03 - 0.01 AND 0.03 + 0.01 AND l_quantity < 24;" 2>&1
$M -e "SELECT count(*) FROM FILES(\"path\"=\"file://$D/lineitem/*.parquet\",\"format\"=\"parquet\");" 2>&1
grep -a -E 'nixl bandwidth canary|transmitted batches via nixl' $OUT/cluster2.log | sed 's/\x1b\[[0-9;]*m//g' | grep -o 'peer=.*\|stream_id=.*' | sort | uniq -c | head -6
pkill -f "$SR/target/release/sirius-starrocks-cn"; pkill -f "$SR/starrocks/output/fe"; sleep 5; pkill -9 -f "$SR/target/release/sirius-starrocks-cn" 2>/dev/null; pkill -9 -f "$SR/starrocks/output/fe" 2>/dev/null; sleep 2
for ds in tpch_sf1_f64_1file tpch_sf1_f64_multi tpch_sf10_f64_1file tpch_sf10_f64_multi; do
  echo "##### DEMO 4 CN $ds $(date -u +%T)"
  NUM_CNS=4 GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB SIRIUS_EXCHANGE_STAGING_BYTES=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 QUERY_TIMEOUT=120 COLD_TIMEOUT=180 bash $SP/run-step3.sh $WT /scratch/prestouser/aocsa/demo-q1q6/$ds demo-$ds 2>&1 | grep -E '^== (alive|EXPLAIN|WARM|COLD|nvidia)|pass|REFUSED|WEDGE|MATCH|DIFFER|NO-|negative|^\.cn|MiB|AGGREGATE|EXCHANGE|PARTITION|SORT|MERGING|uuid|^ +[0-9] GPU'
done
echo "STEP3 DONE $(date -u +%T)"
