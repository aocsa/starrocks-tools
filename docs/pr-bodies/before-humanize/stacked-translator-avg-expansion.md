<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

TPC-H q01 has three avg measures, and until this layer any two-phase plan with an avg was refused. StarRocks allocates one opaque VARBINARY slot per avg state. Sirius keeps a DOUBLE sum and a BIGINT count. Every other two-phase state is one column wide, so avg is the only measure whose wire row is wider than the FE's tuple.

The partial aggregation now emits two measures for the one FE slot, `sum(cast(arg AS DOUBLE))` and `count(arg)`, and names the extra column `<slot>__count` (`partial_state::COUNT_SUFFIX`) so the receiving fragment spells it the same way. The merge aggregation sums both columns, and the finalizing projection divides them. A summed count of zero yields NULL, as SQL does for the average of no values, instead of a division by zero. `merge_exchange_overrides` and `merge_state_columns` already stepped the exchange row by each state's modeled width; this layer records where each measure's state starts and deletes the temporary refusal in `two_phase_wire_columns`.

The wider row breaks the one-slot-per-column invariant the rest of the translator relies on. I chose to fence where the expansion may appear rather than teach every consumer about it.

- The expanded partial has to be the fragment root. `translate_plan` refuses a node above it.
- No conjuncts on the expanded node. A HAVING would read the aggregate's columns at FE positions.
- No fragment `output_exprs` over an expanded partial.
- A second expanded partial in one fragment is refused.
- Hash-partition keys must be grouping keys. Keys sit ahead of every measure and keep their index.

I want to be plain about what the fences buy. A plan shape that slipped past them would read a wrong column and return wrong rows, not raise. The fences are the review, and one test pins each of them.

`AggregateCall` gains `raw_arguments`, the arguments before the decimal-to-FP64 lowering, because the count has to run over the uncast values while the sum casts them. `WireColumn::suffix` in `partial_state.rs` loses its `#[allow(dead_code)]` and the comment that said nothing reads it yet; `expanded_output_names` is that reader. `TranslatedPlan`'s public fields do not change, so the CN crate is untouched.

Verified 2026-09-03 on the GB200 (aarch64, pixi cn env, rustc 1.96.0), commit 5f9349d: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 213 passed, 0 failed (translator 148, partial_state 4).

The translate.rs count goes from 142 to 148. I removed `two_phase_avg_is_refused_until_the_next_layer` and added `partial_avg_expands_to_sum_and_count`, `merge_avg_divides_the_summed_state`, `two_phase_avg_columns_agree_end_to_end`, plus one test per fence: `expanded_partial_refuses_fragment_output_exprs`, `expanded_partial_must_be_the_fragment_root`, `conjuncts_over_an_expanded_partial_are_rejected`, `hash_partition_key_on_an_expanded_measure_is_rejected`. With the base `src/` restored, those seven fail and the other 141 pass.

Not handled here: common-expr slots carried through projects (a later layer) and any relaxation of the fences above. This is the layer q01's aggregation fragments were waiting on.

Layer 4 of the translator stack; base stacked/translator-wire-order.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
Refs #1236 (the FP64 lowering the avg sum column relies on).
