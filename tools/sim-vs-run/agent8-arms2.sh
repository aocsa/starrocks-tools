#!/usr/bin/env bash
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/perf
R=$SP/sim8/run-agent8b.sh
busy=1; for i in $(seq 1 60); do if pgrep -f '[a]b-4cn.sh' >/dev/null || pgrep -f '[s]irius-starrocks-cn|[S]tarRocksFE' >/dev/null; then sleep 30; else busy=0; break; fi; done
echo "idle check: busy=$busy after $((i*30))s $(date -u)"; pgrep -af '[s]irius-starrocks-cn|[S]tarRocksFE' | cut -c1-140
if [ $busy = 1 ]; then echo "BOX BUSY after 30 min: arms 5/6 NOT RUN $(date -u)"; exit 0; fi
export QUERY_TIMEOUT=600 QUERIES=q06
echo "##### ARM 5 already completed at 07:22 (agent8-sf1000-4cn-asyncon-q06); skipping"
sleep 3
echo "##### ARM 6: SF1000 2CN async ON, q06 x15 $(date -u)"
NUM_CNS=2 SIRIUS_CN_ASYNC_SENDER_DISPATCH=1 GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB bash $R $WT /scratch/sirius/datasets/tpch_sf1000 sf1000-2cn-asyncon-q06 14 | grep -E '^==|pass|MATCH|VALUES|cn[0-3] |negative|CN p|shutdown|forcing|engine thread'
echo "AGENT8 ARMS2 DONE $(date -u)"
