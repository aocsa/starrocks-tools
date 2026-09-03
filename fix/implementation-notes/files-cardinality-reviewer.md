# files-cardinality: adversarial review notes (fix 3, PR-A + PR-B)

Worktree /home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality, branch fix/files-cardinality, commits
7f38171c (PR-A) and 00302c5c (PR-B) on 45dab3be. Working tree clean; submodule pointer 14b7e3fa662 unchanged;
Cargo.lock unchanged; no build outputs, telemetry or .pixi symlink committed. Nothing edited by the reviewer.

## What was re-run (all green)

- `fix/check-patches-clean.sh` (scratch repo seeded from `git show HEAD:<file>`): the four patches apply from a
  pristine tree in glob order, a second pass reports every one already applied, and all eight files are
  byte-identical to the worktree submodule (incl. the new FilesScanStatisticsTest.java and the proto).
- CN: `cargo fmt -- --check` clean; `cargo clippy --all-targets --no-default-features -- -D warnings` clean;
  `cargo test -p sirius-starrocks-cn --no-default-features`: 157 lib + 9 bin tests pass, incl.
  file_schema::{schema_only_file_reports_zero_rows, multi_file_num_rows_sums_footers} and
  compute_node_service::{get_file_schema_attachment_reports_num_rows, get_file_schema_route_returns_num_rows,
  get_file_schema_route_error_leaves_num_rows_unset, ..._infers_across_multiple_ranges}.
  Logs: files-cardinality-reviewer-{clippy,cntest}.log next to this file.
- Pre-commit on the ten changed files: all hooks pass.
- FE JUnit (TableFunctionTableTest, FilesScanStatisticsTest, StatisticsCalculatorTest) via the implementer's
  fe-ut.sh: see files-cardinality-reviewer-feut.log (result recorded at the bottom of this file).

## Source facts checked against the spec's claims

- `TableFunctionTable.getFileSchema()` sends `getGetFileSchemaRequest(fileStatuses)` = every listed file, so the
  CN's footer sum covers the whole scan set; `autoDetectSampleFiles` only travels as a param the Rust CN ignores.
  The exact-or-absent contract holds for the Sirius CN today.
- `columnsFromPath` is initialised to an empty list (`:185`) and `getSchemaFromPath()` appends exactly one STRING
  column per entry after the file columns, so the `size - columnsFromPath.size()` subtraction is right on the
  query path.
- `OperatorVisitor` has both `visitLogicalTableFunctionTableScan` (:143) and `visitPhysicalTableFunctionTableScan`
  (:454); the new override compiles and StatisticsCalculatorTest covers it.
- `isTableRowCountMayInaccurate` consumers (DeriveStatsTask MV-only, EnforceAndCostTask one-stage agg already
  blocked by unknown columns, StatisticsEstimateUtils returns on unknown, CostModel only under a skew info,
  DataSkew returns NOT_SKEWED / no candidates) are inert with unknown column statistics in both flag states.
- `VariableMgr:797` renders booleans with `Boolean.toString`, so the `tolower($2) == false` read-back is right;
  `SHOW GLOBAL VARIABLES` has Value in column 2; `ADMIN SHOW FRONTEND CONFIG` header has a `Value` column
  (ShowResultMetaFactory:793-796). `CTEContext.needInline` returns inline when `!enableCTE` (cbo_cte_reuse=false).
- run-abc.sh's engine B is a separate stock StarRocks 3.5.20 FE, so bench.sh's presence-based guard
  (`files_scan_estimate_row_count` exposed => patched FE) cannot leak cbo_cte_reuse=false into the B column.
- `num_rows` occurs exactly once in the patched proto (the added line), so a stock proto fails the build.rs guard.

## Findings (severity, file, summary)

1. MAJOR (landing order, user-decided): knob default `true` + no RIGHT_SEMI translator arm. The implementer's own
   FilesScanStatisticsTest proves the FE commutes LEFT SEMI to RIGHT SEMI when the outer side is smaller, which is
   q04's shape (orders 3.75e8 vs lineitem 3e9) and q20's; node_translator.rs has no RIGHT_SEMI_JOIN arm, so a CN
   fed by an FE built from this branch refuses two of the 15 passing queries until the fusion worktree's RIGHT_SEMI
   commit lands. DECISIONS.md items 1-2 accept this; the commit body says "needed before the knob is on in any
   sweep" while the code turns it on by default. Either keep the PR Draft until item 5 is in the CN build (say so
   in the PR body), or ship `false` here and flip it in the RIGHT_SEMI commit.
2. MINOR: `hasExactRowCount()` ignores the knob / listFilesOnly, so with the knob off the INFO line prints
   `rows=1 source=footer` and the javadoc ("true when estimateRowCount() returns the footer total") is false.
   Gate B reads `source=` per table; make it `Config.files_scan_estimate_row_count && !listFilesOnly &&
   footerRowCount > 0`, or print a third label.
3. MINOR: `getFileColumns()` assumes the path columns are trailing. `InsertAnalyzer.rewriteFileTableSchema` (full
   schema push-down for INSERT ... SELECT FROM FILES) rebuilds the schema from the SELECT list and can drop or
   reorder them, so on that path the subtraction can cut real file columns or return an empty list (width 0 ->
   1 row). No plan impact (scan -> sink; PR-B's footer total wins anyway); filter by name against `columnsFromPath`
   or state the invariant.
4. MINOR: build.rs `message_body` doc says `num_rows` "appears in several messages"; it appears once. The scoped
   check is fine, the justification is not true of the file.
5. MINOR: commit bodies are 28 / 21 lines against the 3-10 line house rule, and the "Tested:" paragraphs carry
   machine-log detail (157 lib tests, fe-build 269 s) the user's unslop rule asks to drop.
6. MINOR: bench.sh `frontend_config` swallows errors (`2>/dev/null`), so an FE that is patched but refuses
   ADMIN SHOW FRONTEND CONFIG is classified as stock and the CTE guard is skipped silently; the two sibling
   helpers are named `set_global_and_verify` (bench.sh) and `set_and_verify_global` (run-abc.sh); DEMO.md says the
   knob is on by default but not that CTE-reusing queries need `SET GLOBAL cbo_cte_reuse = false` outside the
   bench (TUNABLES.md has it).
7. MINOR (style): compute_node_service.rs spells `crate::file_schema::FilesSchema` fully qualified in the match
   pattern and return type while the file otherwise imports its crate types with `use`.

Not findings: the LogicalPlanPrinter gap and the CTE-named FILES() scans in FilesScanStatisticsTest are test-side
workarounds for upstream utility limits and are documented in the test; `FilesSchema.files` is the spec's
permitted variant; the unbounded RPC wait is pre-existing and out of scope.
