# profile-diff: demo-plus (dp-cn4) vs perf baseline (cn4), 4 CNs, SF1000, 15 common queries

Written 2026-09-04. Arms: `demoplus/arms/dp-cn4` (demo/q1q6-integration-plus-fixes @ 281b13bc, dev-based carve + fixes 1/2/4)
vs `perf/sf1000/cn4` (perf/profile-sf1000 @ 45dab3be = feat/pin-table-cn d24f02c4 + 3 perf commits). Same box, same
knobs (GPU_MEM=100GiB, STAGING=16GiB, HOST_MEM=160GiB, Quent on, `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` exported by both
capture scripts: `perf/sf1000/capture-cn.sh`, `fix/capture-arm.sh`). Common queries: q01 q02 q03 q04 q06 q07 q10 q11 q12
q13 q14 q15 q19 q20 q22. "Warm" = mean of r1 and r2. Every number below is measured unless marked inference.

## 0. Method and why the shipped tools came up empty on this branch

The carve's Quent query records carry `instance_name = "sirius_streaming_fragment"` for all 1010 fragments (and
`sirius_ffi` for 46 result fragments), not the baseline's `<query id>:<fragment id>` label
(`dp-cn4/quent/.cn*/*/query/*.ndjson` vs `perf/sf1000/cn4/quent/...`). `quent_bp.py` therefore collapsed the whole arm
into one group (`dp-cn4/quent.txt` line 1: `fragments=1010 span=80.952s`). The carve's CN log lines
`transmitted batches via nixl`, `declared input stream cardinality`, `relayed native batches` also lack the
`query_id=`/`fragment_instance_id=` fields and the `elapsed_ms/lease_ms/write_ms/write_gbps` timings, so
`cnlog_extract.py` reports `cards[]` and no `nixl=` for every dp run (`dp-cn4/cnlog.txt`).

Both arms were therefore re-profiled with time-window mapping (task `Created` timestamp, nixl line timestamp, engine
plan timestamp against `runs/runs.csv` windows):

- `agent-notes/profile_diff.py <arm> <out.json>` -> `prof-perf-cn4.{json,txt}`, `prof-dp-cn4.{json,txt}`
  (per run: state sums, per-operator Computing by type, Reserving requested / peak alloc, batches, leases, nixl frames,
  per-CN scan and Queued).
- `agent-notes/compare_arms.py` -> `compare-warm-perf-vs-dp.txt` (warm means, deltas, flags >10% and >50 ms / >0.05 s).
- `agent-notes/plan_shapes.py` -> `plan-shapes-{perf,dp}-cn4.txt` (engine pipeline chains per run window).
- `agent-notes/timeline.py`, `dispatch_skew.py`, `percn_timeline.py` -> `timeline-*.txt`, `dispatch-skew-*.txt`,
  `percn-q06-r1.txt`, `percn-q03-r1.txt` (CN-side fragment/RPC/nixl timelines relative to the client start).

## 1. Plans, reservations, transfers: identical

- Engine plan shapes (multiset of `Pipeline #N: A -> B -> C` chains across the 4 engine logs) are identical for all 15
  queries at r1 (`plan-shapes-*.txt`, checked pairwise: "SAME plan chains" x15). Fragment counts per query are identical
  (`dp-cn4/cnlog.txt` vs `perf/sf1000/cn4-cnlog.txt`: q01 9, q02 49, q03 17, q04 17, q06 5, q07 39, q10 22, q11 32,
  q12 17, q13 17, q14 13, q15 22, q19 13, q20 26, q22 18).
- Reservation vs peak is unchanged to 3 digits (`compare-warm-perf-vs-dp.txt`, req_GB / alloc_GB columns): q01 943.6 /
  743.1 GB in both; q11 380 / 5.6 vs 379 / 5.8 GB (same 68x over-reservation); q03 1042 / 345 vs 1039 / 345 GB. The
  estimator and the peak allocations are the same code in both trees.
