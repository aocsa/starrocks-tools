# Fix 2 independent verification: per-query parked-output bookkeeping (CN layer)

Worktree `/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping`, branch `fix/parked-bookkeeping`
= 45dab3be (perf/profile-sf1000) + 63e7e0c1 + 0f4b1c19. Verifier role, 2026-09-03 ~17:50 UTC. GPU 1 only.
No code edited; nothing pushed; no FE/CN cluster started; no SF1000 queries run.

## Starting state

- `git status --short`: empty. `git status --ignored --short experimental/starrocks/.pixi` -> `!!` (ignored symlink
  to the clone's envs), so "clean apart from the .pixi symlink" holds.
- Layer touched: CN Rust (`experimental/starrocks/src/*.rs`, 8 files) + 6 docs. No engine C++
  (`git diff --stat perf/profile-sf1000..HEAD -- src/ CMakeLists.txt Makefile test/` is empty), no FE patch, no
  submodule pointer change (`git diff perf/profile-sf1000..HEAD -- experimental/starrocks/starrocks` empty).
  So: no engine `make release` and no `fe-build`; the engine artefacts the GPU tests load already exist from the base
  (`build/release/extension/sirius/sirius.duckdb_extension` 15:51, `extension/parquet/parquet.duckdb_extension`
  15:47, both after the base commit 12:57, and the branch changes no engine source).
- To make the cached cargo results real, the 8 changed `.rs` files were `touch`ed (mtime only; `git status` stayed
  empty) before clippy/test/build so cargo recompiled the crate instead of reusing the reviewer's artefacts.

## CI-equivalent checks for the CN layer (all via cn-build.sh, `-e cn`, clone manifest)

| check | command | result |
|---|---|---|
| fmt | `cargo fmt -- --check` | exit=0 (log `parked-bookkeeping-verifier-fmt.log`) |
| clippy (cached) | `cargo clippy --all-targets --no-default-features -- -D warnings` | `Finished dev ... in 0.97s`, exit=0 |
| clippy (after touch) | same | `Checking sirius-starrocks-cn v0.1.0 ...` / `Finished dev ... in 3.66s`, exit=0 (`-clippy-fresh.log`) |
| pre-commit | `pre-commit run --files <14 changed files>` (repo-root manifest) | exit=0; every hook `(no files to check) Skipped` because `.pre-commit-config.yaml:18` excludes `^experimental/.*`; tree still clean afterwards |
| tests, no engine | `cargo test --workspace --no-default-features` (after touch; `Compiling sirius-starrocks-cn`) | CN lib 175 passed / 0 failed; CN bin 9 passed; translator 18 + 136 passed; thrift 0; doc-tests 0; `Finished test profile ... in 13.92s`, exit=0 (`-test-noengine.log`) |
| build | `cargo build --release` (default features = engine + nixl; after touch) | `Compiling sirius-starrocks-cn` / `Finished release profile [optimized] target(s) in 16.02s`, 0 warnings, exit=0; `target/release/sirius-starrocks-cn` rewritten 17:56:16 (`-build-release.log`) |
| tests, engine-linked (GPU 1) | `CUDA_VISIBLE_DEVICES=1 cargo test --release -p sirius-starrocks-cn` | lib 187 passed / 0 failed / 4 ignored (the pre-existing debug/nixl harnesses), bin 9 passed, 0 warnings, exit=0 (`-test-gpu.log`) |

Chain timing: 17:55:42 -> 17:56:59 (`parked-bookkeeping-verifier-chain.txt`: `test-noengine exit=0`,
`build-release exit=0`, `test-gpu exit=0`, `CHAIN DONE 17:56:59`).

## Spec-named tests, each confirmed `ok` by name in the logs

- Spec 6.1 registry (9): park_then_release_last_claim_drops_the_fragment, second_release_of_a_live_slot_is_a_loud_error,
  duplicate_slot_is_refused_before_anything_is_inserted, retire_drops_only_that_query_and_poisons_its_slots,
  retire_of_the_unlabeled_bucket_leaves_labeled_output_alone, release_after_retire_is_ok_exactly_once,
  repark_of_a_retired_slot_forgets_its_torn_down_entry, torn_down_is_bounded, retired_queries_keep_the_first_cause_and_evict_fifo.
- Spec 6.2 result_store (2): failure_of_reports_the_first_recorded_failure, cancel_query_fails_waiting_entries_only_and_is_visible_to_failure_of.
- Spec 6.3 local_exchange (2): retire_receiver_returns_its_sources_and_refuses_later_frames, retired_receivers_are_bounded.
- Spec 6.4 service (10): queued_fragments_of_a_failed_query_are_skipped, ..._inline, a_fragment_failure_retires_the_query_on_the_executor,
  translation_failure_retires_the_slots_in_hand_and_releases_remote_leases, a_result_fragment_arriving_after_the_failure_still_reports_the_cause,
  an_inline_sender_failure_is_recorded_at_query_level, cancel_reason_matrix_retires_and_records_by_reason,
  query_finished_cancel_keeps_delivered_rows, cancel_purges_the_receivers_staged_frames_and_refuses_late_ones,
  cancel_for_the_phased_dummy_instance_still_retires_the_query.
- Spec 6.5 GPU (4 new + 2 guards): a_failed_run_retires_only_its_own_query, retire_query_drops_parked_output_and_refuses_later_runs,
  an_unlabeled_failure_retires_only_the_unlabeled_bucket, output_parked_after_a_retire_is_dropped_not_parked,
  engine_executes_local_files_and_sequential_exchange, engine_pushes_staged_remote_batches.

## Spot checks against the spec (read, not re-derived)

- Gate 1 is the first statement of `run_fragment_inner` (engine.rs:486-501), ahead of `context.fragment()`, so the
  `run_fragment` lease sweep still runs on a refusal (spec 3, risk 9.1).
- Non-test callers of `retire(`/`.mark(`/`retire_query(`/`cancel_query(`: engine `Err` arm (engine.rs:376, 378),
  the handle's `retire_query` (engine.rs:833), `fail_fragment` (compute_node_service.rs:1095), `cancel_plan_fragment`
  (compute_node_service.rs:493, 503). Nothing else (spec risk 9.2, matches the reviewer).

## Commits

| commit | title | CC | len | body lines | trailer | files |
|---|---|---|---|---|---|---|
| 63e7e0c1 | `fix(cn): retire a failed query's parked output instead of wiping every query's` | yes | 78 | 11 | exact, 1 | 7 `.rs` |
| 0f4b1c19 | `feat(cn): cancel_plan_fragment tears down the cancelled query on this CN` | yes | 72 | 11 | exact, 1 | 4 `.rs` + 6 `.md` |

- Trailer is exactly `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line of both.
- Body content is what/why + tests + what is left, written for a reviewer. Both bodies are 11 lines against the
  3-10 line house rule (the reviewer flagged the same); one line over, content-wise fine. Not a blocker, noting it.
- No build outputs, telemetry, generated YAML, `.pixi`, logs or CSVs in either commit
  (`git diff --name-only perf/profile-sf1000..HEAD | grep -E '\.pixi|target/|telemetry|\.ya?ml$|\.log$|\.csv$|\.so$|output/'` -> none).
  Submodule pointer unchanged. `rmm_log.txt` is tracked but predates this branch (ad86c032), not touched here.
- No commit rewritten; HEAD still 0f4b1c19 after all runs.

## End state

`git status --short` empty (the `.pixi` symlink stays ignored); HEAD 0f4b1c19; GPU 1 idle (0 MiB) after the run.
Not run by design: engine `make release` (no engine source changed; artefacts from the base are present), `fe-build`
(no FE change, no new patch), the SF1000 arms A-D (orchestrator's).

## Verdict

build_ok, tests_ok, lint_ok, commits_ok all true. Only nit: both commit bodies are 11 lines (rule says 3-10).

## Addendum: the two `warning:` lines in the no-engine test log

`cargo test --workspace` compiled the translator crate's `tests/translate.rs`, which the implementer/reviewer runs
(`-p sirius-starrocks-cn`) never build. It emits one pre-existing dead-code warning:
`function sort_over_a_carried_common_slot_resolves_and_narrows is never used` (translate.rs:6144, a test fn without
`#[test]`). The file is byte-identical to perf/profile-sf1000 and this branch touches nothing under `crates/`; CI's
clippy step (`--all-targets --no-default-features`, root package) does not lint that target and passed here. Not this
fix's problem; a one-line `#[test]` follow-up on the base branch.
