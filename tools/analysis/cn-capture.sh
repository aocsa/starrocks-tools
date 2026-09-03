#!/usr/bin/env bash
# Quent CN capture on the instrumented build: N CNs, SF10 decimal, q06 x4 then q01 x2, Quent ON via SIRIUS_CN_ENABLE_QUENT=1.
# usage: cn-capture.sh <num_cns>
N=${1:-1}
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/quent; SR=$WT/experimental/starrocks
export NUM_CNS=$N GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB SIRIUS_EXCHANGE_STAGING_BYTES=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 SIRIUS_CN_ENABLE_QUENT=1
source $SP/cluster-env.sh $WT
M="mysql --host 127.0.0.1 --port 9030 --user root --batch --connect-timeout=5"
OUT=$SP/quent/cn-capture-${N}cn; mkdir -p $OUT
# remember pre-existing sessions so we can pick the new ones
for i in $(seq 0 $((N-1))); do ls $SR/.cn$i/telemetry 2>/dev/null > $OUT/before-cn$i.txt; done
bash $SP/start-cluster.sh $WT $OUT/cluster.log
for i in $(seq 1 60); do n=$($M -e "SHOW COMPUTE NODES;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Alive") c=i; next} $c=="true"{n++} END{print n+0}'); [ "$n" = "$N" ] && break; sleep 3; done
echo "alive=$n after $((i*3))s"; grep -a -h 'enable_quent\|wrote derived' $SR/.cn0/derived-sirius-config.yaml $OUT/cluster.log 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | head -3
D=/scratch/sirius/datasets/tpch_sf10; QD=$SR/benchmarks/tpch/queries
for q in q06 q06 q06 q06 q01 q01; do Q=$(sed "s|__TPCH_DATA__|$D|g" $QD/$q.sql); t0=$(date +%s%3N); $M -e "$Q" > $OUT/$q.$t0.out 2>&1; echo "$q $(( $(date +%s%3N) - t0 )) ms rows=$(( $(wc -l < $OUT/$q.$t0.out) - 1 ))"; done
sleep 5
bash $SP/stop-cluster.sh $WT >/dev/null
for i in $(seq 0 $((N-1))); do ls $SR/.cn$i/telemetry | grep -v -F -f $OUT/before-cn$i.txt > $OUT/new-sessions-cn$i.txt; echo "cn$i new sessions: $(cat $OUT/new-sessions-cn$i.txt | tr '\n' ' ')"; done
echo "--- stitch lines:"; sed 's/\x1b\[[0-9;]*m//g' $OUT/cluster.log | grep -a -E 'fragment run (started|finished)|transmitted batches via nixl|remote exchange stream ended|wrote derived' | head -12 | cut -c1-260
