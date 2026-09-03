# files-cardinality: implementer notes (fix 3, PR-A + PR-B)

Worktree: /home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality (branch fix/files-cardinality off 45dab3be).
Spec: scratchpad/fix/designs/files-cardinality-SPEC.md; decisions: DECISIONS.md (knob default TRUE, PR-A and PR-B both,
RIGHT_SEMI translator arm NOT here).

## Working method

1. Submodule: `git -C experimental/starrocks/starrocks commit -m "tmp: existing sirius patches"` (local only) so
   `git diff` yields only this fix's FE change; removed with `git reset --soft HEAD~1 && git reset -q` before the
   patch is regenerated and the submodule pointer checked (must stay 14b7e3fa662).
2. Tests first (FE JUnit x3 classes, CN Rust x3), then code, then build/lint.

## Deviations from the spec (code said otherwise)

- 17:10 The spec's patch-generation recipe (temporary commit in the submodule, then soft reset) was refused by the
  permission classifier. Replacement: pre-fix copies of the two files that already carry patch hunks
  (`Config.java`, `internal_service.proto`) saved under scratchpad `fix/files-cardinality-baseline/`; the new
  patches are `git diff` (files with no earlier hunk) + `diff -u --label a/.. --label b/..` against those copies
  (`fix/gen-patches.sh`). Same result, no history rewrite anywhere.
- bench.sh: the spec puts the `SET GLOBAL cbo_cte_reuse` right after `MYSQL=` (:85), which runs before
  `wait_alive`; a SET there fails on a still-booting FE. Placed after the "cluster: ..." line instead, and guarded
  on the FE exposing `files_scan_estimate_row_count` (a stock engine-B FE keeps its own CTE default; the refusal
  the SET works around is a Sirius-CN limitation).
- run-abc.sh: read-backs added to engine A's bring-up only (engine B is stock StarRocks).
- `FilesSchema` gained `files: usize` (the spec left the span's `files` source open); the handler records both
  span fields and one INFO line with `elapsed_ms`.
- Rust: `PSlotDescriptor` import in `compute_node_service.rs` became unused (handler returns `FilesSchema`);
  removed so `clippy -D warnings` stays green.

## Log
- 17:14 cargo test --no-run before the code: 20 errors (no `FilesSchema`, no `num_rows`) = expected failure.
- 17:17 FE UT before the code: testCompile fails on `getTotalFileBytes()` etc. = expected failure (2.5 min run).
- 17:17 cargo test --no-default-features with the code: 157 lib tests pass incl. the 7 new/extended
  get_file_schema / file_schema tests.
- 17:19 clippy -D warnings green after dropping the unused `PSlotDescriptor` import; cargo fmt needed one import
  reflow (applied); 157 lib tests + 9 bin tests pass.
- 17:36 FE UT run 1 (three classes, ~3 min compile + tests): TableFunctionTableTest 16/16 pass (incl. the three
  new ones: bytes/width 153, list_files_only 1, footer 1000/768/768/768). FilesScanStatisticsTest failed in the
  analyzer, not in statistics: `files(...) a join files(...) b on a.col_int = ...` -> "Column a.col_int cannot be
  resolved" (FileTableFunctionRelation is built with the fixed name table_function_table; the bench never aliases
  FILES() directly, it wraps each scan in a CTE). Test rewritten to the bench idiom `with a as (select * from
  files(...)) ...`. StatisticsCalculatorTest never ran: its MockedFrontend fork timed out in waitForCatalogReady
  (600 s, `fe start failed`, EditLog NPE) while two other forks started their own FE; rerunning with two classes.
- Patch generation verified in a scratch repo seeded from `git show HEAD:<file>`: all four patches apply from clean
  in glob order (files-query-whole-file-ranges, files-scan-row-count, files-schema-row-count-proto,
  nixl-exchange-proto), a second pass reports every one already applied, and the results are byte-identical to
  the worktree (`fix/check-patches-clean.sh`).
- 17:41 FE UT run 2: StatisticsCalculatorTest 14/14 pass (incl. testTableFunctionTableScanRowCount: 153 rows, unknown
  columns, inaccurate flag, computeSize 3060, limit 100, physical 153, knob off -> 1). The FE-start timeout of run 1
  did not recur (environmental: three forks starting mocked FEs at once).
- FilesScanStatisticsTest needed three test-side fixes, none in the FE code under test (spec 5.1.5 assumed fake://
  FILES() statements are *planned* in UTs; they are only *analyzed* there):
  1. `HdfsUtil.getTProperties` mocked to a no-op: building the physical FileScanNode asks HDFS for the fake:// path's
     properties ("No FileSystem for scheme fake").
  2. Plan through `StatementPlanner.plan` + `ExecPlan.getExplainString(COSTS)` instead of `getCostExplain()`:
     `UtFrameUtils.getPlanAndFragment` also prints the optimizer tree with `LogicalPlanPrinter`, which has no arm for
     `PhysicalTableFunctionTableScanOperator` and recurses to StackOverflowError (upstream test-utility gap; not fixed
     here, out of scope).
  3. COSTS prints the sink as `OutPut Exchange Id: 02`, not `EXCHANGE ID: 02`.
- 18:05 FilesScanStatisticsTest 2/2 pass: (a) `cardinality: 153` on both FileScanNodes, no `cardinality: 1` node;
  (b) with injected 1e9 vs 10 rows: `INNER JOIN (BROADCAST)`, the 10-row scan under the build-side EXCHANGE, the
  1e9 scan as probe (join est. 2.5e8 = max(L,R) x 0.25, the >= 1e7-gap rule); and the LEFT SEMI statement is
  commuted by the FE to `RIGHT SEMI JOIN` -> plan item 5 (translator RIGHT_SEMI arm, fusion worktree) is a hard
  prerequisite for running with the knob on, as the spec predicted (risk 8.2).
