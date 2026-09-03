<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Why.** A StarRocks FE hands a compute node a byte range of a parquet file, not the file: bytes
`0..512MB` to instance 0, `512MB..1GB` to instance 1, and so on. For that to be correct, a
distributed split scan must read exactly the row groups whose start offset falls inside its
range — no more (rows duplicated across instances) and no less (rows lost) — and every instance
must derive the same answer from the same footer. The previous layer of this stack landed that
rule as a pure function (`row_groups_in_byte_range`), wired to nothing. This PR threads it into
the real scan path: the parquet ingestible learns a per-file byte range and applies the rule
when it builds each file's scan info. A `(0,0)` range means "the whole file", so every existing
plan is byte-identical to before.

One cache fact falls out of this and is handled here rather than left as a trap. A ranged scan
holds a fraction of each file's rows, so the pinned-table cache must not match it on the file
set alone: a whole-file pin serving a ranged scan would return extra rows, and a pin built from
a ranged scan serving a whole-file scan would return missing rows. Both directions are now a
clean cache miss.

**What changed** (one reviewable unit, ~240 lines):

- `src/include/op/scan/parquet_gpu_ingestible.hpp` — `parquet_ingestible_table_info` gains
  `std::vector<std::pair<uint64_t, uint64_t>> resolved_file_ranges` (parallel to
  `resolved_file_paths`; empty = every file whole; a `(0,0)` entry = that file whole) and the
  `has_byte_ranges()` predicate (true when any entry is a real range). `build_file_scan_info`
  takes the file's range.
- `src/op/scan/parquet_gpu_ingestible.cpp` —
  - the constructor refuses a partial pairing (`resolved_file_ranges` non-empty but not the same
    length as `resolved_file_paths`) with `sirius::invalid_input_exception`: a half-paired plan
    would make row-group ownership ambiguous, and the failure mode of guessing is silent
    duplication;
  - `next_split_provider` looks up the file's range (default `(0,0)`) and captures it in the
    per-file metadata task;
  - `build_file_scan_info` applies `row_groups_in_byte_range` right after
    `reader.all_row_groups(opts)` and **before** stats pruning, so a range and a filter compose
    rather than fight. An empty selection (a range that starts and ends inside one row group) is
    a valid empty split and flows through the existing all-pruned fallback (`set_num_rows(0)`) —
    never a whole-file read.
- `src/include/scan_manager/sirius_scan_manager.hpp`, `src/scan_manager/sirius_scan_manager.cpp` —
  `cache_entry_info::has_byte_ranges`, recorded in `cache_entry_info::from`, and one guard at the
  top of the parquet branch of `can_serve_with_columns`: `if (has_byte_ranges ||
  p->has_byte_ranges()) return {};`. The existing `matches_parquet_files` line is untouched.
  These two hunks are clear of the edits #1547 and #1555 made to this file.
- Tests:
  - `test/cpp/scan/test_parquet_byte_range.cpp` — two new cases tagged `[parquet_byte_range][scan]`
    drive the real provider → coalescer path on a generated 50k-row / 10-row-group parquet: a
    two-way split selects disjoint row groups whose row counts sum to the file; `(0,0)` selects
    the same row groups as no range at all; a range inside one row group is a valid empty scan;
    a mismatched range/path pairing is refused at construction. (Also re-adds the six includes
    the previous layer trimmed because only this half uses them.)
  - `test/cpp/scan/test_can_serve_with_columns.cpp` — one new `[scan][can_serve]` case: a
    whole-file pin cannot serve a ranged scan; `cache_entry_info::from` records the fact and the
    resulting ranged pin cannot serve a whole-file scan, while the same pin built from a
    whole-file scan does (so the miss is the range, not the file set); a `(0,0)` entry still
    hits.
- No `CMakeLists.txt` change: both test files are already registered.

**Still inert in production.** Nothing populates `resolved_file_ranges` yet — the FFI layer that
extracts `FileOrFiles.start/length` from the Substrait plan and installs them on the scan is the
next layer (`stacked/ffi-substrait-byte-ranges`, on the ffi stack), and the StarRocks translator
stops refusing split scan ranges only after that has merged. Merging this alone changes no
observable behavior.

Verified: GB200 aarch64 (CUDA 13.0, driver 580.105.08) @ a22235e10d2d4f1824db0b51539b9cab4ff8bc98 -- incremental `pixi run make` OK (466/466 ninja steps, exit 0); `sirius_unittest "[parquet_byte_range]"` 6/6 cases passed (84 assertions); `sirius_unittest "[can_serve]"` 12/12 passed (39 assertions); `sirius_unittest "[scan]"` 282/282 passed (274695 assertions); all on CUDA_VISIBLE_DEVICES=0 (idle).

**Intentionally not handled here:** the pin-table file-subset serve path (`chunk_file_paths`
provenance, `matches_parquet_file_set`, `allowed_chunks`, the coalescer's file-boundary mode) —
that is the next layer of this stack (`stacked/scan-pinned-file-subset`); Substrait byte-range
extraction (ffi stack); the translator's `resolve_ranges` (CN side, must land last — a CN that
emits ranges into an engine that ignores them duplicates rows N times with no error). No docs
update: the range is unreachable from SQL until the FFI layer lands; the scan-doc subsection
comes with the layer that makes it observable.

Layer 2 of the **scan** stack, on top of `stacked/scan-byte-range-rule` (`94a77836`); this PR's diff is against that layer, which is its own PR in the stack.

- Base `94a77836` — `feat(scan): a deterministic byte-range -> row-group ownership rule`:
  `sirius::op::scan::detail::row_group_start_offset` / `row_groups_in_byte_range` in
  `parquet_byte_range.{hpp,cpp}`, mirroring the StarRocks BE reader's start-offset convention
  (min of the first column chunk's page offsets and the row group's `file_offset`, each only when
  present; ownership = start containment);
- four `[parquet_byte_range]` Catch2 cases (convention corners, exact-tiling sweep, edge cases,
  real-footer cross-check against cudf) and the two `CMakeLists.txt` registration lines;
- wired to nothing on its own.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- Supersedes #1637 (same change, re-carved onto the `stacked/` scan stack with the new
  `has_byte_ranges` coverage; that draft is being closed).
- Refs #1635 (step 2 of the byte-range split plan); #1636 (step 1, superseded by the base
  commit of this stack).
- Refs #1547 (column-aware pinned-entry lookup) and #1555 (per-chunk MVCC gating) — the two
  merged changes to `sirius_scan_manager.cpp` this PR's hunks sit beside without touching.
