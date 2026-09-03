# mbrobbel's translator drafts: minimal changes to merge-ready

Scope: five Draft PRs by mbrobbel on `sirius-db/sirius` (`experimental/starrocks/crates/starrocks-plan-translator`).
All apply cleanly to upstream `dev` (`84ea4ab5`); deps #1111 (`e4112409`), #1234 (`07453497`), #1208 (`a09c1f48`) are merged.
Source of truth for "what the code should eventually be" is `origin/feat/pin-table-cn` (SOT); every quoted string
below was checked against the PR `.diff` and SOT on 2026-09-02. Only minimal changes go into these PRs; anything
larger is a proposed issue (last section). CONTRIBUTING bar: Conventional Commit title; body states motivation,
what changed, how verified, what is intentionally not handled; Draft until that is true (the flip pings CODEOWNERS).

## Merge order

| # | PR | State (2026-08-26) | Action | Why this position |
|---|---|---|---|---|
| 1 | #1236 decimal → fp64 | Draft, no approval, aocsa requested | update (2 tests + 1 string + body) → merge | Gates Q1/Q6 and the two-phase-agg work: dev refuses every decimal `ARITHMETIC_EXPR` and decimal `avg` |
| 2 | #1235 anti joins | Draft, no approval, aocsa requested | body only → merge | Strictly ahead of SOT (null-aware refusal + real assertions); #1233 must land after it |
| 3 | #1233 common slots | Draft, **approved by aocsa** ("merge after #1234 and #1235") | body edit + trivial rebase → merge | Shares the moved `emit_mapping` test helper with #1235 |
| 4 | #1232 complete scan splits | Draft, **approved by aocsa**; dhruv9vats, wmalpica pending | 2 small additions + body → merge (any time) | Independent files (`scan_paths.rs`); improves dev today |
| — | #1242 materialized exchanges | Draft, no approval | **close** | Superseded on design grounds by the stream-view exchange |

## #1236 fix(starrocks): lower decimal operations to fp64

Current body: "Lower unsupported decimal arithmetic and aggregates to FP64 for GPU execution. Depends on #1111 and #1208." (2 commits).

| Change | File / function | Exact edit |
|---|---|---|
| 1 | `src/expr_translator.rs`, `aggregate_call` | replace `reason: "temporal avg is not supported"` (mislabels every non-DOUBLE, non-decimal avg) with SOT's `reason: "avg is only supported where it lowers to the GPU's FP64 avg (DOUBLE and DECIMAL inputs)"` (SOT `expr_translator.rs:814-815`) |
| 2 | `tests/translate.rs` | new test: DECIMAL_LITERAL with precision > 18 emits an `Fp64` literal (code below) |
| 3 | `src/type_mapper.rs` | new `#[cfg(test)] mod tests` (module is `pub(crate)`, unreachable from `tests/`): precision 18 stays `Decimal`, 19 becomes `Fp64` (code below) |
| 4 | PR body | replace with the text below; drop "Depends on …" |

```rust
// tests/translate.rs — next to decimal_literal_encodes_little_endian_unscaled_value
/// A decimal literal wider than 18 digits follows the slot rule and is emitted as FP64.
#[test]
fn wide_decimal_literal_is_lowered_to_fp64() {
    let plan = filter_with_conjunct(binary_pred(
        TExprOpcode::EQ,
        slot_ref(1, 0, scalar_type(TPrimitiveType::BIGINT)),
        decimal_literal("1.5", 19, 2),
    ));
    match literal_type(scalar_arg(filter_condition(&plan), 1)) {
        expression::literal::LiteralType::Fp64(value) => assert_eq!(*value, 1.5),
        other => panic!("expected fp64 literal, got {other:?}"),
    }
}
```
```rust
// src/type_mapper.rs — append
#[cfg(test)]
mod tests {
    use super::*;
    fn decimal(precision: i32) -> TScalarType {
        TScalarType::new(TPrimitiveType::DECIMAL128, None, Some(precision), Some(2))
    }
    /// Precision 18 is the last width kept as DECIMAL; 19 and up lower to FP64.
    #[test]
    fn decimal_precision_boundary_is_18() {
        assert!(matches!(map_scalar_type(&decimal(18), true).unwrap().kind, Some(r#type::Kind::Decimal(_))));
        assert!(matches!(map_scalar_type(&decimal(19), true).unwrap().kind, Some(r#type::Kind::Fp64(_))));
    }
}
```

