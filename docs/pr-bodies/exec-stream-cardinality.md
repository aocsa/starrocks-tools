<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Why.** A `sirius_stream_source` relation has no rows behind it at bind time, so DuckDB's optimizer estimates cardinality 1 for every stream input of a fragment. `create_streaming_source_plan()` already asks `op.EstimateCardinality(context)` for the `STREAMING_SOURCE` operator. It never gets a real answer. So at every fragment boundary the optimizer picks join order and the hash-join build/probe side blind. On the two-CN TPC-H q07 the receiver fragment built its hash table on a multi-GB lineitem-derived stream while a 2-row nation stream probed, 14.8 s -> 164 s at SF500. With one CN per GPU every non-leaf fragment input is an exchange stream, so the blindness grows with GPU count.

**What changed.** One reviewable unit, C++ engine only, no FFI or Rust files.

`stream_input_spec::estimated_rows` and `stream_input_binding::estimated_rows` are both `std::optional<std::uint64_t>`, and `streaming_fragment::build()` forwards the spec's value into the binding. I appended both as the last member so every existing positional brace-init compiles untouched, including the one in `src/sirius_ffi.cpp`.

`stream_bind_catalog::estimated_rows(id)` is a new reader. Unlike `get()` it never throws, and that is deliberate. DuckDB may ask for cardinality outside the window where a fragment's declarations are alive, and a missing declaration there has to mean "no estimate", never a bind error.

`stream_source_cardinality()` in `src/exec/stream_plan_bindings.cpp` becomes `sirius_stream_source`'s `TableFunction::cardinality`. When a count is declared it returns `NodeStatistics(rows)`. In every other case it returns `nullptr`: no bind data, wrong bind type, no registered state, no catalog on the connection, undeclared stream, declaration without a count. The `nullptr` path is bit-for-bit today's behaviour. For the catalog lookup I used `registered_state->Get<>` rather than `catalog_for()`, because `catalog_for()` throws. The wiring is one line, `stream_source.cardinality = stream_source_cardinality;`, the same shape as `sirius_read_parquet.cardinality = SiriusReadParquetCardinality` in `src/sirius_extension.cpp`.

Two tests. `stream_bind_catalog CAT-10` pins the accessor contract: nullopt for an undeclared id and for a declaration without a count, verbatim round-trip, replacement on redeclare, drop on erase.

`FRAG-10`, tagged `[integration][streaming_fragment][stream_cardinality]`, builds a two-stream equi-join fragment. It asserts on the built physical tree that the declared counts, 2 and 100'000, reach the lowered `STREAMING_SOURCE` operators verbatim, and that the tiny stream lands under `join->children[1]`, DuckDB's build side. Two SECTIONs swap which stream is the tiny one, so the test cannot pass on the optimizer's accidental default order. A third SECTION pins that the undeclared path still plans, runs, and estimates `<= 1` on both sources. Every section then pushes, closes, calls `run()` and checks the join's 5 rows. FRAG-10 needs a GPU and `test/cpp/integration/data`, so it does not run in a no-GPU lane. CAT-10 is the only signal there.

Docs in `docs/super-sirius/streaming-fragments.md`: the new method in the `stream_bind_catalog` snippet, a new `stream_input_spec` snippet showing the field, and one Contracts bullet. The struct snippet is new; the doc did not show `stream_input_spec` before this PR. The bullet says undeclared keeps the optimizer's default and declared feeds the count to `LogicalGet::EstimateCardinality`.

This is additive and opt-in. Nothing in this tree declares a count yet, so no plan changes until a caller does.

**How I tested it.** On a GB200 box (aarch64) I did a full release build, then ran the Catch2 tags `[stream_bind_catalog]` and `[streaming_fragment]` on a GPU. All 17 test cases pass, two of them new. FRAG-10 needs a GPU and the integration data, so CI's no-GPU lane only exercises CAT-10. The docs file also passed the rumdl markdown check.

**Intentionally not handled here.** I left out the FFI entry points `Fragment::declare_input_cardinality` and `output_row_count`, the Rust bindings, and the StarRocks CN code that sums local parked plus remote staged rows and declares the count before `build()`. Those land in later PRs of the fragment/CN stack, which build on this one. FRAG-10 also omits the fork version's `SIRIUS_QUERY_WATCHDOG_SECS` hang backstop because the engine-side watchdog it drives is not on `dev`; that PR restores it. This PR stands alone on `dev`. The next layer, the FFI, adds the declaration entry points on `sirius::ffi::Fragment`.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Supersedes the C++ engine half of fork commit aocsa/sirius@55e63371 (`feat/pin-table-cn`). No prior upstream draft PR covered this ground. The FFI, Rust, and CN halves follow as stacked PRs.
- Refs #1481, the streaming fragment PR that added the plan builder, blocking runner, and Fragment FFI. It introduced `stream_bind_catalog`, `sirius_stream_source`, and the `create_streaming_source_plan()` `EstimateCardinality` call this PR feeds.
