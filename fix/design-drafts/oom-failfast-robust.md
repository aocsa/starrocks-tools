# Fail fast in the OOM reschedule loop: progress-gated futility abort

Design angle: the version a senior engineer ships for the long run. Correct under cancellation
and concurrency, observable, configurable where it must be, failure modes enumerated.

Source tree read (read-only): `/home/prestouser/aocsa/sirius-stacks-wt/perf` (branch
`perf/profile-sf1000` = d24f02c4 + 45dab3be). Evidence:
`scratchpad/perf/sf1000/` (cn1/engine-cn0.log, cn1/cluster.log, cn1-cnlog.txt, cn1-quent.txt,
cn1/runs/, agent-notes/oom-failures.md, agent-notes/skeptic-oom4/). Findings B2, B3 (and C3) of
`starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md`.

"Measured" below means read from those logs or computed from them in this session. "Inference"
is marked as such.

---

## 1. Summary

Today a GPU pipeline task that OOMs is rescheduled up to `MAX_RETRIES = 100` times with a 50 ms
sleep, and nothing in the loop asks whether a retry can ever succeed. At SF1000 on one CN the six
queries whose lineitem projection does not fit the 100 GiB pool die after 54-232 s each
(775 s of a 910 s sweep), emitting 17,900 reschedule warnings and 89,580 log lines, while every
downgrade sweep frees 0 bytes and the grantable reservation (3.08-3.32 GB) stays below the
task's own OOM-derived floor (4.90-6.64 GB).

The design adds a **futility check that is decided at the reservation gate and confirmed by the
next OOM**, and only when four independent signals all say "nothing can change":

1. the downgrade sweep that just ran freed 0 bytes (nothing spillable exists right now);
2. the reservation granted is below the task's retry floor (what the previous attempt provably
   needed);
3. no task has completed successfully anywhere on this context since this task was rescheduled;
4. no first-attempt task is in flight on any executor (nothing running could release memory).

A task whose gate visit is flagged under those conditions and then OOMs again counts one
"futile attempt"; after `max_futile_attempts` (default 2) consecutive futile attempts the query
is failed with an error that names the real cause (usage vs limit, resident bytes outside live
reservations, grant vs floor, bytes freed, attempts). The 100-retry cap, the backoff and the
whole reschedule mechanism stay as they are for every other case, in particular the cross-GPU
batch-lock contention case that motivated `MAX_RETRIES = 100` (arrives with a full grant and a
first-attempt task in flight on the other GPU, so neither signal 2 nor signal 4 holds).

Predicted effect on the measured case: each of the six failures dies ~2 s after its first OOM
(q05 ~3.7 s instead of 101.5 s; q18 ~11.4 s because its first OOM is at +9.3 s), the sweep
drops from 910 s to ~160 s, per-failure log volume from ~12,500 lines to ~400. The 15 passing
queries never enter the changed branch (`oom_resched=0` for all of them, cn1-cnlog.txt).

---

## 2. Measured facts the design rests on

### 2.1 The storm (cn1/engine-cn0.log, q05 window 13:03:43.185 .. 13:05:24.700)

Computed in this session from the engine log (script inline, same regexes as
agent-notes/skeptic-oom4/retry_stats.py):

| quantity | value |
|---|---|
| first OOM | 13:03:44.795 = +1.61 s after client start (engine-cn0.log:49221) |
| distinct original tasks retried | 25, every one reaches exactly retry 100 |
| retry rounds are lockstep | round 1 spans +1.610..+2.617 s, round 2 +2.662..+3.590, round 100 +100.402..+101.330; average round period **1.007 s** = 25 tasks x ~40 ms of manager-thread time each |
| `after downgrade (N bytes freed)` lines | 2503, **N = 0 in all of them** |
| grant vs request | grant 3,076,488,960..3,318,948,864 B; request (= retry floor) 4,903,616,910..6,637,897,728 B |
| pool usage at every OOM | `global usage 107374182400 bytes` = the usage limit, every line |
| per-attempt peak | 3,376,381,184 B against a 3,318,948,864 B reservation; the attempt fails asking for ~749 MB more |
| exit | `exceeded 100 retries` at 13:05:24.553 (engine-cn0.log:61723); `Error executing query` :61725; drain done :61735 at .624 (71 ms) |

Quent for the q05 lineitem sender (cn1-quent.txt:352): `tasks=2565 Q=2228.588 Rsv=99.940
Comp=229.539`; `GPU_SCAN(0) n=2543 sum=229.538s in=6782.51GB`; `STREAMING_SINK(1) n=40 ...
in=104.30GB` (40 useful batches, 2503 wasted re-reads of the same splits).

Same shape for the other five (cn1-cnlog.txt): q08 3600 reschedules / 168.3 s, q09 5600 /
232.5 s, q17 2100 / 103.9 s, q18 2300 / 54.2 s, q21 1800 / 114.5 s. Report B3: exactly one
successful task finalised after the first OOM in each window.

### 2.2 What the client saw (cn1/runs/q05.r0.err, cn1/cluster.log:6050-6054)

```
ERROR 1064 (HY000) at line 1: fragment instance ...576d failed: fragment instance ...5774 failed:
failed to execute fragment: GPU pipeline task exceeded maximum retry limit (100) for original
task 2567: OOM at operator GPU_SCAN (index 0)
```

The engine's `report_error` text travels verbatim: `sirius_engine.cpp:268` -> FFI -> engine.rs
run_fragment `Err` (:291-316, process-wide parked wipe, `warn!("discarding every parked sender
output ...")`) -> `compute_node_service.rs:864-905 run_ready_fragment` -> `results.fail_query`
(`result_store.rs:161-185`, "First failure wins") -> FE `DefaultCoordinator.getNext()` -> client.
So whatever the engine puts in the message is what the operator reads. Today it names a retry
count, not the cause.

### 2.3 Why nothing is freed (B2, verified in source)

`downgrade_executor.cpp:223-232` TIER 1 sweeps `_data_repo_registry.get_all()`; `:294-300`
TIER 2 sweeps the pipeline task queue. The sender's `STREAMING_SINK` output repositories
escape the per-query manager (`streaming_fragment.cpp:103-112`), so the 104.3 GB parked by the
running fragment is invisible. `request_downgrade(...).get()` therefore returns 0
(`downgrade_executor.cpp:397 req->result.set_value(total_bytes)`). The WARN at `:357-363`
blames the missing DISK tier although the cause is "no convertible candidates".

---

## 3. Current mechanism (code relied on, with file:line)

### 3.1 Reservation gate, `src/pipeline/gpu_pipeline_executor.cpp:163-296` (manager thread)

