<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** `dev`'s `rust/crates/sirius-sys` binds only `Context` + `execute_substrait` (a 61-line bridge) and `rust/crates/sirius` has no `Fragment` at all, while the C++ `sirius::ffi::Fragment`/`Context` surface has been on `dev` since #1481. Every StarRocks compute-node layer above this one (`engine.rs`, `fragment_executor.rs`, `compute_node_service.rs`) imports `Fragment`, so this is the bottom of the CN stack. The fragment *borrows* the context rather than owning it: a distributed plan keeps several fragments of one query alive at once (senders parked until their receiver relays from them), so the factory is `SiriusContext::fragment(&self)` and the `UniquePtr<Context>` moves behind a `RefCell` whose borrow never outlives a single call. `stream_view_name` is exposed on both sides so the front end emitting a stream read and the engine creating the view share exactly one definition of the name.

**What changed** (one reviewable unit: the Rust half of the Fragment FFI; no C++ signature changes):

- `rust/crates/sirius-sys/src/lib.rs` — cxx bridge for `type Fragment`, `make_fragment`, `stream_view_name`, `declare_input_column`, `declare_input_sender`, `declare_output`, `declare_output_broadcast`, `declare_output_hash_key`, `build`, `relay_from`, `close_input`, `run`, `unsafe fn result_to_arrow`, `output_batch_count`, `output_types`, and the widened `pub use`. Doc comments carry the contracts (usage order, broadcast/hash-key mutual exclusivity, `build`'s two rejection rules, `relay_from`'s preconditions).
- `rust/crates/sirius/src/lib.rs` — `SiriusContext.inner: RefCell<UniquePtr<Context>>`; `fragment(&self) -> Result<Fragment<'_>, Exception>`; `execute_substrait`/`execute_substrait_result` relaxed from `&mut self` to `&self` (source-compatible for callers); the Arrow drain extracted to `collect_arrow_stream` and shared by both entry points; `pub fn stream_view_name`; `pub struct Fragment<'ctx>` (a `PhantomData` lifetime tie, so a fragment cannot outlive its engine) with the methods above. Crate doc now names the two entry points.
- `src/include/sirius_ffi.hpp` — a 3-line doc note on the free function `stream_view_name` explaining why it returns `unique_ptr` (so the cxx bridge can bind it directly).

**Rename note.** The drain is `Fragment::result_to_arrow(&mut self)`, matching the C++ method it binds and the earlier draft #1598. The development branch called it `into_arrow`; that name lies — it takes `&mut self` and does not consume the fragment, and Rust reserves `into_*` for by-value conversions. Compute-node call sites are renamed at carve time in their own layers.

**Tests** (`rust/crates/sirius/src/lib.rs`, all GPU-only except where noted — CI compiles and links them with `cargo test --no-run` but does not execute them, so the pass log is below):

- `broadcast_fragment_feeds_every_destination` — two outputs + `declare_output_broadcast`; each receiver relays its stream and sees all 3 rows.
- `hash_partitioned_fragment_routes_keys_deterministically` — two 20k-row INT64 fixtures with different row-group boundaries (5000 vs 7000) must be disjoint, union to the whole, and agree key-for-key on stream assignment: the cross-sender hash-parity contract a shuffle join rests on.
- `hash_partitioned_fragment_routes_decimal_keys_deterministically` — the same contract for a `DECIMAL(15,2)` key (TPC-H q10's shuffle-key shape; `dev`'s `derive_key_cast_type` already hashes DECIMAL through FLOAT64, so no C++ change was needed).
- `relay_from_rejects_a_mismatched_schema` — also pins that `output_types()` errs before `build()` and reports `[BIGINT, VARCHAR]` after.
- Restored from #1598: `stream_view_name_matches_the_engine_convention` (no GPU), `routing_modes_are_mutually_exclusive`, `context_makes_several_fragments_at_once` (the test that justifies the `RefCell`).
- `dev`'s `constructs_and_drops` / `executes_local_files_plan_on_gpu` / `missing_config_file_is_an_error` are unchanged apart from `let mut ctx` → `let ctx` (clippy `-D warnings` once `execute_substrait` takes `&self`) and the parquet fixture moving into a `write_users_parquet` helper the new tests reuse. New helpers: `stream_read_plan`, `decimal_stream_read_plan`, `stream_read_plan_f64`, `rows`, `decimal_rows`, `write_multi_row_group_parquet`, `write_multi_row_group_decimal_parquet`.

