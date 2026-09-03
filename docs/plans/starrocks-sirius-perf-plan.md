# StarRocks-on-Sirius vs standalone Sirius: 1-GPU parity and scale-out plan

## Summary (non-profiled warm medians, decimal TPC-H, 4x GB200, `d24f02c4`)

- 1-GPU parity, baseline: q01 `T_sr1/T_sirius` 4.08 (SF10), 5.23 (SF100), 4.89 (SF1000); q06 1.91, 1.47, 0.79. After gating the canonical float-sum sort (`SIRIUS_CANONICAL_FLOAT_SUMS`, default off): q01 1.20 at SF100 (644/538 ms) and 0.94 at SF1000 (4954/5271 ms).
- Scale-out, baseline SF1000: q01 `T1/T4` 3.63 (efficiency 0.91), q06 1.47 (0.37). After the gate and the opt-in async sender dispatch (`SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`): q06 4 CN 1245 → 844 ms (`T1/T4` 2.12, efficiency 0.53; every "on" run faster than every "off" run in a 5+5 A/B), q01 4 CN 7088 → ~1.7–2.2 s.
- Cause 1 (q01): the CN path lowers decimal sums to FP64 and the local aggregate then sorts every 2.4 GB batch for bit-stable sums (`gpu_aggregate_impl.cpp:158-169`); Quent showed `HASH_GROUP_BY` 523 vs 54 ms per task. Cause 2 (q06): sender fragments ran inside their `exec_plan_fragment` RPC and the FE waits for the first deploy wave, so the merge-hosting CN's scan started one full scan late (fragment `Init` stagger of 726 ms at 4 CN).
- Correctness: q01 on the CN path fails the oracle on decimal data (8 cells, rel 9.57e-04: `1 - 0.07` truncated to 0.92 by the FP64→DECIMAL cast); q03 and q14 inherit it. Standalone matches. Reported as correctness, not slowness.
- Deliverables: baseline table §1, Quent-based analysis §2, ranked bottlenecks §3, ordered work §4, implemented and re-timed changes §5 (commits `85658f09`, `9a5a4da6` on `perf/float-sum-canonicalize-flag`), simulation artifacts 01/03/04/06/07/08 under `~/.claude/plans/starrocks-sirius-perf/` (08: async sender dispatch and the FE-floor fit verified against the box; E3/E6 partial, E7/E8 blocked on the unimplemented throttles).

Written 2026-09-03 06:30 UTC from measurements taken 05:33–06:25 UTC on the 4x GB200 box (aarch64, 189 GiB HBM each, NV18 mesh, CUDA 13). Code: `aocsa/feat/pin-table-cn` @ `d24f02c4`, built in `/home/prestouser/aocsa/sirius-stacks-wt/sot` (engine `build/release`, CN `target/release/sirius-starrocks-cn`, nixl-linked). Patched build: `/home/prestouser/aocsa/sirius-stacks-wt/perf` (branch `perf/float-sum-canonicalize-flag`, same commit plus the two changes in "Implemented").

Data: decimal-typed TPC-H parquet already on the box, unpinned: `/scratch/sirius/datasets/tpch_sf10` (lineitem 1 file, 56 row groups), `tpch_sf100` (6 files, 564 row groups), `tpch_sf1000` (60 files, 166 GB, 5640 row groups). Monetary columns are `DECIMAL(15,2)`.
Queries: `experimental/starrocks/benchmarks/tpch/queries/q01.sql`, `q06.sql` (standard TPC-H parameters), the same text on both arms. Standalone runs them through `scratchpad/perf/standalone_run.py` (FILES() rewritten to `read_parquet`, `LOAD` the extension, `SET gpu_execution = true`, `perf_counter` around `execute().fetchall()`, one process on GPU 0). StarRocks runs them through `bench.sh` (`mysql --batch`, wall clock) against `cluster8.sh` clusters, CN i on GPU i, `CUDA_VISIBLE_DEVICES` unset, `NIXL_NO_STUBS_FALLBACK=1`, watchdog 60 s.
Configs: SF10 `GPU_MEM=64GiB STAGING=8GiB HOST_MEM=128GiB` (standalone YAML = the CN's derived YAML, 64/128); SF100 and SF1000 `GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB` (standalone 100/160). Quent telemetry was ON in every arm (the CN's derived YAML does not set `enable_quent`, and the C++ default is `true`; the standalone YAML was copied from it), so both arms pay the exporter equally; absolute times carry that cost.
Oracle: `bench/rtxpro6000-2gpu/tools/oracle.py` + `compare.py` (rel tol 1e-6) over the same parquet, all numbers below were compared.

## Ask-once answers
1. Artifacts: everything on disk was SF1000 at older SHAs (`f05fd97b`, `c1842df3`) or 4-CN only. All arms were run fresh; the 2026-09-02 4-CN SF1000 rows (`/scratch/prestouser/aocsa/bench-results/sf1000-4gpu-q01-q06-POSTREBASE-20260902T024536Z`, c1842df3, decimal: q01 warm 7496 ms, q06 1737 ms) agree with the fresh 4-CN numbers (7088 / 1245 ms).
2. Scale and data: user direction during the run: SF10 and SF100, SF1000 as the stress tier, decimal datasets only. SF1 rows (f64 copies measured before that direction) are kept only as the fixed-overhead floor: standalone q01 122 ms / q06 96 ms with the harness SQL; StarRocks 1 CN q01 196 ms / q06 141 ms with the kit SQL.
3. Queries: q01 and q06; q03, q12, q14 added at SF100 as extra cases (see "Extra queries").
4. Implementation: default, plan first, then only evidenced small items (two landed, see "Implemented").