```cpp
// :166
auto reservation_info = gpu_task->get_estimated_reservation_size_info(_memory_space);
auto bytes_needs      = reservation_info.reservation_size;
// :178-192  clamp to space max
if (auto const space_max = _memory_space->get_max_memory(); space_max > 0 && bytes_needs > space_max) { ... bytes_needs = space_max; }
// :203
auto reservation = _memory_space->make_reservation(bytes_needs);      // blocks until *some* grant
// :212
} else if (reservation->size() < bytes_needs && _downgrade_executor) {
  size_t shortfall = bytes_needs - reservation->size();
  ...
  reservation.reset();                                                 // :225
  size_t freed = 0;
  try {
    freed = _downgrade_executor->request_downgrade([...]() { ... make_reservation_or_null(bytes_needs) ... }).get();   // :232-243
  } catch (const std::exception& e) { SIRIUS_LOG_INFO("... downgrade request cancelled ..."); break; }
  if (new_reservation) { reservation = std::move(new_reservation); }
  else { reservation = _memory_space->make_reservation(bytes_needs); } // :249-251
  ...
  if (reservation->size() < bytes_needs) {
    SIRIUS_LOG_WARN("... after downgrade ({} bytes freed), reservation still partial ({}/{} bytes) ... -- proceeding with partial reservation", ...);   // :263-273
  }
}
// :277-280
if (auto* local_state = dynamic_cast<sirius_pipeline_task_local_state*>(gpu_task->local_state())) {
  local_state->set_reservation(std::move(reservation), reservation_info);
```

Everything a feasibility decision needs is in scope here: `freed`, `reservation->size()`,
`reservation_info.retry_reservation_floor`, `bytes_needs`, whether the request was clamped, and
(via `dynamic_cast<gpu_pipeline_task_local_state*>`) `retry_count` / `original_task_id`.

`memory_space::make_reservation` (`cucascade/src/memory/memory_space.cpp:257-266`) loops on a
notification channel and returns a partial (`make_reservation_upto`) only when the channel goes
IDLE, which is why the manager thread spends ~40 ms per gate visit (Quent `Rsv=99.940 s` for the
101 s window) and why the storm is paced by this thread, not by the 50 ms sleep.

### 3.2 Reschedule path, `gpu_pipeline_executor.cpp:311-419` (worker thread)

```cpp
} catch (task_reschedule_exception& ex) {
  if (_completion_handler && _completion_handler->has_error()) { return; }          // :314-318
  ...
  exc_stream->synchronize();                                                         // :329
  auto* cur_local = dynamic_cast<gpu_pipeline_task_local_state*>(gpu_task->local_state());
  uint32_t next_retry_count = 1; uint64_t orig_task_id = gpu_task->get_task_id();
  if (cur_local && cur_local->original_task_id.has_value()) { next_retry_count = cur_local->retry_count + 1; orig_task_id = *cur_local->original_task_id; }
  static constexpr uint32_t MAX_RETRIES = 100;                                       // :348
  if (next_retry_count > MAX_RETRIES) {
    SIRIUS_LOG_ERROR("... task {} (original task {}) exceeded {} retries at operator index {} — terminating query: {}", ...);
    _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(
      "GPU pipeline task exceeded maximum retry limit (" + std::to_string(MAX_RETRIES) + ") for original task " + std::to_string(orig_task_id) + ": " + ex.what())));   // :359-361
    return;
  }
  SIRIUS_LOG_WARN("GPU Pipeline Executor: reschedule (retry {}/{}) for task {} (original task {}), resuming from operator index {}: {}", ...);   // :366
  auto intermediate_data = ex.release_intermediate_data(); ... remove_read_only_lock();
  auto new_local_state = std::make_unique<gpu_pipeline_task_local_state>(std::move(intermediate_data), ex.get_resume_operator_index());
  new_local_state->retry_count = next_retry_count; new_local_state->original_task_id = orig_task_id;
  if (cur_local) { new_local_state->inherit_retry_reservation_floor(*cur_local); }     // :389
  ... preferred device pin ...
  auto new_task = gpu_task->create_rescheduled_task(new_task_id, std::move(new_local_state));
  std::this_thread::sleep_for(std::chrono::milliseconds(50));                         // :409
  ... telemetry finalizing({.success=false}); exit(); set_telemetry_finalized();
  this->schedule(std::move(new_task));                                                // :418
  return;
}
```

Success path: `_tasks_executed.fetch_add(1)` at `:310` (success-only counter, exposed via
`get_metrics()` `:485-488`). The rescheduled task goes into the executor's own `_task_queue`
(`itask_executor::schedule`, `src/parallel/task_executor.cpp:47-60`), interleaved FIFO with the
tasks the scheduler pushes per `device_ready` signal (`:112-122`).

### 3.3 OOM throw sites, `src/pipeline/gpu_pipeline_task.cpp`

- Operator OOM `:427-493`: computes `requested_bytes`, `global_usage` from
  `cucascade_out_of_memory` (`cucascade/include/cucascade/memory/error.hpp:43-54`), updates the
  retry floor, records failure history, throws
  `oom_reschedule_exception(data, i, "OOM at operator " + name + " (index i)")` (`:489`).
- Prepare OOM `:639-665` (`lock_or_prepare_batch` -> HOST/DISK upgrade or cross-GPU clone,
  `src/include/pipeline/batch_lock_utils.hpp:67-186`): throws with resume index 0 and message
  `"OOM while preparing batches for processing: ..."` (`:661`). **This is the path of the
  cross-GPU contention case** named in the `MAX_RETRIES` comment (`gpu_pipeline_executor.cpp:339-347`).
- Sink deferral restore OOM `:735-751`.

Retry floor: `src/include/pipeline/gpu_pipeline_task.hpp:99-118`
`next_floor = max(2 * current_reservation, live_allocated + requested, 1 MiB)`, monotonic,
inherited across reschedules; `gpu_pipeline_task.cpp:855,886` feed it into
`reservation_size_info.retry_reservation_floor` and `reservation_size = max(normal, floor)`.

### 3.4 Downgrade contract

`downgrade_executor::request_downgrade(predicate) -> std::future<size_t>` "Resolves to total
bytes freed" (`src/include/downgrade/downgrade_executor.hpp:144-153`). `has_disk_tier()`
(`:155-161`) is public; `has_viable_downgrade_target()` (`:179-187`, impl
`downgrade_executor.cpp:406-446`) is private and probes HOST capacity, not candidate existence,
so it returns true in the SF1000 case (HOST 160 GiB is empty). The only trustworthy "nothing
can be spilled now" signal is `freed == 0` from a sweep that just ran.

### 3.5 Completion / error / drain

