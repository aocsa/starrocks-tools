<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

TPC-H q01 groups by (l_returnflag, l_linestatus) and orders by the same two keys. The FE lists the grouping expressions in GROUP BY order, but it materializes the aggregation output tuple's slots in ascending slot id (every aggregation-output slot is serialized with column_pos -1). Every consumer above the node resolves the row through that descriptor order. This fragment's output names, the sink row, the receiver's declared stream schema and the ordering slot refs all read the tuple the same way. The translator emitted the keys in GROUP BY order, so whenever GROUP BY was not in ascending ref-id order the engine row and the descriptor view were permutations of each other. The next hop then read a wrong column, with no error. q03 and q18 fail at the hop today with "stream 14 column 0 is declared DATE but the source sink produces BIGINT".

The sort tuple has the same problem. The FE builds it in two passes (ordering keys first, then the leftover payload slots in ascending slot id) and lists `sort_tuple_slot_exprs` in that two-pass order, while the descriptor again holds the slots in ascending slot id. An ORDER BY on a non-leading slot shipped the key column first.

Both nodes now emit their new tuple in the descriptor's materialized-slot order. `grouping_materialization_order` pairs each output key slot with the grouping expression that carries the same ref id (the FE reuses the expression's ref id as the output key slot id). `sort_materialization_order` pairs each ordering expression with the sort-tuple slot it names and appends the remaining materialized slots as the payload. `translate_aggregation` iterates `key_order` for the key-type check and the grouping expressions; `translate_sort` translates each materialization expression into a slot-keyed map and drains it in descriptor order.

I made the pairing a strict bijection rather than a best effort. Every grouping expression must be a bare slot ref, no slot may appear twice, and every key slot must find its expression. Anything else means the FE laid the tuple out differently than this model, and reordering on a wrong model would ship wrong columns without an error, which is the exact failure this PR removes. A single key needs no pairing and skips the check, so a `GROUP BY CAST(x)` with one key still translates. With several keys a non-slot-ref grouping expression is refused with an UnsupportedPlanNode error that says so. The sort side refuses ordering expressions that are not slot refs into the sort tuple for the same reason.

**How I tested it.** On a GB200 box (aarch64) I ran the CI trio: cargo fmt, clippy with warnings as errors, and the CN test suite without the engine feature. All 207 tests pass, 7 of them new.

Tests: 7 new integration tests in `tests/translate.rs` (135 to 142), all built around the q03 shape (keys 18, 13, 16 over a tuple serialized 13, 16, 18, 35). `group_by_keys_out_of_slot_order_ship_tuple_slot_order` and `order_by_on_a_non_leading_sort_slot_ships_tuple_slot_order` translate both sides of a hop and assert the sender's produced (name, type) columns equal the receiver's declared stream columns. `topn_over_group_by_keys_out_of_slot_order_resolves_both_sort_keys` composes the two reorders under a merging exchange and checks both ordering keys resolve to the fields that hold them on each side. Two controls pin that already-ordered plans do not move. Two rejection tests cover the non-slot-ref and the unpaired-slot cases. Without the source change 5 of the 7 fail and the 2 controls pass. dev's three existing descriptor-side slot-order tests are unchanged; they assert the invariant this PR makes the emitted row obey.

Q1's GROUP BY and ORDER BY translate correctly after this layer. Not handled here: avg (its two-column partial state and the finalizing division land in layer 4, `stacked/translator-avg-expansion`), and the multi-key GROUP BY over a non-slot-ref expression, which is refused rather than guessed.

Layer 3 of the translator stack; base `stacked/translator-two-phase-agg`.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

Refs #1110, which added the descriptor-side slot-order tests this PR makes the emitted row obey. Follows layer 2 (`stacked/translator-two-phase-agg`), which refuses two-phase avg until layer 4.
