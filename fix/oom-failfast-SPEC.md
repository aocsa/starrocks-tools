# Fix 1 spec: fail the query fast when an OOM retry cannot make progress

Implementation spec for an engineer working alone in the worktree
`/home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast` (branch off `perf/profile-sf1000` =
`45dab3be`, GPU 0 assigned). Source anchors below are `file:line` in that tree; the engine files this
fix touches are byte-identical on `origin/dev` (checked with `git diff --stat origin/dev..d24f02c4`
over the touched files: only `task_scheduler.{hpp,cpp}` differ, +53 lines that do not overlap the
edits here), so the same line numbers hold for the upstream PR. Evidence bundle:
`scratchpad/perf/sf1000/` (cited as `E/...`). "Measured" means read from a named file or produced by
re-running `E/agent-notes/skeptic-oom4/retry_stats.py` today; "inference" is marked.

## 0. What was decided and why

Two judges scored the three designs (surgical / robust / upstream). Their totals tie: surgical 43,
upstream 43, robust 32. Both rejected robust's config schema, constructor-signature churn and
exception-type change; both asked for the same set of merges. This spec is the merge:

| piece | taken from | why |
|---|---|---|
| Decision made in the **reschedule path**, confirmed by the retry's own OOM | upstream | The gate-time abort (surgical) would fire ~40 ms earlier at best: both fire at the first retry-1 gate/OOM (section 3.6). Confirming by an actual OOM removes the pre-emptive-abort risk at negligible cost, and the reschedule path already has the error-exit shape and the operator name. |
| Hard lower bound `live + requested` recorded by the OOM handler | surgical | It is exact (raa accounting, section 2.3) and replaces upstream's `granted < floor` test, which judge 2 showed degenerates (the floor is raised to >= 2 x granted before the throw, so it is always true). |
| Pure, header-only predicate with a GPU-free Catch2 table | upstream | Reviewable decision rule; CI-testable without a GPU. |
| Scheduler-wide progress signals: `completed_epoch` + `inflight_first_attempts` in a small struct owned by `task_scheduler`, passed to executors through a **defaulted** extra ctor argument | robust (via judge 1) | Closes the multi-GPU blind spot (a completion on another GPU can make a subscribed GPU-0 batch convertible, `downgrade_executor.cpp:231-232`). No config, no signature break: the single test construction site keeps compiling. |
| `has_error()` early-out before the gate blocks | robust | Stops a failed query's queued retries from each costing the manager a blocking `make_reservation`. |
| `oom_reschedules` / `futile_aborts` counters in `executor_metrics` | robust | Makes component-test assertions exact. |
| Holder-task test that pins cucascade's IDLE semantics; arm-B log assertions; "held outside any task reservation" wording | surgical | The proof depends on partial grants being issued only when no reservation is alive; this test fails loudly if cucascade changes. |
| `has_disk_tier()` in the message; stale-doc fix at `execution-flow.md:150` | upstream | Gives the documented-but-uncalled hook (`downgrade_executor.hpp:155-161`) its caller. |
| `MAX_RETRIES = 100` and the 50 ms backoff unchanged; constant hoisted to file scope so both messages name it | all three | The #732 cross-GPU contention budget is not this fix's business. |
| Hysteresis = 1 (abort at the first retry whose OOM confirms the gate's verdict) | surgical / judge 2 | The bound is hard; N=2 (robust) costs a full extra round (2.4 s for q08/q09) for no safety. |
| Acceptance measured **from the first OOM in the engine log**, not from client start | all three | q18's first OOM is at +9.29 s because its own senders run first; q08/q09 rounds are 2.3-2.4 s. |

Dropped: robust's YAML knob, `gpu_pipeline_executor_config`, `oom_details` on the exception, HOST-tier
claims (`task_scheduler.cpp:91-93` builds GPU-tier executors only); surgical's gate-time `break`;
upstream's per-executor `_tasks_executed` mark and creation-time snapshot (replaced by the shared
epoch snapshotted at the gate, which also avoids the one-round delay the single stray completion per
window could otherwise cause).

## 1. Goal and non-goals

**Goal.** A GPU pipeline task whose OOM retry provably cannot be granted what its last OOM needed fails
the query at that retry instead of replaying the same OOM up to 100 times, and the error names the
cause: bytes needed vs grantable, bytes held outside any task reservation vs the pool, bytes the
downgrade freed, disk-tier state, and that nothing completed or was running that could release memory.

Measured target (1 CN, 100 GiB pool, SF1000): q05 q08 q09 q17 q18 q21 currently die after 101.5 /
168.3 / 232.5 / 103.9 / 54.2 / 114.5 s (`E/cn1/runs/runs.csv`), 774.9 s of the 910.3 s sweep, with
17,900 reschedule warnings and 17,917 "0 bytes freed ... proceeding with partial reservation" lines.
After this fix each dies within one retry round of its first OOM (section 6).

**Non-goals.**
- Making the six queries pass on 1 CN (fix 4), attributing the held bytes to fragments or dropping a
  dead query's parked output (fix 2), FE cardinalities (fix 3).
- Changing `MAX_RETRIES`, the backoff, the retry-floor policy (`2 x reservation`), the downgrade
  executor's misleading "disk not configured" WARN (`downgrade_executor.cpp:357-363`, belongs to 4b),
  the watchdog fingerprint, or the sink-deferral OOM path (`gpu_pipeline_task.cpp:721-751`; see
  decision 3).
- Any CN (`experimental/starrocks`), FE, proto, cucascade or configuration change. The error string
  already travels verbatim to the client (section 3.5).

## 2. The measured failure and the three facts the fix rests on

### 2.1 The loop (measured)

