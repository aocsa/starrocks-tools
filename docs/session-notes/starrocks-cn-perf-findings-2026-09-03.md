---
name: starrocks-cn-perf-findings-2026-09-03
description: "Two measured causes of the StarRocks-on-Sirius gap (canonical float-sum sort on the CN path; inline sender RPC serializing the FE's deploy waves) and the Quent method that found them"
metadata: 
  node_type: memory
  type: project
  originSessionId: 25f3e903-c551-4a98-b7fc-a6e0ed53ade6
  modified: 2026-09-03T06:47:40.489Z
---

Measured 2026-09-03 on the 4x GB200 box against `aocsa/feat/pin-table-cn` @ d24f02c4, decimal TPC-H
parquet in `/scratch/sirius/datasets/tpch_sf{10,100,1000}`, plan at
`~/.claude/plans/starrocks-sirius-perf-plan.md`, fixes on branch `perf/float-sum-canonicalize-flag`
in `/home/prestouser/aocsa/sirius-stacks-wt/perf`.

1. q01 on the CN was 4.9x slower than standalone at SF1000 (25.75 s vs 5.27 s) because
   `gpu_aggregate_impl.cpp` sorts every batch (`canonicalize_row_order`) when a SUM is FLOAT64, and the
   CN path lowers decimal sums to FP64. Standalone sums DECIMAL and never hits it. Gated behind
   `SIRIUS_CANONICAL_FLOAT_SUMS` (default off): 1-CN q01 4.95 s, ratio 0.94.
2. q06 does not scale because `exec_plan_fragment` ran the sender scan inside the RPC and the FE
   waits for the first-instance-per-node wave before deploying the rest; the merge-hosting CN's scan
   started one full scan late. Opt-in `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` queues sender-only fragments
   on the dispatch worker.
3. Quent telemetry (`.cn<i>/telemetry/<session>/task/*.ndjson`, `Computing` states with `instance_name`
   and `input_bytes`) gave per-operator time without nsys; `scratchpad/perf/quent_summary.py` was the
   extractor. Query `Init` timestamps aligned across CNs expose dispatch skew.

**Why:** these two are the first things to check before blaming nixl or pool sizing on any new
StarRocks-on-Sirius number; both were invisible to `bench.sh` and to the CN logs alone.

**How to apply:** for CN-path aggregate slowness, compare `HASH_GROUP_BY` task time CN vs standalone
in Quent first; for a gather that does not scale, align fragment `Init` times across CNs. See also
[[tpch-decimal-datasets-cn-truncation]] for the q01 correctness caveat on decimal data.
