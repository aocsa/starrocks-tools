# oom-failfast implementer notes (2026-09-03)

Worktree: /home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast (branch fix/oom-failfast, base 45dab3be).
Spec: scratchpad/fix/designs/oom-failfast-SPEC.md (contract). DECISIONS.md items 1-6 taken as spec defaults.

## Method
Tests first (test_retry_futility.cpp new; four component cases in test_oom_reschedule.cpp), see them fail
to compile / fail, then the change, then build + run [retry_futility] [gpu_pipeline_executor] [downgrade]
and pre-commit on the changed files.

## Deviations from the spec (code needed it)
(filled in below as they arise)

## Status log

### Deviation 1: the retry cap constant lives in retry_futility.hpp
Spec 3.4 said "move MAX_RETRIES to file scope in an anonymous namespace" in gpu_pipeline_executor.cpp, and
spec 3.1 said the pure predicate's reason text names "(retry cap 100)". A header-only pure function cannot
see a .cpp-local constant, so the single definition is `sirius::pipeline::kMaxTaskRetries = 100` in
`src/include/pipeline/retry_futility.hpp` (with the #732 comment moved there), and the .cpp's anonymous
namespace keeps `constexpr uint32_t MAX_RETRIES = kMaxTaskRetries;` so every existing use of MAX_RETRIES
in the reschedule path is unchanged. No behaviour change.

### Deviation 2: none in the tests' arithmetic
The fixture numbers in spec 4.2 check out against cucascade: `get_available_memory()` is capacity -
total_allocated (reservation_aware_resource_adaptor.cpp:276-280), a plain allocation on the space's
default allocator charges total_allocated (do_allocate_unmanaged), `make_reservation` returns the upto
grant only after the channel reports IDLE (memory_space.cpp:257-266, notification_channel.cpp:49-60).

### Status
- 17:2x UTC: tests written (test_retry_futility.cpp new; 4 cases + 3 task types + fixture flag in
  test_oom_reschedule.cpp); both TUs fail to compile before the change (missing header) -- seen.
- header + task/executor/scheduler edits + 3 docs done; pre-commit clean (clang-format reformatted only).
- make release running (log: oom-failfast-build.log in this dir).
- make release: BUILD_EXIT=0 (418 steps, ~10 min with ccache; gpu_pipeline_task.hpp is widely included).
- [retry_futility]: All tests passed (29 assertions in 1 test case). GPU-free.
- [gpu_pipeline_executor] on GPU 0: All tests passed (53 assertions in 7 test cases), wall 8.4 s:
    reschedules tasks on OOM                                   0.139 s (unchanged, full grants -> rule 4)
    fails after max OOM retries                                5.097 s (unchanged: xl_task records no need -> rule 6, 100 retries)
    fails the query when an OOM retry cannot make progress     0.103 s  (new; was the 100-retry shape)
    keeps retrying an OOM while another task holds a reservation 0.342 s (new; blocked ~300 ms on the holder, oom_count==1)
    does not fail a first attempt on a partial reservation     0.050 s  (new)
    drops queued tasks once the query has failed               0.241 s  (new)
- [downgrade_executor],[downgrade_lifecycle],[downgrade_disk],[task_scheduler],[pipeline_queue] on GPU 0:
  All tests passed (146 assertions in 33 test cases), 8.7 s.
- Full sirius_unittest (the test step of `make test`) on GPU 0, 17:15-17:24 UTC (~9 min):
  3108 test cases | 3107 passed | 1 failed:
  test/cpp/operator/aggregate/test_gpu_merge_impl.cpp:1115 "Grouped local aggregate float64 sum is
  bit-identical across row orders" -- REQUIRE(bits_run1 == bits_run2) on a direct
  gpu_aggregate_impl::local_grouped_aggregate call (no executor, no reschedule path). This is the
  canonical float-sum sort that 85658f09 (perf/profile-sf1000) gated behind SIRIUS_CANONICAL_FLOAT_SUMS;
  see the re-run below for whether it is a pre-existing base-branch failure.
  Re-run of that single case on the same binary: fails plain (2/2 runs), passes with
  SIRIUS_CANONICAL_FLOAT_SUMS=1 (161 assertions). Pre-existing on perf/profile-sf1000 (85658f09 gated the
  canonical sort but left this test asserting determinism); not touched here (scope).

## Result
- Commit 19f9eee4 on fix/oom-failfast (parent 45dab3be), 12 files, +927/-34. Working tree clean. Not pushed.
- Contract strings in place: "gave up after", "held outside any task reservation", "freed 0 bytes",
  "is futile" (ERROR log), "(retry cap 100)"; the kept 100-retry line "exceeded {} retries" still matches
  retry_stats.py's regex.

## Left for the orchestrator / reviewer
1. CN relink (`pixi run cn-build` in experimental/starrocks) before SF1000 arms A/B (spec 6.1/6.2) on GPU 0.
2. 2-GPU contention guard: SF100 q11, cache=table_gpu, num_gpus=2 (spec 6.4, DECISIONS item 1) before Ready.
3. Multi-GPU Catch2 tests skipped on the single-GPU worktree (say so in the PR).
4. Pre-existing on the base branch, out of scope: test_gpu_merge_impl.cpp:1115 needs
   SIRIUS_CANONICAL_FLOAT_SUMS=1 since 85658f09 gated the canonical sort; a one-line test fix or an env
   default belongs in that perf commit's follow-up, not here.
5. PR later: cherry-pick onto a fork branch off origin/dev (touched engine files identical there except
   task_scheduler.{hpp,cpp}, whose +53 lines do not overlap these edits).