- nixl transmit bytes and frame counts are identical per query (`prof-*.json` tx_GB, tx): q03 60.34 GB / 39 transmits,
  q07 69.01 / 105, q10 48.04 / 54, q04 23.64 / 39, q12 23.47 / 39, q13 13.80 / 39, q14 5.43 / 27, q15 1.27 / 45, q19
  2.75 / 27, q20 15.01 / 66, q22 18.56 / 33, q11 0.40 / 75; q02 44.63 vs 44.12 GB (the skewed `n_regionkey`
  shuffle varies per run in both arms). Transmit pacing is also the same: in q03.r1 each lineitem sender emits its
  three 4.82-4.89 GB frame sets ~94 ms apart in both arms (baseline sender ec5f: TX at 903, 998, 1091 ms; dp sender 3:
  838, 932, 1026 ms; `percn-q03-r1.txt`), i.e. ~51 GB/s per sender stream either way. Per-transmit `elapsed/lease/
  write_ms` cannot be compared because the carve does not log them.
- Fixes 1, 2 and 4 are inert on the 15 passing queries: over the 45 passing dp runs `fix2_skipped=fix2_retired=
  fix4_fused=fix4_skipped=0`, `oom_reschedules=futile_aborts=reservation_clamped=partial_proceed=0`
  (`dp-cn4/cnlog.json`, summed); the baseline likewise has `oom_reschedules=0` on all 45 runs. `fix2_retired=4` appears
  only on the six failing runs. Fusion mode is `Leaf` (`dp-cn4/cluster.log` tunables line) and fuses nothing at 4 CNs.
  Every profile difference below is therefore the tree lineage (dev-based engine + carved CN), not the fixes.

## 2. Warm-run comparison (base -> dp), from `compare-warm-perf-vs-dp.txt`

| query | wall ms | engine span s | Computing s | GPU_SCAN s | non-scan s | Queued s | nixl GB |
|---|---:|---:|---:|---:|---:|---:|---:|
| q01 | 2160 -> 2335 (+8%) | 1.93 -> 2.12 | 26.8 -> 27.1 | 19.6 -> 19.9 | 7.26 -> 7.17 | 82.2 -> 82.4 | 0 |
| q02 | 1154 -> 1141 (-1%) | 0.95 -> 0.96 | 3.67 -> 2.82 | 2.65 -> 1.80 | 1.02 -> 1.01 | 0.03 -> 0.03 | 44.6 -> 44.1 |
| q03 | 1607 -> 1730 (+8%) | 1.39 -> 1.51 | 13.6 -> 12.3 | 11.4 -> 10.1 | 2.20 -> 2.21 | 17.9 -> 16.7 | 60.34 |
| q04 | 1000 -> 1066 (+7%) | 0.78 -> 0.83 | 8.72 -> 8.63 | 7.27 -> 7.23 | 1.45 -> 1.40 | 7.4 -> 7.5 | 23.64 |
| **q06** | **853 -> 1224 (+44%)** | **0.67 -> 1.04** | 9.51 -> 7.77 | 9.27 -> 7.57 | 0.24 -> 0.20 | 16.9 -> 12.9 | 0 |
| q07 | 2009 -> 1972 (-2%) | 1.79 -> 1.76 | 17.0 -> 16.7 | 14.8 -> 14.5 | 2.22 -> 2.25 | 30.0 -> 28.8 | 69.01 |
| q10 | 1789 -> 1786 (0%) | 1.51 -> 1.53 | 14.5 -> 14.2 | 11.9 -> 11.6 | 2.59 -> 2.58 | 13.9 -> 13.8 | 48.04 |
| q11 | 665 -> 669 (+1%) | 0.40 -> 0.39 | 2.31 -> 2.26 | 1.86 -> 1.84 | 0.45 -> 0.42 | 0.01 | 0.40 |
| q12 | 1284 -> 1290 (+1%) | 1.06 -> 1.07 | 11.9 -> 11.5 | 10.2 -> 9.9 | 1.69 -> 1.58 | 18.0 -> 18.1 | 23.47 |
| q13 | 1030 -> 1035 (0%) | 0.89 -> 0.89 | 10.6 -> 10.5 | 9.21 -> 9.12 | 1.35 -> 1.34 | 8.9 -> 8.8 | 13.80 |
| q14 | 1226 -> 1258 (+3%) | 1.03 -> 1.05 | 13.9 -> 13.9 | 13.6 -> 13.6 | 0.30 -> 0.28 | 18.9 -> 18.9 | 5.43 |
| q15 | 2035 -> 2073 (+2%) | 1.80 -> 1.84 | 24.7 -> 25.6 | 23.7 -> 24.8 | 1.02 -> 0.80 | 33.6 -> 35.6 | 1.27 |
| q19 | 1441 -> 1459 (+1%) | 1.25 -> 1.27 | 16.0 -> 15.8 | 14.6 -> 14.3 | 1.43 -> 1.42 | 72.2 -> 71.9 | 2.75 |
| q20 | 2000 -> 1811 (-9%) | 1.70 -> 1.54 | 17.2 -> 15.6 | 14.1 -> 14.3 | 3.09 -> 1.33 | 16.9 -> 16.9 | 15.01 |
| q22 | 832 -> 852 (+2%) | 0.50 -> 0.33 | 3.65 -> 3.64 | 1.23 -> 1.23 | 2.42 -> 2.42 | 0.01 | 18.56 |