`completion_handler::report_error` first-call-wins, `has_error()`
(`src/include/pipeline/completion_handler.hpp:51-83,122`). `sirius_engine.cpp:263-274` logs
`Error executing query`, calls `drain_after_error()` (`task_scheduler.cpp:263-…`: stop creator,
`drain_and_wait` each executor: interrupt pool + queue, join manager, wait in-flight,
`task_executor.cpp:78-114`), rethrows. Watchdog `SIRIUS_QUERY_WATCHDOG_SECS`
(`sirius_engine.cpp:113-171`) fingerprints `tasks_created + tasks_completed`, which every
reschedule advances, so it cannot see this storm (B3).

### 3.6 Config plumbing

`sirius.executor.pipeline` is parsed as `exec::thread_pool_config` with `reject_unknown()`
(`src/sirius_config.cpp:139-144`, `:692`), held as `_gpu_pipeline_executor_config`
(`src/include/sirius_config.hpp:316-317`), handed to `task_scheduler(const
exec::thread_pool_config&, ...)` (`src/include/pipeline/task_scheduler.hpp:72`,
`src/sirius_context.cpp:864-869`), which constructs one `gpu_pipeline_executor` per GPU space
(`task_scheduler.cpp:117-124`). `downgrade_executor_config` (`src/include/exec/config.hpp:39-58`)
is the precedent for "thread pool + component settings" in one struct. The CN's derived YAML
deliberately emits no `pipeline:` block (`experimental/starrocks/src/engine_settings.rs:133-136`,
test `:367-380`), so today the knob is reachable from a CN only via a full `sirius.yaml`.

---

## 4. Design

### 4.1 Principles

- **Decide with evidence, confirm with the failure.** The gate knows the memory facts; the OOM
  that follows proves the partial reservation really was insufficient. Never abort a task that
  has not OOMed under flagged conditions: "proceeding with partial reservation" exists because
  the estimate can be pessimistic and partial grants often succeed.
- **Progress anywhere cancels futility.** Any successful task completion on any executor of the
  context, or any first-attempt task still running, means memory may yet be released; keep the
  old behaviour (retry up to the cap). This is what protects the contention case and any
  "long neighbour" case without special-casing exception types.
- **Keep every existing bound.** `max_retries` (100) and the backoff (50 ms) stay as the hard
  cap and the pacing; they just become configuration instead of constants.
- **The error names the cause**, with the numbers an operator needs, and reaches the client
  unchanged through the existing path.
- **One place to reason about**: all new logic lives in `gpu_pipeline_executor.cpp`, in the two
  blocks that already own the gate and the reschedule; the rest is plumbing.

### 4.2 New state

`exec/config.hpp` (mirrors `downgrade_executor_config`):

```cpp
/// Retry policy for task_reschedule_exception (OOM and transient CUDA launch failures).
struct oom_retry_policy {
  uint32_t max_retries = 100;                                  ///< hard cap per original task (today's MAX_RETRIES)
  std::chrono::milliseconds backoff{50};                       ///< sleep before re-queueing (today's constant)
  /// Consecutive attempts that (a) were flagged futile at the reservation gate and (b) then OOMed
  /// before the query is failed. 0 disables the fail-fast and restores the pure retry cap.
  uint32_t max_futile_attempts = 2;
};

struct gpu_pipeline_executor_config {
  exec::thread_pool_config thread_pool{.num_threads = default_gpu_pipeline_num_threads, .thread_name_prefix = "gpu_pipeline"};
  oom_retry_policy oom_retry;
};
```

`pipeline/execution_progress.hpp` (new, tiny, shared by all executors of a `task_scheduler`):

```cpp
/// Context-wide progress signals the OOM fail-fast consults. Owned by task_scheduler, shared by
/// every gpu_pipeline_executor it creates; relaxed atomics, no locks.
struct execution_progress {
  std::atomic<uint64_t> completed_epoch{0};        ///< +1 per task that finished execute() successfully, any executor
  std::atomic<int64_t>  inflight_first_attempts{0};///< first-attempt (retry_count == 0) tasks currently inside execute(), any executor
};
```

`gpu_pipeline_task_local_state` (`gpu_pipeline_task.hpp:92-94` neighbourhood):

```cpp
uint32_t futile_attempts = 0;                 ///< consecutive flagged-then-OOMed attempts, carried across reschedules
uint64_t progress_epoch_at_reschedule = 0;    ///< completed_epoch snapshot when this attempt was created (0 for first attempts)
bool     gate_flagged_futile = false;         ///< set by the manager loop for THIS attempt; consumed by the reschedule path
void inherit_retry_state(const gpu_pipeline_task_local_state& prev) noexcept;  // floor + futile_attempts + pin
```

`oom_reschedule_exception` gains optional details so the abort message can quote the last
attempt (all three throw sites already compute them, `gpu_pipeline_task.cpp:446-451, 643-646,
742-747`):

```cpp
struct oom_details { std::size_t requested_bytes = 0; std::size_t global_usage = 0; std::size_t peak_allocated_bytes = 0; };
class oom_reschedule_exception : public task_reschedule_exception {
 public:
  using task_reschedule_exception::task_reschedule_exception;              // keeps existing tests compiling
  oom_reschedule_exception(std::unique_ptr<op::operator_data>, size_t, std::string, oom_details);
  [[nodiscard]] const oom_details& details() const noexcept;
};
```

`executor_metrics` (`gpu_pipeline_executor.hpp:53-55`) gains `size_t oom_reschedules{0};
size_t futile_aborts{0};`.

### 4.3 Algorithm at the reservation gate (manager thread)

Inserted after the existing downgrade branch, i.e. once `reservation`, `freed` (0 when there
was no downgrade executor) and `reservation_info` are final (between `:273` and `:277`):

```cpp
auto* cur_local = dynamic_cast<gpu_pipeline_task_local_state*>(gpu_task->local_state());
bool const retried = cur_local && cur_local->original_task_id.has_value();
if (retried && _policy.max_futile_attempts > 0) {
  auto const grant = reservation->size();
  auto const floor = reservation_info.retry_reservation_floor;
  bool const grant_below_floor = grant < floor;
  bool const nothing_freed     = freed == 0;
  bool const no_completion     = _progress->completed_epoch.load(std::memory_order_acquire) == cur_local->progress_epoch_at_reschedule;
  bool const nothing_running   = _progress->inflight_first_attempts.load(std::memory_order_acquire) == 0;
  // Second, independent trigger: the space cannot give more than everything it has.
  bool const whole_space_granted = space_max > 0 && grant >= space_max;
  cur_local->gate_flagged_futile = (grant_below_floor && nothing_freed && no_completion && nothing_running) || whole_space_granted;
  if (cur_local->gate_flagged_futile) {
    SIRIUS_LOG_DEBUG("... task {} (original {}) attempt {} flagged futile at the gate: grant {} < floor {}, downgrade freed {}, "
                     "usage {}/{} bytes, resident outside live reservations ~{} bytes, completions since reschedule 0, first attempts in flight 0", ...);
  }
}
```

