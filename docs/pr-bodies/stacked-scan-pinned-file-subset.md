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

**How I tested it.** On a GB200 box (aarch64) I did a full release build in a worktree, then ran the scan, cache and pin_table Catch2 suites on a GPU, plus the new end-to-end test. Everything passes and pre-commit is clean. These are GPU tests that CI does not run.

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
