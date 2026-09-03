<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

StarRocks plans every distributed aggregate in two phases. Each instance runs a partial node ("update serialize") over its own rows, and one merge node ("merge finalize") reads the partial states after the exchange. dev refuses the merge half outright, so a StarRocks-fronted Sirius cluster can only run TPC-H with `SET new_planner_agg_stage = 1`. That setting forces one finalized aggregation instance, which pins the query to one GPU.

This PR translates both halves, without avg.

`agg_phase::classify` reads `need_finalize` and every measure's `is_merge_agg` and returns OneShot, Partial or Merge. It replaces both legacy guards in one commit, the node-level `need_finalize` check in `translate_aggregation` and the blanket `is_merge_agg` rejection in `aggregate_call`. They cannot be relaxed one at a time. New-optimizer plans always set `intermediate_tuple_id == output_tuple_id`, so the node-level check alone does not stop a merge node; a half-landed change would translate it as a one-shot aggregate and double-aggregate without any error.

`partial_state::wire_columns` models what the engine binds for each partial state, not what the FE declares. The FE slot says DECIMAL128 for a decimal sum, but the translator already casts the argument to FP64, so the column on the wire is a DOUBLE. For avg the FE slot is an opaque VARBINARY. Integer sum and count are I64, min and max keep their input type, anything else is refused by name. Both fragments derive their side of the hop from this one pure function of the FE-serialized `TFunction`. The partial measure's declared output type comes from it, and so does the merge fragment's declared stream schema (`merge_exchange_overrides` rewrites the exchange's FE slot types before `translate_exchange` declares the stream). If the model ever drifts from the engine's real binding, the engine refuses the batch at the hop instead of producing a wrong number.

A merge node substitutes sum->sum, min->min, max->max and count->sum. The engine executes exactly the function names it is given, and merging counts with count would count arriving rows. Every merge node leaves through `merge_projection`, one throwing cast per measure back to the FE-declared output type, because DuckDB binds a merged BIGINT sum as HUGEINT and the next hop's schema guard would refuse it.

Q6 translates after this layer.

Verified 2026-09-02 on the GB200 (aarch64, pixi cn env, rustc 1.96.0), commit ce41439: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 200 passed, 0 failed (translate.rs 135, translator unit 16 of which partial_state 8, cn unit 49).

Tests: 8 new integration tests in `tests/translate.rs` (127 to 135, with `merge_aggregation_is_rejected` retargeted to `merge_over_a_scan_is_rejected`) and the 8 unit tests of `partial_state.rs` (8 to 16 crate unit tests). `two_phase_wire_types_agree_end_to_end` pins that the partial and merge fragments of one query produce the same wire types column for column.

Not handled here: avg (its state is two columns, so a two-phase avg is refused with an error that names the next layer, which lands it); GROUP BY and ORDER BY key order vs slot order (layer 3); DISTINCT in two-phase plans; 3/4-phase DISTINCT plans ("merge serialize" nodes are refused); opaque VARBINARY states such as ndv, hll and max_by, which `wire_columns` refuses by name.

Layer 2 of the translator stack; base `stacked/translator-exchange-stream-read`.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

Refs #1236 for the decimal-to-FP64 lowering that makes a decimal sum's wire type a DOUBLE. `type_mapper::i64_type`, `expr_translator::cast_to` and the test helper `cast_parts` are copied byte for byte from #1704. When #1704 merges, the first two resolve to identical lines on rebase and the duplicate `cast_parts` definition is deleted once.