Nothing else changes at the gate: the task is still dispatched with its partial reservation.
Two hygiene additions on the same thread, both no-ops today for non-error runs:

- Before `make_reservation` (`:203`): `if (_completion_handler && _completion_handler->has_error()) { /* drop task; continue */ }`.
  After an error, the manager must not block another ~40 ms per queued retry in
  `make_reservation` while `drain_after_error` waits to join it (`task_executor.cpp:97-99`).
- Before dispatch: if the task is a first attempt (`!retried`) increment
  `inflight_first_attempts`; the dispatched lambda owns an RAII guard that decrements on every
  exit path (success, reschedule, exception). The guard is created inside the lambda so a
  dispatch that never runs cannot leak a count.

### 4.4 Algorithm in the reschedule path (worker thread)

Replaces `:339-364` (constants -> policy) and extends the local-state copy at `:385-395`:

```cpp
auto const max_retries = _policy.max_retries;
if (next_retry_count > max_retries) { /* unchanged: exceeded-retries error */ }

// Futility: a gate visit flagged this attempt and it OOMed anyway; and still nobody progressed.
uint32_t futile_attempts = 0;
if (cur_local && cur_local->gate_flagged_futile &&
    _progress->completed_epoch.load(std::memory_order_acquire) == cur_local->progress_epoch_at_reschedule) {
  futile_attempts = cur_local->futile_attempts + 1;
}
if (_policy.max_futile_attempts > 0 && futile_attempts >= _policy.max_futile_attempts) {
  _futile_aborts.fetch_add(1);
  auto const message = format_futility_error(*gpu_task, *cur_local, ex, futile_attempts, next_retry_count - 1, max_retries);   // section 4.5
  SIRIUS_LOG_ERROR("GPU Pipeline Executor: {}", message);
  if (_completion_handler) { _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(message))); }
  return;   // identical exit shape to the exceeded-retries path: report_error only, drain_after_error does the rest
}
_oom_reschedules.fetch_add(1);
... existing WARN at :366 ...
new_local_state->retry_count = next_retry_count; new_local_state->original_task_id = orig_task_id;
if (cur_local) { new_local_state->inherit_retry_state(*cur_local); }      // floor + pin, as today
new_local_state->futile_attempts              = futile_attempts;          // 0 if progress happened or the gate did not flag
new_local_state->progress_epoch_at_reschedule = _progress->completed_epoch.load(std::memory_order_acquire);
std::this_thread::sleep_for(_policy.backoff);
```