Wall regressions above 10%: **q06 only (+44%)**. q01/q03/q04 are +7-8% (q01 within its known scan variance: dp r1
1846 / r2 2824 ms, GPU_SCAN 15.9 vs 23.9 s for identical bytes; baseline r1 2022 / r2 2298). Improvements above 10%:
none in wall; q20 is -9% (MERGE_GROUP_BY -80%). Sum of the 15 warm means: baseline 21.08 s, demo-plus 22.36 s (+6%),
of which q06 alone is +0.37 s.

Operator-level deltas above 10% and 0.05 s (same file):
- q06 `GPU_SCAN` 9.27 -> 7.57 s (-18%) with wall +44%: see section 3.
- q20 `MERGE_GROUP_BY` 2.086 -> 0.407 s (-80%, n=12 tasks, 24.4 GB input), `HASH_GROUP_BY` 0.70 -> 0.62 s (-11%);
  q15 `MERGE_GROUP_BY` 0.346 -> 0.054 s (-84%); q10 `MERGE_GROUP_BY` 0.095 -> 0.063 s: see section 4.
- q02 `GPU_SCAN` 2.65 -> 1.80 s (-32%): baseline run-to-run spread (base r1 3.58 s / r2 1.71 s, dp 1.73 / 1.88 s,
  `prof-*.json` per_cn; the report already flagged q02's 21% warm spread on one partsupp fragment). Not a tree effect.
- q03 `GPU_SCAN` 11.41 -> 10.07 s (-12%), consistent in both runs (base 11.45 / 11.37, dp 9.99 / 10.15) with the same
  82 scan tasks and 177.62 GB; per-CN 2.55-3.12 s (base) vs 2.35-2.62 s (dp). Not attributed: `src/op/scan/
  sirius_gpu_scan_operator.cpp` and the parquet ingestible differ between the trees (byte-range scan path,
  `diff -rq` list below) and the same arm-to-arm I/O variance seen on q01 is of this size. Inference, low confidence.
- q12 `STREAMING_SINK` 0.69 -> 0.61 s (-12%): 80 ms, no wall effect.
- `batches` -13..-36% everywhere: telemetry only (section 5).

## 3. The q06 regression (+44%) is the carved CN running sender fragments inside the exec_plan_fragment RPC

Measured, `percn-q06-r1.txt` and `dispatch-skew-*.txt`:

- Baseline q06.r1: the 4 lineitem senders start at 155.9 / 155.9 / 155.9 / 156.9 ms after the client start, finish
  821-844 ms, result fragment runs 845-850 ms, client end 853 ms. Every `exec_plan_fragment` RPC closes with
  `idle <= 1 ms` (995 RPCs over the arm, max 1 ms, sum 0.4 s).
- demo-plus q06.r1: three senders start at 159.7-159.8 ms and finish 728-767 ms; their RPCs close only then
  (`idle=571 / 584 / 609 ms`, i.e. the RPC blocked for the whole scan). The fourth sender, on the CN that also hosts the
  result fragment (9102), starts at 770.3 ms, right after the last of those three RPCs returned (768.2 ms), finishes at
  1201 ms; the result fragment then runs 1202-1206 ms; client end 1209 ms. The same pattern in r0 (late sender on 9112
  = result host, starts 858.6 ms) and r2 (9112, starts 819.0 ms). Leaf start skew per run: baseline 1 / 1 / 1 ms,
  demo-plus 652 / 611 / 662 ms.
- Arm-wide, 75 dp `exec_plan_fragment` RPCs took >= 1 s and 291 took 100 ms-1 s (sum 215.8 s over 1251 RPCs);
  0 in the baseline.
- Consequence for Computing: the late scan runs alone and is 25-35% faster per fragment (431 / 437 / 412 ms vs
  568-659 ms for the three concurrent ones), which is why dp's q06 `GPU_SCAN` sum is 18% lower and Queued 24% lower
  while wall is 44% higher. This is the scan-contention effect B7 of yesterday's report seen from the other side.

Source (verified, read-only): the baseline CN reads `SIRIUS_CN_ASYNC_SENDER_DISPATCH` and queues sender-only fragments
on the dispatch worker (`perf/experimental/starrocks/src/compute_node_service.rs:207-297`, commit 9a5a4da6 "perf(cn):
opt-in async dispatch of sender-only fragments", whose doc comment describes exactly this q06 wave: "the node that also
hosts the merge fragment only receives its own scan after every other node's scan has finished"). The demo-plus CN has
no reference to that variable (`grep -rn ASYNC_SENDER_DISPATCH demo-plus/experimental/starrocks/src` is empty; `git
branch --contains 9a5a4da6` lists only fix/files-cardinality, fix/fragment-fusion, fix/oom-failfast); its
`exec_plan_fragment` -> `exec_single_attachment` -> `process_inline` -> `process_fragment` runs the fragment on the RPC's
blocking task (`demo-plus/experimental/starrocks/src/compute_node_service.rs:271-292, 592-624`). `ASYNC=1` in
`demoplus/run-arms.log` is therefore a no-op on this branch.

Second-order effect on multi-wave queries (measured, `percn-q03-r1.txt`): in q03 the second wave of leaf senders
(the 34-39 ms customer scans) starts at 1145 ms on all four dp CNs, after the slowest first-wave RPC closed (1101 ms,
`idle=932 ms` = scan + drain); in the baseline each CN starts its second-wave sender as soon as its own drain finishes
(1091 / 1100 / 1142 / 1168 ms). That is the +0.1 s of q03 (span 1.39 -> 1.51 s) and, by inference, the +7% of q04.
The FE-side overhead outside the CNs is unchanged: mean client-start to first leaf-sender start 196 ms (base) vs 195 ms
(dp); result-finish to client end 16.0 vs 15.6 ms (`timeline-*.txt`, 30 warm runs each).

## 4. The q20 / q15 MERGE_GROUP_BY speedups are the baseline's ungated merge-side float-sum sort, absent in dev

Measured: q20 `MERGE_GROUP_BY` 2.084 / 2.087 s (base r1 / r2) vs 0.408 / 0.406 s (dp), same 12 tasks and 24.4 GB;
q15 0.346 vs 0.054 s (16 tasks, 5.4 GB); q10 0.095 vs 0.063 s (`prof-*.json` ops_type). q20 wall -9%
(2000 -> 1811 ms); q15 wall unchanged because it is scan-bound (GPU_SCAN 23.7 -> 24.8 s).

Source (verified): `perf/src/op/merge/gpu_merge_impl.cpp:123-133` sorts the partials (`cudf::sort`) before every FP32/
FP64 ungrouped SUM and `:207-221` gathers the concatenated partials into canonical (keys, float values) order
(`canonicalize_row_order`) and runs the sort-based groupby whenever any aggregate `is_order_sensitive_sum`. Neither
path checks `canonical_float_sums_enabled()`: the `SIRIUS_CANONICAL_FLOAT_SUMS` gate (85658f09) covers only the local
aggregate in `gpu_aggregate_impl.cpp:164-172`. The merge-side sort entered with 441f05b2 ("Add Sirius-as-StarRocks-CN
multi-fragment execution ...", on feat/pin-table-cn, not on dev). The demo-plus tree (dev-based) has plain
`cudf::groupby(..., null_policy::INCLUDE)` and `cudf::reduce` over the unsorted partials (`diff -u perf/src/op/merge/
gpu_merge_impl.cpp demo-plus/...`), and also lacks the `throw_if_int64_sum_could_overflow` minmax guard on every SUM
(`aggregate_op_util.cpp`, `sirius_physical_ungrouped_aggregate.cpp:400-403` in perf only).

Trade-off (inference): the baseline pays 1.7 s of q20 for bit-stable merged sums across arrival orders; the carve does
not have that stability. Both arms are already non-deterministic on q15 (rows: base r0 1 / r1 0 / r2 0, dp r0 1 / r1
1 / r2 0, `runs.csv`), so the measured correctness picture did not change, but a dev-based CN build has one fewer
defence than feat/pin-table-cn if the merge order matters.

## 5. Telemetry regressions of the carve (not runtime)

- Quent query labels: baseline `instance_name = "<StarRocks query id>:<fragment instance id>"` (commit 45dab3be,
  `sirius_extension.cpp` query_label plumbing exists in both trees, but the carved CN never sets it); demo-plus:
  `sirius_streaming_fragment` for all 1010 fragments. `quent_bp.py --runs` / `cnlog_extract.py` per-query grouping is
  unusable on this branch; use time windows (`profile_diff.py`).
- Missing Quent events: `staging_lease` data batches (baseline q03.r1: 474 leases, 122.67 GB, max 221 concurrent;
  dp: 0), `scan_split_read` statistics, stream hop placements (`perf/src/telemetry/scan_telemetry.cpp`,
  `staging_arena_telemetry.cpp` exist only in the perf tree). The `batches` column drops by exactly the remote frame
  count (q03: 806 - 569 = 237 = tx_batches), so the -13..-36% "batches" deltas are accounting, not fewer batches.
- CN log: `transmitted batches via nixl` without query/fragment ids and without `elapsed_ms/lease_ms/write_ms/
  write_gbps`; `declared input stream cardinality` and `relayed native batches` without the `fragment{...}` span. The
  baseline's `cn4-transmits.txt` style per-transmit timing table cannot be produced for demo-plus.
- Recommendation: before this branch replaces perf/profile-sf1000 as the measurement tree, port 45dab3be's labels and
  the transmit-line fields, otherwise every per-query tool in `perf/sf1000/` silently reports empty.

## 6. Cold runs (not the focus, recorded)

q03.r0: 3987 ms (dp) vs 2071 ms (base). The dp lineitem senders of that run took 1337-1340 ms each vs 572-646 ms warm
(`percn-q03-r1.txt`, negative-offset FINISH lines belong to q03.r0's tail); leaf start skew 1908 ms. Other cold runs
moved both ways (q02 2158 -> 1971, q04 2155 -> 1528, q01 3366 -> 3048). One run each; not attributed.

## 7. Tree differences consulted (read-only)

`diff -rq demo-plus/src perf/src` (42 files, excluding legacy): pipeline (fix 1: `retry_futility.hpp`,
`gpu_pipeline_executor.cpp`, `task_scheduler.*`), aggregate/merge (`aggregate_op_util.*`, `gpu_aggregate_impl.cpp`,
`gpu_merge_impl.cpp`, `sirius_physical_ungrouped_aggregate.cpp`), scan (`sirius_gpu_scan_operator.cpp`,
`parquet_gpu_ingestible.hpp`, `gpu_ingestible_types.hpp`), exec (`exchange_staging_arena.*`, `streaming_fragment.*`,
`stream_bind_catalog.hpp`), telemetry (`batch_telemetry.*`; `scan_telemetry.*` and `staging_arena_telemetry.*` only in
perf), ffi/engine/extension, vss. CN: 19 files differ; `parked_registry.rs` (fix 2) only in demo-plus; `admin_command.rs`,
`wire_type_parity.rs`, nixl bench/echo/harness only in perf. Engine commits in the carve over dev (`git log
01613070..HEAD -- src`): 9c002f97 (fix 1), b7452f67, 92e91adf, f1e8fb17, 1e61c16c, a22235e1, d53b44b1, 98661d7d,
14386a77, 6c0b83a1, 98fe1a84, 94a77836 and merges. feat/pin-table-cn over dev in src: 11 commits (d24f02c4 ... 54506fa0);
dev has 0 src commits that feat/pin-table-cn lacks.
