---
name: starrocks-sirius-sf1000-planning-findings-2026-09-03
description: "SF1000 profiling facts for StarRocks-on-Sirius (2026-09-03 afternoon): FE plans FILES() scans at 1 row so join distribution is cardinality-blind and depends on CN count; 15/22 TPC-H queries run on the CN path, 6 OOM in the engine's 100-retry loop, q16 is a translator gap; q15 flakes with the float-sum sort gated off; where the evidence and tools live"
metadata:
  type: project
---

Campaign of 2026-09-03 ~13:00-14:00 UTC on the 4x GB200 box, branch `perf/profile-sf1000` in worktree
`/home/prestouser/aocsa/sirius-stacks-wt/perf` (= d24f02c4 + aggregate gate + async sender dispatch + Quent probes
45dab3be), decimal TPC-H SF1000 at `/scratch/sirius/datasets/tpch_sf1000`. Evidence + tools in that session's scratchpad
`perf/sf1000/` (WORKFLOW-PLAN.md indexes everything); report (done, 389 lines, 33-agent workflow, 9 findings double-confirmed) at `~/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md`; summary section appended to `~/.claude/plans/starrocks-sirius-perf-plan.md`.

Facts (all verified against code or logs):
- StarRocks FE `StatisticsCalculator.computeFileScanNode` sets outputRowCount = 1 and every column statistic UNKNOWN for
  FILES() scans, so EXPLAIN COSTS shows `cardinality: 1` on every node. With 1 CN alive the CBO picks BROADCAST for
  almost every join (orders 1.5B rows, lineitem 3.79B rows as the build side of q04's LEFT SEMI JOIN); with 4 CNs alive
  the same queries flip to PARTITIONED shuffles and gain fragments. Engine stream ids == FE exchange node ids, and the
  receiver logs `declared input stream cardinality stream_id=N rows=R` with the exact parked row count, so FE estimate
  vs actual per exchange is directly measurable (card_compare.py).
- CN path at SF1000 (100 GiB pool per CN): q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22 pass on 1 and 4
  CNs; q05 q08 q09 q17 q18 q21 die in `gpu_pipeline_executor.cpp` after MAX_RETRIES=100 OOM reschedules of GPU_SCAN
  (50 ms sleep per retry, thousands of warnings, 1-4 minutes to fail; parked intermediates cannot spill); q16 needs
  `multi_distinct_count` in the translator's partial-state model.
- q15 returns 0 rows in most CN runs (streams 18/22 empty) because the FE inlines the revenue CTE twice and joins two
  independently computed FP64 sums; the canonical float-sum gate does NOT fix it (A/B: 4/6 empty with it on, 5/6 off).
  Standalone (DECIMAL sums) is exact every time. Fix: compute the CTE once (MULTI_CAST sink) or exact decimal sums.
- `SIRIUS_CN_TRANSLATE_ONLY=1` is not a support oracle: receivers fail "exchange node requires a bound same-node input stream" because inputs are bound only in real dispatch.
- FE audit log stays empty on this FE; map CN log events to runs by time window (cnlog_extract.py) instead of query markers.
- Engine reservation estimate vs measured peak per task is 1-54x (q11 54x, q14 15x, q03 14x on 1 CN); pipeline pool default num_threads 4 shows up as large Quent `Queued` sums.

**Why:** these explain most of the SF1000 behaviour and would cost another sweep to rediscover.

**How to apply:** start any planning/cardinality question from EXPLAIN COSTS (all 1s) and the declared cardinalities; treat OOM failures at GPU_SCAN as parked-intermediate memory pressure, not scan bugs; keep q15 out of determinism-sensitive comparisons unless the canonical sort is on. See [[starrocks-cn-perf-findings-2026-09-03]], [[quent-cn-telemetry-gotchas]].
