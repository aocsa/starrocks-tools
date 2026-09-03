# 03 — Throttle I/O by scheduling it (design only)

Checkout read: `/home/prestouser/aocsa/sirius-stacks-wt/sot` @ `d24f02c4` (`aocsa/feat/pin-table-cn`).
All paths below are relative to that tree unless absolute. Nothing was modified or run.

Goal: make "this scan waits, that scan runs" an explicit, countable decision (inflight cap or
rate) taken before I/O is issued and released when the decoded batch is handed off, so a
data-flow simulation can model it and scan overlap on a CN becomes a knob, not an accident.

## 0. How scan concurrency is decided today (per CN process)

A CN is one process, one GPU, one Sirius engine that runs one fragment at a time
(`experimental/starrocks/src/engine.rs:239-281`, "One request at a time"; receiver fragments go
through a single `fragment-dispatch` thread, `compute_node_service.rs:241-249`). Inside a
fragment the scan path has four stages, each with its own implicit concurrency:

| Stage | Where | What bounds it today |
|---|---|---|
| Metadata (footer read + row-group prune) | `split_provider::run` enqueues one task per file (`src/scan_manager/split_provider.cpp:50-61`), files claimed by `_next_file_idx.fetch_add` (`src/op/scan/parquet_gpu_ingestible.cpp:652`), footer read at `:707` | `scoped_dispatcher` in-flight = scan_manager pool size (`src/include/exec/scoped_dispatcher.hpp:39-43`; pool built at `src/scan_manager/sirius_scan_manager.cpp:1268-1272`), default `hardware_concurrency - 7`, min 4 (`src/include/scan_manager/config.hpp:37-43`) |
| Coalesce into data splits | `parquet_batch_coalescer::push` (`parquet_gpu_ingestible.cpp:254-329`), cap = `approximate_batch_size` = `scan_task_batch_size` (`:636-637`; `src/planner/sirius_physical_plan_generator.cpp:206,243`; default `derived_default_batch_size()`, `src/include/sirius_config.hpp:116`) | One sequencer per scan op (`src/scan_manager/load_balancing_scan_batch_coalescer.cpp:75-86`); `emit` fires fadvise + opportunistic prefetch then `push_split` (`:96-111`). Connector is an unbounded deque (`src/scan_manager/split_connector.cpp:31-46`, `src/include/scan_manager/split_connector.hpp:130`): every split of the query is discovered and queued as fast as metadata runs |
| Task creation | `task_creator::manager_loop` on a pool of default 1 thread (`src/include/creator/config.hpp:91`; `src/creator/task_creator.cpp:357`), loop `while (!node->all_ports_empty())` (`:430`) pops one split per task via `sirius_gpu_scan_operator::get_next_task_input_data` (`src/op/scan/sirius_gpu_scan_operator.cpp:411-426`, blocking pop at `split_connector.cpp:61-77`, then `prefetch(immediate)` at `:424`), schedules it (`:604`); lookahead creates one and breaks (`:606`) | Demand-driven: `task_scheduler` hands one task per `device_ready` (`src/pipeline/task_scheduler.cpp:383-386, 399-430`); a GPU executor sends `device_ready` only when a worker thread is free (`src/pipeline/gpu_pipeline_executor.cpp:105-117`); workers per GPU = `pipeline.num_threads`, default 4 (`src/include/exec/config.hpp:28`; stream pool sized the same, `gpu_pipeline_executor.cpp:55`) |
| Data I/O + decode | `materialize_metadata_to_table` → `cudf::io::read_parquet(sources, metadatas, opts, stream, mr)` (`parquet_gpu_ingestible.cpp:1120-1121`) through `sirius_datasource` → uring reactor | GPU memory reservation is taken before the task runs (`gpu_pipeline_executor.cpp:186`, downgrade retry `:229-245`) — memory is the only admission gate. Below it, one reactor (`uring_n_reactors` default 1, `scan_manager/config.hpp:91`), one worker thread, 64 bounce slots × 1 MiB, ring depth 128, an `inflight` counter (`src/io/uring/uring_reactor.cpp:53,862,877,901`; `src/include/io/uring/config.hpp:24,28`); requests round-robin across reactors (`src/include/io/templated_ioctx.hpp:302-309`) |