PR body (paste):
```markdown
## Description
**Motivation.** The GPU expression and aggregate paths cannot consume decimal arithmetic. Today `translate_arithmetic`
refuses every decimal `ARITHMETIC_EXPR` and `aggregate_call` refuses decimal `avg`, so TPC-H Q1
(`l_extendedprice*(1-l_discount)*(1+l_tax)`, three `avg`s), Q6 (`sum(l_extendedprice*l_discount)`) and every other
revenue query fail to translate on the StarRocks CN.
**What changed.** Decimal operands of `ARITHMETIC_EXPR` and the arguments of decimal `sum`/`avg` are cast to FP64
(throwing cast) and the expression yields FP64; `DECIMAL_LITERAL` with precision > 18 becomes an FP64 literal;
`map_scalar_type` keeps DECIMAL at precision <= 18 and maps wider decimals to FP64 (`type_mapper::fp64_type`).
The lowering is **unconditional, approximate, and never cast back**: a slot the frontend declared DECIMAL can arrive as
a double and differ from StarRocks in its last digits (~1e-14 relative). This is preferred over refusing the shape;
a decimal-native GPU path is the real fix.
**Verified.** `cargo fmt --check`, `cargo clippy --all-targets --no-default-features -D warnings`,
`cargo test --workspace --no-default-features` (new: `decimal_arithmetic_is_lowered_to_fp64`,
`decimal_avg_is_lowered_to_fp64`, `wide_decimal_literal_is_lowered_to_fp64`, `type_mapper::tests::decimal_precision_boundary_is_18`).
**Intentionally not handled.** Exchange-boundary type mismatch for FE precision <= 18 results and the scan/GROUP BY
slot rewrite for precision > 18 are tracked as separate issues (see References). A shared `cast_to_fp64` helper and
the merge-phase guard on `aggregate_call` arrive with the two-phase aggregation PR.
## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)
## References
Refs #1111, #1208 (merged). Issues: <decimal-exchange-boundary>, <decimal-slot-rewrite> (numbers once opened).
```
Review comment (paste): "Two small asks before flipping to ready, then I'll approve: (1) the avg refusal string now
says 'temporal avg is not supported' but fires for every non-DOUBLE, non-decimal avg — please use 'avg is only supported
where it lowers to the GPU's FP64 avg (DOUBLE and DECIMAL inputs)'; (2) two missing tests: DECIMAL_LITERAL with
precision > 18 → Fp64 literal, and the map_scalar_type 18/19 boundary (unit test in type_mapper.rs since the module is
pub(crate)). Body draft attached. This PR gates the two-phase aggregation work, so if you're busy I can push these
commits to your branch."
Leave for later (not this PR): `cast_to_fp64` helper; `merge: bool`/`raw_arguments` on `aggregate_call`; the two issues.

## #1235 fix(starrocks): lower anti joins

Current body: "Lower anti joins through supported outer and mark join forms. Depends on #1111." (3 commits). Code unchanged.

