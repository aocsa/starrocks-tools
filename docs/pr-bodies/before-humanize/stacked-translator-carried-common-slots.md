<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 5 of the translator stack; base `stacked/translator-avg-expansion`.

TPC-H q14 is `100.00 * sum(case when p_type like 'PROMO%' then l_extendedprice * (1 - l_discount) else 0 end) / sum(l_extendedprice * (1 - l_discount))`. The FE computes the revenue expression once as a common slot in the PROJECT_NODE below the aggregate and references that slot from both measures. No output tuple materializes it. On the stack below this layer the fragment is refused with a descriptor error, because #1233 resolves a common slot only inside the project that owns it. Q1 and Q6 do not hit this shape and do not need this layer.

StarRocks' BE outputs a project's common slots whenever a node above references them, and the FE plans against that (it even emits a `slot_map` entry for the consumed slot). This layer reproduces that behavior.

What changed, as one unit:

- `common_slots_consumed_above` is a pre-pass over the flat preorder plan. For every PROJECT_NODE with a non-materialized common slot it scans the expression payloads of the strict ancestors, plus the fragment's `output_exprs`, for references to that slot. Matching is by slot id alone, because an ancestor ref can carry a stale tuple id. `translate_plan` gains a `root_output_exprs` parameter for this; `lib.rs` passes `fragment.output_exprs`.
- `translate_project_node` emits each consumed slot as a trailing column past the descriptor row (preferring the FE's own `slot_map` entry, falling back to the common binding) and records it as a `CarriedSlot { tuple_id, slot_id, column }` on the new `TranslatedRel.carried_slots`.
- Row layout invariant, written on the field: columns `[0, output_width - carried.len())` are the descriptor row, the carried columns occupy the tail. Every construction site now decides what happens to carried columns. Filters, fetches, sorts without a sort tuple and `append_project` pass them through. Aggregates, sort-tuple projections, scans, exchange reads and `emit_columns` start from an empty list because they materialize a fresh row.
- `ExprContext::with_carried` and the extended `translate_slot_ref` ladder: exact synthetic override, exact carried `(tuple, slot)`, descriptor `slot_global_index`, and only when the descriptor fails a carried column matched by slot id alone. A key that matches both an override and a carried column is refused; several by-slot-id candidates are refused. I preferred refusing over guessing everywhere, because a wrong column read is not an error the engine would catch.
- `refuse_carried_join_child`: a carried slot entering a hash or nested-loop join is refused. Join conjuncts resolve over the concatenated child rows using descriptor widths, so a wider left child would shift every right-side column while the descriptor math would not.
- `confine_carried` at the fragment root, called in `lib.rs` before output names are derived. Carried columns are fragment-internal. Without this a width-preserving root (a SELECT conjunct over the carrying project) would ship columns that neither the sender's names nor the receiver's declared stream schema describe.
- A merge aggregation whose child carries columns is refused so the merge-state remapping and the carried remapping can never combine. This is unreachable today (a merge child is always an exchange, and streams never carry) and is kept as a guard.

Tests: 8 new integration tests in `tests/translate.rs` (`consumed_common_slot_is_carried_to_the_aggregate`, `consumed_common_slot_without_slot_map_entry_falls_back_to_the_common_binding`, `common_slot_consumed_only_inside_the_project_is_not_carried`, `carried_common_slot_entering_a_join_is_refused`, `common_slot_consumed_beyond_the_aggregate_fails_loudly`, `root_filter_over_a_carried_common_slot_is_narrowed_at_the_root`, `fragment_output_exprs_consume_a_common_slot`, `sort_over_a_carried_common_slot_resolves_and_narrows`) and 3 unit tests for the resolution ladder in `expr_translator.rs`. The `#1233` tests on dev are unchanged and still pass, including `common_slots_outside_a_project_are_rejected`; `reject_common_slots` is untouched because no shape here needs a non-PROJECT `common_slot_map`.

Verified 2026-09-03 on the GB200 (aarch64, pixi cn env, rustc 1.96.0), commit 268b759: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 224 passed, 0 failed (sirius-starrocks-cn 49, starrocks-plan-translator 19 unit + 156 integration in tests/translate.rs, starrocks-thrift 0).

Not handled here: a consumer of a common slot across a join (refused, see above), and a consumer beyond an aggregate (fails with the existing descriptor error, pinned by `common_slot_consumed_beyond_the_aggregate_fails_loudly`). Scan byte ranges and CN-side changes are separate stacks.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- #1233 (materialize common project slots; this layer extends it to consumers above the project)
- #1711 (layer 4, the base of this PR)