Net effect: on a CN the number of scan reads in flight is `pipeline.num_threads` (4) minus
whatever is blocked on memory, regardless of how many scan pipelines the fragment has. Which
scan's split runs next is decided by task priority (source-first ranks, `task_creator.cpp:156-207`)
and by who wins `device_ready`, never by I/O state. There is one existing I/O admission primitive,
`exec::admission_control` (budget + RAII slot, `src/include/exec/admission_control.hpp:39-102`),
but it is used only by the prefetching cache (`src/include/io/cache/prefetching_cache.hpp:256`,
`src/io/cache/prefetching_cache.cpp:277,773`, budget `inflight_io_chunk_budget=2048`,
`src/include/io/cache/config.hpp:24`) and that cache is off by default (`scan_manager/config.hpp:99`)
and measured as a 2.1x regression (`bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:162`). So no I/O
admission is in effect today.

## 1. Scheduling unit: the data split, weighted by its compressed bytes

Unit = one `parquet_split_info` (== one `scan_operator_input` == one pipeline task's read),
with weight `sum(rg_slices[i].reserved_compressed_bytes)` (`src/include/op/scan/row_group_metadata.hpp:69`,
filled from `row_group_entry::compressed_bytes`, `src/include/op/scan/parquet_gpu_ingestible.hpp:226`).
Cap is per CN process, counted in splits (v1) with an optional bytes budget (v2).

Why not the alternatives:
- Row group: all row groups of a split go into one `read_parquet` call (`:1120-1121`); cudf
  computes and issues the column-chunk byte ranges internally. Gating per row group means
  interposing inside the reader — out of scope (no reader rewrite).
- FE byte range: a range becomes one `parquet_file_scan_info` (`build_file_scan_info`, ranges
  applied at `:825-833` via `row_groups_in_byte_range`, `src/op/scan/parquet_byte_range.cpp:51-62`),
  which the coalescer then chunks into several splits when it exceeds the byte cap (`:315-327`)
  or fuses with other files (`batch_within_file_boundaries=false` on the query path,
  `parquet_gpu_ingestible.hpp:98`). The range is gone by I/O time; it is not a 1:1 I/O unit.
- uring slot / reactor depth: `NUM_CHUNKS=64` (`uring_reactor.cpp:53`) already bounds device
  reads at 1 MiB granularity, but it is media depth, blind to which scan owns the bytes, and
  absent on the kvikio path (`use_sirius_datasource=false`, do not flip — `notes/OPEN.md:45`).
- Split is what the creator/scheduler already moves one at a time; its `estimated_bytes` /
  `estimated_working_set_bytes` (`parquet_gpu_ingestible.hpp:171-187`) already drive the memory
  reservation; and its lifetime brackets exactly "I/O issue → decoded batch exists".

Metadata I/O (footer fetch, `:707`, cached in the ioctx metadata store `:713-717`) stays
ungated: it is small, per file, and must run ahead to build the splits.

## 2. Where the gate sits

Recommended: a non-blocking admission check in the scan operator's source interface, with a
wake-up on release, so no thread blocks and no GPU memory is pinned while a scan waits.

- New object `scan_io_admission` (wrap `exec::admission_control`, add `try_acquire(weight)` and
  an on-release callback) owned by `sirius_scan_manager` next to `_io_ctx`
  (`sirius_scan_manager.cpp:1268-1320`), configured from `scan_manager_config`, handed to each
  `sirius_gpu_scan_operator` in `prepare_for_query` where the `split_provider` is built
  (`:1438-1448`) — same wiring shape as the connector.
- `get_next_task_hint()` (`sirius_gpu_scan_operator.cpp:383-386`) returns "starved" instead of
  `READY` when the operator's next split needs I/O and no token is free. The creator already
  treats a head that yields no task as starved and moves on (`task_creator.cpp:372-411`).
- `get_next_task_input_data()` (`:411-426`): `try_acquire` first; on failure return `nullptr`
  without popping (the creator's loop breaks at `:434-441`, connector stays intact). On success
  pop the split and stash the RAII token on the `scan_operator_input`
  (`src/include/op/scan/sirius_gpu_scan_operator_data.hpp:92-130`). Resident (pinned-cache)
  splits do no disk I/O and bypass the gate: test `has_scan_metadata()` (`:126-128`).
- Release = "batch handoff": in `gpu_ingestible::materialize_table` right after
  `materialize_metadata_to_table` returns (`src/op/scan/gpu_ingestible.cpp:33-35`) — the read
  has completed and the decoded table exists; the rest of `execute()` (`:499-557`, ending in
  `make_data_batch` at `:557`) is GPU-side. The token's destructor is the safety net for
  exception / reschedule paths. On release, wake the creator with `task_creator::schedule(op)`
  (`task_creator.cpp:320-325`), the same call producers use to wake consumers.
- Rate variant (v2): a token bucket in bytes/s; admission succeeds when
  `bucket >= split.reserved_compressed_bytes`; refill on a timer thread that also wakes the
  creator. Same hook points. The sim needs a clock for this; do the inflight cap first.

Rejected placements:
- Blocking `acquire` inside `get_next_task_input_data()`: with the default 1-thread creator this
  head-of-line blocks task creation for every other pipeline (the PLAN-04 stall class,
  `notes/OPEN.md:25`). Only tolerable as a one-day spike with
  `task_creator.num_threads >= number of scan pipelines`, and each blocked thread still holds a
  pool slot (`task_creator.cpp:357`).
- Blocking inside `materialize_table` on the GPU worker: the memory reservation is already held
  (`gpu_pipeline_executor.cpp:186`); with cap=1 and 4 workers, three reservations sit idle and
  the downgrade executor sees phantom pressure.
- In the coalescer `emit` (`load_balancing_scan_batch_coalescer.cpp:96-111`): delays discovery,
  breaks `total_source_input_bytes()` (`sirius_gpu_scan_operator.cpp:391-397`) which needs the
  connector closed, and hides queued work from the scheduler.

## 3. Semantics: no rows dropped, query end, N CNs on one file

- Correctness: admission only delays the pop; the connector stays FIFO; `all_ports_empty()` is
  still `connector closed && empty` (`split_connector.cpp:89-93`); every split still becomes
  exactly one task, so pipeline completion accounting (`tasks_completed`,
  `src/pipeline/sirius_pipeline.cpp:470-504`) and the coalescer's zero-split fallback
  (`parquet_gpu_ingestible.cpp:331-359`) are untouched.
- Query end: tokens are RAII. `sirius_scan_manager::reset()` (`sirius_scan_manager.cpp:1808-1821`)
  runs per query: add `admission.wait_for_all()` (`admission_control.hpp:90`) with an assert that
  in-use is zero after `_dispatcher->wait_for_all()`. Error drain (`task_scheduler.cpp:267-310`)
  destroys in-flight tasks, releasing tokens through the destructor; a token must never outlive
  its `scan_operator_input`.
- OOM reschedule re-runs the same split (`src/pipeline/gpu_pipeline_task.cpp:433-492`). The
  token was released after the first read, so the re-read is unadmitted (over-admits by one).
  Re-acquire blocking on the GPU worker in that path (safe there) and log it; it is rare.
- N CNs splitting one file: each CN owns disjoint row groups by start offset
  (`parquet_byte_range.cpp:51-62`; overlaps refused by the translator,
  `experimental/starrocks/crates/starrocks-plan-translator/src/scan_paths.rs:149-257`; ranges
  ride the Substrait item as `start/length`, `node_translator.rs:2076-2094`, installed by
  `attach_byte_ranges`, `sirius_physical_plan_generator.cpp:140-160`). Per-CN throttling is
  therefore independent and correct. A cluster-wide cap is not needed for correctness; it
  matters only when CNs share media (all four GB200 CNs read `/raid`, runbook `:100-113`).
  There is no FE-side hook to coordinate it (ranges are assigned statically per instance,
  `scan_paths.rs:57-92`), so express it as `per_cn_cap = cluster_cap / NUM_CNS` at launch — the
  launcher already starts identical CNs (`configs/gb200-4gpu/cluster4-numa.sh:334-350`) — and
  let the simulation sum the CNs.
- Overlap the cap actually controls: between scan pipelines of one fragment (q03: lineitem,
  orders, customer) and between the 4 executor threads over one scan's splits. Cross-fragment
  overlap inside a CN does not exist today (single engine thread, section 0).

## 4. Seeing it work locally

Instrumentation to add with the feature (both cheap, both keyed so agent 1's trace can join):
- Log, INFO level so it survives release builds: `[scan-io-admit] query={} op={} task={} split_bytes={} inflight={}/{} waited_us={}`
  at grant and `[scan-io-release] ... held_us={}` at release. Correlate with the existing DEBUG
  lines `[parquet_gpu_ingestible] Byte range [{}, +{}) of {} owns {} row group(s)`
  (`parquet_gpu_ingestible.cpp:828`) and `Row group pruning {}: {} -> {}` (`:837`). Level via
  `SET sirius_log_level='debug'` (`src/sirius_extension.cpp:3261`); CN logs land under
  `SIRIUS_LOG_DIR` (`engine.rs:208-218`).
- NVTX: `sirius::scan_io_wait` around the starved interval and `sirius::scan_io` from grant to
  release. Existing ranges to read alongside: `sirius::query` (`src/sirius_engine.cpp:238`),
  `Pipeline {} Task {} [GPU_SCAN -> ...]` (`gpu_pipeline_task.cpp:591-593`),
  `Pipeline {}: GPU_SCAN (id=..)` (`:235-237`), `Pipeline {}: {} -> {}`
  (`sirius_pipeline.cpp:461-466`). Kernels that mark the read/decode: cudf's
  `gpuDecodePageData*`, `gpuComputePageSizes`, `gpuDecodeStringPageData`, nvcomp snappy/zstd
  batched decompress, and the H2D copies from the reactor bounce slots.

Expected picture: with cap=1 the `sirius::scan_io` ranges on the four `gpu_pipeline` threads
never overlap and decode kernels come in serial bursts; with cap=4 up to four overlap and the
bursts interleave. Number of concurrent `scan_io` ranges == inflight.

Command shapes (GB200 box; avoid 02:00-03:50 UTC, nightly CI owns the GPUs):
```bash
# 1. Profile one CN: wrap the CN binary in the launcher (cluster4-numa.sh:349 runs "$CN_BIN" "${cn_args[@]}")
nsys profile --trace=cuda,nvtx,osrt --sample=none -o /raid/prestouser/nsys/cn$i.%p "$CN_BIN" "${cn_args[@]}"
# 2. Run one query twice with the cap flipped (knob delivery in section 5)
SIRIUS_CN_SCAN_IO_INFLIGHT=1 NUM_CNS=1 SCALE_FACTOR=100 ./cluster4-numa.sh   # then:
TPCH_DATA=/raid/prestouser/aocsa/tpch_parquet_sf100_f64 QUERY_TIMEOUT=180 MIN_BACKENDS=1 \
  experimental/starrocks/benchmarks/tpch/bench.sh /tmp/bench/cap1/t.csv 1 q06 q03
SIRIUS_CN_SCAN_IO_INFLIGHT=4 ... bench.sh /tmp/bench/cap4/t.csv 1 q06 q03
# 3. Read
grep -h 'scan-io-admit' $SIRIUS_LOG_DIR/*.log | awk -F'waited_us=' '{print $2}' | sort -n | tail
bash test/tpch_performance/nsys_analyze.sh /raid/prestouser/nsys/cn0.<pid>.sqlite   # NVTX by domain
```
q06 is a single-scan query (one throttled stream); q03 shows two scans competing. `bench.sh` has
no correctness gate (`bench.sh:178,187` score on rc/non-empty only) — oracle the rows against
DuckDB before quoting any timing. In-process, the same knob as a `SET` option lets
`test/tpch_performance/nsys_report.sh --sf <N> 6` (profile-analyzer skill, `:35-36`) flip it per
query without a cluster.

## 5. Knobs, defaults, delivery

YAML under `sirius.executor.scan_manager` (parsed at `src/sirius_config.cpp:243-256`; unknown
keys are rejected, so add them there):

| Key | Type | Default | Meaning |
|---|---|---|---|
| `scan_io_inflight_splits` | int | 0 (off, today's behaviour) | Max splits between admit and release per CN process |
| `scan_io_inflight_bytes` | bytes | 0 (off) | Compressed-byte budget alternative; a split needing more than the budget is admitted alone (admission_control's oversized rule, `admission_control.hpp:34-37`) |
| `scan_io_rate_bytes_per_sec` | bytes/s | 0 (off) | v2 token bucket; mutually exclusive with the two above at load time |

Delivery to a CN: env `SIRIUS_CN_SCAN_IO_INFLIGHT`, `SIRIUS_CN_SCAN_IO_INFLIGHT_BYTES`,
`SIRIUS_CN_SCAN_IO_RATE` → derived YAML in `engine_settings.rs:78-91` (same pattern as
`SIRIUS_CN_USE_SIRIUS_DATASOURCE`), registered in `tunable.rs` so a typo fails startup
(`experimental/starrocks/docs/TUNABLES.md:8-13`). This keeps the flag path: `--sirius-config`
conflicts with `--gpu-memory-limit` (`main.rs:306-310`) and the launchers use the latter
(`cluster4-numa.sh:342-343`). Also expose `scan_io_inflight_splits` as an internal DuckDB option
like `scan_task_batch_size` (`sirius_extension.cpp:3171-3177`) for in-process runs.

Values worth sweeping: 1, 2, 4. Anything above `pipeline.num_threads` (4) is a no-op unless
that pool is raised too.

## 6. Risks

1. Lost wake-up → starved scan never re-scheduled → hang (the PLAN-04 class). Mitigate: wake on
   every release and on every `push_split`; a watchdog log when inflight==0, the connector is
   non-empty, and no admit happened for 1 s.
2. Creator head-of-line blocking if anyone ships the blocking variant (section 2).
3. Over-admission on OOM reschedule (section 3); logged, bounded by one.
4. Pinned / resident scans and insert-delta splits share the connector; they must bypass the
   gate or GPU-resident reads get throttled for no reason.
5. Delaying a scan moves the point at which it snapshots dynamic filters
   (`sirius_gpu_scan_operator.cpp:420-424`), so bytes read can change between cap values.
   Results must not; timings will. Compare rows, not just ms.
6. The duckdb-native ingestible uses the same operator; its I/O is inside the decoder
   (`native_reads` NVTX, `src/op/scan/duckdb_native_decoder.cpp:829`) and gets gated too. Fine,
   but its splits have no compressed-byte weight — count-based cap only.
7. A split cap with variable split sizes (up to `scan_task_batch_size` decoded, compressed
   bytes vary with column set) is a coarse rate; the sim should consume `split_bytes` from the
   admit line, not assume equal splits.
8. nsys inflates timings (profile-analyzer skill `:18`); use the profiled runs for overlap
   shape only, the unprofiled runs for ms.

## 7. Trace events for agent 1

`~/.claude/plans/starrocks-sirius-perf/01-trace-schema.md` did not exist when this was written
(directory empty), so its I/O events cannot be cited. Proposed events to align on, all carrying
`cn_id, query_uuid` (the `telemetry_query` mapping logged at `sirius_engine.cpp:250-254`),
`pipeline_id, task_id, op_id, split_seq`:
`scan_io.wait_start`, `scan_io.admit` (`split_bytes, inflight_after, waited_us`),
`scan_io.read_start` / `scan_io.read_end` (brackets `read_parquet`, `:1120-1121`),
`scan_io.release` (`held_us`), plus the existing per-file `byte_range` / `rg_pruned` facts from
`:828` and `:837` keyed by `file, start, length`.
