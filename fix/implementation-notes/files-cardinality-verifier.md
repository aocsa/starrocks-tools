# files-cardinality: independent verification notes (fix 3, PR-A + PR-B + review-fix commit)

Worktree /home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality, branch fix/files-cardinality.
Base perf/profile-sf1000 = 45dab3be; HEAD = cde7ab22. Three commits: 7f38171c (FE PR-A), 00302c5c (CN PR-B),
cde7ab22 (review fix). Nothing edited, nothing committed, nothing pushed by the verifier. Logs next to this file
are prefixed files-cardinality-verifier-*.

## Starting state

- `git status --short` in the worktree: empty (clean). The .pixi symlink is untracked-ignored and does not show.
- Submodule experimental/starrocks/starrocks at 14b7e3fa662 (unchanged vs base; `git diff base..HEAD -- <submodule>`
  empty). Working tree of the submodule carries the four patches (7 M + 1 ?? FilesScanStatisticsTest.java).
- Layers touched: FE (Java, shipped as patches/*.patch), CN (Rust: build.rs, src/compute_node_service.rs,
  src/file_schema.rs), CI workflow, bench scripts, docs. No src/ (engine) change, so `make release` was not
  needed and no Catch2 tag applies; the engine .so from 16:00 is what the CN release link consumed.

## Commits

| commit | Conventional title | trailer exact | non-blank body lines | files |
|---|---|---|---|---|
| 7f38171c | feat(starrocks-fe): ... (OK) | 1 | 21 | DEMO.md, bench.sh, run-abc.sh, TUNABLES.md, +files-scan-row-count.patch |
| 00302c5c | feat(starrocks-cn): ... (OK) | 1 | 15 | experimental.yml, DEMO.md, build.rs, patch (regen), +proto patch, compute_node_service.rs, file_schema.rs |
| cde7ab22 | fix(starrocks-fe): ... (OK) | 1 | 10 | DEMO.md, bench.sh, run-abc.sh, build.rs, TUNABLES.md, patch, compute_node_service.rs |

- No build outputs, telemetry, generated YAML, target/, output/, Cargo.lock or .pixi in any commit
  (`git ls-files experimental/starrocks/.pixi experimental/starrocks/target experimental/starrocks/starrocks/output`
  empty; the only .yml is the hand-edited CI workflow).
- Deviation (not fixable here, house rule forbids amending): bodies of 7f38171c / 00302c5c are 21 / 15 non-blank
  lines against the 3-10 line rule. The fixer's notes carry trimmed replacements for the orchestrator.

## Patch set

- verifier-check-patches-clean.sh (own scratch repo seeded from `git show HEAD:<file>`): pass 1 applied all four
  patches in glob order, pass 2 reported all four "already applied", all eight files byte-identical to the worktree
  submodule (incl. FilesScanStatisticsTest.java and internal_service.proto). Log: ...-patches-clean.log.
- scripts/apply-starrocks-patches.sh on the real worktree submodule: four x "already applied", rc=0; submodule
  status and pointer unchanged afterwards.

## CN (cached run, 19:04 UTC, all rc=0)

- cargo fmt -- --check: rc=0; cargo fmt --all -- --check: rc=0.
- cargo clippy --all-targets --no-default-features -- -D warnings: rc=0.
- cargo test --workspace --no-default-features: 157 lib + 9 bin (sirius-starrocks-cn), 18 lib + 136 translate
  (starrocks-plan-translator), 0 (starrocks-thrift); 0 failed. The six spec-named tests ran ok:
  file_schema::{schema_only_file_reports_zero_rows, multi_file_num_rows_sums_footers},
  compute_node_service::{get_file_schema_attachment_reports_num_rows, get_file_schema_route_returns_num_rows,
  get_file_schema_route_error_leaves_num_rows_unset, get_file_schema_attachment_infers_across_multiple_ranges}.
- cargo test -p sirius-starrocks-cn --no-default-features: 157 + 9 pass.
- cargo build --release (engine-linked, default features): Finished release in 15.48 s; target/release/sirius-starrocks-cn
  rebuilt 19:04.
- One rustc dead_code warning in the translator's `translate` test target
  (`sort_over_a_carried_common_slot_resolves_and_narrows`): file not touched by this fix (see below for pre-existence).

## Fresh CN rebuild (cargo clean -p sirius-starrocks-cn, then clippy / test / build --release)

- cargo clean -p sirius-starrocks-cn: rc=0 (dev profile). clippy --all-targets --no-default-features -- -D warnings:
  "Compiling sirius-starrocks-cn", Finished dev in 10.03 s, rc=0 (build.rs re-ran with both proto guards).
- cargo test --workspace --no-default-features: recompiled, 157 + 9 + 18 + 136 + 0 passed, 0 failed, rc=0; the six
  spec-named tests ok (6/6).
- cargo clean --release -p sirius-starrocks-cn, then cargo build --release (engine-linked): "Compiling
  sirius-starrocks-cn", Finished release in 17.61 s, rc=0; target/release/sirius-starrocks-cn relinked.
- The dead_code warning (`sort_over_a_carried_common_slot_resolves_and_narrows`, crates/starrocks-plan-translator/
  tests/translate.rs:6144) is pre-existing: same line in parked-bookkeeping-verifier-test-noengine.log from another
  worktree earlier today, the file last changed in d24f02c4 on the base, and the fix touches no crates/ file.

## FE

- JUnit via run-fe-ut.sh --test (three classes in one run, pixi fe env, STARROCKS_THIRDPARTY set as fe-build does):
  TableFunctionTableTest 16/16, FilesScanStatisticsTest 2/2, StatisticsCalculatorTest 14/14; "Tests run: 32,
  Failures: 0, Errors: 0, Skipped: 0"; BUILD SUCCESS; fe-ut rc=0 at 19:08:18 (started 19:04:14). Log: ...-feut.log.
- `pixi run -e fe fe-build` returned rc=0 in 1 s: "Task 'fe-build' can be skipped (cache hit)" -- pixi's task
  input/output cache saw no change since the fixer's 19:00 build. That is not a build, so the task body was run
  directly (verifier-fe-build-body.sh, same commands, same env) with the cache bypassed; result below.
- Direct fe-build: Maven "BUILD SUCCESS", "Successfully build StarRocks √ Frontend ... TotalTime:256s"
  (19:09:58-19:14:14), rc=0. Log: ...-febuild-direct.log. output/fe/lib/fe-core-4.1.1.jar refreshed at 19:14:07 and
  its classes carry the new symbols: TableFunctionTable {estimateRowCount, getTotalFileBytes, footerRowCount,
  hasExactRowCount, rowCountSource, numRows, files_scan_estimate_row_count}, Config.files_scan_estimate_row_count,
  generated PGetFileSchemaResult.numRows. fe.conf copied. gen_notice.py restored by the task's trap (submodule
  status identical before/after: 7 M + 1 ??; pointer 14b7e3fa662).
- fe-core checkstyle in the JUnit run: 0 violations.

## End state

- Worktree `git status --short` empty; HEAD cde7ab22 unchanged; no commits, amends, stashes, pushes or config changes.
- No FE / CN cluster started, no SF1000 queries run (orchestrator's arms). Gate A / Gate B untouched.

## Verdict

build_ok = true (CN release link fresh; FE packaged fresh), tests_ok = true (32 FE JUnit, 320 CN/translator Rust
tests, patch set clean/idempotent/byte-identical), lint_ok = true (fmt, fmt --all, clippy -D warnings, pre-commit),
commits_ok = true on the three checked criteria (Conventional title, exact trailer, no build outputs); caveat: bodies
of the first two commits exceed the 3-10 line house rule (21 / 15 non-blank lines), unamended per house rule.
Landing-order caveat carried from the reviewer: knob default true + no RIGHT_SEMI_JOIN translator arm (documented in
TUNABLES.md / DEMO.md / commit body; user decision, not a code defect).

## Pre-commit

`pre-commit run --files <10 changed files>` via the clone's manifest: every applicable hook Passed (check yaml,
end-of-file, trailing whitespace, codespell, smartquotes, large files, merge conflicts, private key, mixed line
ending, destroyed symlinks); the rest "no files to check" because `.pre-commit-config.yaml` excludes
`^experimental/.*` and `\.patch$`. Working tree still clean afterwards.

## Shell

bash -n on bench.sh and run-abc.sh: ok. shellcheck not installed on this box.
