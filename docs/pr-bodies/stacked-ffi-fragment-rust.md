<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 2 of the ffi stack, on top of #1697 (`stacked/ffi-transaction-scope`). The C++ `sirius::ffi::Fragment` is already in the base branch. This PR binds it from Rust so a caller can drive a distributed plan one fragment at a time, moving native GPU batches between fragments with no Arrow or file in between. Three files change. Two are the Rust crates. The third is a three-line comment in `src/include/sirius_ffi.hpp` saying why `stream_view_name` returns a `unique_ptr<std::string>`. cxx can bind that directly, and the view-name convention then has exactly one definition that both languages read.

**`sirius-sys`.** The cxx bridge gains `type Fragment`, `make_fragment`, `stream_view_name`, and the fragment methods `declare_input_column`, `declare_input_sender`, `declare_output`, `declare_output_broadcast`, `declare_output_hash_key`, `build`, `relay_from`, `close_input`, `run`, `result_to_arrow`, `output_batch_count` and `output_types`. `result_to_arrow` is `unsafe` at this level because it takes the address of a caller-owned `ArrowArrayStream`. `Fragment`, `make_fragment` and `stream_view_name` are re-exported from the crate root.

**`sirius`.** `SiriusContext::fragment(&self)` returns a `Fragment<'ctx>` tied to the context through `PhantomData<&'ctx SiriusContext>`, so the compiler, rather than the C++ side, enforces that a fragment cannot outlive its engine. The safe methods take `&str` for column names and DuckDB type names and `&[u8]` for the Substrait plan. `relay_from` returns the number of batches moved. `result_to_arrow` returns a `SubstraitResult` and owns the `FFI_ArrowArrayStream` on its own stack frame for the duration of the call, the same pattern `execute_substrait_result` already used. I pulled the drain into one `collect_arrow_stream` helper that both call. `output_types` returns `Vec<String>`. The crate also exports `stream_view_name(u64) -> String` so a front end can emit a read of the view the engine will create at `build`.

**Why `fragment()` takes `&self`.** A distributed plan keeps several fragments alive at once. Senders sit parked until their receiver relays from them. With a `&mut self` factory the second `ctx.fragment()` would not borrow-check. So the context now holds its `UniquePtr<Context>` in a `RefCell` and takes the mutable borrow inside each call that needs `Pin<&mut Context>`, releasing it before the call returns. The context is neither `Send` nor `Sync`, so there is no cross-thread aliasing to think about. That costs existing callers one signature change. `execute_substrait` and `execute_substrait_result` now take `&self` instead of `&mut self`, and the existing test dropped its `let mut ctx` to match.

**Naming.** `result_to_arrow` keeps the C++ verb rather than `into_*`. Rust reserves `into_*` for by-value conversions, and this one borrows and could in principle be called more than once.

**Tests.** Seven new tests in `rust/crates/sirius/src/lib.rs`. All but the first take the existing `GPU_CONTEXT_LOCK` and need a GPU.

- `stream_view_name_matches_the_engine_convention`. No GPU, no context. Asserts `sirius_stream_0`, `sirius_stream_42` and the `u64::MAX` form.
- `routing_modes_are_mutually_exclusive`. GPU only for context bring-up. Broadcast then hash key errors, and hash key then broadcast errors. The "needs at least two destinations" rule is not checked here because it cannot be known until `build`.
- `context_makes_several_fragments_at_once`. Two live fragments from one context, then `relay_from` before `build` returns `Err`. This only trips the build-ordering guard. The "source must have run" guard needs two built fragments and a real plan, so the fan-out tests cover it instead.
- `broadcast_fragment_feeds_every_destination`. A sender with outputs 0 and 1 under broadcast. Each receiver gets all three rows of the users fixture.
- `hash_partitioned_fragment_routes_keys_deterministically`. Two parquet files with the same 20000 `BIGINT` keys but row groups of 5000 vs 7000. Each sender's two partitions are disjoint and union to 20000, and the two independently built senders assign every key to the same stream.
- `hash_partitioned_fragment_routes_decimal_keys_deterministically`. Same contract for a `DECIMAL(15,2)` key, the shape of TPC-H q10's shuffle key. Precision 15 keeps the parquet physical type `INT64`. The engine hashes decimals through a `FLOAT64` cast. What a shuffle needs from that cast is determinism, equal values landing in equal buckets on every sender, not injectivity, so the assertions match the `BIGINT` test.
- `relay_from_rejects_a_mismatched_schema`. `output_types()` errs before `build` and returns `["BIGINT", "VARCHAR"]` after. A receiver that declares `id` as `DOUBLE` fails at `relay_from`, before any batch moves, and the error names `column 0`, `DOUBLE` and `BIGINT`.

Test-only helpers: `write_users_parquet`, `write_multi_row_group_parquet`, `write_multi_row_group_decimal_parquet`, `stream_read_plan`, `decimal_stream_read_plan`, `stream_read_plan_f64`, `rows`, `decimal_rows`.

**Not covered here.** `declare_input_sender`, `close_input` and `output_batch_count` are bound but no test in this PR calls them. Both `fragment()` and `execute_substrait` take `&self`, so the Rust types allow a whole-plan execute while a fragment sits between `build` and `run`. There is no test for that combination either.

**How I tested it.** On a GB200 box (aarch64), a full C++ build, then cargo fmt, clippy with warnings as errors, the `sirius` crate's test suite on one GPU (10 tests, 7 of them new), and the Catch2 tag `[sirius_ffi]`. The seven new tests need a GPU, so CI does not run them. The bare `cargo test --no-run` that CI runs fails to link on this aarch64 box for a pre-existing toolchain reason (it needs an rpath-link flag for the conda libs); x64 CI is the authority for that command.

**Intentionally not handled.** Each of these is its own layer or PR on the same two files: the exchange staging arena (`staging_*`, `StagingArena`, `PackedBatch`, `export_packed` and `push_packed`); `declare_input_cardinality` and `output_row_count` (the Rust half of #1694); `pin_table`, `unpin_table` and `PinTableSpec`; the byte-range `local_files_plan_ranged` helper and its test, which need the scan-side PRs first; `fragment_over_an_empty_input_stream_terminates` with `under_watchdog`, a hang-class test that lands above the pipeline-completion work; and the `derive_key_cast_type` FLOAT/DOUBLE widening in `src/exec/streaming_fragment.cpp`, which nothing here needs. `rust/Cargo.lock` is untouched and byte-identical to `dev`; I did not take #1598's uuid downgrade. No docs change: `docs/super-sirius/streaming-fragments.md` and `streaming-sessions.md` already describe the cross-language lifecycle. One runtime caveat: with the `UniquePtr` behind a `RefCell`, a re-entrant call into the context from inside another call panics with `BorrowMutError` instead of failing to compile. No such caller exists in-tree or in `experimental/starrocks`.

**What I want eyes on.** The `RefCell`. Every borrow is taken and released inside one call, and `Fragment` holds no reference to the context beyond `PhantomData`, so I do not see a re-entrant borrow. If you can find one, that is the review comment I most want.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Supersedes #1598 (draft `aocsa/stream/16-rust-fragment` at 516e0333, 47 commits stale). Its doc comments and three tests are carried here; its lockfile hunk is not.
- Refs #1481, the merged C++ `sirius::ffi::Fragment` and `Context`. Every symbol this bridge binds already exists on `dev`.
- Refs #1328, the DuckDB 1.5.5 pin, which is why the base layer's transaction fix is required for these tests to run.
- Base: #1697 (`stacked/ffi-transaction-scope`).
