<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Why.** A StarRocks FE never hands a worker "read `lineitem.parquet`". It stats each file and
chops it into byte-range splits, so bytes `0..512MB` go to instance 0, `512MB..1GB` to
instance 1, and so on. With one lineitem file and N GPUs, no splits means no parallel scan at
all. A distributed split scan is only correct if every reader of the same file derives the
*same* owner for every row group. Otherwise two readers read the same row, or nobody does, and
nothing reports it. So the one rule everything else rests on is "which row groups belong to
byte range `[start, start+length)`". It has to match what the StarRocks BE reader does
(`be/src/formats/parquet/utils.cpp`), because that is the convention the FE load-balances
against. This PR is that rule by itself, so a reviewer can take it on its own.

**What changed.** One reviewable unit, about 350 lines, a pure function plus tests.

- `src/include/op/scan/parquet_byte_range.hpp` and `src/op/scan/parquet_byte_range.cpp` add
  `sirius::op::scan::detail::row_group_start_offset(metadata, i)` and
  `row_groups_in_byte_range(metadata, start, length)`.
  - The start offset is the minimum over the first column chunk's `data_page_offset`,
    `index_page_offset`, `dictionary_page_offset` and the row group's own `file_offset`. Each
    candidate counts only when present. cudf's thrift structs carry no `__isset`, so I treat an
    offset of `0` as absent; real offsets start after the 4-byte magic. A row group with no
    candidate at all throws `sirius::invalid_input_exception`. I would rather it throw than
    guess.
  - Ownership is start-offset containment, `start <= rg_start < start + length`. A row group
    that straddles the range end belongs in full to the range holding its start. The byte range
    bounds ownership, not I/O. A range that sits inside one row group owns nothing, which is a
    valid empty split, and `length == 0` owns nothing. Together these make any exact tiling of
    a file read every row group exactly once.
- `test/cpp/scan/test_parquet_byte_range.cpp` adds four Catch2 cases tagged
  `[parquet_byte_range]`. One case walks the StarRocks convention corners: a dictionary page
  before the data page, zero offsets read as absent, `file_offset` participating, and no
  candidate throwing. A tiling sweep runs k in {1,2,3,4,5,7,16} and checks the owned sets are
  pairwise disjoint and their union complete. An edge-case set covers a straddling row group, a
  range inside one row group, zero length, and a boundary that lands exactly on a start. A
  real-footer check reads `test/cpp/integration/data/parquet/lineitem.parquet`, found through
  `SIRIUS_PROJECT_ROOT`, and also cross-checks cudf's
  `hybrid_scan_reader::filter_row_groups_with_byte_range`. That cross-check is informational.
  Our rule stays authoritative and a divergence only `WARN`s, because cudf does not pin its
  definition of a row group's start in its API docs.
- `CMakeLists.txt` gets the two registration lines, `EXTENSION_SOURCES` and `TEST_SOURCES`.

Nothing calls the rule yet. That is deliberate. This is layer 1 of the scan stack. The next
layer (`stacked/scan-byte-range-ingestible`) adds `resolved_file_ranges` to the parquet
ingestible and applies this rule right after `all_row_groups()` and before stats pruning, plus
the two `[parquet_byte_range][scan]` cases that drive the real provider/coalescer path. Above
that, the FFI layer carries `FileOrFiles.start/length` from the Substrait plan into the scan,
and the StarRocks CN stops refusing split scan ranges. Merging this alone changes no behavior.

Verified: GB200 aarch64 (CUDA 13.0 driver 580.105.08 / nvcc 13.2): baseline dev build exit 0; `pixi run make` incremental build at 94a77836 OK (410/410, no new warnings); `./build/release/extension/sirius/test/cpp/sirius_unittest "[parquet_byte_range]"` -> All tests passed (58 assertions in 4 test cases, 0 failed, 0 skipped); rumdl: no .md files in `git diff --name-only origin/dev` (nothing to check).

**Intentionally not handled here.** Threading the rule into `parquet_gpu_ingestible` and the
pinned-table cache guard for ranged scans both come in the next layer. Substrait byte-range
extraction and the S3-path refusal belong to the ffi stack. The translator's `resolve_ranges`
is CN side and must land last, because a CN that emits ranges into an engine that ignores them
duplicates rows N times with no error. No docs update either. The rule is internal and
unreachable until the next layer lands, so the scan-doc subsection comes with the layer that
makes it observable.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- Supersedes #1636. Same files, rebased onto current `dev`; I am closing that draft.
- Refs #1635, step 1 of the byte-range split plan.
- StarRocks BE reader convention this rule mirrors:
  https://github.com/StarRocks/starrocks/blob/main/be/src/formats/parquet/utils.cpp
