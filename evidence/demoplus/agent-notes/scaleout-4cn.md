# scaleout-4cn: demo/q1q6-integration-plus-fixes at 4 CNs, SF1000 (2026-09-04)

Arm: `demoplus/arms/dp-cn4` (demo-plus @ 281b13bc, NUM_CNS=4, GPU_MEM=100GiB, STAGING=16GiB, ASYNC=1 exported,
FUSION unset -> `fusion_mode=Leaf`; run-arms.log, fix/capture-arm.sh:12-15). Baseline: `perf/sf1000/cn4` (perf/profile-sf1000,
same box, 2026-09-03, unpinned). Pinned reference: `demoplus/reference-pin-table-cn.md` (feat/pin-table-cn, tables pinned in GPU
memory). All paths below are relative to the scratchpad root unless absolute. "Measured" = read from the named file; "inference" is
marked.

Tooling caveats that affect how the numbers were obtained (measured):

- `dp-cn4/cnlog.txt` shows `relayed=0b/0s cards[]` and no `nixl=` for every run, while `dp-cn4/cluster.log` contains 2535
  `transmitted batches via nixl`, 780 `relayed native batches` and 897 `declared input stream cardinality` lines. The carve's lines
  carry no `query_id=`, which `cnlog_extract.py` requires (`if not q: continue`). Volumes below come from time-window mapping
  (`agent-notes/_dispatch_skew.py`), not from cnlog.txt.
- `dp-cn4/quent.txt` lumps all 1010 fragments under one label `sirius_streaming_fragment` (the CN fragment labels and the staging-lease
  probe from perf 45dab3be are not in the carve: `leases=0` everywhere, `scan_reads: null` 1056 times in quent.json). Per-query
  operator sums below come from `agent-notes/_scan_by_query.py` (fragments assigned to runs.csv windows).
- Quent for the last runs is truncated (q22.r1 6 fragments, q22.r2 none): CN exit before flush.

## 1. Per-query warm medians (seconds)

dp-cn4 = median of the two warm runs in `dp-cn4/runs/runs.csv`; perf = `perf/sf1000/cn4/runs/runs.csv`; pinned = 4CN column of
`reference-pin-table-cn.md`. GPU_SCAN share = GPU_SCAN Computing seconds / all-operator Computing seconds, summed over all tasks on
all 4 CNs for run r1 (r0 for the failures), from quent.json (`_scan_by_query.py` output).

| query | pinned 4 CN | perf 4 CN | demo-plus 4 CN | demo-plus cold | dp/perf | dp/pinned | GPU_SCAN share (dp) |
|---|--:|--:|--:|--:|--:|--:|--:|
| q01 | 5.76 | 2.16 | 2.33 | 3.05 | 1.08 | 0.41 | 69% (15.9/23.2 s) |
| q02 | 1.25 | 1.15 | 1.14 | 1.97 | 0.99 | 0.91 | 63% (1.7/2.8 s) |
| q03 | 1.03 | 1.61 | 1.73 | 3.99 | 1.08 | 1.68 | 82% (10.0/12.2 s) |
| q04 | 0.73 | 1.00 | 1.07 | 1.53 | 1.07 | 1.46 | 83% (7.3/8.7 s) |
| q05 | 1.78 | fail (cn1 only) | fail | 1.41 | - | - | 97% (11.7/12.0 s) |
| q06 | 0.41 | 0.85 | 1.22 | 1.30 | 1.44 | 2.99 | 97% (7.5/7.7 s) |
| q07 | 1.29 | 2.01 | 1.97 | 2.00 | 0.98 | 1.53 | 87% (14.6/16.8 s) |
| q08 | 2.05 | fail | fail | 2.39 | - | - | 97% (19.0/19.6 s) |
| q09 | 2.73 | fail | fail | 2.12 | - | - | 96% (18.9/19.6 s) |
| q10 | 1.62 | 1.79 | 1.79 | 1.87 | 1.00 | 1.10 | 81% (11.4/14.0 s) |
| q11 | 1.03 | 0.66 | 0.67 | 0.76 | 1.01 | 0.65 | 81% (1.8/2.2 s) |
| q12 | 1.01 | 1.28 | 1.29 | 1.30 | 1.01 | 1.28 | 86% (9.9/11.5 s) |
| q13 | 0.85 | 1.03 | 1.03 | 1.16 | 1.00 | 1.22 | 87% (9.3/10.6 s) |
| q14 | 0.56 | 1.23 | 1.26 | 1.23 | 1.03 | 2.25 | 98% (13.9/14.2 s) |
| q15 | 0.60 | 2.03 | 2.07 | 2.01 | 1.02 | 3.46 | 97% (24.5/25.4 s) |
| q16 | 0.74 | fail (cn1) / not run | 0.66 | 0.74 | - | 0.90 | 44% (0.5/1.1 s) |
| q17 | 3.89 | fail | fail | 1.32 | - | - | 97% (11.2/11.5 s) |
| q18 | 2.89 | fail | fail | 1.35 | - | - | 80% (9.2/11.4 s) |
| q19 | 0.88 | 1.44 | 1.46 | 1.56 | 1.01 | 1.66 | 91% (14.1/15.5 s) |
| q20 | 1.13 | 2.00 | 1.81 | 1.78 | 0.91 | 1.60 | 91% (14.2/15.6 s) |
| q21 | 3.33 | fail | fail | 2.10 | - | - | 95% (20.3/21.3 s) |
| q22 | 0.63 | 0.83 | 0.85 | 0.90 | 1.02 | 1.35 | 32% (1.2/3.6 s) |

