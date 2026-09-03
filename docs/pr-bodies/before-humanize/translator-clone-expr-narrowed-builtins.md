<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Two expression-translator fixes the multi-CN branch needed to run TPC-H. Both stand alone from the translator stack.

**CLONE_EXPR.** TPC-H q01's projection reads `l_extendedprice` twice, in two products. The FE's `CloneDuplicateColRefRule` wraps the second use in `CLONE_EXPR` because the BE has no copy-on-write. The translator had no arm for it and refused the whole fragment as an unsupported expression. The node has no value semantics, so the new `translate_clone` returns the child unchanged. I chose not to re-cast it to its declared type. The type is the child's by construction, and a cast would re-narrow a product the arithmetic path already lowered to FP64, so the cloned column would differ from the uncloned one the FE says it equals.

**FE-narrowed builtins.** `year`, `month`, `day`, `length` and `char_length` bind through DuckDB, where they return BIGINT. The FE declares SMALLINT, TINYINT, TINYINT, INT and INT. When the fragment's row crosses an exchange, the receiver declares its stream from the FE slots and the hop's schema guard refuses the BIGINT column. `translate_function_call` now wraps those five calls in a throwing cast back to the declared type through the new `cast_to` helper. The scalar function itself states the BIGINT the engine produces. The binder elides a cast to a type the expression already has, so the cast costs nothing where the two agree.

Verified 2026-09-02 on the GB200 (aarch64, pixi cn env, rustc 1.96.0), commit 2752838: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 176 passed, 0 failed (translator 121 incl. the 5 new tests).

Changes sit in `experimental/starrocks/crates/starrocks-plan-translator`, four files. `type_mapper::i64_type` is new. `lib.rs` gains one row in the expression table.

Tests added in `tests/translate.rs`: `clone_expr_is_transparent`, `clone_expr_keeps_the_child_lowering`, `clone_expr_requires_exactly_one_child`, `fe_narrowed_functions_cast_to_declared_return_type`, `matching_return_type_function_stays_cast_free`.

Not handled. Other FE-narrowed builtins are not in the match; add them as they show up. Aggregates and slot resolution are untouched.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Refs #1236, the decimal to FP64 lowering. The CLONE_EXPR rule returns the child as is so it cannot undo that lowering.
- Carved from the multi-CN source branch `aocsa/feat/pin-table-cn`; independent of the translator stack (T1 to T4), which can merge in any order relative to this PR.