Verified: GB200 (aarch64, CUDA 13.0, driver 580.105.08) at 14386a77 (stacked/ffi-fragment-rust, on top of stacked/ffi-transaction-scope): `pixi run make` OK (463/463, 0 errors); `cargo fmt --check` OK; `cargo clippy --all-targets -- -D warnings` OK; `cargo test --no-run` links only with conda's linker flags (`RUSTFLAGS='-C link-arg=-Wl,--allow-shlib-undefined -C link-arg=-Wl,-rpath-link,$CONDA_PREFIX/lib'`) — the bare CI command fails at link on this aarch64 box (476 undefined cudf/rmm/spdlog refs; conda ld does not search libsirius.so's RPATH for DT_NEEDED, pre-existing toolchain behaviour, not from this branch); `CUDA_VISIBLE_DEVICES=3 cargo test -p sirius -- --test-threads=1`: 10 passed, 0 failed (16.57s); `sirius_unittest "[sirius_ffi]"`: All tests passed (8 assertions in 3 test cases).

**Intentionally not handled** (each is its own layer or PR on the same two files): the exchange staging arena (`staging_*`, `StagingArena`, `PackedBatch`, `export_packed`/`push_packed`); `declare_input_cardinality`/`output_row_count` (#1694's Rust half); `pin_table`/`unpin_table`/`PinTableSpec`; byte-range `local_files_plan_ranged` and its test (needs the scan-side byte-range PRs first); `fragment_over_an_empty_input_stream_terminates` + `under_watchdog` (a hang-class test that needs the pipeline-completion fix, so it lands above it); the `derive_key_cast_type` FLOAT/DOUBLE widening in `src/exec/streaming_fragment.cpp` (nothing here needs it). `rust/Cargo.lock` is untouched (byte-identical to `dev`; #1598's uuid downgrade is not taken). No docs changes: `docs/super-sirius/streaming-fragments.md` and `streaming-sessions.md` already describe the cross-language lifecycle, and the README table row belongs to another PR. One runtime caveat to be aware of: with the `UniquePtr` behind a `RefCell`, a re-entrant call into the context from inside another call would now panic (`BorrowMutError`) instead of failing to compile; no such caller exists in-tree or in `experimental/starrocks`.

Layer 2 of the **ffi** stack, on top of `stacked/ffi-transaction-scope` (`98661d7d`); this PR's diff is against that layer, which is its own PR in the stack.

- `fix(ffi): own a transaction while lowering a fragment's Substrait plan` — `lower_substrait()` now owns a `ClientContext` transaction when none is active (commit on success, rollback on failure), because DuckDB 1.5.5 (`dev`'s pin since #1328) throws `TransactionContext::ActiveTransaction called without active transaction` from every catalog lookup and `Fragment::build()` commits its view-creation transaction before lowering; `Context::execute_substrait` opens its own and is unchanged.
- `bring_up()` also honors `SIRIUS_LOG_BACKEND`/`SIRIUS_LOG_DIR`/`SIRIUS_LOG_LEVEL`, which the FFI path ignored.
- Adds a Catch2 case in `test/cpp/exec/test_sirius_ffi_fragment.cpp` over a hand-built Substrait stream read (+2 protobuf include roots in `CMakeLists.txt`). Every Fragment test in this PR depends on that fix.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above — n/a, no configuration changes
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md) — n/a, the existing `docs/super-sirius/streaming-fragments.md` / `streaming-sessions.md` already cover the cross-language Fragment/Context lifecycle

## References
Supersedes #1598 (draft `aocsa/stream/16-rust-fragment` @ 516e0333, 47 commits stale; its doc comments and three tests are carried here, its lockfile hunk is not).
Refs #1481 (C++ `sirius::ffi::Fragment`/`Context`, merged — every symbol this bridge binds already exists on `dev`).
Refs #1328 (DuckDB 1.5.5 pin — why the base layer's transaction fix is required for these tests to run).