The perf 4-CN baseline never ran the six (capture-cn4.log skipped them after cn1 failed); "fail" in the perf column is the cn1 result
(`perf/sf1000/results.md`). The six fail in dp-cn4 on the cold run and are not retried (runs.csv has only r0 for them).

Sums (measured from the medians above): demo-plus 16 passing = 22.36 s; pinned on the same 16 = 19.52 s; perf 15 passing = 21.08 s;
on the 15 queries both unpinned arms pass, demo-plus 21.70 vs perf 21.08 (1.029x). With the five that pass at 48 GiB
(`dp-cn4-six-stg48/runs/runs.csv`: q05 4.29, q08 3.59, q17 2.65, q18 2.03, q21 5.24 = 17.80 s; pinned same five 13.94 s) the 21-query
total is 40.16 s vs the pinned 21-query 33.46 s (36.19 - 2.73 for q09) = 1.20x; q09 does not pass at any arena size tried
(section 6).

## 2. What moved vs the perf 4-CN baseline, and why

dp - perf per query (s): q06 +0.371, q20 -0.189, q01 +0.175, q03 +0.123, q04 +0.066, q15 +0.038, q14 +0.032; every other common
query within +-0.02. Fix 3 is on neither branch and the plans are identical: declared cardinality rows per run are the same
(e.g. q03 13 declarations / 3,500,159,817 rows, q22 14 / 6,000,000,043, q10 21 / 1,802,745,091 in both `_dispatch_skew.py` outputs),
and so are nixl bytes (q03 60.34 GB, q07 69.01, q10 48.04, q22 18.56, q12 23.47, q04 23.64 in both logs).

### q06 +0.37 s (+44%): the leaf sender on the result CN runs after the other three (measured)

In all three dp-cn4 q06 runs, three leaf senders start together and the fourth starts 611-662 ms later, right after the third
finishes, and the late one is always on the CN that hosts the result fragment (`dp-cn4/cluster.log`, `fragment run started`):

| run | three senders start | fourth sender start (CN) | result fragment CN |
|---|---|---|---|
| r0 | 22:02:35.9615 (9132, 9122, 9102) | 22:02:36.6136 (9112) | 9112 |
| r1 | 22:02:37.2217 (9112, 9122, 9132) | 22:02:37.8323 (9102) | 9102 |
| r2 | 22:02:38.4315 (9102, 9132, 9122) | 22:02:39.0940 (9112) | 9112 |

Baseline q06: all four start within 1 ms (`perf/sf1000/cn4/cluster.log` 13:19:52.9078-.9089, leaf skew 1 ms in all three runs) and
the per-fragment times are the same or slower (623-687 ms vs 431-606 ms in dp), so the whole regression is the serialization, not the
engine (dp GPU_SCAN 7.5 s summed vs 9.4 s in baseline).

