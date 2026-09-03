<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

A pinned parquet table only served a scan whose resolved file set equaled the pinned set. StarRocks hands each compute node a per-query subset of a table's files, so a whole-glob pin on each CN never matched and served nothing while still holding the memory. The plain DuckDB path has the same gap. Pin a glob, query two of its three files, and the read falls through to disk.

This PR makes a pin serve any strict subset of its files. It is one unit from pin time to serve time.

Pin time. `build_parquet_pin_info` turns on a new coalescer mode, `batch_within_file_boundaries`, so no pinned chunk bundles two files. `materialize_pin_batches` records each chunk's source files (canonicalized, sorted, deduplicated) and the three sinks push that vector unconditionally so it stays parallel to the chunks. The three `insert_pinned_entry*` paths store it as `pinned_entry::chunk_file_paths` and throw on a length mismatch. The GPU merge path adopts provenance an existing entry lacks and throws when the two disagree.

Serve time. `cache_entry_info::matches_parquet_file_set` classifies a scan as exact, strict subset, or miss. It is now the one parquet matcher behind both `can_serve_with_columns` and the plan-time residency gate; `matches_parquet_files` stays as an exact-only wrapper with no production caller. `try_match_cached_entry` selects the chunks whose provenance the scan covers and hands them to `build_cached_scan_plan` as `allowed_chunks`. I apply that restriction before zone-map pruning so the all-pruned sentinel can only pick an allowed chunk; a sentinel outside the set would return another file's rows. `find_pinned_entry_for_parquet_files` accepts a subset under the same provenance condition, so the plan-time residency gate and the serve path agree. The header doc on `can_serve_with_columns` spells out that a non-empty projection on the subset branch is necessary but not sufficient; the caller still has to check `chunk_file_paths`.

Subset matching requires duplicate-free canonical sets on both sides. A duplicated pinned path means that file's chunks were materialized twice, and serving them to a scan that names the file once would double its rows. Exact matching stays duplicate-symmetric, so existing pins behave as before. Entries without provenance (pins made before this change, duckdb-native pins) keep exact-only matching. Byte-range scans never serve or get served; the guard from #1700 in `can_serve_with_columns` stays above the new check.

Tests added, all Catch2:
- `test/cpp/scan/test_can_serve_with_columns.cpp`: `matches_parquet_file_set` over exact, subset, miss, superset, empty, duplicate poisoning, and a duckdb entry.
- `test/cpp/scan_manager/test_cached_serving_hardening.cpp`: `build_cached_scan_plan` with an allowed-chunks restriction (identity, out-of-range indices dropped, empty set); `insert_pinned_entry` provenance on all three insert paths (arity throws `std::invalid_argument`, merge adopts, merge mismatch throws `std::runtime_error` and leaves the entry intact), plus the residency gate refusing a subset probe until provenance exists.
- `test/cpp/scan/test_parquet_scan_sizing.cpp`: the coalescer bundles three files into one split by default and emits three single-file splits with `batch_within_file_boundaries`.
- `test/cpp/integration/test_pin_table_file_subset.cpp` (new, registered in `CMakeLists.txt`): pins a three-file glob on the gpu and host tiers, checks one file per chunk in the recorded provenance, then runs two-file, one-file, exact, superset, filtered, and selects-nothing scans against the unpinned read and asserts the `serves operator ... as a file subset` log line where a subset was served.

Verified 2026-09-03 on the GB200 (aarch64, CUDA 13.0, driver 580.105.08) at 6c0b83a over stacked/scan-byte-range-ingestible a22235e1, built in a worktree with `make release` (1414 steps, exit 0): sirius_unittest [can_serve] 13/13 cases (50 assertions), [cached_serving] 25/25 cases (283 assertions), [scan_manager] 81/81 cases (980 assertions), [scan] 284/284 cases (274720 assertions), [parquet_byte_range] 6/6 cases (84 assertions), [column_order] 1/1 cases (28 assertions), [pin_table][merge] 1/1 cases (42 assertions), [pin_table_host_streaming] 1/1 cases (21 assertions), [pin_table_mvcc] 14/14 cases (281 assertions), [pin_table_mvcc_insert] 25/25 cases (12399 assertions), [pin_table_mvcc_update] 9/9 cases (219 assertions), [pin_table_mvcc_delete] 16/16 cases (13829 assertions), [pin_table_type_drift] 5/5 cases (630 assertions), [pin_table_zone_map] 2/2 cases (211 assertions), and by name "gpu_execution - a pinned parquet glob serves scans over a subset of its files" 1/1 cases (124 assertions), each a separate process on CUDA_VISIBLE_DEVICES=3 (idle); pre-commit clean.

The source branch measured this on 2x RTX PRO 6000, SF100 lineitem, 2 CNs, q06 shape, at 0.63 s unpinned and 0.09 s pinned, byte-identical to the DuckDB oracle. I did not re-measure here.

Not handled. A superset scan is a miss. Duckdb-native pins record no provenance. A pin over many tiny files now yields at least one chunk per file. The FE lever (`files_query_whole_file_ranges`), the FFI `pin_table`, and the CN admin channel are later PRs in this series.

Layer 3 of the scan stack; base stacked/scan-byte-range-ingestible (#1700).

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Supersedes the file-subset part of #1686 (closed umbrella).
- Refs #1547, #1555 (merged neighbours in the same functions).
