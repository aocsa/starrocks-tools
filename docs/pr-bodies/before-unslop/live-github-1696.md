<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Why.** A StarRocks FE never hands a worker "read `lineitem.parquet`": it stats each file and
chops it into byte-range splits (bytes `0..512MB` to instance 0, `512MB..1GB` to instance 1, ...).
With one lineitem file and N GPUs, no splits means no parallel scan at all. A distributed split
scan is correct only if every reader of the same file derives the *same* owner for every row
group; otherwise rows are read twice or not at all, silently. So the one rule everything else
rests on is "which row groups belong to byte range `[start, start+length)`", and it has to match
what the StarRocks BE reader does (`be/src/formats/parquet/utils.cpp`), because that is the
convention the FE load-balances against. This PR is that rule, in isolation, so it can get a
reviewer's full attention on its own.

**What changed** (one reviewable unit, ~350 lines, pure function + tests):

- `src/include/op/scan/parquet_byte_range.hpp`, `src/op/scan/parquet_byte_range.cpp` —
  `sirius::op::scan::detail::row_group_start_offset(metadata, i)` and
  `row_groups_in_byte_range(metadata, start, length)`.
  - Start offset = min over the first column chunk's `data_page_offset`, `index_page_offset`,
    `dictionary_page_offset` and the row group's own `file_offset`, each counting only when
    present. cudf's thrift structs carry no `__isset`, so an offset of `0` is treated as absent
    (real offsets start after the 4-byte magic). A row group with no candidate at all throws
    `sirius::invalid_input_exception` — never a guess.
  - Ownership = start-offset containment: `start <= rg_start < start + length`. A row group
    straddling the range end belongs in full to the range holding its start (the byte range
    bounds ownership, not I/O); a range inside one row group owns nothing (a valid empty split);
    `length == 0` owns nothing. Together these make any exact tiling of a file read every row
    group exactly once.
- `test/cpp/scan/test_parquet_byte_range.cpp` — four Catch2 cases tagged `[parquet_byte_range]`:
  the StarRocks convention corners (dictionary page precedes data page, zero offsets are absent,
  `file_offset` participates, no-candidate throws); an exact-tiling sweep for k in {1,2,3,4,5,7,16}
  (pairwise disjoint, union complete); the straddle / inside-one-row-group / zero-length /
  boundary-on-a-start edge cases; and a real-footer check on
  `test/cpp/integration/data/parquet/lineitem.parquet` (located via `SIRIUS_PROJECT_ROOT`) that
  also cross-checks cudf's `hybrid_scan_reader::filter_row_groups_with_byte_range`. That
  cross-check is informational: our rule stays authoritative and a divergence only `WARN`s,
  since cudf does not pin its definition of a row group's start in its API docs.
- `CMakeLists.txt` — the two registration lines (`EXTENSION_SOURCES`, `TEST_SOURCES`).

**The rule is wired to nothing yet.** That is deliberate: this is layer 1 of the **scan** stack.
The next layer (`stacked/scan-byte-range-ingestible`) adds `resolved_file_ranges` to the parquet
ingestible and applies this rule right after `all_row_groups()` and before stats pruning, plus
the two `[parquet_byte_range][scan]` cases that drive the real provider/coalescer path. Above
that, the FFI layer carries `FileOrFiles.start/length` from the Substrait plan into the scan, and
the StarRocks CN stops refusing split scan ranges. Merging this alone changes no behavior.

Verified: GB200 aarch64 (CUDA 13.0 driver 580.105.08 / nvcc 13.2): baseline dev build exit 0; `pixi run make` incremental build at 94a77836 OK (410/410, no new warnings); `./build/release/extension/sirius/test/cpp/sirius_unittest "[parquet_byte_range]"` -> All tests passed (58 assertions in 4 test cases, 0 failed, 0 skipped); rumdl: no .md files in `git diff --name-only origin/dev` (nothing to check).

**Intentionally not handled here:** threading the rule into `parquet_gpu_ingestible` (next
layer); the pinned-table cache guard for ranged scans (next layer); Substrait byte-range
extraction and the S3-path refusal (ffi stack); the translator's `resolve_ranges` (CN side, must
land last — a CN that emits ranges into an engine that ignores them duplicates rows N times with
no error). No docs update: the rule is internal and unreachable until the next layer lands; the
scan-doc subsection comes with the layer that makes it observable.

## Checklist

- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References

- Supersedes #1636 (same files, re-based onto current `dev`; that draft is being closed).
- Refs #1635 (step 1 of the byte-range split plan).
- StarRocks BE reader convention this rule mirrors:
  https://github.com/StarRocks/starrocks/blob/main/be/src/formats/parquet/utils.cpp

