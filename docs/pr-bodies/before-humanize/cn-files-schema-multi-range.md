<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** The FE answers a FILES() query by sending one `get_file_schema` request to a
single CN, with one `TBrokerRangeDesc` per matched file (`TableFunctionTable.getGetFileSchemaRequest`
in the pinned StarRocks tree). On `dev` the CN reads the schema from range 0 and refuses any
request with more than one range with `multi-file FILES() schema inference is not supported yet
(N files); use a single file path`. So a FILES() query over a table written as one parquet file
per generator chunk, which is the layout a distributed scan wants, fails at planning time.

**What changed** (one reviewable unit across two existing files).

- `experimental/starrocks/src/file_schema.rs`: new `parquet_files_schema(paths)`. The first file
  wins the spelling. Every other file must have the same column count, the same names under ASCII
  case-insensitive comparison, and the same `slot_type`. Positional slot fields (`id`, `slot_idx`,
  `column_pos`) are per-file bookkeeping and are not compared. Each mismatch error names the
  offending file and the file it disagrees with. The parquet fixture writer moved from the test
  module into `pub(crate) mod test_support` so the service tests can reuse it.
- `experimental/starrocks/src/compute_node_service.rs`: `file_schema_from_attachment` rejects an
  empty range list, rejects a non-parquet range with an error that names both the format and the
  path, collects every path and calls `parquet_files_schema`. Signature and placement are
  unchanged.

I chose whole-set agreement over what native StarRocks does. Its `FileScanner::sample_schema`
reads `schema_sample_file_count` files and `merge_schema` promotes conflicting types. That is a
reasonable contract when the scanner re-reads each file's own footer at scan time. Here the scan
reads every file with the inferred schema, so a file that disagrees would be misread rather than
promoted. Failing closed at planning, with the path in the error, is the honest behaviour for
this engine. Sampling would also make the answer depend on which files happen to be sampled.

**Tests.** Eight new tests, all in CI's `--no-default-features` job. In `file_schema.rs`:
`multi_file_agreeing_schemas_infer_the_single_file_slots`,
`multi_file_rejects_column_count_mismatch`, `multi_file_rejects_column_name_mismatch`,
`multi_file_rejects_column_type_mismatch`, `multi_file_accepts_case_differing_column_names`,
`multi_file_missing_file_error_names_its_path`. In `compute_node_service.rs`:
`get_file_schema_attachment_infers_across_multiple_ranges` and
`get_file_schema_attachment_rejects_non_parquet_range_by_path`, which build a real
`TGetFileSchemaRequest` the way the FE does and run it through the service function.

Verified 2026-09-02 on the GB200 (presto-gb200-gcn-18, aarch64, pixi cn env, rustc 1.96.0), commit 3056cda: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 179 passed, 0 failed (sirius-starrocks-cn lib 57, starrocks-plan-translator lib 6 + tests/translate.rs 116, starrocks-thrift 0, doc-tests 0); file_schema::tests 14 and compute_node_service::tests 13, including all six multi_file_* tests and both get_file_schema_attachment_* tests; worktree clean, exactly one commit over origin/dev 01613070.

**Intentionally not handled.** No type promotion across files; a `bigint` column in one file and
a `double` in another is an error, not a widened column. Non-parquet formats are still rejected.
No configuration or documentation changes; the only user-visible text is the error messages.

Self-contained fork PR against `dev`. The CN dispatch rewrite that follows moves this function
into its `ServiceCore`; landing the fix first means that PR only relocates an existing call.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Carved from the `aocsa/feat/pin-table-cn` umbrella (draft #1686, being closed and re-cut).