## 1. Baseline table (non-profiled, decimal data, `d24f02c4`)

Warm = median of 3 timed runs after one discarded first run; first-contact = run 0 on a warm cluster; cold-restart = run 0 after `RESTART_CMD` relaunched FE + CNs. Standalone has no cluster to restart.

| arm | SF | q01 warm ms | q01 first-contact ms | q01 cold-restart ms | q06 warm ms | q06 first-contact ms | q06 cold-restart ms | oracle q01 / q06 |
|---|---|---|---|---|---|---|---|---|
| standalone 1 GPU | 10 | 87 | 615 | - | 45 | 53 | - | MATCH / MATCH |
| StarRocks 1 CN | 10 | 355 | 470 | 1099 | 86 | 93 | 855 | VALUES-DIFFER 9.574e-04 / MATCH |
| StarRocks 2 CN | 10 | 261 | 1618 | 1714 | 123 | 139 | 980 | VALUES-DIFFER / MATCH |
| StarRocks 4 CN | 10 | 216 | 2333 | 971 | 118 | 135 | 1444 | VALUES-DIFFER / MATCH |
| standalone 1 GPU | 100 | 538 | 1083 | - | 198 | 219 | - | MATCH / MATCH |
| StarRocks 1 CN | 100 | 2814 | 2927 | 3585 | 292 | 303 | 1419 | VALUES-DIFFER / MATCH |
| StarRocks 2 CN | 100 | 1541 | 1968 | 2266 | 357 | 383 | 1369 | VALUES-DIFFER / MATCH |
| StarRocks 4 CN | 100 | 903 | 3839 | 2314 | 309 | 330 | 1596 | VALUES-DIFFER / MATCH |
| standalone 1 GPU | 1000 | 5271 | 7501 | - | 2302 | 2594 | - | MATCH / MATCH |
| StarRocks 1 CN | 1000 | 25750 | 27194 | 27231 | 1826 | 1958 | 3474 | VALUES-DIFFER / MATCH |
| StarRocks 2 CN | 1000 | 13962 | 13525 | 14423 | 1944 | 1903 | 4887 | VALUES-DIFFER / MATCH |
| StarRocks 4 CN | 1000 | 7088 | 7488 | 8223 | 1245 | 1332 | 3448 | VALUES-DIFFER / MATCH |

Commands: `bash scratchpad/perf/run-decimal-chain.sh` (log `scratchpad/perf/decimal-chain.log`; per-arm dirs `scratchpad/step3/dec-sf<SF>-<N>cn/{summary.txt,cluster.log,warm,cold}`, standalone `scratchpad/perf/standalone/dec-sf<SF>/`).