Mechanism (measured + source): the demo-plus CN holds the `exec_plan_fragment` RPC open for the sender's whole run: RPC close
`time.idle` max per run = 651/609/662 ms for q06 (= fragment elapsed), 2040 ms for q01.r0, 1860 ms for q03.r0; in the baseline
the max idle is 1 ms in every run (both arms, `_dispatch_skew.py` column `rpc_idle_ms`). The baseline had
`SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` honoured (perf commit 9a5a4da6; `perf/.../compute_node_service.rs:208,271,291`); the demo-plus tree
has no such code (`grep -rn ASYNC_SENDER_DISPATCH sirius-stacks-wt/demo-plus/experimental/starrocks/src` = nothing; the bring-up
tunables line lists `fusion_mode=Leaf` and no dispatch knob), so capture-arm.sh exporting it (line 14) had no effect. The FE's
`Deployer.createFragmentInstanceExecStates` (demo-plus `fe-core/.../qe/scheduler/Deployer.java`, three-stage split) puts an instance
whose worker already has a deployed instance into stage 2 and deploys stage 2 only after `waitForDeploymentCompletion` of stage 1;
the result fragment is deployed first, so the leaf sender on that CN is in stage 2, and with blocking RPCs stage 1 completes only when
the other three senders finish. Inference: q06 is the only query where a leaf sender fragment is the direct child of the result
fragment (5 fragments); in q01 the merge stage sits between them, all four workers are already in `deployedWorkerIds` when the leaf
fragment is deployed, and the four leaves start together (skew 0-1 ms, measured). Yesterday's report line 313 already predicted
this ("with the default, the same drain holds the RPC open and delays the FE's next deploy stage").

### q20 -0.19 s (0.91x): MERGE_GROUP_BY (measured)

Quent operator sums, all tasks and CNs: MERGE_GROUP_BY 2.08-2.09 s per run in the baseline vs 0.41 s in dp-cn4 (HASH_GROUP_BY
0.64-0.76 vs 0.61-0.64, GPU_SCAN 14.0-14.1 vs 14.2-14.3). This is an engine difference in the dev base the carve sits on, not one
of the four fixes (inference).

### q01 +0.18 s, q03 +0.12 s: GPFS scan variance (measured, attribution inference)

q01 warm runs 1846 and 2824 ms with GPU_SCAN sums 15.9 and 23.9 s (baseline 2022/2298 ms, 18.5/20.6 s); q03 cold 3987 ms with
GPU_SCAN 24.8 s vs baseline cold 2071 ms / 16.6 s. Scan seconds move with wall; the join/aggregate sums are identical to the baseline
(q03 HASH_JOIN 1.50 s both). `/scratch` is GPFS read with O_DIRECT (report section 1), so warm runs re-read from GPFS.

## 3. Fix activity and counters at 4 CNs

- Fix 1 (engine OOM fail-fast): inert. `grep -ciE 'out_of_memory|bad_alloc|OOM|futile'` over `dp-cn4/engine-.cn*.log` = 0 on every CN;
  same for dp-cn1-six, dp-cn4-six-stg48, dp-cn4-q09-stg72 (cnlog.txt `oom_resched=0 futile=0 partial=0` on every run). No query
  reached an engine OOM; all six failures are CN-side (staging arena) so the engine never had an OOM retry to cut short.
- Fix 2 (per-query parked-output bookkeeping, cancel teardown): active on every failure. 24 `retired a query's parked sender outputs`
  lines = 6 failing queries x 4 CNs (`fix2_retired: 4` per failing run in cnlog.txt), all `trigger=cn_err`, all `still_parked=0`
  (fragments 1-7, slots 1-9 per line). `cancel_plan_fragment retired the query on this CN` replaces the baseline's
  `acknowledging cancel_plan_fragment (best-effort: no engine-side abort yet)`. On the 48 passing runs every cancel is
  `reason=QUERY_FINISHED released_leases=0` (0 lines with `released_leases>0` in any passing window). On the six failures the
  INTERNAL_ERROR cancels released leftover staging leases: 67 cancel lines with `released_leases>0`, 663 leases in total
  (q05 77, q08 151, q09 107, q17 49, q18 104, q21 175; `_dispatch_skew.py` `cancels/released`). `skipping fragment of a retired query`:
  0 in dp-cn4, 1 in stg48 q09.
