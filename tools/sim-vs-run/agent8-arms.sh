#!/usr/bin/env bash
# agent 8 driver: 4 cluster launches, one at a time, box must be idle first.
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf
R=$SP/sim8/run-agent8.sh
for i in $(seq 1 60); do pgrep -f '[a]b-4cn.sh' >/dev/null || pgrep -f '[s]irius-starrocks-cn|[S]tarRocksFE' >/dev/null || break; sleep 30; done
echo "box idle check done after $((i*30))s $(date -u)"
export QUERY_TIMEOUT=600
echo "##### ARM 1: SF1000 4CN async ON $(date -u)"
NUM_CNS=4 SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB bash $R $WT /scratch/sirius/datasets/tpch_sf1000 sf1000-4cn-asyncon 5 | grep -E '^==|pass|MATCH|VALUES|cn[0-3] |negative|CN processes'
sleep 3
echo "##### ARM 2: SF100 4CN async ON $(date -u)"
NUM_CNS=4 SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB bash $R $WT /scratch/sirius/datasets/tpch_sf100 sf100-4cn-asyncon 5 | grep -E '^==|pass|MATCH|VALUES|cn[0-3] |negative|CN processes'
sleep 3
echo "##### ARM 3: SF10 4CN async ON $(date -u)"
NUM_CNS=4 SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB bash $R $WT /scratch/sirius/datasets/tpch_sf10 sf10-4cn-asyncon 5 | grep -E '^==|pass|MATCH|VALUES|cn[0-3] |negative|CN processes'
sleep 3
echo "##### ARM 4: SF10 4CN async OFF $(date -u)"
NUM_CNS=4 GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB bash $R $WT /scratch/sirius/datasets/tpch_sf10 sf10-4cn-asyncoff 5 | grep -E '^==|pass|MATCH|VALUES|cn[0-3] |negative|CN processes'
echo "AGENT8 ARMS DONE $(date -u)"
