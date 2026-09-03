# oom-failfast adversarial review (2026-09-03, ~17:40 UTC)

Reviewed: commit 19f9eee4 on fix/oom-failfast (parent 45dab3be), worktree
/home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast, against
scratchpad/fix/designs/oom-failfast-SPEC.md. No code edited. Tree clean before and after.

## Re-ran myself (GPU 0, binary current: no source newer than sirius_unittest, built 17:14)
- `[retry_futility]`: All tests passed (29 assertions in 1 test case), GPU-free.
- `[gpu_pipeline_executor]`: All tests passed (53 assertions in 7 test cases). Timings match the
  implementer's: max-retries 5.095 s (unchanged shape), futile 0.163 s, holder 0.340 s,
  partial-first 0.049 s, dropped 0.238 s.
- `[downgrade_executor],[downgrade_lifecycle],[downgrade_disk],[task_scheduler],[pipeline_queue]`:
  All tests passed (146 assertions in 33 test cases).
- `pre-commit run --files <12 changed files>`: every hook Passed (clang-format, codespell, rumdl,
  check-orphan-tests included).

## What I tried to break, and what held
- SF1000 shape (q05 chain, spec 2.1): queue order is F1..F25 then R1..R25, so at R1's OOM every
  first attempt has finished (inflight 0), nothing completed (epoch equal), gate partial, freed 0,
  required 3.376e9 > granted 3.076e9 -> rule 9 at the first retry. `get_max_memory()` is
  `config.reservation_limit()` (memory_space.cpp:106/358), so `held = limit - granted` is exact under
  IDLE (notification_channel.cpp:55: IDLE only when `_n_active_notifiers == 0`).
- The 15 passing queries: the predicate is reachable only inside the `task_reschedule_exception`
  catch; hot-path additions are one `has_error()` load, one epoch load, a POD write, two atomics.
  The manager thread now dereferences the raw `_completion_handler` per task, but only after a pop,
  which only happens after prepare_for_query installed the fresh handler (task_scheduler.cpp:187-197);
  no new dangling window.
- Cancellation: `sirius_engine::cancel_tasks()` (src/sirius_engine.cpp:205) only clears pipeline
  vectors; it never sets has_error, so the early-out neither helps nor hurts it. Watchdog/timeout
  throws into execute()'s catch -> drain_after_error, unchanged.
- Dropped tasks at the gate: `~sirius_pipeline_itask` finalizes telemetry with success=false
  (sirius_pipeline_itask.cpp:44-53), identical to `drain_leftover_tasks()`; slot release on drop is
  the documented bounded_thread_pool contract (bounded_thread_pool.hpp:42, :58).
- #732 shape: batch_lock_utils.hpp throws rmm::out_of_memory, so it does record a requirement via the
  prepare-path handler; rule 4 (full grant) is what keeps it retrying, as the spec says.
- Error text: contract substrings present; `ex.what()` is "OOM at operator X (index i)".
- Scope: 12 files, none under experimental/, no Rust/FE/proto/config; docs limited to the three
  the spec named; anchor `#reschedule-handling-oom-and-cuda-launch-failures` exists.
- Tests that would pass without the change: holder (2) and partial-first (3) are pins the spec asked
  for; futile (1) and dropped (4) fail without the change (100 retries / completed_count 1).

## Findings (all minor; verdict approve)
1. Rule-8 uncounted window (gpu_pipeline_executor.cpp:371). The OOM'd task's reservation is
   released during execute() unwinding (`absl::Cleanup source_closer`, gpu_pipeline_task.cpp:604),
   before the executor's catch runs. The manager can then grant and `dispatch()` a queued first
   attempt N while R sits in `exc_stream->synchronize()`. If R's inflight load lands between the
   work_queue_ push (bounded_thread_pool.hpp:210) and N's lambda's first statement, R sees 0 and can
   abort while N is about to run. The spec's bound ("a task that has not started holds no memory")
   is not quite right: N holds its input batches, invisible to R's gate downgrade because the
   pull-signal backpressure leaves at most one task in the executor's own _task_queue. Not
   reachable in the SF1000 shape (the only N during an R's OOM is another R, which carries no
   guard); consequence elsewhere is a fast failure where one more round might have helped.
   Fix: build the guard on the manager thread before dispatch() and move it into the lambda (add a
   move ctor that nulls the source) so the count covers the dispatch queue too.
2. Commit body is 13 lines (house rule 3-10) and several lines are 79 chars. Cannot be fixed here
   (never amend); tighten at the cherry-pick onto the fork branch: move the
   SIRIUS_CANONICAL_FLOAT_SUMS sentence to the PR description, merge "Left:" into the tests line.
3. `inherit_retry_reservation_floor`'s new `_oom_required_bytes` line (gpu_pipeline_task.hpp:123) is
   not exercised: in test 1 the retry's own handler re-records 300 MB before the predicate reads it,
   so deleting the line leaves every test green. Ten-line GPU-free check wanted.
4. `retry_futility.hpp` drags `<format>` and `<atomic>` into gpu_pipeline_task.hpp (the implementer's
   ~10 min rebuild). The task header only needs the `retry_gate_observation` POD; splitting the PODs
   from the `<format>`-using predicate would spare every TU that includes the task header.
5. Two names for one constant (`kMaxTaskRetries` and the `MAX_RETRIES` alias). Documented
   deviation; using `kMaxTaskRetries` at the three .cpp sites would drop the alias.

## Still owed before Ready (unchanged from the implementer's list)
- 2-GPU SF100 q11 contention guard (spec 6.4) -- rules 7-8 across executors are source reasoning
  on this single-GPU worktree.
- SF1000 arms A/B after the CN relink (orchestrator).
