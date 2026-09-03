<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 1 of the translator stack; base `dev`.

An `EXCHANGE_NODE` is a fragment boundary. `dev` refuses it, so every multi-fragment plan fails to translate: every two-phase aggregate, every distributed join. TPC-H runs today only with `SET new_planner_agg_stage = 1` on one GPU.

This layer lowers the receiver's exchange to a `ReadRel` over the engine's `sirius_stream_<node_id>` view. That name is the one the CN and the engine already share (`sirius::ffi::stream_view_name`, Rust binding in #1702). The compute node supplies, per exchange node, an `ExchangeInput { node_id, stream_view, names }` through the new `PlanTranslator::translate_fragment_with_exchange_inputs`; `translate_fragment` delegates with no inputs and keeps refusing exchanges. A stream has no file to infer a schema from, so the translator records each exchange's schema (name plus DuckDB type name per column, derived from the same `Type` the read carries) on `TranslatedPlan.stream_inputs` for the CN to declare.

Rows never leave the GPU (`relay_from` on one node, packed bytes across nodes). That is why I did not take the materialize-to-parquet route of #1242; it added a GPU to file to GPU round trip per hop and could not name a remote sender.

What the exchange becomes:

- The read's schema comes from `input_row_tuples` (`named_struct_for_tuples`, which spans several tuples). Its column names are the sender's, bound by position: one name per column of the row layout, or the translation fails with a descriptor error. The receiver's own tuple still names the fragment root.
- A merging exchange (`sort_info` present) becomes a `SortRel` over the stream read, built with the existing sort helpers. The senders' runs arrive in no fixed interleaving, so a plain read would drop the cross-fragment ORDER BY.
- An exchange's own `offset` reaches `apply_fetch`, so `OFFSET n` on an exchange emits a `FetchRel` with an explicit unlimited count instead of being ignored.
- Guards: an unbound exchange is refused naming the node, as are empty `input_row_tuples`, an empty `stream_view`, and a names/width mismatch.

Two pieces the read needs ride along. `slot_global_index` falls back to the slot id when exactly one row tuple carries it (the FE leaves grouping refs bound to the tuple below a multi-stage aggregation, the TPC-H q16 shape); two candidates is still an error. And `TranslatedPlan.output_partition_columns` resolves a HASH_PARTITIONED stream sink's keys to output column indices, bare `SLOT_REF`s only. A transformed key would make one sender hash a value its peers do not, so it is refused, as are a hash sink with no partition expressions and one combined with `output_exprs`. A root-level guard now rejects any width/name drift between the emitted row and its names.

The CN crate changes are mechanical: three test literals gain the two new `TranslatedPlan` fields.

**How I tested it.** On a GB200 box (aarch64) I ran the CI trio: cargo fmt, clippy with warnings as errors, and the CN workspace tests without the engine feature. All 184 tests pass; the translator integration suite goes from 116 on dev to 127, and the 11 new ones (among them `merging_exchange_becomes_sort_over_stream_read`, `exchange_offset_becomes_a_fetch` and `hash_partitioned_sink_with_a_transformed_key_is_rejected`) plus 2 new descriptor_table unit tests all pass.

Not here: aggregation phases (partial/merge translation is the next layer; this one still accepts one-phase aggregates only) and the CN wiring that declares the streams and routes batches (the `cn` stack).

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- Supersedes #1242 (closed): its `translate_fragment_with_exchange_inputs` / `ExchangeInput` API and three guard tests survive here, rewritten for streams.
- Refs #1481, #1702.
