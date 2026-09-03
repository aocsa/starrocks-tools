<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 2 of the scan stack. The base is `stacked/scan-byte-range-rule` (#1696), which provides `parquet_byte_range.hpp` and `detail::row_groups_in_byte_range`. This layer teaches the parquet ingestible and the scan cache to honor a per-file byte range. Nothing in this PR populates the range, so with this PR alone every scan still reads whole files and the cache behaves as before.

`parquet_ingestible_table_info` gains `resolved_file_ranges`, a `std::vector<std::pair<std::uint64_t, std::uint64_t>>` parallel to `resolved_file_paths`. Each entry is `(start, length)`. An empty vector means every file is read whole. A `(0,0)` entry means that one file is read whole. `has_byte_ranges()` is true when any entry is something other than `(0,0)`.

The `parquet_gpu_ingestible` constructor refuses a partial pairing. If `resolved_file_ranges` is non-empty and its size differs from `resolved_file_paths`, it throws `sirius::invalid_input_exception` with the message "parquet scan carries {} byte ranges for {} files; a partial pairing would make row-group ownership ambiguous". I would rather fail at construction than guess which file a range belongs to.

**Row-group selection.** `next_split_provider` looks up the file's range by index and passes it to `build_file_scan_info`, which takes a new `byte_range` parameter. When the range is not `(0,0)`, `build_file_scan_info` replaces the result of `reader.all_row_groups(opts)` with `detail::row_groups_in_byte_range(metadata, start, length)`. Only row groups whose start offset falls inside `[start, start+length)` survive. This happens before stats pruning, so filter pushdown then prunes within the owned set. A range that owns no row group is a valid empty split. It flows through the existing all-pruned fallback and never turns into a whole-file read. A `SIRIUS_LOG_DEBUG` line reports how many row groups each range owns.

**Cache.** `cache_entry_info` gains `bool has_byte_ranges`, which `cache_entry_info::from` copies from the table info. `can_serve_with_columns` returns a miss when either side has byte ranges, before it compares file sets at all. The cache identifies a parquet scan by its file set, and a ranged scan holds only a fraction of each file's rows. A whole-file pin would hand a ranged scan extra rows. A pin built from a ranged scan would hand a whole-file scan missing rows. Missing in both directions is the simple safe answer, and I did not try to key the cache on ranges.

Two tests live in `test/cpp/scan/test_parquet_byte_range.cpp`, tagged `[parquet_byte_range][scan]`.

- "a two-way split scan selects disjoint, complete row groups" writes a one-column BIGINT file of 50000 rows at 5000 rows per row group. cudf clamps `row_group_size_rows` to a 5000-row floor, so that is 10 real row groups. For each half of the file it drives the production path: `make_ingestible`, then `next_split_provider`, then `create_batch_coalescer` push and flush, with a `kvikio_context` as the io context. The row-group indices come out of `parquet_split_info::rg_slices`. Row counts come from the footer metadata, no decode. It asserts both halves are non-empty and disjoint and that their rows sum to 50000. A `(0,0)` range yields all 50000 rows and the same row-group count as the two halves together. The range `(10, 5)`, which sits inside one row group, yields zero row groups and zero rows.
- "mismatched range/path pairing is refused at construction" passes two paths and one range to `make_ingestible` and expects `sirius::invalid_input_exception`.

One more test lands in `test/cpp/scan/test_can_serve_with_columns.cpp` under `[scan][can_serve]`.

- "cache_entry_info: a byte-range split scan neither serves nor is served by a pin" checks four things. A whole-file pin does not serve a scan with range `(0, 4096)`. A pin built from that ranged scan does not serve a whole-file scan. A control pin built from the whole-file scan does serve it with projection `{0}`, so the miss comes from the range and not the file set. A `(0,0)` entry still hits.

Run them with:

```
pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[parquet_byte_range]"
pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[can_serve]"
```

Verified: GB200 aarch64 (CUDA 13.0, driver 580.105.08) at a22235e1 on CUDA_VISIBLE_DEVICES=0 (idle): incremental `pixi run make` OK (466/466 ninja steps, exit 0); `sirius_unittest "[parquet_byte_range]"` 6/6 cases passed (84 assertions); `sirius_unittest "[can_serve]"` 12/12 passed (39 assertions); `sirius_unittest "[scan]"` 282/282 passed (274695 assertions).

**Intentionally not handled here.** The pin-table file-subset serve path (`chunk_file_paths` provenance, `matches_parquet_file_set`, `allowed_chunks`, the coalescer file-boundary mode) is the next scan layer. Carrying `FileOrFiles.start/length` from the Substrait plan into `resolved_file_ranges` is the ffi stack. Until that lands, this PR changes no behavior on its own.

What I want eyes on: the ordering in `build_file_scan_info`, range selection first and stats pruning second, and whether a blanket cache miss for any ranged scan is the right call for now.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Supersedes #1637 (the same change, re-carved onto the scan stack with the new `has_byte_ranges` coverage; that draft is closed).
- Refs #1635, step 2 of the byte-range split plan. #1636 was step 1 and is superseded by the base of this stack, #1696.
- Refs #1547 (column-aware pinned-entry lookup) and #1555 (per-chunk MVCC gating), the two merged changes to `sirius_scan_manager.cpp` that this PR's hunks sit beside without touching.
