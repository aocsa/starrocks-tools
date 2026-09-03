## Description

**Why.** A `sirius_stream_source` relation has no rows behind it at bind time, so DuckDB's optimizer estimates cardinality 1 for every stream input of a fragment. `create_streaming_source_plan()` already asks `op.EstimateCardinality(context)` for the `STREAMING_SOURCE` operator — it just never gets a real answer — so at every fragment boundary the join-order optimizer and the hash-join build/probe flip are decided blind. On the two-CN TPC-H q07 the receiver fragment built its hash table on a multi-GB lineitem-derived stream while a 2-row nation stream probed (14.8 s -> 164 s at SF500). With one CN per GPU every non-leaf fragment input is an exchange stream, so the blindness grows with GPU count.

**What changed** (one reviewable unit; C++ engine only, no FFI/Rust files):
- `stream_input_spec::estimated_rows` and `stream_input_binding::estimated_rows` (`std::optional<std::uint64_t>`), forwarded by `streaming_fragment::build()`. Both are appended as the last member, so every existing positional brace-init (including the one in `src/sirius_ffi.cpp`) compiles untouched.
- `stream_bind_catalog::estimated_rows(id)` — a deliberately non-throwing reader, unlike `get()`: DuckDB may ask for cardinality outside the window where a fragment's declarations are alive, and a missing declaration there must mean "no estimate", never a bind error.
- `stream_source_cardinality()` in `src/exec/stream_plan_bindings.cpp`, wired as `sirius_stream_source`'s `TableFunction::cardinality`. Returns `NodeStatistics(rows)` when a count is declared and `nullptr` in every other case (no bind data, wrong bind type, no registered state, no catalog on the connection, undeclared stream, declaration without a count) — bit-for-bit today's behaviour. It reads the catalog via `registered_state->Get<>` rather than `catalog_for()` because the latter throws. Same wiring shape as the existing `sirius_read_parquet.cardinality = SiriusReadParquetCardinality` in `src/sirius_extension.cpp`.
- Tests: `stream_bind_catalog CAT-10` pins the accessor contract (nullopt for an undeclared id and for a declaration without a count, verbatim round-trip, replacement on redeclare, drop on erase). `FRAG-10` (`[integration][streaming_fragment][stream_cardinality]`) builds a two-stream equi-join fragment and asserts on the built physical tree that the declared counts reach the lowered `STREAMING_SOURCE` operators verbatim (2 and 100'000) and that the tiny stream lands under `join->children[1]` — DuckDB's build side — in both directions, so it cannot pass on the optimizer's accidental default order; a third SECTION pins that the undeclared path still plans, runs, and estimates `<= 1` on both sources. All sections then push, close, `run()` and check the join's 5 rows. FRAG-10 needs a GPU and `test/cpp/integration/data` (it does not run in a no-GPU lane; CAT-10 is the only signal there).
- Docs: `docs/super-sirius/streaming-fragments.md` — the new method in the `stream_bind_catalog` snippet, the new field in a `stream_input_spec` snippet, and one Contracts bullet (undeclared = optimizer default; declared = fed to `LogicalGet::EstimateCardinality`).

Additive and opt-in: nothing in this tree declares a count yet, so no plan changes until a caller does.

Verified: GB200 (aarch64, CUDA 13.0, driver 580.105.08): baseline dev release build OK, incremental `pixi run make` on f1e8fb17 OK (381/381); `sirius_unittest "[stream_bind_catalog]"` → 11 test cases / 36 assertions passed; `sirius_unittest "[streaming_fragment]"` → 6 test cases / 134 assertions passed (0 failed, 0 skipped); `pixi run --locked rumdl check docs/super-sirius/streaming-fragments.md` → no issues.

**Intentionally not handled here:** the FFI entry points (`Fragment::declare_input_cardinality` / `output_row_count`), the Rust bindings, and the StarRocks CN plumbing that sums local parked + remote staged rows and declares the count before `build()` — those land in later PRs of the fragment/CN stack, which build on this one. FRAG-10 also omits the fork version's `SIRIUS_QUERY_WATCHDOG_SECS` hang backstop because the engine-side watchdog it drives is not on `dev`; it will be restored with that PR. Standalone on `dev`; the next layer (FFI) adds the declaration entry points on `sirius::ffi::Fragment`.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Supersedes the C++ engine half of fork commit aocsa/sirius@55e63371 (`feat/pin-table-cn`); no prior upstream draft PR covered this ground. The FFI/Rust/CN halves follow as stacked PRs.
- Refs #1481 (streaming fragment — plan builder, blocking runner, and Fragment FFI), which introduced `stream_bind_catalog`, `sirius_stream_source`, and the `create_streaming_source_plan()` `EstimateCardinality` call this PR feeds.
