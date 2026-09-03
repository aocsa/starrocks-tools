# oom-failfast independent verification (2026-09-03, ~17:45-18:10 UTC)

Verified: commit 19f9eee4 on fix/oom-failfast (parent 45dab3be = perf/profile-sf1000), worktree
/home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast. No source edited, nothing pushed, no stash,
no amend. Tree clean before and after (only ignored artifacts + the two .pixi symlinks).

## Scope of the change (drives which arms apply)
12 files, +927/-34, all engine: CMakeLists.txt, 3 docs under docs/super-sirius/, 5 headers/sources
under src/include/pipeline/ + src/pipeline/, 2 Catch2 files under test/cpp/pipeline/. Nothing under
experimental/ -> the CN (cargo fmt/clippy/test) and FE (fe-build, patches) arms do not apply.

## Build
- `pixi run --manifest-path .../sirius-stacks/pixi.toml bash -c "cd <wt> && make release"`
  -> `[391/391] repository`, EXIT=0 (log: oom-failfast-verifier-build.log).
- 3 compiler warnings, all in test/cpp/scan_manager/test_pinned_entry_column_lookup.cpp
  (deprecated insert_pinned_entry return, pre-existing); none in a touched file.
- sirius_unittest and sirius.duckdb_extension rebuilt 17:47 UTC (newer than every source).

## Tests (GPU 0, CUDA_VISIBLE_DEVICES=0)
- `[retry_futility]` (GPU-free predicate table): All tests passed (29 assertions in 1 test case).
- `[gpu_pipeline_executor]` (includes [oom] and [futile]): All tests passed (53 assertions in 7 test
  cases). Per-case wall: reschedules-on-OOM 0.142 s; max-retries 5.144 s (unchanged 100-retry
  shape); futile 0.092 s; holder 0.341 s; partial-first 0.049 s; dropped 0.239 s.
- `[downgrade_executor],[downgrade_lifecycle],[downgrade_disk],[task_scheduler],[pipeline_queue],[retry]`:
  All tests passed (155 assertions in 34 test cases).
- Full sirius_unittest: see the line appended below.

## Lint
- `pre-commit run --files <12 changed files>`: every hook Passed or Skipped (no files of that type);
  clang-format, codespell, cmake-format/lint, rumdl, check-orphan-tests all Passed. PRECOMMIT_EXIT=0,
  `git status --short` empty afterwards (no hook rewrote a file).

## Commit message
- Title `fix(pipeline): fail the query fast when an OOM retry cannot make progress` -- Conventional
  Commits (type fix, scope pipeline), 74 chars.
- Trailer exactly `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` -- present, last line.
- Body: 13 non-blank lines (house rule says 3-10), max 79 chars. Content is what/why/tests/left for a
  human reviewer. Over the line budget; cannot be fixed here (no amend). Tighten when cherry-picking
  onto the fork branch (move the SIRIUS_CANONICAL_FLOAT_SUMS sentence to the PR body).
- No build outputs, telemetry, generated YAML, logs or the .pixi symlink in the commit.

## Code reading (things I checked myself, beyond the reviewer's notes)
- Early-out `continue` in manager_loop after `_task_queue.pop()`: the bounded_thread_pool slot is an
  RAII handle (bounded_thread_pool.hpp:42/58: dropping without dispatch releases immediately), the
  device_ready signal was already sent, and the task unique_ptr dies at end of iteration -> no slot
  or counter leak; test 4 exercises drain_and_wait() returning after a drop.
- `dynamic_cast<oom_reschedule_exception*>(&ex)`: oom_reschedule_exception derives from
  task_reschedule_exception (oom_reschedule_exception.hpp:35/64) and the header is included.
- `_completion_handler->has_error()` on the manager thread: prepare_for_query installs the fresh
  handler on every executor (task_scheduler.cpp:193-197) before anything is scheduled; same pointer
  the lambda already dereferenced.
- `_execution_progress` is declared before `_gpu_executors` in task_scheduler and the executors hold
  a shared_ptr copy -> no lifetime hazard either way; the default-null ctor arg keeps other callers.
- Docs anchor `#reschedule-handling-oom-and-cuda-launch-failures` resolves to the heading at
  pipeline-execution.md:417; the numbered list there matches the code path order.
- Predicate false-positive actor (an exchange sender draining parked output mid-retry) is addressed
  by spec section 7 risk 1 (one request at a time in engine.rs); the SF1000 arms are the acceptance.
- Agree with the reviewer's minor finding 1 (rule-8 window between dispatch and lambda start):
  consequence is a fast failure where one more round might have helped; not reachable in the SF1000
  shape; not a blocker.

## Pre-existing failure, independently attributed
test/cpp/operator/aggregate/test_gpu_merge_impl.cpp:1115 (`REQUIRE(bits_run1 == bits_run2)` on a
direct gpu_aggregate_impl::local_grouped_aggregate call). 85658f09 "perf(agg): gate the canonical
float-sum sort behind SIRIUS_CANONICAL_FLOAT_SUMS" is an ancestor of perf/profile-sf1000 and touches
only aggregate files; this commit touches no aggregate/merge code.

## Still owed before Ready (unchanged)
- 2-GPU SF100 q11 contention guard (spec 6.4). SF1000 arms A/B (orchestrator). Multi-GPU Catch2
  cases skip on this single-GPU worktree.

## Full sirius_unittest (appended after the run)
- `CUDA_VISIBLE_DEVICES=0 build/release/extension/sirius/test/cpp/sirius_unittest` (log:
  oom-failfast-verifier-fullsuite.log):
  `test cases: 3108 | 3107 passed | 1 failed`, `assertions: 32809742 | 32809741 passed | 1 failed`,
  SUITE_EXIT=1. The one failure is test/cpp/operator/aggregate/test_gpu_merge_impl.cpp:1115
  `REQUIRE( bits_run1 == bits_run2 )` -- the pre-existing base-branch case (see above); same count
  and same case as the implementer's run.
- Suite wall: 17:50:22 -> 17:58:18 UTC (~8 min). No stray sirius_unittest processes afterwards.
- Re-run of the single failing case "Grouped local aggregate float64 sum is bit-identical across row
  orders" on the same binary: plain -> `test cases: 1 | 0 passed | 1 failed` (123 assertions, 1 failed
  at :1115); with SIRIUS_CANONICAL_FLOAT_SUMS=1 -> `All tests passed (161 assertions in 1 test case)`.
  Confirms the implementer's attribution to 85658f09 on the base branch; unrelated to this fix.

## Verdict
build_ok=true, tests_ok=true (only the pre-existing float-sum case fails, exactly as on the base),
lint_ok=true, commits_ok=true on the three named checks (Conventional Commits title, exact trailer,
no build outputs); one house-rule deviation: body is 13 lines vs the 3-10 budget -- fix at cherry-pick.
Worktree clean at 19f9eee4; main repo and every other worktree untouched.