- Fix 4 (fragment fusion): resolved `fusion_mode=Leaf` on all four CNs (`dp-cn4/cluster.log` 22:01:55.94 tunables line), and zero
  `fused sender fragment into its local receiver` lines and zero `fragment fusion skipped` lines in the whole 4-CN log
  (`grep -c` = 0 / 0; the skip reason is logged at debug level per fragment-fusion-SPEC.md:382-402, so silence is expected). At 1 CN
  the same binary fuses 2-4 fragments per run (`dp-cn1-six/cnlog.txt` `fix4_fused` 4/2/2/2/3/2 for q05/q08/q09/q17/q18/q21). Inert
  at 4 CNs by design: the rule needs a single local destination, and a HASH_PARTITIONED sink has four.

## 4. Correctness vs the DuckDB oracle (`dp-cn4/compare.txt` vs `perf/sf1000/compare-cn4.txt`)

Identical verdicts on the 15 common queries, cell for cell: MATCH q02 q04 q06 q12 q13 q14 q20 q22; VALUES-DIFFER q01 (9.570e-04,
8 cells), q03 (1.766e-03, 4), q07 (9.558e-04, 4), q10 (1.229e+01, 48), q15 (1.126e-03, 1), q19 (9.552e-04, 1); q11 EMPTY. 8/22 match
at 1e-6 (8/15 in the baseline; the 7 extra are q16 NO-ORACLE and the six failures).

- The ~1e-3 diffs are the decimal-dataset truncation already on record (memory: decimal TPC-H truncates 1-0.07 on the CN path).
  q10's 12.29 is the same revenue perturbation (row 1 734306.4329 vs oracle 736354.4839) re-ordering two rows
  (95772536 / 124473127 swap, `dp-cn4/runs/q10.r1.out` vs `oracle/tpch_sf1000/q10.tsv`); the dp and baseline q10 outputs are the
  same rows in the same order.
- q11: 0 rows in both arms and the oracle file `oracle/tpch_sf1000/q11.tsv` is header-only, so the oracle also returned 0 rows;
  EMPTY is the compare script's label, not a mismatch.
- q15: r2 returned 0 rows (r0/r1 1 row; baseline r1/r2 0 rows, r0 1 row). The one-row outputs differ in the last digit
  (3351778.0989 vs 3351778.0988999996), i.e. the float-sum equality against `max(total_revenue)` flakes when the two sums land on
  different bits (SIRIUS_CANONICAL_FLOAT_SUMS finding, memory). Present in both branches, not a regression.
- q16 passes only in demo-plus (27840 rows, no oracle); the perf cn1 run failed in the translator
  (`multi_distinct_count has a partial state this translator does not model`, `perf/sf1000/cn1/runs/q16.r0.err`).
