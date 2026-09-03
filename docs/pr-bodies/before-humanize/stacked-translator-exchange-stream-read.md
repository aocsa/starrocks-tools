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

Verified 2026-09-02 on the GB200 (presto-gb200-gcn-18, aarch64, pixi cn env, rustc 1.96.0, cargo 1.96.0), commit 4389e3f9 (20da894 plus a one-word module-doc fix, no code change): cargo fmt --check clean for sirius-starrocks-cn, starrocks-plan-translator, starrocks-thrift; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 184 passed, 0 failed (sirius-starrocks-cn 49 unit; starrocks-plan-translator 8 unit + 127 integration in tests/translate.rs; starrocks-thrift 0; doc-tests 0); clippy and test were then repeated from scratch in an empty CARGO_TARGET_DIR with identical results. Slice facts: translator integration tests go from 116 on dev to 127, and the 11 new ones all passed (bound_exchange_feeds_aggregate_from_a_stream, exchange_bound_to_an_empty_stream_view_is_rejected, exchange_columns_take_the_senders_names_positionally, exchange_names_must_match_the_row_layout_arity, exchange_offset_becomes_a_fetch, exchange_without_input_row_tuples_is_rejected, hash_partitioned_sink_resolves_partition_keys_to_output_columns, hash_partitioned_sink_with_a_transformed_key_is_rejected, hash_partitioned_sink_without_resolvable_keys_is_rejected, merging_exchange_becomes_sort_over_stream_read, stale_tuple_ids_from_a_multi_stage_distinct_resolve_by_slot_id), plus 2 new descriptor_table unit tests (slot_global_index_falls_back_to_the_slot_id_across_tuples, slot_global_index_refuses_an_ambiguous_slot_id_fallback); git diff origin/dev --stat touches only 5 files in crates/starrocks-plan-translator plus the 3 mechanical TranslatedPlan test literals in src/engine.rs (+4 lines) and src/fragment_executor.rs (+2 lines); exactly one commit over origin/dev 0161307; git status clean.

Not here: aggregation phases (partial/merge translation is the next layer; this one still accepts one-phase aggregates only) and the CN wiring that declares the streams and routes batches (the `cn` stack).

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- Supersedes #1242 (closed): its `translate_fragment_with_exchange_inputs` / `ExchangeInput` API and three guard tests survive here, rewritten for streams.
- Refs #1481, #1702.