`src/pipeline/gpu_pipeline_executor.cpp:348` `static constexpr uint32_t MAX_RETRIES = 100;`, `:409`
`sleep_for(50ms)`; the only exits are the cap (`:349-364`) and `has_error()` (`:312-316`). The
reservation gate proceeds on a partial grant after the downgrade freed nothing (`:262-271`, the WARN
"after downgrade ({} bytes freed), reservation still partial ({}/{} bytes) ... proceeding with partial
reservation"). Per query on cn1 (`retry_stats.py`, re-run 2026-09-03):

| query | stuck originals | reschedules | first OOM (from client start) | round 1 span | wall to die |
|---|---:|---:|---:|---|---:|
| q05 | 25 | 2500 | +1.61 s | +1.61..+2.62 (1.01 s) | 101.5 s |
| q08 | 36 | 3600 | +1.73 | +1.73..+4.09 (2.36) | 168.3 |
| q09 | 56 | 5600 | +2.38 | +2.38..+4.68 (2.30) | 232.5 |
| q17 | 21 | 2100 | +1.20 | +1.20..+2.41 (1.21) | 103.9 |
| q18 | 23 | 2300 | +9.29 | +9.29..+9.98 (0.69) | 54.2 |
| q21 | 18 | 1800 | +2.68 | +2.68..+3.81 (1.13) | 114.5 |

Every original reaches exactly retry 100; every OOM is `GPU_SCAN` index 0 with
`global usage 107374182400` (the limit); every one of the 17,917 gate passes freed 0 and granted
partially; zero reschedule lines fall outside the six windows on cn1, and none exist on the three
other CNs of the 4-CN sweep. The q05 chain of original task 2567 (`E/cn1/engine-cn0.log:49219-49223`,
`:61597`, `:61723`): first attempt granted 3,318,948,864 of 5,803,790,251 requested, OOM asking
748,875,880 B at peak 3,376,381,184 B; every retry granted 3,076,488,960 of the 6,637,897,728 floor,
freed 0, same OOM; `exceeded 100 retries` at 13:05:24.553, drain done at .624 (71 ms), CN "fragment
run failed" 74 ms later (`E/cn1/cluster.log`).

Rounds are lockstep because the pool is full: the manager thread blocks in `make_reservation` until
the one running task releases (Quent q05 fragment 5774: `Rsv=99.940 s` of a 101.2 s window,
`E/cn1-quent.txt`). At most one task executes on the GPU at a time during the storm (fact A).

### 2.2 Fact A: a partial grant means no other reservation is alive on the space

`cucascade/src/memory/memory_space.cpp:257-266`: `make_reservation` loops on `make_reservation_or_null`
and only returns `make_reservation_upto(size)` when `_notification_channel->wait()` returns `IDLE`.
`cucascade/src/memory/notification_channel.cpp:49-60`: `IDLE` is returned only when
`_n_active_notifiers == 0`. Every reservation owns a notifier for its lifetime
(`reservation_aware_resource_adaptor.cpp:365-381`, `memory_reservation.hpp:183-198`). The upto grant is
`min(request, reservation_limit - total_allocated)` (`reservation_aware_resource_adaptor.cpp:375-380`
-> `do_reserve_upto` `:535-543` -> `add_bounded` `cucascade/include/cucascade/utils/atomics.hpp:87-97`).
So for a **partial** grant, `reservation_limit - granted == total_allocated at the gate == bytes held
outside any reservation` (parked fragment output, pinned tables, idle batches). Exact, independent of
`reservation_limit_fraction`; on the CN the fraction is 1.0 (`engine_settings.rs:98,102`).

### 2.3 Fact B: a LIMIT_EXCEEDED OOM proves `live + requested > reservation`

`reservation_aware_resource_adaptor.cpp:184-214` (`check_reservation_and_handle_overflow`): an
allocation charges the pool only for the part of `live + requested` that exceeds the stream's
reservation (`upstream_tracking_size == 0` while `post_allocation_inc < reservation_size`). The pool
check `_total_allocated_bytes.try_add(tracking_bytes, _capacity)` (`:465`) throws
`cucascade_out_of_memory(..., LIMIT_EXCEEDED, allocation_bytes, ...)` (`:477-482`). Hence a
LIMIT_EXCEEDED OOM implies `live + requested > reservation` strictly. `gpu_pipeline_task.cpp:433-457`
already computes both (`cc_oom->requested_bytes`, `_allocator->get_allocated_bytes(stream)`) and folds
them into the floor (`gpu_pipeline_task.hpp:99-108`); the per-stream peak is updated with the failed
allocation included (`:199`), which is why the logged `peak allocated 3376381184` equals
`live + requested` for q05 (inference from the code path; the log prints peak, not live).

Combining A and B for a retry at gate k with a partial IDLE grant and 0 bytes freed:
`granted_k = limit - held_k <= limit - held_{k-1}` (held only grows during a fragment run; the CN
releases parked output only after the fragment finishes, `engine.rs:278-280`, finding B1) and the
previous OOM proved the task needs `> reservation_{k-1} >= granted_k`. The retry will OOM again, and so
will every later one unless something outside this task frees memory: a downgrade (shows as
`freed > 0`), or a task completing (shows in the shared epoch / in-flight counter).

### 2.4 Fact C: `freed == 0` is the only trustworthy "nothing convertible" signal

`request_downgrade(...).get()` returns the bytes freed by TIER 1 (repositories,
`downgrade_executor.cpp:223-232`) and TIER 2 (queued tasks, `:294-300`), `set_value(total_bytes)`
at `:397`. Parked sink output escapes TIER 1 by design (`streaming_fragment.cpp:103-112`, finding
B2). `has_viable_downgrade_target()` (`downgrade_executor.cpp:406-446`) probes HOST capacity, not
candidate existence, and is true in the SF1000 case (160 GiB HOST, empty). Use `freed`.

## 3. Mechanism

### 3.1 New header `src/include/pipeline/retry_futility.hpp` (GPU-free, header-only)

Includes only `<atomic> <cstddef> <cstdint> <format> <optional> <string>`. No CUDA, no cucascade.

```cpp
namespace sirius::pipeline {

/// Context-wide progress signals consulted by the OOM fail-fast. One instance per task_scheduler,
/// shared by every gpu_pipeline_executor it owns; relaxed/acquire atomics, no locks.
struct execution_progress {
  std::atomic<std::uint64_t> completed_epoch{0};         ///< +1 per task whose execute() returned normally, any executor
  std::atomic<std::int64_t>  inflight_first_attempts{0}; ///< first-attempt tasks (retry_count == 0) currently inside execute(), any executor
};

/// What the GPU executor's reservation gate saw for one attempt. Written by the manager thread
/// right before set_reservation(); read by the reschedule path after the attempt threw.
struct retry_gate_observation {
  std::size_t   requested_bytes         = 0;     ///< bytes_needs after the space-max clamp
  std::size_t   granted_bytes           = 0;     ///< reservation->size() handed to the task
  std::size_t   freed_by_downgrade      = 0;     ///< bytes the gate's downgrade request freed (0 when none ran)
  bool          downgrade_requested     = false; ///< the gate asked a downgrade executor (partial grant + executor present)
  bool          disk_tier_configured    = false; ///< downgrade_executor::has_disk_tier()
  std::size_t   space_max_bytes         = 0;     ///< memory_space::get_max_memory() (the reservation limit)
  std::uint64_t completed_epoch_at_gate = 0;     ///< execution_progress::completed_epoch read before make_reservation()
};

struct retry_futility_input {
  bool          is_oom                      = false; ///< the exception is an oom_reschedule_exception
  std::uint32_t retry_count                 = 0;     ///< of the task that just threw (0 = first attempt)
  std::size_t   oom_required_bytes          = 0;     ///< live + requested recorded by the OOM handler; 0 = unknown
  std::optional<retry_gate_observation> gate;        ///< this attempt's gate observation
  std::uint64_t completed_epoch_now         = 0;
  std::int64_t  inflight_first_attempts_now = 0;
};

/// Returns std::nullopt when another retry may succeed; otherwise the human-readable reason it
/// cannot (used verbatim in the query error). Pure: no I/O, no globals.
inline std::optional<std::string> assess_retry_futility(const retry_futility_input& in);

}  // namespace sirius::pipeline
```

Decision rule, in this order (each early-return is one row of the test table in 4.1):

1. `!in.is_oom` -> nullopt (CUDA-launch reschedules keep the plain 100-retry budget).
2. `in.retry_count == 0` -> nullopt (a first attempt never fails fast; partial first attempts often succeed).
3. `!in.gate` -> nullopt (defensive: the gate did not record).
4. `gate.granted_bytes >= gate.requested_bytes` -> nullopt (full grant: the gate was not the constraint; this is
   the #732 contention shape and the existing `xl_task` test).
5. `gate.freed_by_downgrade != 0` -> nullopt (spilling is making progress; fix 4b lives here).
6. `in.oom_required_bytes == 0 || in.oom_required_bytes <= gate.granted_bytes` -> nullopt (unknown need, or it would fit).
7. `in.completed_epoch_now != gate.completed_epoch_at_gate` -> nullopt (something finished since the gate; its
   memory may now be convertible or free).
8. `in.inflight_first_attempts_now != 0` -> nullopt (a first attempt is running somewhere and may release memory).
9. Otherwise futile. Reason text (q05 numbers, `held = space_max - granted`):

```
the last attempt needed at least 3376381184 bytes (3.14 GiB) on its own stream but the reservation gate could
grant only 3076488960 bytes (2.87 GiB) of the 6637897728 requested; 104297693440 bytes (97.1% of the
107374182400-byte space) are held outside any task reservation and were not convertible by the downgrade
executor, which freed 0 bytes (disk tier not configured); no task completed and no first attempt was running
since this attempt's reservation was granted, so another retry would hit the same limit (retry cap 100)
```
When `downgrade_requested == false` the middle clause reads "and no downgrade executor is attached to this
memory space". The GiB figures are `bytes / 1073741824.0` with `{:.2f}`; percentages `{:.1f}`. The substrings
`held outside any task reservation` and `freed 0 bytes` are contract (tests and acceptance grep them).

### 3.2 `src/include/pipeline/gpu_pipeline_task.hpp` (about 12 lines)

Include `pipeline/retry_futility.hpp`. In `gpu_pipeline_task_local_state`:

- After `original_task_id` (`:91-94`): `std::optional<retry_gate_observation> gate_observation;`
  doc-comment "filled by the GPU executor's reservation gate on every attempt; consumed by the
  reschedule path".
- `update_retry_reservation_floor_after_oom` (`:99-108`): compute
  `auto const required = memory::saturating_add(live_allocated_bytes, request_bytes);` once, use it
  for the existing `next_floor` max, and add `_oom_required_bytes = std::max(_oom_required_bytes, required);`
  with a comment: hard lower bound on what the failed attempt had to allocate on its own stream (fact B);
  unlike the floor it carries no policy multiplier, and the reschedule path compares the gate's grant
  against it.
- `inherit_retry_reservation_floor` (`:110-114`): also
  `_oom_required_bytes = std::max(_oom_required_bytes, previous._oom_required_bytes);`.
- Getter `[[nodiscard]] std::size_t get_oom_required_bytes() const noexcept;` next to
  `get_retry_reservation_floor` (`:116-119`); private `std::size_t _oom_required_bytes = 0;` next to
  `_retry_reservation_floor` (`:143`).

No change to the two callers (`gpu_pipeline_task.cpp:456-457`, `:646-647`) or the estimator (`:855`,
`:886`).

### 3.3 `src/include/pipeline/gpu_pipeline_executor.hpp` (about 15 lines)

- Include `pipeline/retry_futility.hpp`.
- `executor_metrics` (`:53-55`) becomes
  `{ size_t tasks_executed{0}; size_t oom_reschedules{0}; size_t futile_aborts{0}; }`.
- Constructor (`:76-81`) gains a sixth, defaulted parameter:
  `std::shared_ptr<execution_progress> progress = nullptr` (doc: shared by all executors of one
  `task_scheduler`; when null the executor creates a private instance, which is what the tests and any
  single-executor caller get). The only external construction site is
  `test/cpp/pipeline/test_oom_reschedule.cpp:112-117`; it keeps compiling.
- Members (`:146-152`): `std::shared_ptr<execution_progress> _progress;`,
  `std::atomic<size_t> _oom_reschedules{0};`, `std::atomic<size_t> _futile_aborts{0};`.

### 3.4 `src/pipeline/gpu_pipeline_executor.cpp` (about 70 lines)

**Constant.** Move `MAX_RETRIES` with its #732 comment (`:339-348`) to file scope in an anonymous
namespace at the top of the file (after the includes, before `namespace sirius`). No behaviour change.

**Constructor** (`:46-58`): `_progress(progress ? std::move(progress) : std::make_shared<execution_progress>())`.

**Gate, manager thread** (`manager_loop`, `:95-296`), four edits:

1. Early-out, after the `cast_to_gpu_pipeline_task` check (`:131-139`) and before the estimate (`:143`):
   ```cpp
   if (_completion_handler && _completion_handler->has_error()) {
     SIRIUS_LOG_DEBUG("GPU Pipeline Executor: dropping task {} for pipeline {}: the query already failed",
                      gpu_task->get_task_id(), gpu_task->get_pipeline_id());
     continue;  // destroys the task and the reserved slot (bounded_thread_pool.hpp:42: a dropped slot is released)
   }
   ```
   Why safe for the next query: `task_scheduler::set_query` installs a fresh `completion_handler` on every
   executor before any task of the next query is scheduled (`task_scheduler.cpp:192-197`), after
   `drain_leftover_tasks()` (`:185-187`). Dropping a task without running it is the existing drain behaviour
   (`task_executor.cpp:58`, `:123`).
2. Snapshot the epoch right before `make_reservation` (`:186`):
   `auto const epoch_at_gate = _progress->completed_epoch.load(std::memory_order_acquire);`
3. Hoist `size_t freed = 0;` and `bool downgrade_requested = false;` above the `if (!reservation) ... else if`
   chain (`:187`); in the partial+downgrade branch (`:196`) set `downgrade_requested = true` and assign the
   existing `freed` to the hoisted one (delete the inner declaration at `:221`).
4. Record the observation immediately before `set_reservation` (`:284-286`), inside the successful
   `dynamic_cast<sirius_pipeline_task_local_state*>` branch, reading `reservation->size()` **before** the move:
   ```cpp
   if (auto* gpu_local = dynamic_cast<gpu_pipeline_task_local_state*>(local_state)) {
     gpu_local->gate_observation = retry_gate_observation{
       .requested_bytes         = bytes_needs,
       .granted_bytes           = reservation->size(),
       .freed_by_downgrade      = freed,
       .downgrade_requested     = downgrade_requested,
       .disk_tier_configured    = _downgrade_executor != nullptr && _downgrade_executor->has_disk_tier(),
       .space_max_bytes         = _memory_space->get_max_memory(),
       .completed_epoch_at_gate = epoch_at_gate};
   }
   local_state->set_reservation(std::move(reservation), reservation_info);
   ```
   Recorded on every attempt (full or partial grant, with or without a downgrade executor). The WARN texts at
   `:263-271` and `:275-282` are unchanged.

**Dispatch, manager -> worker** (`:301-307`): compute on the manager thread
`bool const first_attempt = !(cur_local && cur_local->retry_count > 0)` where `cur_local` is
`dynamic_cast<gpu_pipeline_task_local_state*>(gpu_task->local_state())`, capture the bool, and make the
**first statement of the lambda** an RAII guard:
```cpp
struct first_attempt_guard {   // file-scope helper in the anonymous namespace
  execution_progress* p;
  explicit first_attempt_guard(execution_progress* progress) : p(progress) { if (p) p->inflight_first_attempts.fetch_add(1, std::memory_order_acq_rel); }
  ~first_attempt_guard() { if (p) p->inflight_first_attempts.fetch_sub(1, std::memory_order_acq_rel); }
  first_attempt_guard(const first_attempt_guard&) = delete; first_attempt_guard& operator=(const first_attempt_guard&) = delete;
};
// inside the lambda, first line:
first_attempt_guard inflight(first_attempt ? _progress.get() : nullptr);
```
Every exit of the lambda (normal, `return` in the reschedule catch, `return` in the generic catches) runs the
destructor, so the counter cannot leak. The uncounted window between `dispatch()` and the lambda's first
statement is harmless: a task that has not started holds no memory that a retry's next gate could reclaim.

**Success path** (`:309-310`): after `_tasks_executed.fetch_add(1, ...)` add
`_progress->completed_epoch.fetch_add(1, std::memory_order_acq_rel);`.

**Reschedule path** (`catch (task_reschedule_exception& ex)`, `:311-421`): directly after the `MAX_RETRIES`
block (`:349-364`) and before the reschedule WARN (`:366`):
```cpp
bool const is_oom = dynamic_cast<oom_reschedule_exception*>(&ex) != nullptr;
if (is_oom && cur_local) {
  auto const reason = assess_retry_futility({
    .is_oom                      = true,
    .retry_count                 = cur_local->retry_count,
    .oom_required_bytes          = cur_local->get_oom_required_bytes(),
    .gate                        = cur_local->gate_observation,
    .completed_epoch_now         = _progress->completed_epoch.load(std::memory_order_acquire),
    .inflight_first_attempts_now = _progress->inflight_first_attempts.load(std::memory_order_acquire)});
  if (reason) {
    _futile_aborts.fetch_add(1, std::memory_order_relaxed);
    auto message = std::format(
      "GPU pipeline task gave up after {} OOM {} with no way to make progress for original task {} on GPU:{}: {}; {}",
      cur_local->retry_count, cur_local->retry_count == 1 ? "retry" : "retries", orig_task_id,
      _memory_space->get_device_id(), ex.what(), *reason);
    SIRIUS_LOG_ERROR("GPU Pipeline Executor: task {} (original task {}) OOM retry {} is futile -- failing the query: {}",
                     gpu_task->get_task_id(), orig_task_id, cur_local->retry_count, *reason);
    if (_completion_handler) {
      _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(std::move(message))));
    }
    return;  // same exit shape as the MAX_RETRIES path: report_error only; execute()'s catch drains
  }
}
if (is_oom) { _oom_reschedules.fetch_add(1, std::memory_order_relaxed); }
```
Everything after (`release_intermediate_data`, the new local state at `:385-390` including
`inherit_retry_reservation_floor`, the device pin, `create_rescheduled_task`, the 50 ms sleep, telemetry
finalisation, `schedule`) is unchanged. Note the reads of the epoch and in-flight counter happen after
`exc_stream->synchronize()` (`:329`) and after the task's reservation was released during unwinding; if the
manager admitted a first attempt in that gap the rule waits one round (section 3.6, risk 3).

`get_metrics()` (`:485-488`) returns all three counters.

### 3.5 `src/pipeline/task_scheduler.{hpp,cpp}` (about 8 lines)

- hpp: include `pipeline/retry_futility.hpp`; member
  `std::shared_ptr<execution_progress> _execution_progress = std::make_shared<execution_progress>();` next to
  `_gpu_executors` (`:243`). It outlives the executors (destroyed in `~task_scheduler`, `:127`).
- cpp: pass `_execution_progress` as the sixth argument at `:117-124`.
- cpp, `drain_after_error` (`:265-311`): after the `drain_and_wait()` loop (`:291-293`) log once per executor
  `SIRIUS_LOG_INFO("task_scheduler: GPU:{} executor metrics (cumulative): tasks_executed={} oom_reschedules={} futile_aborts={}", ...)`.
  One line, cumulative for the process; useful next to "draining after error" in CN logs.

### 3.6 Error propagation (unchanged, verified) and behaviour

`completion_handler::report_error` first-call-wins (`completion_handler.hpp:51-62`) ->
`sirius_engine.cpp:264-278` logs `Error executing query`, `drain_after_error()`, rethrow ->
`streaming_fragment::run` (`streaming_fragment.cpp:204-227`) -> `ffi::Fragment::run` (`sirius_ffi.cpp:990-1027`)
-> `engine.rs:693-695` `"failed to execute fragment: {err}"` (and the process-wide parked wipe at
`engine.rs:291-316`, fix 2's territory) -> `compute_node_service.rs:864-910` -> `:523-528`
`"fragment instance {id} failed: {cause}"` -> FE -> `ERROR 1064 (HY000)`. Today's client text
(`E/cn1/runs/q05.r0.err`) ends `GPU pipeline task exceeded maximum retry limit (100) for original task 2567:
OOM at operator GPU_SCAN (index 0)`; after the fix it ends with the message of 3.4 + the reason of 3.1.

**SF1000, q05 (inference from the measured chain).** Round 1: 25 first attempts, each partial-granted, each
OOMs (retry_count 0: rule 2 says no). Each retry-1 gate (round 2, from +2.62 s) is partial (3.08 GB of
6.64 GB), freed 0, epoch snapshotted; its OOM ~40 ms later: required 3.38 GB > granted 3.08 GB, epoch
unchanged (nothing can complete while it runs: fact A), no first attempt in flight -> futile -> ERROR at about
+2.66 s, `Error executing query` and drain (~71 ms), CN failure ~75 ms later, client error at about +2.85 s
instead of +101.5 s. The remaining 24 retries hit `has_error()` at `:312-316` or are dropped at the new
gate early-out. The one stray successful task per window (finding B3) ran before the retry-1 gates it could
affect, so it does not add a round; if it does for one task, the next task's retry fires ~40 ms later.

| query | first OOM | expected futile ERROR (engine log) | expected client fail (runs.csv) | today |
|---|---:|---:|---:|---:|
| q05 | +1.61 s | ~+2.66 s | ~2.9 s | 101.5 s |
| q08 | +1.73 | ~+4.13 | ~4.3 | 168.3 |
| q09 | +2.38 | ~+4.72 | ~4.9 | 232.5 |
| q17 | +1.20 | ~+2.45 | ~2.6 | 103.9 |
| q18 | +9.29 | ~+10.02 | ~10.2 | 54.2 |
| q21 | +2.68 | ~+3.85 | ~4.0 | 114.5 |

Log volume per failing query: `reschedule (retry` <= 2 x stuck originals (was 100 x), `proceeding with
partial reservation` about 2 x stuck originals + a few drain stragglers, exactly one `is futile` ERROR, no
`exceeded 100 retries`.

**The 15 passing queries.** The rule is reached only inside the `task_reschedule_exception` catch, which no
passing query enters (0 reschedule lines in their windows on cn1 and on all cn4 CNs; `E/cn1-cnlog.txt`
`oom_resched=0`; `scratchpad/fix/designs/_bucket_oom.py`). Added hot-path work per task: one `has_error()`
load, one epoch load, filling seven fields, two relaxed atomics for the guard, one epoch increment. No data
path changes, so results cannot move.

**Cases that must keep retrying, and do.**
- #732 cross-GPU batch-lock contention (SF100 q11, `cache=table_gpu`, `num_gpus=2`): the probe's OOM comes
  from the clone in `lock_or_prepare_batch` (`batch_lock_utils.hpp:122-124`) via the prepare-path handler
  (`gpu_pipeline_task.cpp:638-664`) with a **full** grant (the pool is not full; the problem is a lock) ->
  rule 4. If the pool were coincidentally full, the build task holding the batch is a first attempt in flight
  on the other executor -> rule 8; when it completes the epoch advances -> rule 7. Inference from source;
  the 2-GPU run in section 6.4 checks it.
- `cuda_launch_reschedule_exception` (#1253): rule 1.
- Sink-deferral OOM (`gpu_pipeline_task.cpp:721-751`): records no requirement -> rule 6 (dormant, today's
  100 retries; decision 3).
- `_allocator == nullptr` or a plain `rmm::out_of_memory` (requested unknown): `required` is 0 or a weak
  bound -> rule 6 dormant-never-wrong.
- Retry whose downgrade freed something: rule 5; `MAX_RETRIES` remains the backstop for spills that free
  too little per round.
- First attempt on a partial grant: rule 2 (measured: 40 useful lineitem batches were parked by
  partial-granted first attempts before the pool filled).
- Standalone DuckDB extension: single-flight `query_lifecycle_mutex_` (`sirius_context.cpp:1555-1596`)
  means no other query holds memory outside repositories; same reasoning.

## 4. Tests to write first

All C++ (Catch2). No Rust `#[cfg(test)]` and no FE JUnit: nothing in `experimental/starrocks` or the FE
changes. New test files must be listed in `CMakeLists.txt` `TEST_SOURCES` (the `check-orphan-tests`
pre-commit hook, `.pre-commit-config.yaml:91-95`, `scripts/check_orphan_tests.py`, fails otherwise).

### 4.1 GPU-free predicate table: `test/cpp/pipeline/test_retry_futility.cpp` (new; add after `CMakeLists.txt:850`)

`TEST_CASE("assess_retry_futility decides from the gate observation and progress signals", "[retry_futility]")`
with one `SECTION` per row; a helper builds the "q05 futile" input and each row perturbs one field:

| row | perturbation | expect |
|---|---|---|
| not an OOM | `is_oom=false` | nullopt |
| first attempt | `retry_count=0` | nullopt |
| no observation | `gate=nullopt` | nullopt |
| full grant | `granted == requested` | nullopt |
| spilling progressed | `freed_by_downgrade=1` | nullopt |
| need unknown | `oom_required_bytes=0` | nullopt |
| need fits | `oom_required_bytes = granted` | nullopt |
| something completed | `completed_epoch_now = at_gate + 1` | nullopt |
| first attempt running | `inflight_first_attempts_now = 1` | nullopt |
| q05 | requested 6,637,897,728; granted 3,076,488,960; freed 0; downgrade_requested true; disk false; space_max 107,374,182,400; required 3,376,381,184; epochs equal; inflight 0 | futile; reason contains `held outside any task reservation`, `104297693440`, `freed 0 bytes`, `disk tier not configured`, `retry cap 100` |
| clamped floor | `requested == space_max`, granted < space_max, rest as q05 | futile (the unreachable-floor case the clamp comment at `gpu_pipeline_executor.cpp:153-164` owns is still short) |
| no downgrade executor | `downgrade_requested=false`, rest as q05 | futile; reason contains `no downgrade executor` |
| disk tier present but freed 0 | `disk_tier_configured=true` | futile; reason names the disk tier |

### 4.2 Component tests in `test/cpp/pipeline/test_oom_reschedule.cpp` (existing file, already in `CMakeLists.txt:850`)

Fixture changes (`oom_test_fixture`, `:77-121`): `setup(int num_threads, const std::string& prefix,
bool with_downgrade_executor = false)`; new members declared **before** `executor` so they outlive it:
`std::unique_ptr<sirius::data::data_repository_manager_registry> repo_registry;`,
`std::unique_ptr<sirius::parallel::downgrade_executor> downgrade;`,
`std::shared_ptr<sirius::pipeline::execution_progress> progress = std::make_shared<...>();` (passed as the sixth
ctor argument so tests can read the counters). With `with_downgrade_executor`, build it the way
`test/cpp/downgrade/test_downgrade_executor.cpp:126-134` does:
`downgrade_executor_config{.thread_pool={.num_threads=1,.thread_name_prefix="downgrade"}, .monitor_period=0ms}`,
`memory_space_id(Tier::GPU, 0)` (`:66`), `mem_space`, `*manager`; `downgrade->start()`; pass `downgrade.get()` as
the fourth ctor argument. Includes to add: `data/data_repository_manager_registry.hpp`,
`downgrade/downgrade_executor.hpp`, `pipeline/retry_futility.hpp`, `<rmm/device_buffer.hpp>`.

"Memory the sweep cannot see" (the analogue of parked sink output): `rmm::device_buffer hog(kHogBytes,
rmm::cuda_stream_default, mem_space->get_default_allocator())` held by the test (`memory_space.hpp:119`; the
same unreserved path `make_gpu_batch` uses at `test_downgrade_executor.cpp:106-119`). Arithmetic with the
fixture's 1100 MB capacity and 0.95 reservation fraction (reservation limit 1045 MB): `kHogBytes = 900 MB`
leaves an upto grant of exactly 145 MB and 55 MB of unreservable overflow headroom. Each test asserts its own
premise: `REQUIRE(f.mem_space->get_available_memory() <= kGpuCapacity - kHogBytes + 1 MB)` after creating the hog.

New task types next to `xl_task` (`:304-339`), modelled on `oom_test_task` (`:217-260`):

- `floor_aware_task`: `get_estimated_reservation_size_info` returns
  `reservation_size = max(kReservationSize, ls.get_retry_reservation_floor())` and sets
  `retry_reservation_floor` (production does this at `gpu_pipeline_task.cpp:855, 886`; test tasks override the
  estimator so they must honour the floor themselves). `execute`: remember `reservation->size()` before
  `setup_allocator` attaches it (`:178-207`, `ignore_reservation_limit_policy` at `:200` is the production
  overflow path), allocate `kFloorAllocation = 300 MB`; on `rmm::out_of_memory` read
  `requested = dynamic_cast<const cucascade_out_of_memory*>(&oom)->requested_bytes` (optional),
  `live = allocator->get_allocated_bytes(stream)`, call
  `local.update_retry_reservation_floor_after_oom(reservation_bytes, live, requested)` (mirrors
  `gpu_pipeline_task.cpp:449-457`), `oom_count++`, throw `oom_reschedule_exception(std::move(local._input_data), 0, ...)`.
- `holder_task`: estimator asks 900 MB; `execute` sleeps 300 ms and completes without allocating (the reservation
  itself charges the pool, `reservation_aware_resource_adaptor.cpp:525-531`).
- `big_estimate_task`: estimator asks 2 GB (clamped to the space max at `gpu_pipeline_executor.cpp:165-175`),
  allocates 50 MB, completes.

Tests (tag `[gpu_pipeline_executor][oom][futile]`; obtain `auto fut = f.completion.get_awaitable();` once,
before scheduling, `completion_handler.hpp:108`):

1. **"GPU pipeline executor fails the query when an OOM retry cannot make progress"** —
   `setup(1, "oom-futile", /*with_downgrade_executor=*/true)`, hog 900 MB, one `floor_aware_task`. Expected
   trace: attempt 0 gets its 50 MB in full (900+50 <= 1045), allocates 300 MB -> overflow 250 MB -> 1200 > 1100
   -> LIMIT_EXCEEDED, required = 300 MB, floor = 300 MB; retry 1 asks 300 MB -> `or_null` fails -> IDLE -> upto
   145 MB -> partial -> real sweep over the empty registry frees 0 -> final `make_reservation` -> 145 MB again ->
   observation {300 MB, 145 MB, 0, true} -> OOM again (145+155 overflow -> 1200 > 1100) -> futile. Assert:
   `completion.has_error()` within 5 s (poll like `:490-500`); `REQUIRE_THROWS_WITH(fut.get(),
   ContainsSubstring("gave up after 1 OOM retry") && ContainsSubstring("held outside any task reservation") &&
   ContainsSubstring("freed 0 bytes"))`; `oom_count == 2`; `executor->get_metrics()` has
   `oom_reschedules == 1`, `futile_aborts == 1`; after `drain_and_wait()`: `is_task_queue_empty()` and
   `progress->inflight_first_attempts == 0`; wall < 2 s. Release the hog after the executor is stopped.
2. **"GPU pipeline executor keeps retrying an OOM while another task holds a live reservation"** (pins fact A)
   — `setup(2, "oom-holder", true)`, no hog, schedule `holder_task` then `floor_aware_task`. The floor task's
   first attempt OOMs (900 reserved + 50 + 250 overflow > 1100); its retry blocks in `make_reservation`
   (holder's notifier alive, so no IDLE, so no partial grant), is granted 300 MB in full when the holder
   finishes at 300 ms, and completes. Assert: no error; `completed_count == 2`; `oom_count == 1`;
   `futile_aborts == 0`; `inflight_first_attempts == 0` after drain. If cucascade ever returns a partial grant
   while a reservation is alive, this test fails loudly.
3. **"GPU pipeline executor does not fail a first attempt that runs on a partial reservation"** —
   `setup(1, "oom-first", /*with_downgrade_executor=*/false)` (covers the no-executor branch at
   `gpu_pipeline_executor.cpp:273-283` recording `downgrade_requested=false`), hog 900 MB, one
   `big_estimate_task`: gate grants 145 of 1045 requested, proceeds (retry_count 0), 50 MB fits, completes.
   Assert: `completed_count == 1`; no error; `futile_aborts == 0`.
4. **"GPU pipeline executor drops queued tasks once the query has failed"** (pins the early-out) —
   `setup(1, "oom-dropped")`, `f.completion.report_error("simulated earlier failure")`, schedule one
   `small_task`, sleep 200 ms. Assert: `completed_count == 0`; `drain_and_wait()` returns;
   `is_task_queue_empty()`. Without the early-out the task runs and `completed_count == 1`.

Existing cases stay green unchanged: "reschedules tasks on OOM" (`:358-429`, full grants -> rule 4) and
"fails after max OOM retries" (`:446-536`: `xl_task`'s estimator is a fixed 50 MB granted in full and it
never records a requirement -> rules 4 and 6; still 100 retries, still ~5-6 s).

## 5. Build and test commands

```bash
cd /home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast
git submodule update --init --recursive          # worktrees do not auto-init submodules (already built once; sirius_unittest exists)
pixi run make                                    # incremental engine build
CUDA_VISIBLE_DEVICES=0 pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[retry_futility]"          # no GPU needed, runs anywhere
CUDA_VISIBLE_DEVICES=0 pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[gpu_pipeline_executor]"   # includes [oom] and [futile], seconds
CUDA_VISIBLE_DEVICES=0 pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[downgrade]"
CUDA_VISIBLE_DEVICES=0 pixi run make test        # full C++ + SQLLogic (what CI runs)
pixi run pre-commit run -a                       # clang-format, check-orphan-tests, codespell, rumdl (docs)
```
`std::format` is already used in this file (`gpu_pipeline_executor.cpp:39, 76, 99`); do not introduce `fmt::`.
Multi-GPU tests (`test/cpp/scan_manager/test_pin_table_multi_gpu.cpp`, `test/cpp/operator/*_mgpu.cpp`) skip on
the single-GPU worktree; say so in the PR.

**CN** (only to run the SF1000 arms; no Rust change): the CN must relink against the rebuilt engine:
`cd experimental/starrocks && pixi run cn-build` (`experimental/starrocks/pixi.toml:129-135`, depends on
`engine-build` `:122` and `apply-starrocks-patches` `:94`). The CN unit tests are `pixi run cn-test`
(`:136-140`, engine-linked, one GPU) and `pixi run cn-test-no-engine` (`:148`); running them is a regression
check, not a requirement of this fix.

**FE**: untouched. For the record, FE changes are delivered as patches in `experimental/starrocks/patches/`
applied by `scripts/apply-starrocks-patches.sh` (idempotent; `git apply --check` then apply) and rebuilt with
`pixi run fe-build` (`pixi.toml:197`, a long Maven build). This fix adds no patch and needs no FE rebuild.

## 6. Acceptance at SF1000 (orchestrator-run, 1 CN, GPU 0, outside the 02:00-03:50 UTC nightly window)

Environment: `E/capture-cn.sh` (100 GiB GPU pool, 160 GiB host, 16 GiB staging, watchdog 300 s, Quent on, async
sender dispatch on). The script hardcodes `WT=/home/prestouser/aocsa/sirius-stacks-wt/perf` (line 7); copy it to
`capture-cn-fix1.sh` with `WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-oom-failfast` (its `run-queries.sh`
reads the query SQL from the perf worktree, which is fine: identical files). On the fix-1-only build run arms A
and B as separate cluster sessions so arm B is not polluted by a failed query's parked leftovers (fix 2's leak);
once fix 2 is in the build one 22-query session covers both (INTEGRATION.md V2c, which is also the report's
before/after row for this fix). Point
`E/agent-notes/skeptic-oom4/retry_stats.py` (lines 2-4) and `scratchpad/fix/designs/_bucket_oom.py` (lines 2-3)
at the new arm directories for the post-analysis.

### 6.1 Arm A: the six, cold, campaign order (~3 min including cluster up/down)

`bash capture-cn-fix1.sh 1 fix1-armA 60 0 q05 q08 q09 q17 q18 q21`

Pass criteria, per query, in this order of authority:

1. **Engine log**: `t(first "is futile" ERROR) - t(first "OOM at operator" line in the query's window)
   <= 2 x (round-1 span measured in the same run)`, where the round-1 span is the time between the first and
   the last first-attempt OOM (`retry_stats.py` prints `round1 spans`). Expected: about one round plus 40 ms.
   Today's rounds: q05 1.01 s, q08 2.36 s, q09 2.30 s, q17 1.21 s, q18 0.69 s, q21 1.13 s.
2. **Client** (`runs/qNN.r0.err`): contains `gave up after`, `held outside any task reservation` and
   `freed 0 bytes`; does not contain `exceeded maximum retry limit`.
3. **Wall** (`runs/runs.csv` ms, from client start; secondary because fix 2's leftover-fragment time between
   queries lands here): q05 <= 4.0 s, q08 <= 6.8 s, q09 <= 7.3 s, q17 <= 4.0 s, q18 <= 11.0 s, q21 <= 5.3 s
   (= today's first OOM + 2 rounds + 0.3 s). Expected values in 3.6.
4. **Log volume**: per query `reschedule (retry` <= 2 x distinct originals; `exceeded 100 retries` = 0; exactly
   one `is futile` ERROR; `proceeding with partial reservation` <= 3 x distinct originals.
5. Total for the six: about 30 s of query time (was 774.9 s).

### 6.2 Arm B: the fifteen, cold + 2 warm, campaign order (~6 min)

`bash capture-cn-fix1.sh 1 fix1-armB 300 2 q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22`

Pass: all `pass`; oracle set identical to `E/compare-cn1.txt` (run
`bench/rtxpro6000-2gpu/tools/compare.py <runs dir> scratchpad/oracle/tpch_sf1000`: MATCH for q02 q04 q06 q12 q13
q14 q20 q22, the known VALUES-DIFFER rows for q01 q03 q07 q10 q19 q15 and EMPTY for q11 unchanged); warm
medians within the cn1 run-to-run spread (about 6%) of `E/results.md`'s cn1 column; engine log has 0
`reschedule (retry`, 0 `is futile`, 0 `proceeding with partial reservation` lines in the fifteen windows
(`_bucket_oom.py`); `cnlog_extract.py` shows `oom_resched=0` for every run.

### 6.3 Optional arm C: 4 CNs, the fifteen (0 new lines, unchanged timings; `E/cn4` had 0 reschedules).

### 6.4 Contention guard (needs the user's go-ahead, decision 1): SF100 q11, `cache=table_gpu`, `num_gpus=2`

The configuration named in the `MAX_RETRIES` comment (`gpu_pipeline_executor.cpp:339-347`, #732). Expected:
same pass/fail as before the change, no `is futile` line, reschedule count unchanged within noise. This is
the only run that exercises rules 7-8 across executors; the single-GPU worktree cannot.

## 7. Risks and how the reviewer should probe them

| # | risk | why it is bounded | probe |
|---|---|---|---|
| 1 | False abort: memory would have appeared without any signal changing | Actors considered: downgrade (`freed`), task completion anywhere (epoch), running first attempts (in-flight), CN relay/export of parked output (impossible mid-fragment: `engine.rs:278-280` one request at a time), another standalone query (single-flight `query_lifecycle_mutex_`), `unpin_table` (explicit user action). Residual: none identified; consequence would be a fast failure with a precise message instead of a possible success. | Component test 2; predicate rows 7-8; the 2-GPU run (6.4). Ask: "which actor frees GPU memory that none of the four signals sees?" |
| 2 | Dependence on cucascade IDLE semantics | The whole proof (fact A) rests on `notification_channel.cpp:49-60`. | Component test 2 fails if a partial grant is ever issued while a reservation is alive. |
| 3 | Abort starved by a continuous feed of first attempts (rule 8 always true) | On a saturated GPU tasks are serialized, so at the moment a retry's OOM is examined at most one task can have been admitted since; under a steady 1:1 interleave of fresh scan tasks and retries the rule could defer indefinitely and the query falls back to today's 100 retries (no regression). Measured SF1000: distinct originals stay constant after round 1 (25/36/56/21/23/18), so no first attempts interleave. | Arm A criterion 1; read the argument in 3.4 ("dispatch"). |
| 4 | `has_error()` early-out swallows the next query's tasks | `set_query` installs a fresh handler on every executor before the next query schedules anything (`task_scheduler.cpp:192-197`). | Arm A/B run 21 queries back to back through one context, including six failures each followed by a passing query; `pixi run make test` (SQLLogic with expected-error cases). Component test 4. |
| 5 | `inflight_first_attempts` leak or double count | RAII guard is the lambda's first statement; every exit path runs its destructor; a never-run dispatch never incremented. | Tests 1-2 assert the counter is 0 after `drain_and_wait()`. |
| 6 | Wrong attribution in the message | `held = space_max - granted` is exact under fact A, but the engine cannot say what the bytes are; the text says "held outside any task reservation" and lists the possibilities. | Predicate row q05 asserts the exact figure 104,297,693,440. Decision 2 on wording. |
| 7 | Multi-GPU coverage gap on the single-GPU worktree | mgpu tests skip; rules 7-8 across executors are source reasoning. | 6.4 before the PR goes ready. |
| 8 | Message churn | Only `notes/2026-08-09-gb200-sf100/TPCH-SF100-FAILURES.md` mentions the old text; no code or script greps it. `retry_stats.py`'s `exc` regex still matches the kept 100-retry line. | `grep -rn "exceeded maximum retry"` in the tree. |
| 9 | Sink-deferral OOMs stay on the 100-retry path | No requirement recorded there (`gpu_pipeline_task.cpp:721-751`), rule 6 keeps the fix dormant. | Decision 3. |
| 10 | Timing-only nondeterminism | The attempt count for a stuck task is 2 (first + one confirmed-futile retry); a stray completion can make it 3. Never affects results: the change only turns a slow failure into a fast one. | Arm A criterion 1 allows two rounds. |
| 11 | Fix 4b (spillable parked repositories) interaction | `freed > 0` keeps the rule quiet while spilling progresses; it fires only once HOST is full and nothing completes. Intended division of labour. | Note in the PR. |

## 8. Landing order and dependencies on the other three fixes

- **Land first.** Pure engine change, no shared files with fix 2 (Rust: `engine.rs`, `compute_node_service.rs`,
  `local_exchange.rs`), fix 3 (FE patch) or fix 4 (CN/translator, `streaming_fragment.cpp`,
  `downgrade_executor.cpp`, `sirius_ffi.cpp`). Every later SF1000 arm gets ~12 minutes shorter.
- **Fix 2** changes when the CN's `Err` arm runs (from +100 s to +3 s) and removes the leftover-fragment time
  that lands in fix 1's `runs.csv` numbers (q17 -> q18 stole 6.42 s in the campaign, finding B4). That is why
  arm A's primary criterion is measured from the first OOM in the engine log; the wall bound is secondary.
  Fix 2's tests must not assume the failure arrives after the other senders have parked.
- **Fix 3**: orthogonal.
- **Fix 4a** (fusion) removes the OOM for five of the six; this fix stays as the safety net at the next scale
  factor. Caveat (INTEGRATION.md 2.2): arm A's "one retry round after the first OOM" bound is a statement about
  today's plan shapes, where the bytes filling the pool are a leaf's `STREAMING_SINK` output outside any
  repository (`streaming_fragment.cpp:103-112`) and the downgrade frees 0. A **fused** fragment that OOMs (the
  predicted q21 case: build on the 3.8e9-row l3 scan) holds its join build in inter-pipeline port repositories
  (`src/pipeline/repository_wiring_materializer.cpp:68-69`), which TIER 1 (`downgrade_executor.cpp:223-232`)
  spills to the 160 GiB HOST tier; `freed > 0` keeps rule 5 quiet, so that query either passes slowly or runs up
  to 100 retries x spill rounds. That is the intended division of labour with 4b, not a regression (today q21
  takes 114.5 s to die), but the fusion arm must not be scored against this fix's arm A bound.
  **Fix 4b** (spill): see risk 11.
- **PR base**: `dev`, self-contained, from a personal fork (CONTRIBUTING.md "PR branching strategy"). The
  measurement branch stays on `perf/profile-sf1000`; cherry-pick the commit onto a fork branch off `origin/dev`.
  Touched engine files are identical between `origin/dev` and `d24f02c4`; only `task_scheduler.{hpp,cpp}` differ
  (+53 lines, watchdog and multi-CN additions), so re-check `git diff origin/dev..d24f02c4 -- src/pipeline/task_scheduler.cpp
  src/include/pipeline/task_scheduler.hpp` and place the three-line edits accordingly (decision 4).
- No pushes and no PR until asked (plan rule).

## 9. Docs to update in the same commit

- `docs/super-sirius/pipeline-execution.md:417-436` "Reschedule Handling": new step between 1 and 2 ("Checks
  whether an OOM retry is futile: the gate granted less than requested, the downgrade freed 0 bytes, the OOM
  needed more than the grant, and nothing completed or was running anywhere since the gate; if so the query
  fails with a message naming the held bytes vs the pool") and extend the closing sentence ("If max retries are
  exceeded, or a retry is futile, ...").
- `docs/super-sirius/execution-flow.md:150`: "retry up to 10 times with 5ms backoff" is stale; make it "retry up
  to 100 times with 50 ms backoff, or fail fast when a retry is provably futile (see pipeline-execution.md)".
- `docs/super-sirius/memory-management.md:84` (`oom_reschedule_exception` bullet): one clause pointing at the
  futility rule.
- `docs/super-sirius/configuration.md:292-294` unchanged ("No extra keys" stays true).

## 10. Commit and PR

Title (Conventional Commits, squash-merged as the PR title):

```
fix(pipeline): fail the query fast when an OOM retry cannot make progress
```

Body, written for humans (unslop; no machine-log "Verified" lines): why (at SF1000 on one CN six queries spent
54-232 s replaying a certain OOM 100 times, 775 s of a 910 s sweep, and the error named the retry cap instead of
the cause); what (one new terminal condition in the reschedule path, decided from what the reservation gate saw
and confirmed by the retry's own OOM; `MAX_RETRIES=100` and the 50 ms backoff kept for the #732 contention case;
the error now names bytes needed vs grantable and bytes held outside reservations vs the pool); a short plain
testing note (new GPU-free predicate table, four component cases, the SF1000 arm A/B numbers). Mention that the
multi-GPU tests did not run on the single-GPU worktree and whether 6.4 was run. Trailer:
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Open as Draft until the tests and arm A/B are in the
description (CONTRIBUTING.md "PR reviewability", `:265-282`).

## 11. Decisions that need the user

1. **2-GPU contention check (6.4).** It needs GPUs 0 and 1 for ~10 minutes and a standalone SF100 run with
   `cache=table_gpu`, `num_gpus=2`, which is outside the fix worktree's GPU assignment and not in the evidence
   bundle. All three designers and both judges asked for it before merge. Spec default: required before "Ready
   for review"; the PR can be opened as Draft without it.
2. **Client-facing wording.** Keep the attribution clause "held outside any task reservation (parked fragment
   output, pinned tables or idle batches the downgrade could not move)" or print the bare figure and let fix 2
   append CN-side parked totals. Spec default: the clause as drafted in 3.1.
3. **Sink-deferral OOM path** (`gpu_pipeline_task.cpp:721-751`): leave on the 100-retry path (spec default) or
   also record `live + requested` there (three lines, makes the rule apply to sink-restore OOMs too).
4. **PR base**: `dev` via cherry-pick (spec default) versus stacking on the multi-CN branch alongside the
   ~40-PR carve-up. The touched files make `dev` viable today.
5. **Order of the SF1000 arms relative to fix 2.** Either works because arm A's primary criterion is measured
   from the first OOM; only the secondary `runs.csv` bounds for q18 move with fix 2. Spec default: run arm A/B as
   soon as this fix builds, re-run arm A after fix 2 lands for the report's before/after table.
6. **Quent event for the terminal decision**: not added (the existing `finalizing{success=false}` fires); say if
   one is wanted.