PR body (paste):
```markdown
## Description
**Motivation.** StarRocks emits `LEFT_ANTI_JOIN`/`RIGHT_ANTI_JOIN` for `NOT EXISTS` and `NULL_AWARE_LEFT_ANTI_JOIN`
for `NOT IN`; the translator refused all three. DuckDB's Substrait consumer (`substrait/src/from_substrait.cpp`
`TransformJoinOp`, lines 543-568) has no `JOIN_TYPE_LEFT_ANTI` arm but maps `JOIN_TYPE_LEFT_MARK` to `JoinType::MARK`.
**What changed.** `LEFT_ANTI` → left outer join + `is_null(<build key>)` filter emitting only probe columns;
`RIGHT_ANTI` mirrors it; `NULL_AWARE_LEFT_ANTI` → `LeftMark` + `not(marker)`. The lowerings are asserted (join type,
filter function, emit mapping), not just "translates". `NULL_AWARE_LEFT_ANTI` with more than one equality key or any
`other_join_conjuncts` is refused ("null-aware left anti join with correlated or multi-column keys"): both executors
decide the marker's NULL-ness with one global build-has-null flag (`join_hashtable.cpp`, `sirius_physical_hash_join.cpp`
`table_has_any_null`), so a row another predicate makes definitely FALSE would be reported UNKNOWN and silently dropped.
**Verified.** fmt/clippy/`cargo test --workspace --no-default-features`: `anti_hash_joins_are_lowered`,
`null_aware_anti_join_with_extra_predicates_is_rejected`; `anti_hash_join_is_rejected` removed.
**Intentionally not handled.** Only the first equality key is null-tested; `eq.opcode == None` is treated as EQ;
no end-to-end Q16/Q21/Q22 run — tracked as issues (References). Unblocks TPC-H Q16/Q21/Q22 translation; Q1/Q6 unaffected.
## Checklist
- [x] Read CONTRIBUTING.md … - [x] tests - [ ] config - [x] docs
## References
Refs #1111 (merged). Issues: <anti-join-null-test-strictness>, <eq-conjunct-opcode-none>, <anti-join-e2e>.
```
Review comment: "Approving on content — this is strictly ahead of the feat/pin-table-cn branch (that branch lacks the
null-aware refusal and its test only checks `names.len()`). Please replace the one-line body with the attached
description (the 'Depends on #1111' is stale, #1111 merged as e4112409) and flip to ready." Leave for later: the three
issues; SOT-side adoption of commit `1b686239` and the tightened test (done in the exchange/translator carve-up).

## #1233 fix(starrocks): materialize common project slots

Current body: "Materialize common project expressions before translating visible output expressions. Depends on #1111."
Approved by aocsa: "LGTM. Hidden scratch columns get built first, visible columns read them, and parents never see the
scratch columns. Merge this after #1234 and #1235." Code change: none.

Rebase after #1235 merges: both PRs move `fn emit_mapping` out of `tests/translate.rs:3536` (#1235 to ~2822 above
`anti_hash_joins_are_lowered`, #1233 to ~1126 after `scan_project_preserves_descriptor_output_order`). On rebase, drop
#1233's copy and its removal hunk and keep #1235's; nothing else conflicts (`node_translator.rs` hunks are disjoint:
#1233 adds `reject_common_slots` calls at the top of `translate_hash_join`/`translate_nestloop_join`).

PR body (paste):
```markdown
## Description
**Motivation.** A `PROJECT_NODE` whose `common_slot_map` is non-empty failed to translate: its `slot_map` expressions
reference common slot ids that no tuple materializes, so `translate_slot_ref` hit a descriptor error. The FE emits this
shape whenever a sub-expression is shared by several output columns.
**What changed.** Common expressions are materialized first as hidden columns (one `ProjectRel` per common slot, in
ascending slot-id order, chaining allowed), visible expressions read them through `ExprContext::slot_overrides`, and the
emit mapping hides the scratch columns from parents. `SELECT_NODE`/`HASH_JOIN_NODE`/`NESTLOOP_JOIN_NODE` with a
`common_slot_map` are refused ("common slots are only materialized on PROJECT_NODE") instead of silently mis-resolving.
**Verified.** fmt/clippy/`cargo test --workspace --no-default-features`:
`project_common_slots_are_materialized_before_visible_expressions`, `nested_common_slots_are_appended_in_slot_id_order`,
`common_slots_outside_a_project_are_rejected`.
**Intentionally not handled.** Common slots consumed *above* the project (TPC-H q14 shape) — lands with the
carried-slots follow-up; the refusal is broader than necessary (issue in References).
## Checklist  - [x] reviewability - [x] tests - [ ] config - [x] docs
## References  Refs #1111 (merged). Issue: <narrow-reject-common-slots>.
```
Review comment: "Still LGTM. Rebase once #1235 lands (drop your `emit_mapping` copy, keep #1235's), paste the attached
body, flip to ready." Leave for later: narrowing `reject_common_slots`; carried slots to ancestors; `.with_carried(...)` on
the two `expr_context_with_slots` call sites (all in the SOT carried-slots PR).

## #1232 fix(starrocks): combine complete scan splits

Current body: "Combine broker scan ranges only when they cover the complete Parquet file. Depends on #1111." Approved by
aocsa ("The completeness rule is the right call for this PR…"); dhruv9vats and wmalpica pending.

| Change | File / function | Exact edit |
|---|---|---|
| 1 | `src/scan_paths.rs`, `ScanFilePaths::add_ranges` | at the top of `for range in ranges {` insert SOT's block (`scan_paths.rs:112-123` on SOT): refuse `range.has_more == Some(true)` with `Self::unsupported(node_id, "incremental scan-range delivery (has_more) is not supported")`; `continue` on `range.empty == Some(true)` (fields exist at the pinned thrift `InternalService.thrift:399-401`) |
| 2 | `tests/translate.rs` | port SOT's `has_more_scan_ranges_are_refused` (`translate.rs:807-835` on SOT): `params_with_scan_range(TPlan::new(vec![scan_node(0,0)]), base_desc(), 0, broker_scan_range("file:///data/users.parquet", FORMAT_PARQUET, 0, -1, Some(1024)))`, set `per_node_scan_ranges[&0][0].has_more = Some(true)`, assert `UnsupportedScanRange` with that reason |
| 3 | `src/scan_paths.rs`, `validate_complete_files` doc | "Completeness is required because the range cannot be plumbed through." → "Completeness is required because the range is not plumbed through **yet**: today DuckDB's Substrait `local_files` reader drops `FileOrFiles.start`/`.length` and `parquet_scan` has no byte-range parameter … Explicit partial splits are enabled by the engine byte-range stack (follow-up)." |
| 4 | PR body | text below |

```rust
// src/scan_paths.rs — first statements inside `for range in ranges {` in add_ranges
// Incremental delivery: more ranges follow through deliver_scan_ranges, which this
// CN does not implement — accepting the prefix would silently read a subset.
if range.has_more == Some(true) {
    return Err(Self::unsupported(node_id, "incremental scan-range delivery (has_more) is not supported"));
}
// An explicitly empty placeholder range carries no file.
if range.empty == Some(true) { continue; }
```
PR body (paste):
```markdown
## Description
**Motivation.** `check_range` refused any broker range that was not provably the whole file, so a fragment instance that
owns *every* split of a file (routine with pipeline dop: several driver-sequence splits of one file land on one instance)
failed outright, and a `FILES()` scan of any parquet file above `min_bytes_per_broker_scanner` could not run on the CN.
**What changed.** Ranges are collected per node and path (`CollectedRanges`, BTreeMap for deterministic diagnostics) and
`validate_complete_files` accepts exactly one shape: a strict tiling from byte 0 to EOF, collapsed to one whole-file
`local_files` item. Gaps, prefixes, overlaps, disagreeing/missing file sizes are refused with distinct reasons; a missing
size is reported before a disagreement. Incremental delivery (`has_more`) is refused and empty placeholder ranges skipped.
**Verified.** fmt/clippy/`cargo test --workspace --no-default-features`: `complete_split_broker_ranges_produce_one_local_file`,
`split_broker_ranges_with_a_gap_are_unsupported`, `split_broker_ranges_covering_only_a_prefix_are_unsupported`,
`overlapping_split_broker_ranges_are_unsupported`, `split_broker_ranges_disagreeing_on_file_size_are_unsupported`,
`split_broker_range_missing_file_size_after_the_first_is_unsupported`, `split_broker_ranges_in_descending_order_are_accepted`,
`has_more_scan_ranges_are_refused`.
**Intentionally not handled.** A lone partial split is still refused: emitting `FileOrFiles.start/length` requires the
engine-side byte-range work (row-group ownership rule, ingestible range filter, Substrait range registry). That stack
replaces `validate_complete_files` with explicit splits; it is not part of this PR.
## Checklist  - [x] reviewability - [x] tests - [ ] config - [x] docs
## References  Refs #1111 (merged). Follow-up: engine byte-range stack (old #1636-#1639, being reopened).
```
Review comment: "Approved already; two small additions before ready: refuse `has_more == Some(true)` and skip
`empty == Some(true)` at the top of `add_ranges` (wording/test attached — otherwise the tiling sweep runs over a prefix of
an incremental assignment and blames the FE layout), and soften the `validate_complete_files` doc so 'cannot be plumbed
through' reads as today's limitation. Body draft attached." Leave for later (byte-range PR, not #1232): port this PR's
diagnostics ordering + its descending-order / disagreeing-size / missing-size-after-first tests; SOT's `resolve_ranges`
refuses per node not per path and iterates a `HashMap` (non-deterministic error text).

## #1242 feat(starrocks): translate materialized exchanges — close

Closing comment (paste): "Closing as superseded on design grounds, not staleness (the diff still applies cleanly). The
exchange landed differently on feat/pin-table-cn: `translate_exchange` lowers `EXCHANGE_NODE` to a `ReadRel` over the
engine stream view `sirius_stream_<node_id>` — no parquet materialization or GPU→file→GPU round trip, a remote sender is
expressible, and a merging exchange becomes a `SortRel` over the stream read (this PR refuses it with 'merging exchanges
are not supported by sequential execution', which blocks TPC-H Q1/Q3's top fragment). Your API survives verbatim in the
replacement PR (`translate_fragment_with_exchange_inputs`, `ExchangeInput`), and its three guard tests
`exchange_without_input_row_tuples_is_rejected`, `exchange_names_must_match_the_row_layout_arity`,
`exchange_offset_becomes_a_fetch` will be ported, rewritten for streams (the `paths.is_empty()` guard becomes an
empty-`stream_view` guard). Thanks — the guard/offset coverage is exactly what the replacement needed."
Note for the replacement PR: also fix lib.rs's two stale "materialized same-node input" doc strings on SOT.

## New issues to open (each becomes its own PR later)

| Proposed title | Body (symptom · cause · where · fix) |
|---|---|
| `fix(starrocks): decimal arithmetic with FE precision <= 18 ships FP64 into a DECIMAL-declared stream column` | After #1236 the value of `DECIMAL(15,2)+DECIMAL(15,2)` is FP64 but the FE slot says DECIMAL(16,2); receivers derive stream schemas from slot types (`node_translator.rs` `StreamInputColumn` via `type_mapper::duckdb_type_name` on SOT), so the hop refuses or reinterprets. Only precision > 18 is self-consistent. Fix: cast root output exprs back to the FE-declared slot type in `project_exprs`, or refuse with a structured error; pin with a translate.rs test. |
| `fix(starrocks): precision > 18 -> FP64 rule also rewrites scan and GROUP BY slots` | `map_scalar_type` feeds `descriptor_table.rs:146` (`named_struct` → ReadRel base_schema), so a parquet DECIMAL(20..38,s) column is declared FP64 to the reader and a wide-decimal GROUP BY becomes float-keyed: silent precision loss beyond 2^53, untested (TPC-H stays at DECIMAL(15,2)). Fix: decide (refuse, or read DECIMAL128 and cast only inside arithmetic); add a test. |
| `fix(starrocks): anti-join null test covers only the first equality key` | `filter_is_null` (node_translator.rs, #1235) tests IS NULL on the first eq conjunct's opposite-side expression — correct only if that expression is strict under NULL padding. Fix: AND `is_null` over every build-side key, or refuse `LEFT/RIGHT_ANTI` whose key is not a plain field selection / cast of one. |
| `fix(starrocks): eq conjunct with opcode None is lowered as plain equal` | `translate_hash_join` rejects an eq conjunct only when `eq.opcode` is `Some(x) && x != EQ`; a null-safe `EQ_FOR_NULL` arriving with `None` becomes `equal`. On the new anti paths matched NULL=NULL rows turn into anti output. Fix: refuse `None` (or handle it explicitly) with a test. Also: the unreachable "… has no equality key" branch returns `malformed` where `UnsupportedPlanNode` is the right class. |
| `test(starrocks): end-to-end TPC-H Q16/Q21/Q22 through the CN` | Anti-join lowerings are asserted only at the Substrait level; nothing proves `sirius_physical_hash_join.cpp` `resolve_mark_join_result` returns the right rows. Fix: CN run of Q16 (single-key NOT IN → LeftMark), Q21/Q22 (NOT EXISTS → LEFT_ANTI) diffed against the StarRocks BE. |
| `fix(starrocks): narrow reject_common_slots to non-materialized ids` | #1233 refuses any non-empty `common_slot_map` on SELECT/HASH_JOIN/NESTLOOP nodes, including ids already materialized in the node's `row_tuples`, which would resolve correctly through the descriptor. Fix: compare against `desc.materialized_slot_ids(tuple)` (the filter SOT's `common_slots_consumed_above` uses) and refuse only non-materialized ids; keep a guard because SOT's later `slot_global_index` slot-id fallback could otherwise pick a same-id column from another tuple. Natural home: the carried-slots PR. |

## Verification (all five, from `experimental/starrocks/`)

```bash
pixi run -e cn cargo fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check
pixi run -e cn cargo clippy --all-targets --no-default-features -- -D warnings
pixi run -e cn cargo test --workspace --no-default-features
```
Must pass: #1236 `decimal_arithmetic_is_lowered_to_fp64`, `decimal_avg_is_lowered_to_fp64`, `wide_decimal_literal_is_lowered_to_fp64`,
`type_mapper::tests::decimal_precision_boundary_is_18`; #1235 `anti_hash_joins_are_lowered`, `null_aware_anti_join_with_extra_predicates_is_rejected`;
#1233 `project_common_slots_are_materialized_before_visible_expressions`, `nested_common_slots_are_appended_in_slot_id_order`,
`common_slots_outside_a_project_are_rejected` (after the #1235 rebase compiles with one `emit_mapping`); #1232 the eight tests listed
in its body. The GitHub `Experimental / starrocks` job runs these same three commands (path-filtered to `experimental/**`).

## Who does the work

These are mbrobbel's branches. Default: post each review comment above (with the pasteable body) and let him push. Offer
to push the commits ourselves where "Allow edits from maintainers" is on (not visible from the PR pages; ask) or if he is
slow — #1236 especially, since it gates the two-phase-aggregation stack. Our edits to #1236 stay limited to the two tests
and the one string; no other code. After each merge, the SOT carve-up rebases on top (drops its duplicate hunks/tests).