Ordering argument (why the epoch is read in three places): it is snapshotted when the retry is
created, compared at the gate, compared again at the OOM. A completion anywhere in that window
resets `futile_attempts` to 0. A completion is the only event that can turn a full pool into a
grant, other than a downgrade (covered by `freed`) or the CN engine thread freeing parked
repositories (cannot happen during a run: one fragment at a time, `engine.rs:278-280`, and the
engine's single-flight `query_lifecycle_mutex_`, report B1).

### 4.5 The error message

Built from what the executor can see; everything is a plain number, no attribution the
executor cannot back:

```
GPU pipeline task cannot make progress and is failing the query: original task 2567 (pipeline 0,
OOM at operator GPU_SCAN (index 0)) OOMed on 3 attempts (2 consecutive futile) of at most 100
retries; memory space GPU:0 usage 107,374,182,400 / 107,374,182,400 bytes, ~94.1 GB resident
outside live task reservations (parked or idle batches this task cannot evict); last attempt:
reservation 3,318,948,864 bytes (retry floor 6,637,897,728), allocation request 748,875,880
bytes, peak 3,376,381,184; downgrade sweep freed 0 bytes (no spillable batches; DISK tier not
configured); no task completed since the previous attempt.
```

Sources: usage/limit = `_memory_space->get_max_memory() - get_available_memory()` and
`get_max_memory()` (`memory_space.hpp:113-116`); "resident outside live reservations" =
`usage - get_total_reserved_memory()` (reservations are charged to the same counter as live
allocations, `reservation_aware_resource_adaptor.cpp:525-531`; **inference**: this equals idle
data plus running tasks' overflow, close to but not exactly the parked bytes, so the text says
"parked or idle"); grant/floor from the local state's `reservation_size_info`; request/peak from
`oom_details`; `freed` carried on the local state from the gate; DISK from
`_downgrade_executor->has_disk_tier()`. The CN adds its own outer frame today ("fragment
instance ... failed: failed to execute fragment: ..."); fix 2 (bookkeeping) is the right place to
append the CN's exact parked-slot totals if wanted.

### 4.6 Configuration

`sirius.yaml`:

```yaml
sirius:
  executor:
    pipeline:
      num_threads: 4
      oom_retry:
        max_retries: 100          # hard cap per original task
        backoff_ms: 50
        max_futile_attempts: 2    # 0 = disable the fail-fast (pure retry cap, pre-change behaviour)
```

Parser: `from_yaml(node, gpu_pipeline_executor_config&)` reads `num_threads` / `cpu_affinity`
into `.thread_pool` (unchanged keys, so every existing YAML keeps parsing) and an optional
`oom_retry` map into `.oom_retry` with `greater_than<int>{0}` on `max_retries`/`backoff_ms`
and `>= 0` on `max_futile_attempts`; `reject_unknown()` as today. No env var: defaults are the
intended behaviour and the CN reaches YAML through a full `sirius.yaml` already (cn-tuning
skill, "derived-flag path vs a full sirius.yaml"). Optional follow-on in the fork layer:
`SIRIUS_CN_OOM_MAX_FUTILE_ATTEMPTS` emitted by `derive_sirius_config_yaml` (would need the
`pipeline_pool_is_not_pinned_from_yaml` test at `engine_settings.rs:367-380` changed to assert
on `cpu_affinity` under `pipeline` instead of the literal `pipeline:`).

### 4.7 Observability

- ERROR line with the full message (4.5) once per abort; DEBUG line per flagged gate visit
  (bounded by `max_futile_attempts + 1` per original task); the existing per-retry WARN stays.
- `executor_metrics{tasks_executed, oom_reschedules, futile_aborts}` via `get_metrics()`;
  `task_scheduler` logs the per-executor totals in the existing drain-after-error INFO
  (`task_scheduler.cpp:267`) so a CN log shows `oom_reschedules=75 futile_aborts=1` next to
  "draining after error".
- Quent: unchanged FSM (`quent::task::TaskHandle`, `sirius_pipeline_itask.hpp:110`; the
  finalizing event carries only `success`). The aborted attempt is finalized by the task
  destructor exactly like the exceeded-retries path today. The per-operator Computing sums shrink
  by construction (2543 -> ~75 GPU_SCAN executions for q05), which is itself the measurable
  signal in `quent_bp.py` output.

### 4.8 Cancellation and concurrency

- **First error wins.** Both new exits use `report_error` (first-call-wins). Concurrent workers
  in the reschedule path see `has_error()` at `:314` and return without re-queueing; the manager
  loop's new `has_error()` check stops feeding retries. `drain_after_error` then interrupts the
  pool and queue and joins the manager (`task_executor.cpp:78-114`). Measured drain today: 71 ms
  (engine-cn0.log:61725-61735); the new check can only shorten it.
- **Downgrade cancellation.** `request_downgrade(...).get()` throws when the downgrade executor
  shuts down (`cancel_pending_requests`, `downgrade_executor.cpp:481-490`); the existing catch
  at `:239-244` breaks out of the loop; untouched.
- **Counter integrity.** `inflight_first_attempts` is incremented on the manager thread before
  dispatch and decremented by an RAII guard inside the lambda; exceptions, reschedules and
  successes all pass through the guard's destructor. `completed_epoch` is incremented where
  `_tasks_executed` is today (`:310`). Both are relaxed/acquire atomics on a struct owned by the
  `task_scheduler`, which outlives its executors (it destroys them in `~task_scheduler`).
- **Multi-GPU.** The struct is shared by every executor the scheduler creates
  (`task_scheduler.cpp:117-124`, GPU and HOST tiers), so a first attempt running on GPU 1 (the
  build task holding a batch in the contention case) suppresses the flag for a retry on GPU 0.
  HOST-tier executors' completions also count as progress; that only makes the check more
  conservative (delays an abort by at most one round), never less safe.
- **No engine-side cancel exists** (`compute_node_service.rs:449-476` is a stub; `sirius_ffi.cpp`
  has no abort entry). The design does not depend on one; when one lands it will use the same
  `report_error` and inherit the same drain.
- **Watchdog.** Untouched. Note for later hardening (out of scope): excluding rescheduled tasks
  from `query_progress_fingerprint` (`sirius_engine.cpp:113-125`) would let the 60 s CN watchdog
  catch storms this predicate misses.

### 4.9 The contention case, explicitly

`MAX_RETRIES` was raised 10 -> 100 for SF100 Q11, `cache=table_gpu`, `num_gpus=2`: the build
batch is held in `processing` on one GPU while a probe task on the other GPU needs it; each
convert-release cycle is O(100 ms) (`gpu_pipeline_executor.cpp:339-347`). That OOM comes out of
`prepare_for_processing` (`gpu_pipeline_task.cpp:639-665`, `sirius_physical_operator.cpp:83-115`,
`batch_lock_utils.hpp:67-186`). Two independent reasons the flag cannot be set there:

1. The pool is not at its limit (the problem is a lock, not bytes), so `make_reservation` grants
   in full and the partial-reservation branch that computes `freed`/`grant_below_floor` is not
   entered. The retry floor is only ever compared with a partial grant.
2. Even if the pool were coincidentally full, the task that holds the batch is a first attempt in
   flight on the other executor -> `nothing_running == false`, and when it finishes
   `completed_epoch` advances -> `no_completion == false`.

So that case keeps exactly today's budget: up to 100 retries x (50 ms + gate). The same holds
for `cuda_launch_reschedule_exception` (`d42ff3e4`): it is not an OOM, the gate is normally
satisfied, the flag is never set.

---

## 5. Behaviour under the two workloads

### 5.1 The measured SF1000 case (q05, 1 CN, 100 GiB), timeline with the default policy

Round period is unchanged (1.007 s: 25 tasks x ~40 ms of manager time; the storm's pacing is
`make_reservation` blocking, section 2.1), so the prediction is in rounds:

| time | what happens |
|---|---|
| +1.61 .. +2.62 s | round 1: 25 first attempts OOM at usage == limit (as today). Each is rescheduled with `futile_attempts=0`, epoch snapshot E1 (E1 advanced once by the single late success, so a few early tasks snapshot E0). |
| +2.66 .. +3.59 s | round 2 gate visits: retried, `freed==0`, grant 3.3 GB < floor 5.8 GB, no first attempt in flight (the last one OOMed at +2.62), epoch unchanged for all tasks rescheduled after the late success -> flagged. They OOM (~40 ms later) -> `futile_attempts=1`. |
| +3.66 .. | round 3: first flagged task is re-flagged, OOMs -> `futile_attempts=2 >= 2` -> `report_error` at **~+3.7 s** (1 round after the earliest flagged task; ~2.1 s after the first OOM). |
| +3.7 .. +3.8 s | `Error executing query`, `drain_after_error` (71 ms measured today), CN `fragment run failed`, FE cancels. |

Per query (first OOM from B3's table + ~2.1 s): q05 ~3.7 s (was 101.5), q08 ~3.8 (168.3),
q09 ~4.5 (232.5), q17 ~3.3 (103.9), q18 ~11.4 (54.2; its own senders legitimately run for 9.3 s
first), q21 ~4.8 (114.5). With `max_futile_attempts: 1` subtract one round (~1.0 s). Attempts per
stuck task: 3 (first + 2 flagged) instead of 101; wasted split re-reads for q05: ~75 instead of
2503; reschedule WARN lines ~50 instead of 2500; total per-retry log lines ~375 instead of
~12,500.

The plan's acceptance row says "time-to-fail <= 5 s each". Five of six meet it; q18 cannot,
because 9.3 s of it is real work before the first OOM (cluster.log / B3 table). The criterion
should read "<= 5 s after the first OOM" (or "<= first_OOM + 5 s"); I flag this rather than
promise it.

### 5.2 The 15 passing queries

`oom_resched=0` for every run of q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22
(cn1-cnlog.txt), and all 17,917 `bytes freed` lines fall inside the six failing windows
(2503+3603+5603+2103+2302+1803 = 17,917), so the partial-after-downgrade branch is never
entered by a passing query. New code they execute: one `has_error()` load per gate visit, one
relaxed increment/decrement pair of `inflight_first_attempts` per task, and the epoch increment
that replaces `_tasks_executed`'s. Per task that is three uncontended atomics next to a ~40 ms
GPU read; no measurable change is expected, and the acceptance arm checks it (warm medians
within noise, `compare.py` set unchanged, `oom_resched=0`).

The reservation-gate blocks C3 measured for q10/q12/q14 (47-178 ms, inside `make_reservation`)
are untouched: this design does not change when or how the gate waits, only what the reschedule
concludes afterwards.

---

## 6. Files to touch (engine `src/` -> dev PR; CN and docs as noted)

| file | change |
|---|---|
| `src/include/exec/config.hpp` (:28-58) | add `oom_retry_policy`, `gpu_pipeline_executor_config` |
| `src/sirius_config.cpp` (:139-144, :692) | `from_yaml` for the new struct (existing keys unchanged, new optional `oom_retry` map, `reject_unknown`) |
| `src/include/sirius_config.hpp` (:272, :316-317) | member and accessor type |
| `src/include/pipeline/execution_progress.hpp` (new) | `execution_progress` |
| `src/include/pipeline/task_scheduler.hpp` (:72) / `src/pipeline/task_scheduler.cpp` (:49, :95-125, :267) | take `gpu_pipeline_executor_config`, own the `shared_ptr<execution_progress>`, pass policy + progress to executors, log metrics in `drain_after_error` |
| `src/sirius_context.cpp` (:864-869) | pass the new config |
| `src/include/pipeline/gpu_pipeline_executor.hpp` (:53-55, :76-81, :146-152) | ctor takes `gpu_pipeline_executor_config` + `shared_ptr<execution_progress>` (test-friendly overload keeps the old signature with defaults); `_policy`, `_progress`, counters, metrics fields |
| `src/pipeline/gpu_pipeline_executor.cpp` (:163-296, :311-419) | gate flagging, `has_error` early-out, in-flight RAII guard, policy-driven cap/backoff, futility abort, `format_futility_error` |
| `src/include/pipeline/gpu_pipeline_task.hpp` (:92-118) | `futile_attempts`, `progress_epoch_at_reschedule`, `gate_flagged_futile`, `inherit_retry_state` |
| `src/include/pipeline/oom_reschedule_exception.hpp` (:64-67) | `oom_details` + accessor |
| `src/pipeline/gpu_pipeline_task.cpp` (:489, :661, :748) | pass `oom_details` |
| `test/cpp/pipeline/test_oom_reschedule.cpp` | new cases (section 7); fixture gains an optional real `downgrade_executor` |
| `test/cpp/config/` | YAML parsing test |
| `docs/super-sirius/pipeline-execution.md` (:417-436), `configuration.md` (:92-94), `memory-management.md` (:73-84) | document the policy, the four signals, the error text |
| optional, fork layer: `experimental/starrocks/src/engine_settings.rs` (:73-160, test :367-380) | env -> YAML for the knob |
| optional, with fix 2: `experimental/starrocks/src/engine.rs` (:291-316) | append parked-slot totals to the error |

Not touched: `downgrade_executor.cpp` (its misleading WARN at :357-363 belongs to fix 4b),
`sirius_engine.cpp`, the FFI, the translator, the FE.

---

## 7. Tests

### 7.1 Unit (no GPU)

- `oom_retry_policy` YAML: defaults when the map is absent; explicit values; `max_futile_attempts: 0`
  accepted; `max_retries: 0` and `backoff_ms: -1` rejected; unknown key under `pipeline` and
  under `oom_retry` rejected; a pre-change YAML with only `num_threads` parses identically.
- `gpu_pipeline_task_local_state::inherit_retry_state` carries floor, futile count and pin; a
  fresh state has `futile_attempts == 0`, `gate_flagged_futile == false`.

### 7.2 Component (Catch2, GPU, `test/cpp/pipeline/test_oom_reschedule.cpp`, tag `[gpu_pipeline_executor][oom][failfast]`)

Fixture: the existing 1100 MB space (`kGpuCapacity`) plus a real `downgrade_executor` built as
in `test/cpp/downgrade/test_downgrade_executor.cpp:126-134` over an empty repository registry
(so every sweep frees 0), and a test task type that mimics the real task's retry floor
(`update_retry_reservation_floor_after_oom` before throwing; `get_estimated_reservation_size_info`
returns `max(kReservationSize, floor)`). "Memory the sweep cannot see" = a `rmm::device_buffer`
of 800 MB held by the test from `mem_space->get_default_allocator()` (the analogue of parked
batches).

1. **Fails fast when retries are futile.** One floor-aware task needing 400 MB against 300 MB of
   headroom. Expect `completion.has_error()` within 2 s; message contains `cannot make progress`,
   `usage`, `retry floor`, `downgrade sweep freed 0 bytes`; `oom_count == 1 + max_futile_attempts`
   exactly (deterministic: nothing else runs); `get_metrics().futile_aborts == 1`;
   `drain_and_wait()` leaves the queue empty and `inflight_first_attempts == 0`.
2. **Keeps retrying while a first attempt is in flight.** Same, but a holder task (first attempt)
   owns the 800 MB inside its execute for 600 ms, then frees it. Expect no error, both tasks
   complete, `oom_count >= 1`, `futile_aborts == 0`. This is the contention/"long neighbour"
   shape.
3. **Progress resets the count.** Small tasks scheduled between retries (completing every
   ~100 ms) while the buffer is held for 1 s, then released: no abort; the retrying task
   completes after the release.
4. **Disabled policy restores the retry cap.** `max_futile_attempts = 0`, `max_retries = 5`:
   the futile scenario ends with the pre-change text `exceeded maximum retry limit (5)`,
   `oom_count == 6`.
5. **Whole-space trigger.** A task whose floor exceeds the space on an otherwise empty space:
   aborts after `max_futile_attempts` with the same error shape (this also makes a fast version
   of the existing "fails after max OOM retries" test possible).
6. **Existing tests unchanged**: "reschedules tasks on OOM" (holders are first attempts in
   flight; never flagged) and "fails after max OOM retries" (its test task reports a fixed 50 MB
   reservation and floor 0, so `grant_below_floor` is false and it still runs to the cap).

### 7.3 SF1000 acceptance (orchestrator-run, 1 CN, 100 GiB pool, same order as the campaign)

- q05 q08 q09 q17 q18 q21: `fail` within `first_OOM + 5 s` (from `cn1/runs/runs.csv` start and
  the first `OOM at operator` line); `runs/qNN.r0.err` contains `cannot make progress` and the
  `usage ... / ...` and `retry floor` numbers; per query `reschedule (retry` lines
  <= stuck_tasks x (`max_futile_attempts` + 1) (q05: <= 75 vs 2500); `exceeded maximum retry`
  absent.
- 15 passing queries: status/rows unchanged, oracle `compare.py` set identical, warm medians
  within noise of the campaign table, `oom_resched=0`, no `flagged futile` DEBUG line.
- Sweep wall: from 910 s to ~160 s (six failures ~30 s incl. q18 instead of 775 s).
- Contention regression guard (not SF1000): SF100 Q11 with `cache=table_gpu`, `num_gpus=2`
  (the scenario in the `MAX_RETRIES` comment) still passes.

---

## 8. Predicted effect (numbers derived from the evidence)

| metric | today (measured) | predicted |
|---|---|---|
| time to fail, q05/q08/q09/q17/q18/q21 | 101.5 / 168.3 / 232.5 / 103.9 / 54.2 / 114.5 s | ~3.7 / 3.8 / 4.5 / 3.3 / 11.4 / 4.8 s (first OOM + ~2 rounds of 1.0 s) |
| six failures' share of the 1-CN sweep | 775 s of 910 s | ~30 s of ~165 s |
| attempts per stuck task | 101 | 3 (first + 2 flagged) |
| wasted split re-reads, q05 | 2503 (`GPU_SCAN n=2543`, 40 useful) | ~75 |
| per-retry log lines, q05 | ~12,500 (5 per retry) | ~375 (+1 ERROR, <= 75 DEBUG) |
| error text | retry count | cause with usage/limit, resident-outside-reservations, grant/floor, freed, attempts |
| 15 passing queries | baseline | unchanged (branch not entered; 3 atomics per task) |
| contention case budget | 100 x (50 ms + gate) | identical |

Not fixed by this change (by design): the six still fail on 1 CN (fixes 3/4), the 0-38 GB
post-failure leak (fix 2), the 40 ms gate pacing (C3/B3 mechanism).

---

## 9. Risks and failure modes

| # | failure mode | when it can happen | consequence | mitigation in the design |
|---|---|---|---|---|
| 1 | False abort of a query that would have recovered | memory released by an actor the four signals cannot see | query fails ~2 s after its OOMs with a clear message instead of possibly succeeding | signals cover task completion (epoch), running tasks (in-flight), spill (`freed`); the only remaining actor is a concurrent query on another context in the same process, which the single-flight lifecycle excludes today; `max_futile_attempts` tunable, 0 disables; confirmed by an actual OOM, never by the gate alone |
| 2 | Fail-fast never fires | a first attempt is perpetually in flight elsewhere, or completions keep trickling | falls back to exactly today's 100-retry behaviour | acceptable by construction; the hard cap is still there |
| 3 | Race between gate flag and OOM | a completion lands after the gate check | the OOM would count as futile | epoch re-checked in the reschedule path; any change resets the count to 0; default N=2 absorbs one more |
| 4 | Counter leak in `inflight_first_attempts` | an exit path without the guard | flag suppressed forever (fail-fast disabled) or never suppressed (false aborts) | RAII guard inside the lambda; component test 1 asserts the counter is 0 after drain |
| 5 | Tests relying on `MAX_RETRIES = 100` timing | `test_oom_reschedule.cpp:445-560` asserts `oom_count >= 10` | none: that task's floor is 0, so the flag cannot set | keep the test; optionally speed it up with `max_retries` |
| 6 | Wrong attribution in the error text | "resident outside live reservations" is usage minus reservations, not parked bytes | operator misreads 94 GB as "parked" when 104 GB was parked and ~10 GB was in-flight overflow | wording "parked or idle batches this task cannot evict"; fix 2 can append the CN's exact parked totals |
| 7 | HOST-tier executors count as progress | HOST executors exist in `task_scheduler` | abort delayed by <= one round | acceptable; documented |
| 8 | Post-error manager blocked in `make_reservation` | queued retries after the first error | `drain_and_wait` join waits for a release (today 71 ms, releases are frequent) | new `has_error()` early-out before the gate blocks |
| 9 | Config schema change under `sirius.executor.pipeline` | existing YAML | none: old keys parse unchanged; `reject_unknown` still guards typos | parser test with a pre-change YAML |
| 10 | Whole-space trigger fires on a pessimistic estimate | request clamped to `space_max` on an empty space, task OOMs anyway | abort after N attempts | if the whole space was reserved and the task still OOMed, it needs more than the space; nothing in the engine can help |

Determinism: the decision depends on timing only through "did anyone finish in between", which
is exactly the property that decides whether a retry can help; the outcome for a stuck task is
the same on every run (fail), the exact attempt count may vary by one. No wrong result can be
produced: the change only converts a slow failure into a fast one.

---

## 10. Effort, dependencies, PR plan

- **Effort**: ~300 lines of engine code and ~350 lines of tests; 1.5-2 engineer-days including
  docs; the SF1000 acceptance arm is short because the failures now die in seconds
  (~15 min for the six plus the 15 passing queries).
- **Dependencies on the other three fixes**: none required. Order recommendation: land this
  first; every later SF1000 arm (fixes 2, 3, 4) then spends seconds instead of minutes on each
  failure. Interactions: fix 4b (spillable parked repositories) makes `freed > 0` while HOST has
  room, so the flag correctly stays off while spilling works and turns on again when HOST fills;
  fixes 3/4a change plans so the six may stop OOMing at all, and this stays the safety net; fix 2
  changes what the CN does after the error (parked wipe, queued senders) and is the place for the
  CN-side message enrichment.
- **PR**: one self-contained engine PR against `dev` from a personal fork (CONTRIBUTING "PR
  branching strategy"), Conventional Commit `fix(pipeline): fail the query fast when OOM retries
  cannot make progress`, Draft until the component tests and the SF1000 arm are attached to the
  description in plain prose. Optional follow-up PR in the fork layer for the CN env knob.

---

## 11. Open questions

1. Default `max_futile_attempts`: 2 (this design) costs one extra round (~1 s at 25 stuck tasks)
   over 1; the value should be confirmed on the SF1000 arm and on an SF100 2-GPU contention run.
2. Whether `get_total_reserved_memory()` is exactly "live task reservations" on the GPU adaptor
   (read: `do_reserve` adds to both `_total_allocated_bytes` and `_total_reserved_bytes`,
   `reservation_aware_resource_adaptor.cpp:525-531`); the message wording hedges for now.
3. Whether to also exclude rescheduled tasks from the watchdog fingerprint
   (`sirius_engine.cpp:113-125`); cheap, separate.
4. `has_viable_downgrade_target()` is private; the message uses `has_disk_tier()` only. Exposing
   the viability probe would let the text say "HOST tier has room but holds no spillable
   candidates", which is the true B2 story.

---

## Appendix A. Excerpts relied on (verbatim, file:line)

`src/pipeline/gpu_pipeline_executor.cpp:263-273`
```cpp
      if (reservation->size() < bytes_needs) {
        SIRIUS_LOG_WARN(
          "GPU Pipeline Executor: after downgrade ({} bytes freed), reservation "
          "still partial ({}/{} bytes) for pipeline {} task {} -- proceeding "
          "with partial reservation",
          freed, reservation->size(), bytes_needs, gpu_task->get_pipeline_id(), gpu_task->get_task_id());
      }
```

`src/pipeline/gpu_pipeline_executor.cpp:339-364`
```cpp
          // Bumped from 10 to 100 as part of follow-up #17. SF100 Q11 with
          // cache=table_gpu + num_gpus=2 exhausted the old 10-retry budget
          // against cross-GPU BUILD_PROBE batch-lock contention: the batch
          // was held in `processing` on one GPU while the probe task on the
          // other GPU needed it. Each convert-release cycle is O(100ms) at
          // SF100 scale, so 10 retries × 5ms backoff (50 ms total) was far
          // too short. With 100 retries × 50 ms backoff (~5 s) the probe
          // tasks get enough patience to clear the contention window while
          // still bailing out on truly wedged queries.
          static constexpr uint32_t MAX_RETRIES = 100;
          if (next_retry_count > MAX_RETRIES) {
            ...
              _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(
                "GPU pipeline task exceeded maximum retry limit (" + std::to_string(MAX_RETRIES) +
                ") for original task " + std::to_string(orig_task_id) + ": " + ex.what())));
```

`src/pipeline/gpu_pipeline_executor.cpp:308-311`
```cpp
        try {
          task->execute(exc_stream);
          _tasks_executed.fetch_add(1, std::memory_order_relaxed);
        } catch (task_reschedule_exception& ex) {
```

`src/include/pipeline/gpu_pipeline_task.hpp:99-108`
```cpp
  void update_retry_reservation_floor_after_oom(std::size_t current_reservation_bytes,
                                                std::size_t live_allocated_bytes,
                                                std::optional<std::size_t> requested_bytes) noexcept
  {
    auto const request_bytes = requested_bytes.value_or(kDefaultRetryRequestBytes);
    auto next_floor          = memory::saturating_mul(current_reservation_bytes, 2);
    next_floor = std::max(next_floor, memory::saturating_add(live_allocated_bytes, request_bytes));
    next_floor = std::max(next_floor, kDefaultRetryRequestBytes);
    _retry_reservation_floor = std::max(_retry_reservation_floor, next_floor);
  }
```

`src/pipeline/gpu_pipeline_task.cpp:855, 886`
```cpp
  info.retry_reservation_floor    = ls.get_retry_reservation_floor();
  ...
  info.reservation_size = std::max(normal_reservation, info.retry_reservation_floor);
```

`src/pipeline/gpu_pipeline_task.cpp:489-492` (operator OOM) and `:661-664` (prepare OOM)
```cpp
      throw oom_reschedule_exception(
        std::move(operator_input_output_data),
        i,
        "OOM at operator " + op.get_name() + " (index " + std::to_string(i) + ")");
...
    throw oom_reschedule_exception(
      std::move(local_state._input_data),
      0,
      std::string("OOM while preparing batches for processing: ") + oom.what());
```

`src/include/downgrade/downgrade_executor.hpp:144-153`
```cpp
  /**
   * @brief Asynchronously request a predicate-driven downgrade.
   * Dispatches batch downgrades until the predicate returns true or candidates
   * are exhausted. In-flight batches finish naturally.
   * @return std::future<size_t> Resolves to total bytes freed
   */
  std::future<size_t> request_downgrade(std::function<bool()> predicate);
```

`src/downgrade/downgrade_executor.cpp:357-363, 397`
```cpp
    if (disk_not_configured && !req->satisfied.load() && !req->is_monitor_request) {
      SIRIUS_LOG_WARN(
        "[downgrade] [{}] downgrade request not satisfied and disk memory space is not configured; "
        "data cannot be spilled to disk. Consider configuring a disk memory space to enable "
        "spilling.", _source_label);
    }
    ...
    req->result.set_value(total_bytes);
```

`cucascade/src/memory/memory_space.cpp:257-266`
```cpp
std::unique_ptr<reservation> memory_space::make_reservation(size_t size)
{
  std::unique_ptr<reservation> res = make_reservation_or_null(size);
  while (!res) {
    auto status = _notification_channel->wait();
    if (status == notification_channel::wait_status::SHUTDOWN) { return nullptr; }
    if (status == notification_channel::wait_status::IDLE) { return make_reservation_upto(size); }
    res = make_reservation_or_null(size);
  }
  return res;
}
```

`src/include/pipeline/completion_handler.hpp:51-62`
```cpp
  void report_error(std::exception_ptr error) noexcept
  {
    bool expected = false;
    if (_completed.compare_exchange_strong(expected, true)) {
      try { _has_error.store(true); _promise.set_exception(error); } catch (...) {}
    }
  }
```

`src/sirius_engine.cpp:261-269`
```cpp
  try {
    wait_for_query_future(future, *sirius_ctx);
    sirius_ctx->get_task_scheduler().wait_for_completion();
  } catch (const std::exception& e) {
    SIRIUS_LOG_ERROR("Error executing query: {}", e.what());
    ...
    sirius_ctx->get_task_scheduler().drain_after_error();
    throw;
```

`experimental/starrocks/src/engine.rs:282-316` (Err arm: process-wide wipe, error string kept
verbatim) and `experimental/starrocks/src/compute_node_service.rs:864-905` (`run_ready_fragment`
-> `results.fail_query(query_id, id, error)`), `src/result_store.rs:161-185` ("First failure
wins: later failures are usually downstream echoes of the first").

`src/sirius_config.cpp:139-144, 692`
```cpp
static void from_yaml(const YAML::Node& node, exec::thread_pool_config& opt)
{
  yaml::reader r(node, "thread_pool");
  r.optional("num_threads", opt.num_threads, yaml::greater_than<int>{0});
  r.reject_unknown();
}
...
      if (auto n = er.optional_node("pipeline")) from_yaml(*n, _gpu_pipeline_executor_config);
```

`src/include/exec/config.hpp:39-58` (`downgrade_executor_config` embeds `thread_pool_config` plus
component settings: the precedent for `gpu_pipeline_executor_config`).

`cn1/engine-cn0.log:49219-49223` (first OOM cycle) and `:61723-61735` (exit and 71 ms drain),
`cn1/cluster.log:6050-6054` (CN wipe, `fragment run failed`, FE `getNext` failure),
`cn1/runs/q05.r0.err` (client text).