Correctness. q01 on the StarRocks path fails the oracle on every arm and SF (8 bad cells: `sum_disc_price` and `sum_charge` in all four groups, rel diff 9.57e-04). Cause: only the `l_discount = 0.07` group is wrong; the FE plan casts the FP64-lowered `1 - l_discount` back to `DECIMAL128(16,2)` (`30 <-> cast([29: subtract, DECIMAL64(16,2)] as DECIMAL128(16,2))`) and the engine cast truncates `0.9299999999999999` to `0.92`. Standalone Sirius sums the decimals natively and matches. This is a correctness defect of the CN translation path (`#24` decimal->FP64 lowering in `expr_translator.rs` plus the engine's float->decimal cast rounding), reported here as correctness; it does not change the work done, so the q01 timings stand as performance measurements.

### 1-GPU parity, `T_sr1 / T_sirius` (warm)

| SF | q01 | q06 |
|---|---|---|
| 10 | 355/87 = 4.08 | 86/45 = 1.91 |
| 100 | 2814/538 = 5.23 | 292/198 = 1.47 |
| 1000 | 25750/5271 = 4.89 | 1826/2302 = 0.79 |

q06 at SF1000 is faster on the CN than standalone: both run the same engine and the same 65-task scan (97.50 GB); the standalone process read from GPU 0 while the CN's Quent shows scan tasks at 103.9 ms vs 123–158 ms standalone, i.e. page-cache/I/O variance on a scan-bound query, not a CN advantage. The q01 ratio is a real per-row cost (see §2).

### Scale-out, `T_1 / T_N` and efficiency `T_1 / (N * T_N)` (warm)

| SF | q01 T1/T2 (eff) | q01 T1/T4 (eff) | q06 T1/T2 (eff) | q06 T1/T4 (eff) |
|---|---|---|---|---|
| 10 | 1.36 (0.68) | 1.64 (0.41) | 0.70 (0.35) | 0.73 (0.18) |
| 100 | 1.83 (0.91) | 3.12 (0.78) | 0.82 (0.41) | 0.94 (0.24) |
| 1000 | 1.84 (0.92) | 3.63 (0.91) | 0.94 (0.47) | 1.47 (0.37) |

q01 (hash shuffle) scales near-linearly at SF1000 because each CN's partial aggregate is the cost. q06 (gather) does not scale at any SF: §2 shows why.

## 2. Profiling analysis (Quent telemetry, non-nsys)

Source: per-CN Quent sessions `sot/experimental/starrocks/.cn<i>/telemetry/<session>/` and standalone sessions `scratchpad/perf/telemetry-standalone/<session>/`, parsed with `scratchpad/perf/quent_summary.py` (task FSM: `Created -> Queued -> Routing -> Queued -> Reserving -> Preparing -> Computing{instance_name, input_bytes} ... -> Finalizing -> Exit`; query FSM `Init -> Planning -> Executing -> Exit`). Profiled numbers here are for *why*; the table in §1 is the performance number. Quent's own exporter cost is inside both arms.

### 2.1 q01 1-GPU gap: the aggregate, not the scan or the exchange

SF1000, partial fragment on 1 CN (session `.cn0/telemetry/01a065d1-453c-7780-9dfb-e283d5238d75`, query wall 27.000 s) vs standalone (session `01a065c7-9451-7f32-a29b-f8a385caa68a`, wall 5.258 s), 107 scan tasks each, 4 executor threads:

| operator (pipeline 0: GPU_SCAN -> PROJECTION -> PROJECTION -> HASH_GROUP_BY) | CN 1-CN sum / avg per task / input | standalone sum / avg / input |
|---|---|---|
| `HASH_GROUP_BY(3)` | 55.996 s / 523.3 ms / 443.67 GB | 5.807 s / 54.3 ms / 300.96 GB |
| `GPU_SCAN(0)` | 46.197 s / 431.7 ms / 256.50 GB | 13.969 s / 130.6 ms / 256.50 GB |
| `PROJECTION(2)` | 3.686 s / 34.4 ms / 300.22 GB | 0.460 s / 4.3 ms / 300.96 GB |
| `PROJECTION(1)` | 1.354 s / 12.7 ms / 252.89 GB | 0.528 s / 4.9 ms / 252.89 GB |

Sum of operator time / 4 threads = 26.8 s (CN) and 5.2 s (standalone), matching the walls. The merge fragment, the exchange hop and the FE add 18 ms (query `a8aed1689635` wall 0.018 s; `transmitted batches via nixl ... batches=1 bytes=64`).

The 10x per-task aggregate cost is `src/op/aggregate/gpu_aggregate_impl.cpp:158-169`: when any aggregate `is_order_sensitive_sum` (`aggregate_op_util.cpp:225-229`: `SUM` over FLOAT32/FLOAT64) the batch is gathered into canonical row order by `canonicalize_row_order` (a full sort of the ~2.4 GB batch on keys plus every float value column) and cuDF runs the sort-based groupby. The CN path lowers decimal sums to FP64 (`#1236` and the two-phase `partial_state` wire model), so every q01 batch takes that path; standalone sums `DECIMAL` natively and never does. This code is SOT commit `441f05b2` (plan item A, "deterministic sums"), not on `dev`. The plan's own note said to gate it behind a flag if material; it is 10x.

The scan slowdown (431.7 vs 130.6 ms per task, same bytes) is GPU contention with the sort-heavy aggregate on the other executor threads: on q06, which has no such aggregate, the CN's scan tasks are 103.9 ms (65 tasks, 97.50 GB) against 123–158 ms standalone.

Second factor: the partial aggregate's input is 443.67 GB vs 300.96 GB (+142.7 GB). The three `avg` expansions carry both `sum(cast(x as double))` and `count(x)` on separate columns and the FP64 casts widen the row. `experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs` (`expand_avg`, `build_measure`). Not fixed here.

### 2.2 q06 scale-out ceiling: dispatch serialization on the merge-hosting CN

4-CN SF1000, Quent query `Init` timestamps aligned across CNs (ms relative to the first fragment of the run): cn1/cn2/cn3 scan fragments start at 28919.7 (durations 495–704 ms); cn0's scan starts at 29646.0 (418.9 ms), i.e. 726 ms later, and cn0 also hosts the merge fragment (start 30065.1, 1.7 ms). Next run: cn1/cn3/cn0 at 30250, cn2 (merge host) at 30845.7. 2-CN: cn0 scan 55412.6 (830 ms), cn1 scan 56264.3 (887 ms), merge on cn1 at 57151.7. The CN log confirms the order for one run: three `translated StarRocks plan fragment` lines at 05:59:52.4995, the three `transmitted batches via nixl ... dest=127.0.0.1:9102 batches=1 bytes=64` lines at 53.351/53.831/53.861, the fourth scan translated at 05:59:53.8636, the merge at 54.881.

Mechanism: `experimental/starrocks/src/brpc.rs handle_connection` awaits each request inline (read frame, `call_service`, write, next frame), and `compute_node_service.rs exec_plan_fragment -> exec_single_attachment -> process_fragment` executes a sender fragment (translate, run, remote drains) inside the RPC and only then replies. StarRocks deploys the first fragment instance of every node in one wave and waits for those replies before the next wave; the merge host's first instance is the merge fragment, so its scan is in wave 2 and starts only when the other nodes' scans (wave 1, held open for the whole scan) reply. `T_q06(N) ≈ T_scan(remote) + T_scan(local) + merge`: 4 CN 1245 ms = 0.50–0.70 s + 0.42 s + FE; 2 CN 1944 ms = 0.83 + 0.89 s + FE. q01 has a merge instance on every CN, so its scans are all in wave 2 together and it shows no stagger (merge fragments start within 5 ms on all four CNs).

The scan work itself scales: q06 per-CN scan-fragment spans are 1.755 s (1 CN) -> 0.802/0.819 s (2 CN) -> 0.395–0.667 s (4 CN); byte-range splits are balanced (4 CN q01: 27 tasks and 63.76–64.62 GB per CN).

### 2.3 Fixed overhead (the SF10 floor)

SF10 q06: standalone 45 ms, 1 CN 86 ms (+41 ms), 4 CN 118 ms (+73 ms). The +41 ms of one CN is FE planning and dispatch, two fragments (scan, merge) each built through DuckDB planning on the engine thread, one `relay_from` (pointer handoff, `sirius_ffi.cpp:747-757`), the result collector's D2H and the four host re-encodes (agent 4, §1.1). The N-CN increment is the wave-2 serialization above (at SF10 a scan is ~30 ms) plus the nixl hop RTTs. Quent cannot yet attribute this at fragment level (no FE-side events, no fragment ids on CN events); see the Simulation section.

### 2.4 Cold starts
First-contact and cold-restart columns: a cold cluster pays engine bring-up and the first FILES() planning (q01 cold-restart 1.1 s at SF10, 27.2 s at SF1000 vs 25.75 s warm); the nixl session warmup (`nixl session warmup complete: every peer session is established`) keeps first contact within the canary's reach (85.8–432.2 GB/s across the 12 peer pairs). No `errorCode=62`, no `fail_stalled_query`, no "needs the nixl transport tier", zero `ERROR` lines in any run.

## 3. Ranked bottlenecks

| # | affects | evidence | file | class |
|---|---|---|---|---|
| 1 | 1-GPU (q01 4.1–5.2x) and scale-out (every CN pays it) | Quent `HASH_GROUP_BY(3)` 523.3 vs 54.3 ms per task; scan 431.7 vs 130.6 ms from contention | `src/op/aggregate/gpu_aggregate_impl.cpp:158-169`, `src/op/aggregate/aggregate_op_util.cpp:225` (`canonicalize_row_order`, `is_order_sensitive_sum`) | GPU compute |
| 2 | scale-out (q06 eff 0.18–0.37; any gather/broadcast plan) | fragment start stagger equal to one remote scan; CN log order translate x3 -> transmit x3 -> translate 4th | `experimental/starrocks/src/compute_node_service.rs` (`exec_single_attachment`, `translate_batch_attachment`, `process_fragment`), `brpc.rs handle_connection` | dispatch |
| 3 | 1-GPU (q01) | partial-aggregate input 443.67 vs 300.96 GB | `starrocks-plan-translator/src/node_translator.rs` (`expand_avg`, `build_measure`), `expr_translator.rs` decimal->FP64 casts | GPU memory bandwidth |
| 4 | 1-GPU floor and N-CN increment (SF10: +41 ms, +73 ms) | §2.3 | `compute_node_service.rs` fragment build per fragment, `sirius_ffi.cpp Fragment::build` (DuckDB planning), result path; FE planning outside our tree | CPU / latency |
| 5 | correctness (q01 on every CN arm) | 8 bad cells, rel 9.57e-04, only the 0.07 group | `starrocks-plan-translator/src/expr_translator.rs` (`translate_arithmetic` decimal lowering) plus the engine float->decimal cast | correctness |
| 6 | cold start | cold-restart 0.9–3.5 s above warm at SF10–SF100 | engine bring-up, first FILES() plan; already tracked as M1.5 (warmup landed) | latency |

Mapped to `notes/OPEN.md`: #2 is the PLAN-04 family (one fragment holds the CN's serial path) at the RPC layer rather than the engine thread; #6 overlaps M1.5. Not re-opened: nixl bandwidth (canary 85.8–432.2 GB/s, q06 payload 64 bytes per CN), pool/arena sizing, byte-range splits (balanced), two-phase agg shape (EXPLAIN as required, no `new_planner_agg_stage`).

## 4. Ordered work

Quick 1-GPU cuts first, then scale-out.

1. **Gate the canonical float-sum sort** (bottleneck 1). `SIRIUS_CANONICAL_FLOAT_SUMS` opt-in, default off, in `aggregate_op_util.{hpp,cpp}` + `gpu_aggregate_impl.cpp`; merge-side canonicalization stays on. Expected: q01 1-GPU ratio from ~5x to ~1.2x; every CN arm of q01 faster by the same factor; oracle unchanged within 1e-6 (FP64 sums differ at 1e-14). Landed, see §5. Upstream form: a YAML key under `operator_params` next to the SOT's env knobs; a follow-up should decide whether the deterministic path is needed at all for the q15 `sum = max(sum)` case it was written for (a tolerance in the comparison, or canonicalizing only the tiny merge input, may be enough).
2. **Async sender dispatch** (bottleneck 2). Sender-only fragments (no exchange input, no RESULT_SINK) are queued on the dispatch worker and `exec_plan_fragment` returns at once. Opt-in: `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` at CN bring-up (an `AtomicBool` on `ServiceCore`, read once); default off because eight existing tests pin the inline contract (validation errors and remote-drain failures reported through the sender's RPC), and a queued sender's failure only reaches result instances reserved on its own node. Expected: q06 `T(N) ≈ max(scan_i) + merge`, i.e. 4-CN SF1000 from 1245 ms toward ~0.7 s and 2-CN from 1944 toward ~0.9 s; q01 unchanged. Landed as code and test (`sender_only_fragment_rpc_returns_before_the_sender_executes`) in the perf worktree; CN crate 147 tests pass, clippy clean; re-time in §5.3. To flip the default: forward a queued sender's failure to the receiver's node (a failure frame on the exchange, or `report_exec_status` to the FE), then relax the three drain-order tests.
3. **Per-connection request concurrency in `brpc.rs`** (bottleneck 2, general form). `handle_connection` should spawn each request and serialize only the response writer, so a long `fetch_data` poll or any slow handler on the FE's single multiplexed connection cannot head-of-line block `exec_plan_fragment`/`cancel_plan_fragment`. Verify with the q06 stagger measurement and `sender_rpc_returns_before_the_dispatched_receiver_executes`-style tests.
4. **Narrow the partial aggregate's input** (bottleneck 3). In `expand_avg`, compute `count` over the same cast column (or over a 1-byte validity column) instead of carrying the original decimal column beside the FP64 cast; consider emitting `sum`/`count` of avg from one projected column. Expected: ~30% less bytes into `HASH_GROUP_BY`, a few hundred ms at SF1000 per CN. Verify with Quent `Computing.input_bytes` on `HASH_GROUP_BY(3)` and the oracle.
5. **Cardinality declaration on the CN** (scale-out for joins, not q01/q06). `declare_input_cardinality` from `PTransmitPackedParams.rows` (F5/C6b already wire it; confirm "declared input stream cardinality" appears for every remote stream and that DuckDB's build side follows it on q03/q14). Verify with `EXPLAIN` of the receiver fragment and the q03 arms.
6. **Fixed overhead** (bottleneck 4). Measure with Quent once fragment ids and FE-side timestamps exist (Simulation section); candidates: cache the DuckDB plan per fragment shape across iterations, avoid re-inferring the FILES() schema per query, skip the `PARTITION`/`MERGE_GROUP_BY` pipelines when a fragment has a single output partition.
7. **Correctness** (bottleneck 5, blocking any decimal q01 claim): round instead of truncate in the FP64->DECIMAL cast on the CN path, or lower `1 - l_discount` in decimal arithmetic; test with the 0.07 group.

Verification for every item: non-profiled re-time of the same arms (`run-perf-retime.sh` shape: standalone, 1/2/4 CN, SF100 and SF1000), oracle compare still 0 mismatches for q06 and unchanged bad-cell set for q01 until item 7 lands, and the Quent per-operator table from `quent_summary.py` to show the operator that was supposed to move moved.

## Out of scope
Making StarRocks emit a single pipeline plan; blaming nixl (canary healthy, 64-byte partials); retuning pool/arena as a substitute; the I/O and memcpy throttle schedulers (designs only, 03/04); Quent instrumentation (separate session, `quent-instrument-report.md`); nsys on the CN (Quent was sufficient for the two top items); GB200 config churn; engine B (stock StarRocks) comparison; SF1 as a headline number; the full 22-query sweep.

## 5. Implemented and re-timed
(appended below when the re-time finishes)

## Simulation
Artifacts under `~/.claude/plans/starrocks-sirius-perf/`:
- `01-trace-schema.md` (agent 1, Quent extract table and gap list; verified by a read-only refuter). Key facts: 19 record types per session; the extract table covers query label (`query.Init.instance_name`, constant `sirius_streaming_fragment` on the CN and `unnamed_query` standalone), fragment start/end (`Executing` -> `Exit`), pipeline identity (`operator.Declaration.instance_name`), per-operator task time (`task.Computing`), executor thread / manager thread / task queue, edges and ports, batch bytes (`data_batch.Stationary.capacity_bytes`), batch placement. Missing for the sim: fragment identity (nowhere), scan bytes actually read and byte ranges (nowhere; ranges only via `SIRIUS_CN_DUMP_FRAGMENTS`), park (nowhere), relay (CN log count only), nixl bytes/duration (CN log bytes only; duration measured and dropped at `nixl_transport.rs:713`), H2D/D2H/D2D (model exists, no data), inflight I/O (nowhere), inflight packed exports (CN log peak at teardown), CN id (indirect), rows per batch (nowhere).
- `quent-gaps.md` (inventory for the instrumentation session): every emit site, the `sirius_set_query_label` mechanism, and the verdict that no Quent model change is needed.
- `03-io-throttle.md` (agent 3): unit = the data split weighted by compressed bytes; gate = non-blocking `try_acquire` in `get_next_task_input_data()` / `get_next_task_hint()` (`sirius_gpu_scan_operator.cpp:383-426`), released after `materialize_metadata_to_table`; knobs `scan_manager.scan_io_inflight_splits|scan_io_inflight_bytes|scan_io_rate_bytes_per_sec` (0 = off) via `SIRIUS_CN_SCAN_IO_*` -> derived YAML. Design only.
- `04-memcpy-throttle.md` (agent 4): the 1-GPU path has no inter-fragment device copy (`relay_from` is a `shared_ptr` handoff); the multi-CN path is chunked_pack into a lease, nixl WRITE, unpack + copy-out; unit = one `StagedBatch` to one destination; `CopyBudget` in `ServiceCore` admitted before `export_packed_next`, released after `staging_release`; knobs `SIRIUS_CN_MEMCPY_INFLIGHT` (default 1), `SIRIUS_CN_MEMCPY_RATE_BYTES_PER_SEC`, `SIRIUS_CN_MEMCPY_BURST_BYTES`, `SIRIUS_CN_MEMCPY_MAX_WAIT_MS`. Design only.
- `06-dataflow-sim.md` (agent 6) and `07-local-experiments.md` (agent 7): pending at the time of writing; their status is appended below when they land.
- `08-sim-vs-run.md` (agent 8): gated on a runnable replay plus an implemented throttle or a recorded trace; status appended below.

### 5.1 Canonical float-sum sort gated off (`SIRIUS_CANONICAL_FLOAT_SUMS`, default off)

Code (perf worktree, uncommitted at the time of writing): `src/include/op/aggregate/aggregate_op_util.hpp` (+`canonical_float_sums_enabled()`), `src/op/aggregate/aggregate_op_util.cpp` (env read, cached), `src/op/aggregate/gpu_aggregate_impl.cpp:164` (`use_canonical_sorted_groupby = canonical_float_sums_enabled() && !float_sum_value_cols.empty()`), `docs/super-sirius/configuration.md` (new `SIRIUS_CANONICAL_FLOAT_SUMS` subsection). Merge-side canonicalization (`gpu_merge_impl.cpp`) untouched. Build: full `make release` in the perf worktree (1418 steps; one retry after a corrupt `libcxx_build` rlib on the network filesystem), CN `cargo build --release` 42 s.

Re-time (`bash scratchpad/perf/run-perf-retime.sh`, log `scratchpad/perf/retime-1.log`; same datasets, configs and harness as §1; warm medians of 3, non-profiled):

| arm | SF | q01 before → after | q06 before → after | oracle |
|---|---|---|---|---|
| StarRocks 1 CN | 100 | 2814 → 644 ms | 292 → 274 ms | q01 unchanged 8 bad cells (9.572e-04), q06 MATCH |
| StarRocks 2 CN | 100 | 1541 → 446 ms | 357 → 306 ms | same |
| StarRocks 4 CN | 100 | 903 → 332 ms | 309 → 275 ms | same |
| StarRocks 1 CN | 1000 | 25750 → 4954 ms | 1826 → 1877 ms | same |
| StarRocks 2 CN | 1000 | 13962 → 2878 ms | 1944 → 1863 ms | same |
| StarRocks 4 CN | 1000 | 7088 → 2008 ms | 1245 → 1243 ms | same |

Control: the same binary with `SIRIUS_CANONICAL_FLOAT_SUMS=1` on 1 CN at SF100 gives q01 2721 ms (baseline 2814) and q06 276 ms, so the switch reproduces the baseline and the gate is the cause.

Effect on the two comparisons (standalone unchanged: it never took the path): q01 `T_sr1/T_sirius` SF100 5.23 → 1.20 (644/538), SF1000 4.89 → 0.94 (4954/5271). q01 scale-out at SF1000 `T1/T4` 3.63 (eff 0.91) → 2.47 (eff 0.62): with the aggregate no longer dominant, the 4-CN q01 (2008 ms) is now bounded by the scan plus the same dispatch serialization and fixed costs that q06 shows. q06 is unaffected (no float SUM in the partial).

Cold-restart after the gate: q01 SF1000 1 CN 7160 ms (was 27231), 4 CN 3820 ms (was 8223).

### 5.2 Extra queries at SF100 (patched engine; no code path differs for them except the gate)

| query | standalone 1 GPU warm | StarRocks 1 CN warm | StarRocks 4 CN warm | oracle |
|---|---|---|---|---|
| q03 | 286 ms | 402 ms | 453 ms | standalone MATCH (10 rows); StarRocks VALUES-DIFFER 3.583e+00, 21 bad cells: `revenue` is `sum(l_extendedprice * (1 - l_discount))`, the same truncation shifts the sums (491336.9217 vs oracle 492136.8537 on the first row) and re-orders the top 10 (row 2 is orderkey 507274210 vs 165214338), so q03 is a correctness failure on the CN path until §4 item 7 lands |
| q12 | 244 ms | 338 ms | 331 ms | MATCH (both arms) |
| q14 | 248 ms | 331 ms | 283 ms | standalone MATCH; StarRocks VALUES-DIFFER 5.500e-06, 1 cell (the same `1 - l_discount` truncation as q01, damped by the ratio) |

At SF100 these three sit on the ~250–450 ms fixed floor (§2.3) on the StarRocks side and do not scale to 4 CNs; they are the right shapes for the dispatch and fixed-overhead items (§4 items 2, 3, 6) rather than for the aggregate item.

### Simulation status (agent 6, 06:39 UTC)
`06-dataflow-sim.md`: sketch plus a coded replay. Code: `scratchpad/sim/extract.py` (Quent sessions -> per-task events), `scratchpad/sim/replay.py` (stdlib list scheduler over 4 executor threads per CN, FE two-wave deployment rule, hop constants calibrated from the same generation: `build_ms=20`, `fe_overhead_ms=175`, `nixl_gbps=350`), output `scratchpad/sim/replay-output.txt`. Fit on the SF1000 4-CN warm generation: per-run wall error ≤ 1.5 % (q01 r0 predicted 7504 vs FE 7488 ms; q06 r0 1347 vs 1332), per-fragment compute end within 4.4 ms on 32 fragments with no free parameter, straggler CN predicted correctly (cn3 for q01 r0, cn0 for q06 r0). Out of sample, from the 1-CN trace only: q01 N=2/4 predicted 14178/7635 ms vs measured 13962/7088 (within 5 %); q06 N=2/4 predicted 2111/1267 vs measured 1944/1245 (within 7 %). Counterfactual for the async sender dispatch (item 2): q06 4-CN 907/777/869/813 ms for the four runs (measured 1332/1219/1331/1245), q06 N=2 1212 ms, N=8 498 ms; q01 unchanged. Calibration caveat stated in the file: `fe_overhead_ms` and hop constants are residuals of the same generation, so only the scheduling structure and the N-CN extrapolation are genuine predictions.

### 5.3 Async sender dispatch (`SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`, opt-in)

Code (perf worktree, commit `9a5a4da6` on `perf/float-sum-canonicalize-flag`; the aggregate gate is `85658f09`): `experimental/starrocks/src/compute_node_service.rs` — `ServiceCore.async_sender_dispatch: AtomicBool` read from the env at bring-up, `try_dispatch_sender()` called first in `exec_single_attachment` and per instance in `translate_batch_attachment`; a fragment with no exchange input and no RESULT_SINK is resolved (descriptor cache) and queued as a `ReadyFragment` with no inputs, the RPC returns; `run_ready_fragment -> execute_ready_fragment` runs it on the dispatch worker with its drains. Test `sender_only_fragment_rpc_returns_before_the_sender_executes` (gated sender executor). CN crate: fmt clean, clippy `-D warnings` clean, 147 tests pass (the eight tests that pin the inline contract pass because the default is off). Release CN built (`cargo build --release` 14 s incremental).

Re-time (`bash scratchpad/perf/run-perf-retime2.sh`, log `scratchpad/perf/retime-2.log`, per-arm `scratchpad/step3/async-*/summary.txt`), same harness and configs as §5.1, all with the aggregate gate off; "off" = the same binary without the env var (`asyncoff-sf1000-4cn` control, else §5.1's numbers):

| arm | SF | q01 off → on (warm ms) | q06 off → on (warm ms) | oracle |
|---|---|---|---|---|
| StarRocks 2 CN | 1000 | 2878 → 3119 (runs 3119/3568/3070) | 1863 → 1419 (1397/1419/1444) | q01 same 8 cells; q06 MATCH |
| StarRocks 4 CN | 1000 | 1715 (control 2362/1647/1715) → 2156 (2156/2913/1681) | 1234 (control) → 904 (893/1040/904) | same |
| StarRocks 1 CN | 1000 | 4954 → 4993 | 1877 → 1790 | same |
| StarRocks 2 CN | 100 | 446 → 454 | 306 → 233 | same |
| StarRocks 4 CN | 100 | 332 → 353 | 275 → 215 | same |
| StarRocks 4 CN, extras | 100 | q03 453 → 340, q12 331 → 293, q14 283 → 279 | | q03/q14 VALUES-DIFFER as §5.2, q12 MATCH |

q06 gains 22–27 % wherever there is a remote sender (2 and 4 CNs, both SFs) and q03 25 % at 4 CNs; agent 6's replay predicted 813–907 ms for q06 at 4 CNs (measured 904) and 1212 ms at 2 CNs (measured 1419). Scale-out after both changes, SF1000 q06: `T1/T4` = 1790/904 = 1.98 (efficiency 0.50, from 0.37); the remaining gap to the 0.40–0.70 s scan fragments is the FE and merge path (§2.3, replay `fe_overhead_ms=175`). q01 shows no gain and a run-to-run spread of 1647–2913 ms at 4 CNs that hides any ±10 % effect; a 5-run A/B at 4 CNs (`scratchpad/perf/ab-4cn.sh`) is appended below when it completes. Cold-restart and first-contact numbers moved with the noise of a fresh cluster (q06 4 CN first-contact 873 ms on, 1247 off).

Combined result versus the §1 baseline (warm, non-profiled): q01 1 CN / standalone at SF1000 4.89 → 0.94; q01 4 CN at SF1000 7088 → ~1.7–2.2 s; q06 4 CN at SF1000 1245 → 904 ms; q06 4 CN at SF100 309 → 215 ms; q03 4 CN at SF100 453 → 340 ms.

### Simulation status (agent 7, 06:54 UTC)
`07-local-experiments.md`: eight experiments with a shared protocol (one arm at a time, outside 02:00–03:50 UTC, non-profiled warm medians, oracle on every quoted number, Quent session per arm). E1 SF sweep at 4 CN to turn the FE floor into a function of SF; E2 the async-sender-dispatch A/B (the sim already prints `pred_async_sender`); E3 executor-thread sweep 2/4/8 at SF1000 4 CN with a Quent-tax control; E4 one file vs many files at fixed SF (FE split-assignment rule); E5 standalone vs 1 CN on the same GPU as a service-time input; E6 the bandwidth-shaped q14 exchange at SF100/SF1000 for 1/2/4 CN; E7 (I/O inflight cap) and E8 (memcpy rate cap) are blocked on the unimplemented throttles. Default pass rule: a-priori FE wall within 20 % of the warm median and the same straggler CN. The file lists the small `replay.py` changes each runnable experiment needs (`fe_overhead(sf)`, `--threads`, the FE split rule, standalone grouping, per-peer `drain_ms(bytes)`).

### Agent 8
Launched 06:57 UTC after the gate held (runnable replay + two implemented switches + recorded traces); it reuses the measured arms above and may add at most six cluster launches. Its file is `08-sim-vs-run.md`; its verdict is appended here when it lands.

A/B, 4 CN, SF1000, same binary, 5 timed warm runs per arm, arms interleaved off/on/off/on (`scratchpad/perf/ab-4cn.sh`, log `ab-4cn.log`, 07:01–07:03 UTC, no refusal or wedge):

| mode | q01 warm runs (ms) | q01 median | q06 warm runs (ms) | q06 median |
|---|---|---|---|---|
| off | 1862, 1811, 2008, 1717, 1721 | 1811 | 1269, 1224, 1237, 1200, 1203 | 1224 |
| on | 2224, 1728, 1657, 1616, 1638 | 1657 | 858, 849, 810, 855, 839 | 849 |
| off | 2906, 1669, 1664, 1770, 1633 | 1669 | 1280, 1243, 1272, 1213, 1271 | 1271 |
| on | 2332, 1712, 1741, 1673, 1700 | 1712 | 856, 819, 818, 828, 851 | 828 |

Pooled (n=10 each): q06 1240 → 844 ms (−32 %), every "on" run below every "off" run; q01 1745 → 1706 ms, ranges overlap (1633–2906 vs 1616–2332), no effect. Final scale-out for q06 at SF1000 after both changes: `T1/T4` = 1790/844 = 2.12, efficiency 0.53 (baseline 1.47 / 0.37).

### Agent 8 result (07:39 UTC), `08-sim-vs-run.md`
Gate held (runnable replay, two implemented switches, recorded traces). Six cluster launches on the perf build (`scratchpad/step3/agent8-*/`), oracle verdicts unchanged everywhere (q01 known 8-cell decimal fail, q06 MATCH). Verdicts against 07's pass rule (a-priori within 20 % and the same straggler; post-hoc within 2 %):
- E2 async sender dispatch: **working**. q06 SF1000 4 CN warm medians 828–904 ms over five arms against a-priori 770–841 ms (same-build counterfactual) and 741 ms (1-CN projection); post-hoc −0.9 … +0.4 % on the 15-run capture; scan `Init` spread across CNs 0.4–3.4 ms (was 587–962 ms); sender RPC `time.idle` 1.2–1.6 s → 0.7 ms; q01 unchanged within 5.5 %. 2 CN: 1347 ms measured (14 runs) vs 1212 a-priori (−10 %). SF100: 211 predicted vs 212 measured. SF10: a-priori 75–85 ms misses the measured 99 ms, though the relative gain is close (−26 % predicted, −17.5 % measured).
- E1 FE floor: **working** with `G = 90.3 + 1.75·files` (fit on SF10 and SF1000, predicts SF100 within −5/−16 ms); the shipped constant `fe_overhead_ms=175` misses SF10 by +39/+73 %.
- E5 standalone vs 1 CN: **working post-hoc** (standalone replay within 0.3 % of its Quent window; the standalone−1CN gap explained within 20–23 ms; process factor GPU_SCAN ×1.19–1.52, HASH_GROUP_BY ×1.23).
- E4 one file vs many: task count exact; the a-priori Δ fails (per-file slope 0.7–1.1 ms/file on f64 SF10, not 1.75), post-hoc within 4/14 ms.
- E3 not run (no executor-thread knob without the full-YAML launcher), E6 measured only, E7/E8 blocked (no throttles).
Smallest sim fixes named: `fe_overhead = 70 + 1.75·files + 30·(levels−2)`; a ×1.2–1.35 GPU_SCAN service inflation when N CNs read concurrently (a shared disk-server resource, 03's I/O model, is the proper form); cold first-contact `G` of 730–1044 ms unmodelled; the replay cannot yet group q14/q03 shapes.
Operational finding for the instrumentation session: Quent's per-type buffers flush independently and the final flush is lost when a CN is stopped with SIGTERM (`main.rs:673-681`), so warm sessions keep only a 14–15-fragment prefix and non-merge-host CNs lose their `operator` declarations; agent 8's `scratchpad/sim8/repair.py` rebuilds pipelines from tasks. Wrapper `scratchpad/sim8/sim8.py` reproduces 06's numbers.

## SF1000 profiling: query planning, cardinality estimation, backpressure (2026-09-03 13:00-15:00 UTC)

Full report: `starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md` (389 lines; setup, results, findings by dimension with evidence lines and file-level proposals, ordered to-do list, open questions). Evidence bundle and tools in the session scratchpad `perf/sf1000/` (indexed by `WORKFLOW-PLAN.md` there). Build: `perf/profile-sf1000` = this branch + the Quent probe commit (45dab3be). Data: decimal SF1000 only, as asked.

What ran: translate-only survey of all 22 queries (not a support oracle: receivers only bind in real dispatch), then all 22 on 1 CN, the 15 that passed on 4 CNs and standalone, the 6 CN failures on standalone, a q15 determinism A/B, DuckDB oracle for everything that produced rows. Analysis by a 33-agent workflow (7 family analysts, 24 adversarial verifiers, synthesizer, critic): 9 findings confirmed by both skeptics, 3 disputed in wording, 31 lower-ranked unverified.

Headline results:
- Supported at SF1000 on the CN path: q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22. Six (q05 q08 q09 q17 q18 q21) die on 1 CN after 54-232 s in the engine's 100-retry OOM loop; standalone Sirius runs all six in 2.7-6.5 s with exact answers. q16 is a translator gap (`multi_distinct_count`).
- Cardinality: the FE hard-codes 1 row for every FILES() scan (`StatisticsCalculator.computeFileScanNode`), so every distribution decision is data-blind and flips with the alive-node count (1 CN: broadcast almost everything, incl. 3.79B lineitem rows as the build side of q04's LEFT SEMI JOIN; 4 CNs: shuffle the big side, q03 moves 3.23B lineitem rows). The receiver-first exact cardinality declaration works inside a fragment but cannot undo the FE's fragment cut. Engine reservations over-estimate 2-54x (cold-start scan 8x, join fragments 4-6x).
- Backpressure: nothing streams across fragments; each exchange is a full GPU materialisation (sender runs to completion, parks, receiver starts), parked repositories are outside the downgrade sweep so they cannot spill, the reschedule loop has no feasibility check, and after a failure the dead query's remaining senders still run and park output nobody consumes. Shuffle drains are serialised per destination on one dispatch worker; per-task GPU_SCAN time rises 1.3-1.6x when four CNs scan concurrently, capping 4-CN speed-up at 1.7-2.35x. Staging leases: q07 at 4 CNs holds up to 64 concurrent leases per CN (arena 16 GiB), no lease waits measured.
- Correctness: q15 returns 0 rows in most CN runs because the FE inlines the revenue CTE twice and the join compares two independently computed FP64 sums; the canonical float-sum gate does not fix it (A/B: 4 of 6 empty with it on, 5 of 6 off). q10's TOP-20 order changes for the same `1 - 0.07` truncation family as q01/q03/q07/q19.
- 1-CN parity: 0.95-1.13x of standalone for the scan-heavy queries, 1.2-1.4x for q10/q13/q20, 1.8-2.9x for the fragment-heavy small ones (q02 q11 q15 q22) where a ~200 ms fixed FE head (8 serial FILES() schema RPCs per query) dominates.

The report's "What to do first" table lists 12 changes with file paths and confidence; the top four are the OOM fail-fast, per-query parked-output bookkeeping, real FILES() cardinalities in the FE, and making the six failures runnable on 1 CN (fragment fusion or spillable parked repositories). Nothing was implemented in this pass.