- The 55 x 4 `unsupported plan node TPlanNodeType(19)` FE warnings in `dp-cn4/cluster.log` occur once per query submission (4 per
  second-stamp, every query), not on the benchmark queries themselves; the queries all passed or failed for other reasons. Left
  unexplained (likely the harness's probe statements).

## 5. Suite sum vs the pinned 36.19 s: the unpinned caveat quantified

Pinned reference is 22 queries in 36.19 s with I/O held by a GPU-tier compressed pin; demo-plus reads parquet from GPFS with
O_DIRECT on every run. GPU_SCAN is 63-98% of all operator Computing time in every dp-cn4 query except q16 (44%) and q22 (32%)
(table in section 1). These are task-seconds summed over 4 CNs, not wall, but they show what the pinned run does not pay.

Per-query dp - pinned (s): q15 +1.47, q06 +0.81, q03 +0.70, q14 +0.70, q07 +0.68, q20 +0.68, q19 +0.58, q04 +0.34, q12 +0.28,
q22 +0.22, q13 +0.18, q10 +0.17; q02 -0.11, q16 -0.08, q11 -0.36, q01 -3.42. At 48 GiB: q05 +2.50, q21 +1.91, q08 +1.54,
q17 -1.24, q18 -0.86.

- The queries that are 97-98% scan (q06, q14, q15) are 2.25-3.46x the pinned time; q06 is the clean case: 7.5 s of GPU_SCAN task time
  on 4 CNs behind a 1.22 s wall of which 0.37 s is the dispatch serialization of section 2, leaving ~0.85 s vs 0.41 pinned
  (inference: the remaining gap is the GPFS read).
- Where demo-plus beats the pinned numbers (q01 0.41x, q11 0.65x, q16 0.90x, q17 0.68x and q18 0.70x at 48 GiB) the pinned reference
  is the older feat/pin-table-cn engine; those gains are engine-side and would presumably carry over to a pinned run (inference).
- Net: on the 16 queries that pass at 16 GiB, demo-plus is 22.36 vs 19.52 pinned (1.15x); on 21 queries with the 48 GiB arena for the
  five, 40.16 vs 33.46 (1.20x). Roughly 2.8 s of the 16-query gap sits in the scan-dominated queries listed above; the pinned run
  would need the same 48 GiB arena for the five to be comparable (the reference's dagger marks on q05/q08/q09/q21 are the user's
  "filled, partial 1 CN" annotation and are not explained further in the reference file).

## 6. The six at 4 CNs (short; the arena agent owns the deep dive)

All six die on the cold run with the CN-side error `exchange staging arena exhausted` (`dp-cn4/runs/q05,q08,q09,q17,q18,q21.r0.err`):
requested 422-664 MB, 26-47 leases outstanding holding 16.0-17.1 GB of the 17.18 GB arena; q08 and q21 hit it in the sender's
`failed to export a packed batch`, the other four in `request_staging_lease`. Bytes already transmitted over nixl in the failing
window, per receiving CN (`_dispatch_skew.py` mapping of `transmitted batches via nixl`): q05 10.6 GB to each of two CNs, q08 13.6 GB
to two CNs, q09 5.8 GB x 4, q17 7.5 GB x 2, q18 12.0 GB x 4, q21 15.3 GB x 4 (q03, which passes, moves 15.0-15.1 GB per CN,
q07 16.8-17.5 GB: the passing queries already sit at 88% of the arena, report B6). With `STAGING=48GiB, GPU_MEM=90GiB`
(`dp-cn4-six-stg48`) five pass (correct vs the oracle: q17 q18 q21 MATCH, q05 q08 the usual 1e-3/1e-5 decimal diffs) and q09 still
exhausts 48 GiB with 95 leases holding 50.55 GB; at 72 GiB (`dp-cn4-q09-stg72`) q09 gets past the arena and fails after 10.5 s in
the receiver's pool: `failed to push a staged remote batch from sender 0 into stream 4: std::bad_alloc: out_of_memory`
(`dp-cn4-q09-stg72/runs/q09.r0.err`). No engine OOM/futile/downgrade line in any of these arms.

What this says for "why more than one CN fails" (inference from the above): at 1 CN there is no staging (local parked outputs,
plus fusion in this branch), so these six run; at N>1 CNs the shuffled build sides are staged in a bounded arena that fails hard
instead of throttling (report B6, `exchange_staging_arena.cpp:248`, `compute_node_service.rs:1302`), and these six shuffle
more per CN than 16 GiB (q09 more than 48 GiB). Needed: an arena sized to the largest concurrently staged volume per CN (48 GiB
buys five of six on a 186 GB GB200, impossible next to the pool on a 96 GB RTX PRO 6000, which matches the user's 2-CN
`fail (OOM)` on q05/q08/q09/q18; q17/q21 "fail" there is consistent with the same error but the cause is not in the screenshot),
or the engine change in yesterday's report item 8 (blocking lease with bounded wait, or push remote frames into pool memory on
arrival and release the lease), plus for q09 pool headroom on the receiver. Why feat/pin-table-cn ran most of them at 4 CNs is not
answerable from these files (same arena mechanism in that tree, `sirius/experimental/starrocks/src/engine.rs:744`; the pinned runs'
STAGING value is not in the reference).

## 7. Files produced

- `agent-notes/_scan_by_query.py` (per-run operator sums from quent.json by runs.csv window), `_dispatch_skew.py` (leaf-sender start
  skew, RPC idle, nixl bytes, cancels/released leases per run), `_leases_by_query.py` (Quent lease sums; all zero in the carve).
