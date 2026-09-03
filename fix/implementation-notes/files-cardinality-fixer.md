# files-cardinality: review-fix notes (fix 3, follow-up commit on PR-A + PR-B)

Worktree /home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality, branch fix/files-cardinality. Base for this
pass: 00302c5c (PR-B) on 7f38171c (PR-A). Existing commits untouched; one follow-up commit added. Submodule pointer
unchanged (14b7e3fa662); Cargo.lock unchanged; no build outputs committed.

## Findings and what was done

1. MAJOR landing order (knob default true, translator has no RIGHT_SEMI_JOIN arm). DECISIONS.md item 1 fixes the
   default at TRUE and item 2 places the translator arm in the fusion worktree, so the default is NOT flipped here.
   The dependency is now stated where a reader of the knob lands: docs/TUNABLES.md "Front end" section (new
   "Landing order" paragraph naming q04/q20 and the `hash join type is unsupported` refusal, with "run the knob
   false against such a CN"), DEMO.md (same, plus the cbo_cte_reuse sentence), and the follow-up commit body.
   FOR THE PR BODY (orchestrator): keep the PR Draft until the fusion worktree's RIGHT_SEMI commit is in the CN
   build, and say "depends on the translator RIGHT_SEMI_JOIN commit; until then run with
   files_scan_estimate_row_count=false".
2. MINOR hasExactRowCount() ignored the knob / listFilesOnly: new private `rowCountEstimated()` (knob && !listFilesOnly
   && files listed) gates both `hasExactRowCount()` and `estimateRowCount()`; the INFO line prints a third label
   via `rowCountSource()`: `source=footer | estimated | disabled`. Test: testFooterRowCountPreferred gains a
   knob-off case with numRows=1000 -> estimateRowCount()==1, hasExactRowCount()==false, back to 1000 after.
3. MINOR getFileColumns() trailing-position assumption: now filters getFullSchema() by name against columnsFromPath
   (case-insensitive TreeSet, the comparison checkDuplicateColumns uses). Test: testEstimateRowCountFromFileBytes
   rewrites the schema to [col_path1, col_string, col_int] via setNewFullSchema (path column first, two dropped, the
   InsertAnalyzer.rewriteFileTableSchema shape) and asserts 2 file columns / 153 rows (the old rule gave 0 columns).
4. MINOR build.rs message_body doc: reworded to "stays correct if another message ever gains a field of the same
   name"; no claim that num_rows appears elsewhere.
5. MINOR bench.sh/run-abc.sh frontend_config: captures the statement's exit status; a failed ADMIN SHOW FRONTEND
   CONFIG returns 1 with the error on stderr and the caller aborts (bench.sh `|| exit 1`; run-abc.sh warns and
   returns 1 from the engine-A bring-up). Empty output with status 0 still means "stock FE". The awk header
   detection no longer assumes the header is line 1 (a stray mysql warning line is skipped). Helper renamed to
   set_and_verify_global in bench.sh to match run-abc.sh (and its pre-existing set_and_verify_pipeline). DEMO.md
   gained the cbo_cte_reuse sentence.
6. MINOR style: `use crate::file_schema::{self, FilesSchema};` in compute_node_service.rs; the three fully qualified
   spellings are gone.
7. MINOR commit bodies (28 / 21 lines, machine-log detail): existing commits not amended (house rule). The follow-up
   commit body is 3-10 lines with one plain testing sentence. Suggested trimmed bodies for the orchestrator if the
   commits are rewritten at PR time are at the bottom of this file.

## Verification

- FE patch regenerated with fix/gen-patches.sh (677 lines); fix/check-patches-clean.sh: four patches apply from a
  pristine tree in glob order, second pass all "already applied", eight files byte-identical to the worktree;
  scripts/apply-starrocks-patches.sh on the worktree: all already applied.
- bash -n on bench.sh and run-abc.sh; awk parser exercised by hand (warning line + header + row -> value; empty ->
  empty). shellcheck is not installed on this box.
- CN: cargo fmt --check clean; clippy --all-targets --no-default-features -D warnings clean
  (files-cardinality-fixer-clippy.log); cargo test -p sirius-starrocks-cn --no-default-features: see
  files-cardinality-fixer-cntest.log (summary appended below).
- pre-commit --files on the seven changed files: every hook reports "no files to check" (experimental/ is outside
  the hook filters), i.e. passes.
- FE JUnit TableFunctionTableTest, StatisticsCalculatorTest, FilesScanStatisticsTest: files-cardinality-fixer-feut.log
  (result appended below).
- fe-build: appended below.

## Results

- CN cargo test: 157 lib + 9 bin pass, rc 0; clippy rc 0; fmt clean.
- FE JUnit: Tests run: 32, Failures: 0, Errors: 0, BUILD SUCCESS (fe-ut exit 0, 18:54:50 UTC).
- Follow-up commit: cde7ab223f4fc6218a3d12669cba54620e33dfb5 fix(starrocks-fe): gate the footer flag on the knob and match path columns by name.
- fe-build rc=0 in 251 s (files-cardinality-fixer-febuild.log); starrocks/output/fe refreshed.

## Suggested trimmed commit bodies (orchestrator only; existing commits were not amended)

7f38171c: FILES() scans were planned at 1 row (22/22 TPC-H plans, 126 exchanges costed from column widths). StatisticsCalculator now takes the count from TableFunctionTable: footer total when the CN reports one, else bytes / typed row width (the Hive no-statistics rule); column statistics stay unknown. Mutable FE config files_scan_estimate_row_count (default true) restores the 1-row plans for an EXPLAIN COSTS A/B. Ships as patches/files-scan-row-count.patch; bench scripts set and read back cbo_cte_reuse=false. Tested with three FE JUnit classes and a clean-tree patch apply. Depends on the translator RIGHT_SEMI_JOIN commit before the knob is on in any sweep; does not make the six 1-CN failures pass.

00302c5c: The CN already opens every parquet footer for the FILES() schema; it now returns the summed num_rows in the optional proto2 field PGetFileSchemaResult.num_rows = 3 (exact-or-absent; a stock FE ignores it, a stock BE never sets it). The FE prefers it over the bytes / width estimate, so leaf cardinalities are exact and dataset-copy independent. build.rs gains a second patch guard; CI applies every patches/*.patch. Tested with the CN cargo tests, clippy, fmt and the FE footer JUnit case.
