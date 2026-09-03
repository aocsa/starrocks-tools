# Fix 1, surgical design: fail fast at the reservation gate when a retry cannot be granted what its OOM needed

Angle: the smallest change that fixes the measured failure with the least blast radius. One
engine file carries the logic, one header gains one field, one existing test file gains three
cases, no CN or FE change.

Source tree read (read-only): `/home/prestouser/aocsa/sirius-stacks-wt/perf` (branch
`perf/profile-sf1000`). Evidence: session scratchpad `perf/sf1000/` (cn1/engine-cn0.log,
cn1/cluster.log, cn1/runs, cn1-quent.txt, agent-notes/skeptic-oom4/retry_stats.py,
agent-notes/oom-failures.md). "Measured" below means read from those files; "inference" is
marked.

## 1. Decision in one paragraph

At the reservation gate in `gpu_pipeline_executor::manager_loop`, after the downgrade attempt
has returned and the reservation is still partial (`gpu_pipeline_executor.cpp:262-272`, the
"proceeding with partial reservation" branch), abort the query when all of the following hold:
the task is a retry (`retry_count > 0`), the downgrade freed 0 bytes, and the bytes this task
can still obtain (`reservation->size() + _memory_space->get_available_memory()`) are below the
bytes its last OOM proved it needs (`live_allocated + requested` at the failing allocation, a
new `size_t` recorded next to the existing retry floor). The engine already certifies the two
facts that make this safe: `memory_space::make_reservation` hands out a partial grant only when
no other reservation is alive on the space (`cucascade/src/memory/memory_space.cpp:257-266`
with `notification_channel.cpp:49-60`), and the downgrade sweep just reported that nothing it can
see is convertible. Nothing in flight and nothing downgradable means the grant cannot grow, so the
next attempt is certain to OOM again. `MAX_RETRIES=100` and the 50 ms backoff stay as they are;
the cross-GPU contention and CUDA-launch retry paths never satisfy the new predicate.

## 2. Measured facts the design rests on

### 2.1 The failure, as the client sees it

`cn1/runs/q05.r0.err`:

```
ERROR 1064 (HY000) at line 1: fragment instance 01a0675e-...-70d5745d576d failed: fragment instance
01a0675e-...-70d5745d5774 failed: failed to execute fragment: GPU pipeline task exceeded maximum retry
limit (100) for original task 2567: OOM at operator GPU_SCAN (index 0)
```

`cn1/runs/runs.csv`: q05 101,460 ms, q08 168,285, q09 232,533, q17 103,894, q18 54,202, q21
114,485 (all `fail`, 0 rows). Sum 774.9 s of a 910.3 s sweep.

### 2.2 The retry chain (retry_stats.py over cn1/engine-cn0.log, rerun 2026-09-03)

| query | stuck originals | reschedules | first OOM | round-1 window | round-100 window | abort at |
|---|---:|---:|---:|---|---|---:|
| q05 | 25 | 2500 | +1.61 s | +1.61..+2.62 s | +100.40..+101.33 | +101.37 |
| q08 | 36 | 3600 | +1.73 | +1.73..+4.09 | +166.54..+168.15 | +168.19 |
| q09 | 56 | 5600 | +2.38 | +2.38..+4.68 | +230.17..+232.41 | +232.45 |
| q17 | 21 | 2100 | +1.20 | +1.20..+2.41 | +102.74..+103.74 | +103.78 |
| q18 | 23 | 2300 | +9.29 | +9.29..+9.98 | +53.66..+54.14 | +54.16 |
| q21 | 18 | 1800 | +2.68 | +2.68..+3.81 | +113.25..+114.23 | +114.35 |

Every original reached exactly retry 100; no rescheduled task ever succeeded ("all==100: True"
for every query). Every one of the 17,917 gate passes in the six windows logged
`after downgrade (0 bytes freed), reservation still partial` and every OOM line reports
`global usage 107374182400 bytes` (the pool limit). Zero `reschedule (retry` lines fall outside
the six windows (retry_stats.py prints `OUTSIDE WINDOW` for any; none printed), so the 15 passing
queries never entered the reschedule path on 1 CN. On 4 CNs, `cn4/engine-.cn1.log`,
`.cn2.log`, `.cn3.log` have 0 reschedule and 0 partial-reservation lines; `cn4/engine-.cn0.log`
contains the same 17,900 reschedules, all timestamped 13:03:44-13:18:07 (the 1-CN sweep), none in
the 4-CN window (13:19:30 onward, `cn4/runs/runs.csv`).

The q05 chain of original task 2567 (cn1/engine-cn0.log:49219-49223, 61597, 61723):

```
49219 [13:03:44.748] [warning] [gpu_pipeline_executor.cpp:263] ... after downgrade (0 bytes freed),
      reservation still partial (3318948864/5803790251 bytes) for pipeline 0 task 2567 -- proceeding
49221 [13:03:44.795] [warning] [gpu_pipeline_task.cpp:458] Pipeline 0: OOM at operator GPU_SCAN (id=0,
      index 0/2), requested 748875880 bytes (714.18 MB), global usage 107374182400 bytes (102400.00 MB),
      peak allocated 3376381184 bytes (3219.97 MB), bytes to materialize input 0 bytes, reservation
      3318948864 bytes (3165.20 MB), rescheduling task 2567
49223 [13:03:44.795] ... reschedule (retry 1/100) for task 2567 (original task 2567), resuming from
      operator index 0: OOM at operator GPU_SCAN (index 0)
61597 [13:05:23.587] ... reschedule (retry 100/100) for task 5043 (original task 2567) ...
61723 [13:05:24.553] [error] [gpu_pipeline_executor.cpp:350] ... task 5068 (original task 2567)
      exceeded 100 retries at operator index 0 — terminating query: OOM at operator GPU_SCAN (index 0)
```

After the first OOM the retry floor lifted the request from 5.80 GB to 6.64 GB (= 2 x 3.319 GB)
while the grant stayed at 3.08-3.32 GB for all 101 attempts ("want min=4.90GB max=6.64GB got
min=3.08GB max=3.32GB", retry_stats.py). The grant is the pool cap minus what the fragment's own
sink had parked (104.06 GB by +1.315 s, finding B1; 40 parked batches x 2.668 GB, cn1-quent.txt
q05 5774 `batches=40`).

### 2.3 Where the time goes

cn1-quent.txt, q05 fragment 5774: `wall=101.177s tasks=2565 Q=2228.588 Rsv=99.940 Prep=0.057
Comp=229.539`. The manager thread spent 99.9 of 101.2 s blocked inside `make_reservation`
(Reserving); the 4 worker slots were mostly idle. The storm is serialised by the gate, not paced
by the 50 ms sleeps (which run on the worker thread, `gpu_pipeline_executor.cpp:409`).

Teardown after the error is fast: `exceeded 100 retries` at 13:05:24.553, `draining after error`
at .553, CN `fragment run failed ... elapsed_ms=101193` at 13:05:24.627 (cn1/cluster.log). About
75 ms from engine error to CN failure, plus ~90 ms to the client (runs.csv 101,460 ms vs abort at
+101.37 s).

### 2.4 The campaign's watchdog could not see it

`capture-cn.sh:11` set `SIRIUS_QUERY_WATCHDOG_SECS=300`. The watchdog fingerprint sums
`get_tasks_created() + get_tasks_completed()` per pipeline (`sirius_engine.cpp:113-125`); each
reschedule creates a task, so the fingerprint advanced every ~40 ms and the 300 s watchdog never
fired.

## 3. Mechanism: why the gate can be certain

### 3.1 The gate today (`src/pipeline/gpu_pipeline_executor.cpp`)

```cpp
186    auto reservation = _memory_space->make_reservation(bytes_needs);
187    if (!reservation) {                       // channel shutdown: report_error + break
...
196    } else if (reservation->size() < bytes_needs && _downgrade_executor) {
197      size_t shortfall    = bytes_needs - reservation->size();
...
217      reservation.reset();  // release partial reservation before downgrade
...
224        freed =
225          _downgrade_executor
226            ->request_downgrade([mem_space, bytes_needs, &new_reservation, &reservation_mutex]() {
...                 make_reservation_or_null(bytes_needs); ... })
233            .get();
...
241      if (new_reservation) {
242        reservation = std::move(new_reservation);
243      } else {
244        // Predicate never succeeded — try one final reservation attempt
245        reservation = _memory_space->make_reservation(bytes_needs);
246      }
...
262      if (reservation->size() < bytes_needs) {
263        SIRIUS_LOG_WARN(
264          "GPU Pipeline Executor: after downgrade ({} bytes freed), reservation "
265          "still partial ({}/{} bytes) for pipeline {} task {} -- proceeding "
266          "with partial reservation", freed, reservation->size(), bytes_needs, ...);
272      }
...
286      local_state->set_reservation(std::move(reservation), reservation_info);
```

Existing gate failures already follow the pattern `report_error(...)` then `break` out of the
manager loop (`:187-195`, `:234-238`, `:248-260`, `:289-295`); `task_scheduler::drain_after_error`
(`task_scheduler.cpp:265-311`) then calls `drain_and_wait()`, which joins and restarts the manager
thread (`src/parallel/task_executor.cpp:96-128`). The new abort reuses that pattern.

### 3.2 Fact A: a partial grant means nothing is in flight

`cucascade/src/memory/memory_space.cpp:257-266`:

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

`cucascade/src/memory/notification_channel.cpp:49-60`:

```cpp
notification_channel::wait_status notification_channel::wait()
{
  ...
  _cv.wait(lock, [&, self = shared_from_this()] {
    notified = std::exchange(_has_been_notified, false);
    return notified || (_n_active_notifiers == 0) || not _is_running;
  });
  return !_is_running ? wait_status::SHUTDOWN
         : (notified) ? wait_status::NOTIFIED
                      : wait_status::IDLE;
}
```

Every reservation owns a notifier for its lifetime: `reserve`/`reserve_upto` pass the notifier
into the arena (`reservation_aware_resource_adaptor.cpp:365-381`), `reserved_arena` holds it as
`const notify_on_exit _on_exit` (`cucascade/include/cucascade/memory/memory_reservation.hpp:183-198`),
and `~notify_on_exit` posts on release (`notification_channel.cpp:97-101`). So `IDLE` is reached
only when `_n_active_notifiers == 0`: no reservation is alive on this space. A partial grant
(`make_reservation_upto`) is therefore issued only when no in-flight reservation could free memory
by finishing. The grant itself is everything the reservation limit allows:
`do_reserve_upto` adds `min(request, limit - total_allocated)` via `add_bounded(T& diff, ...)`
(`reservation_aware_resource_adaptor.cpp:535-543`, `cucascade/include/cucascade/utils/atomics.hpp:87`).

This is also why the SF1000 storm was serialised: the manager could only get a grant when the
previous stuck task had released its reservation (2.3).

### 3.3 Fact B: the downgrade reported that nothing visible is convertible

`request_downgrade(...).get()` returns total bytes freed (`downgrade_executor.hpp:145-153`,
`downgrade_executor.cpp:388-397` sets the promise to `req->bytes_freed`). TIER 1 walks every
registered repository manager (`downgrade_executor.cpp:223-232`), TIER 2 the queued pipeline tasks
(`:294-300`). `freed == 0` means neither tier had a candidate that moved. In the measured case the
bytes are the streaming sink's parked output, which escapes the managers by design
(`src/exec/streaming_fragment.cpp:103-112`, finding B2); the HOST tier was configured and empty
(`HOST_MEM=160GiB`, `engine_settings.rs:111-116`), so `has_viable_downgrade_target()`
(`downgrade_executor.cpp:406-446`) would say "viable" and cannot serve as the discriminator.
`freed == 0` from the actual sweep is the fact to use.

### 3.4 Fact C: what the task needs is already computed at the OOM

`src/pipeline/gpu_pipeline_task.cpp:433-491` (compute path; the prepare path `:638-661` is
identical in shape):

```cpp
433    } catch (const rmm::out_of_memory& oom) {
...
      size_t requested_bytes = 0;
      ...
      if (auto const* cc_oom = dynamic_cast<const cucascade::memory::cucascade_out_of_memory*>(&oom)) {
        requested_bytes       = cc_oom->requested_bytes;
        global_usage          = cc_oom->global_usage;
        retry_requested_bytes = requested_bytes;
      }
      size_t reservation_bytes = _local_state->cast<gpu_pipeline_task_local_state>().get_reservation_bytes();
      auto const live_allocated_bytes = _allocator ? _allocator->get_allocated_bytes(stream) : 0;
456    local_state.update_retry_reservation_floor_after_oom(
        reservation_bytes, live_allocated_bytes, retry_requested_bytes);
...
489    throw oom_reschedule_exception(std::move(operator_input_output_data), i, "OOM at operator ...");
```

`src/include/pipeline/gpu_pipeline_task.hpp:96-119`:

```cpp
  static constexpr std::size_t kDefaultRetryRequestBytes = 1024 * 1024;

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

  void inherit_retry_reservation_floor(const gpu_pipeline_task_local_state& previous) noexcept
  { _retry_reservation_floor = std::max(_retry_reservation_floor, previous._retry_reservation_floor); }
```

`live_allocated + requested` is a hard lower bound on what the same attempt must allocate on its
own stream to get past the allocation that failed; the resumed task re-runs the same operator on
the same input (for GPU_SCAN it re-reads the same split, `sirius_gpu_scan_operator.cpp:536-537`;
finding B3 measured the chain byte-identical over 101 attempts). The `2 x reservation` term is the
engine's retry sizing policy, not evidence of need, so the abort compares against
`live + requested` only (see 10.2 for why not the floor).

The floor is carried across reschedules (`gpu_pipeline_executor.cpp:389
inherit_retry_reservation_floor`) and folded into the next request
(`gpu_pipeline_task.cpp:855, 886`: `reservation_size = max(normal_reservation, retry_reservation_floor)`).

### 3.5 Putting A, B, C together

At the gate of a retry, after the downgrade: if `freed == 0`, the grant is everything the space
can give with nothing in flight and nothing to spill. If that is less than `live + requested`, the
attempt is certain to hit the same allocation failure. Waiting cannot help: on the CN path the
parked bytes are released only when the receiver relays them, and the receiver runs only after
this fragment finishes (`engine.rs:278-280` one request at a time; `local_exchange.rs` take_ready;
finding B1). Pinned tables are released only by an explicit unpin. Repositories of the same query
are visible to the sweep (TIER 1) and would have counted as `freed > 0`.

## 4. The change

### 4.1 `src/include/pipeline/gpu_pipeline_task.hpp` (about 8 lines)

Add one field next to `_retry_reservation_floor` (`:143`) and maintain it in the two existing
functions; no new call sites.

```cpp
  void update_retry_reservation_floor_after_oom(std::size_t current_reservation_bytes,
                                                std::size_t live_allocated_bytes,
                                                std::optional<std::size_t> requested_bytes) noexcept
  {
    auto const request_bytes = requested_bytes.value_or(kDefaultRetryRequestBytes);
    auto const required      = memory::saturating_add(live_allocated_bytes, request_bytes);
    // Hard lower bound on what the failed attempt had to allocate on its own stream: the bytes it
    // held plus the allocation that failed. Unlike the floor below, no policy multiplier — the
    // reservation gate compares the grant it can hand out against this to decide whether a retry
    // can possibly succeed.
    _oom_required_bytes = std::max(_oom_required_bytes, required);
    auto next_floor = memory::saturating_mul(current_reservation_bytes, 2);
    next_floor      = std::max(next_floor, required);
    ...unchanged...
  }

  void inherit_retry_reservation_floor(const gpu_pipeline_task_local_state& previous) noexcept
  {
    _retry_reservation_floor = std::max(_retry_reservation_floor, previous._retry_reservation_floor);
    _oom_required_bytes      = std::max(_oom_required_bytes, previous._oom_required_bytes);
  }

  [[nodiscard]] std::size_t get_oom_required_bytes() const noexcept { return _oom_required_bytes; }
 private:
  std::size_t _oom_required_bytes = 0;
```

Both OOM handlers that raise the floor (`gpu_pipeline_task.cpp:456`, `:646`) now also record the
requirement. The sink-deferral OOM handler (`:721-748`) raises no floor today and is left alone.

### 4.2 `src/pipeline/gpu_pipeline_executor.cpp` (about 35 lines, inside the existing branch at `:262-272`)

```cpp
      if (reservation->size() < bytes_needs) {
        // A retry whose last OOM needed more than this space can still hand out cannot succeed:
        // make_reservation() only returns a partial grant once no other reservation is alive on
        // the space (notification_channel IDLE), and the downgrade sweep just freed nothing, so
        // the grant is the most this task will ever get while the query is running. Retrying
        // would replay the same OOM until MAX_RETRIES (measured: 100 x ~1 s per stuck scan task
        // at SF1000 on one CN, 54-232 s per query, 17,917 partial-reservation passes freeing 0 B).
        // First attempts (retry_count == 0) and retries that freed something keep proceeding:
        // a partial grant is often enough (over-reservation up to the pool limit is allowed).
        auto* retry_state = dynamic_cast<gpu_pipeline_task_local_state*>(gpu_task->local_state());
        auto const grantable = memory::saturating_add(reservation->size(),
                                                      _memory_space->get_available_memory());
        auto const required  = retry_state ? retry_state->get_oom_required_bytes() : 0;
        if (freed == 0 && retry_state && retry_state->retry_count > 0 && grantable < required) {
          auto const pool = _memory_space->get_max_memory();
          auto const held = pool > grantable ? pool - grantable : 0;
          auto const orig = retry_state->original_task_id.value_or(gpu_task->get_task_id());
          auto msg = std::format(
            "GPU pipeline task {} (original task {}, retry {}) cannot make progress on GPU:{}: its "
            "last OOM needed {} bytes ({:.2f} GB) but only {} bytes ({:.2f} GB) are grantable; "
            "{} of {} bytes ({:.2f} of {:.2f} GB) are held by data outside any live reservation "
            "(parked fragment output, pinned tables or repositories the downgrade could not move), "
            "the downgrade freed 0 bytes and nothing is in flight that could release memory; "
            "failing the query instead of retrying (retry floor {} bytes, max {} retries)",
            gpu_task->get_task_id(), orig, retry_state->retry_count, _memory_space->get_device_id(),
            required, gb(required), grantable, gb(grantable), held, pool, gb(held), gb(pool),
            retry_state->get_retry_reservation_floor(), MAX_RETRIES);
          SIRIUS_LOG_ERROR("GPU Pipeline Executor: {}", msg);
          if (_completion_handler) {
            _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(msg)));
          }
          break;   // same recovery as the other gate failures: drain_after_error restarts the loop
        }
        SIRIUS_LOG_WARN(... existing "proceeding with partial reservation" ...);
      }
```

Notes:
- `MAX_RETRIES` is currently a function-local `static constexpr` inside the reschedule lambda
  (`:348`); hoist it to file scope in the same file so both messages can name it. No behaviour
  change.
- `reservation` is still held when the message is built and is destroyed by the `break`
  (scope exit), exactly as in the existing `:248-260` failure path.
- `gb()` is a two-line local helper (`bytes / 1073741824.0`); or use the existing MB style.
- The reschedule path (`:311-420`) is untouched: retry counting, the 100 cap, the 50 ms sleep, the
  floor inheritance, the device pin, telemetry finalisation.

### 4.3 Nothing else changes

No new setting (`exec/config.hpp` stays as is: `thread_pool_config`, `downgrade_executor_config`),
no CN change, no FE change, no proto change. The message reaches the client through the existing
chain (section 6).

## 5. Behaviour

### 5.1 Under the measured SF1000 case (1 CN, 100 GiB pool)

Per stuck scan task, attempt 0 proceeds exactly as today (partial grant, `retry_count == 0`),
OOMs, records `required = live + 748,875,880` (q05) and the floor 6.64 GB, and is rescheduled
(retry 1). Its retry reaches the gate after the fresh tasks queued ahead of it (FIFO). At that gate:
`make_reservation(6.64 GB)` blocks until the one task in flight releases, returns `IDLE`, grants
`upto` = 3.08-3.32 GB (measured range); the downgrade frees 0 (measured, 17,917/17,917);
`grantable = grant + get_available_memory() ~= grant` (CN config has
`reservation_limit_fraction: 1.0`, `engine_settings.rs:102`, so capacity == reservation limit and
nothing is allocatable beyond the grant); `required = live + 0.749 GB`.

Does `grantable < required` hold? Accounting argument (inference, the log prints peak not live):
at the OOM the pool was at its limit (`global usage 107374182400`), so what was free for this
task equalled its own live bytes; at the retry the grant equals limit minus the persistent bytes
(parked output), which only grew (one more batch finalised after the first OOM in each window,
finding B3). The storm is serialised (2.3), so no other in-flight task's bytes inflate the grant.
Hence `grant <= live_then < live_then + requested = required`, with a margin of at least
`requested` = 0.33-1.75 GB across the six queries (retry_stats.py "requested bytes"). The abort
fires on the first retry that reaches the gate.

Predicted time-to-fail (upper bound = end of round 1 + ~0.2 s teardown and propagation; lower
bound = first OOM + 0.2 s):

| query | was | first OOM | predicted fail | saved |
|---|---:|---:|---:|---:|
| q05 | 101.5 s | +1.61 | 1.8-2.8 s | ~99 s |
| q08 | 168.3 | +1.73 | 1.9-4.3 | ~164 |
| q09 | 232.5 | +2.38 | 2.6-4.9 | ~228 |
| q17 | 103.9 | +1.20 | 1.4-2.6 | ~101 |
| q18 | 54.2 | +9.29 | 9.5-10.2 | ~44 |
| q21 | 114.5 | +2.68 | 2.9-4.0 | ~111 |

Sum 774.9 s -> about 20-29 s; the 910 s sweep becomes about 155-165 s (82-83% shorter). q18
cannot meet an absolute "<= 5 s" bar: 9.3 s of it is its own senders parking 64.7 GB before the
lineitem sender starts (agent-notes/oom-failures.md section 3). The acceptance criterion should be
measured from the first OOM: fail within one retry round (<= 1.5 s after the first OOM for these
six).

Log volume: `reschedule (retry` lines drop from 17,900 to at most one per stuck original that
OOMed before the abort (<= 179 total); `proceeding with partial reservation` from 17,917 to
about twice that; the five per-retry line types that were 89,580 of 152,097 engine-log lines
shrink by ~98%. GPU_SCAN Computing wasted on re-reads: q05 229.5 s -> ~2-3 s.

The client error becomes (example values from the q05 chain, task 2567 at retry 1; `held` is
the 104.06 GB parked by the sink):

```
fragment instance ...5774 failed: failed to execute fragment: GPU pipeline task 2593 (original task
2567, retry 1) cannot make progress on GPU:0: its last OOM needed 4125257064 bytes (3.84 GB) but only
3318948864 bytes (3.09 GB) are grantable; 104055233536 of 107374182400 bytes (96.91 of 100.00 GB) are
held by data outside any live reservation (parked fragment output, pinned tables or repositories the
downgrade could not move), the downgrade freed 0 bytes and nothing is in flight that could release
memory; failing the query instead of retrying (retry floor 6637897728 bytes, max 100 retries)
```

(The `needed` figure assumes live == peak at the failing allocation; the real value is between
0.75 and 4.13 GB.)

### 5.2 Under the 15 passing queries

Dormant. Measured: 0 reschedule lines and 0 `after downgrade ... still partial` lines outside the
six failure windows on 1 CN, and 0 on all three other CNs of the 4-CN sweep (2.2). The new code is
reached only inside the branch that logs that WARN, and fires only with `retry_count > 0`. Timing
of the passing queries is unaffected: the only added work on any gate pass is one `dynamic_cast`
and one `get_available_memory()` atomic load, and only in the partial-after-downgrade branch.
Result sets are unaffected (no data path changes).

### 5.3 Cases that must keep retrying (and do)

- Cross-GPU batch-lock contention (the reason for the 10 -> 100 bump, `gpu_pipeline_executor.cpp:339-347`,
  commit 2ac106b2 #732). The probe task's OOM arrives through `lock_or_prepare_batch`'s clone
  (`batch_lock_utils.hpp:120-121`) -> `prepare_for_processing` (`sirius_physical_operator.cpp:96-104`)
  -> the prepare-path handler (`gpu_pipeline_task.cpp:638-661`). On retry the gate blocks while any
  other task on this GPU holds a reservation (Fact A), so a transient squeeze from in-flight work
  never yields a partial grant and the predicate cannot fire. If the GPU is instead full of pinned
  `table_gpu` data with no in-flight work, the clone can never fit and the abort is the right
  answer (today: 100 x 50 ms of futile retries, then the same failure with a worse message).
- Transient CUDA launch failures (#1253, `cuda_launch_reschedule_exception`): no OOM handler runs,
  `_oom_required_bytes` stays 0, `grantable < 0` is false. Unchanged.
- Sink-deferral OOM (`gpu_pipeline_task.cpp:721-748`): records history but no floor/requirement;
  predicate cannot fire; still capped by MAX_RETRIES.
- Retry whose downgrade freed something (`freed > 0`): proceeds as today. If a spill can only ever
  free a little, MAX_RETRIES remains the backstop.
- First attempts with a partial grant (`retry_count == 0`): proceed as today. Measured: partial
  first attempts do succeed (40 scan batches parked with partial grants before the pool filled;
  one more fresh task finalised after the first OOM in each window).
- HOST-tier inputs whose upload OOMs: the prepare-path handler records `live + requested`; if the
  retry's grant is below it with nothing in flight and nothing to spill, the abort is correct for
  the same reason as 3.5.
- Standalone DuckDB extension: single-flight `query_lifecycle_mutex_` (`sirius_context.cpp:1555-1596`,
  finding B1) means no other query holds memory outside repositories; same reasoning applies.

## 6. How the error reaches the CN and the FE (unchanged path, verified in source and log)

1. `completion_handler::report_error(exception_ptr)` sets the promise once
   (`src/include/pipeline/completion_handler.hpp:52-63`).
2. `sirius_engine::execute` waits on the future (`sirius_engine.cpp:263-265`,
   `wait_for_query_future` `:136-170`), catches, logs `Error executing query: {}`, calls
   `drain_after_error()` and rethrows (`:267-278`). `drain_after_error` restarts the GPU
   executors' manager loops (`task_scheduler.cpp:290-294` -> `task_executor.cpp:96-128`).
3. `streaming_fragment::run` poisons its outputs and rethrows (`src/exec/streaming_fragment.cpp:204-227`);
   `ffi::Fragment::run` does the same and ends the lifecycle (`src/sirius_ffi.cpp:990-1027`).
4. cxx turns the C++ exception into `cxx::Exception` (`rust/crates/sirius/src/lib.rs:356
   pub fn run(&mut self) -> Result<(), Exception>`); `run_fragment_inner` maps it to
   `"failed to execute fragment: {err}"` (`experimental/starrocks/src/engine.rs:693-695`); the
   engine thread wipes parked outputs and answers `Err` (`engine.rs:291-316`).
5. `run_ready_fragment` attributes the `Err` to the query and calls `results.fail_query`
   (`compute_node_service.rs:864-910`); `fetch_data` returns
   `INTERNAL_ERROR "fragment instance {id} failed: {cause}"` (`:523-528`, `:1765-1770`).
6. The FE surfaces it as `ERROR 1064 (HY000)` with the full chain (2.1).

Only the innermost text changes. No tooling in the tree greps the old text (`grep -rn "exceeded
maximum retry"` finds only `notes/2026-08-09-gb200-sf100/TPCH-SF100-FAILURES.md`).

## 7. Files to touch

| file | change | size |
|---|---|---|
| `src/include/pipeline/gpu_pipeline_task.hpp` | `_oom_required_bytes` field, maintained in `update_retry_reservation_floor_after_oom` (`:99-108`) and `inherit_retry_reservation_floor` (`:110-114`), getter | ~8 lines |
| `src/pipeline/gpu_pipeline_executor.cpp` | abort predicate + message in the partial-after-downgrade branch (`:262-272`); hoist `MAX_RETRIES` (`:348`) to file scope | ~35 lines |
| `test/cpp/pipeline/test_oom_reschedule.cpp` | fixture variant with a `downgrade_executor`; three new `TEST_CASE`s (section 8) | ~200 lines |
| `docs/super-sirius/pipeline-execution.md` | "Reschedule Handling" (`:419-437`): add the fail-fast rule and the certainty argument; `docs/super-sirius/memory-management.md:144`: one sentence next to `record_on_failure` | ~12 lines |

No `CMakeLists.txt` change (the test file is already in `TEST_SOURCES`, `CMakeLists.txt:850`), so
the orphan-test hook is not involved. Target layer per the carve plan: engine `src/` -> dev PR,
self-contained.

## 8. Tests

### 8.1 Unit / component (Catch2, `test/cpp/pipeline/test_oom_reschedule.cpp`, tag `[gpu_pipeline_executor][oom][failfast]`)

The existing fixture (`test_oom_reschedule.cpp:76-121`) builds a 1100 MB GPU space with
`set_reservation_fraction_per_gpu(0.95)` and passes `nullptr` for the downgrade executor. Add a
variant that also builds a `sirius::data::data_repository_manager_registry` and a
`downgrade_executor` the way `test/cpp/downgrade/test_downgrade_executor.cpp:125-133` does
(`monitor_period = 0` so only the gate's explicit request runs), starts it, and passes its address
as the executor's 4th constructor argument.

Two test-task types, modelled on `oom_test_task` (`:216-262`):

- `wall_task`: `get_estimated_reservation_size_info` returns
  `max(kReservationSize, ls.get_retry_reservation_floor())` and fills `retry_reservation_floor`
  (the production estimator does this at `gpu_pipeline_task.cpp:855, 886`; test tasks override
  the estimator, so they must honour the floor themselves). `execute` allocates 300 MB through the
  reservation-aware allocator; on `rmm::out_of_memory` it calls
  `update_retry_reservation_floor_after_oom(reservation_bytes, allocator->get_allocated_bytes(stream),
  requested)` (mirroring `gpu_pipeline_task.cpp:449-457`) and throws `oom_reschedule_exception`.
- `holder_task`: takes its reservation (900 MB via the estimator), sleeps 300 ms, completes.

Cases:

1. **Parked memory, no way out -> fails fast.** Before scheduling, allocate 900 MB directly from
   `mem_space->get_default_allocator()` into an `rmm::device_buffer` held by the test (this is what
   parked sink output looks like to the space: counted in `_total_allocated_bytes`, outside any
   reservation, invisible to the downgrade sweep). `REQUIRE(mem_space->get_available_memory() <= 200 MB)`
   so the fixture checks its own premise. Schedule one `wall_task`. Expect: attempt 0 gets its
   50 MB, OOMs on the 300 MB allocation, is rescheduled; the retry's gate gets a partial grant,
   the downgrade frees 0, and the executor reports an error. `REQUIRE(completion.has_error())`
   within 5 s; rethrow the future's exception and `REQUIRE_THAT(what, ContainsSubstring("cannot make
   progress") && ContainsSubstring("grantable"))`; `REQUIRE(oom_count == 1)`; `REQUIRE(reschedules
   observed == 1)` (count via a counter in `create_rescheduled_task`). Wall time well under a second.
2. **Memory held by an in-flight reservation -> keeps retrying and succeeds.** No parked buffer.
   Schedule `holder_task` then `wall_task` (2 threads). The wall task's first attempt OOMs while the
   holder is alive; its retry blocks in `make_reservation` (holder's notifier is active), is granted
   in full once the holder releases, and completes. `REQUIRE(!completion.has_error())`,
   `REQUIRE(completed_count == 2)`, `REQUIRE(oom_count >= 1)`. This pins the Fact A dependency: if
   cucascade ever returned a partial grant while reservations are alive, this test fails.
3. **First attempt with a partial grant is not aborted.** Parked 900 MB as in case 1; a task whose
   estimator asks for 2 GB (clamped to the space max at `gpu_pipeline_executor.cpp:170-181`) but
   allocates 50 MB. Gate: partial grant, downgrade frees 0, `retry_count == 0` -> proceeds and
   succeeds. `REQUIRE(completed_count == 1)`, `REQUIRE(!completion.has_error())`.

The existing "fails after max OOM retries" case (`:446-536`) stays as is and keeps proving the 100
cap for tasks whose reservation is fully granted (the `xl_task` never sees a partial grant, so the
new predicate never fires there).

Run: `pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[gpu_pipeline_executor][oom]"`
(seconds). Regression net to run too: `"[gpu_pipeline_executor]"`, `"[downgrade]"`,
`test/cpp/pipeline/test_task_scheduler.cpp`, `test/cpp/pipeline/test_completion_signal.cpp`, and
`pixi run make test` before opening the PR. The multi-GPU tests that touch the reschedule path
(`test/cpp/scan_manager/test_pin_table_multi_gpu.cpp`, `test/cpp/operator/*_mgpu.cpp`) skip on the
single-GPU fix worktree; note that gap in the PR.

### 8.2 SF1000 acceptance (orchestrator-run, 1 CN, GPU 0, same env as `perf/sf1000/capture-cn.sh`: `GPU_MEM=100GiB HOST_MEM=160GiB STAGING=16GiB`)

Arm A, the six: run q05 q08 q09 q17 q18 q21 once each (cold). Pass if, per query,
`runs.csv` ms <= 5,000 for q05 q08 q09 q17 q21 and <= 11,000 for q18, and
(abort timestamp - first `OOM at operator` timestamp) <= 1.5 s in the engine log; the client
`.err` contains `cannot make progress` and `held by data outside any live reservation`;
`grep -c 'reschedule (retry'` <= number of distinct original tasks per query (was 100x that);
no `exceeded 100 retries` line. Total for the six: about 25 s (was 775 s).

Arm B, the fifteen: full sweep of q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22
(3 runs each, as in the campaign). Pass if warm medians are within run-to-run noise of
`perf/sf1000/results.md` (cn1 column), `compare.py` MATCH set unchanged
(`compare-cn1.txt`), and `grep -c 'cannot make progress'` == 0 and `grep -c 'reschedule (retry'`
== 0 in the engine log (both were 0 outside the failure windows before).

Optional arm C (4 CNs): the fifteen again; expect 0 new-message lines and unchanged timings
(`cn4` logs had 0 reschedules).

## 9. Predicted effect (numbers)

- Time to fail for the six: 54-232 s -> 1.4-10.2 s each (q18 dominated by its own 9.3 s of
  senders); 774.9 s -> ~20-29 s; the 1-CN 22-query sweep 910 s -> ~160 s.
- Engine log: -~87,000 lines per sweep; reschedule WARNs 17,900 -> <= 179.
- Wasted GPU_SCAN work: q05 229.5 s Computing -> ~2-3 s (25 fresh attempts of ~90 ms).
- Error text names the cause: bytes needed vs grantable, bytes held outside reservations vs pool,
  downgrade freed 0, retry number. Today's text names only the retry cap.
- The 15 passing queries: 0 behavioural change (predicate unreachable); timing change nil.
- Not fixed by this change (by design): the six still fail on 1 CN (fixes 3/4); the post-failure
  parked-output leak of 0-38 GB (fix 2) is unchanged, although it now costs 0.4-6.4 s less of
  stolen engine time per failure because the dead query's own retries stop sooner.

## 10. Risks

1. **Externally released non-reservation memory (false positive).** If bytes outside reservations
   are freed by something the engine cannot see between the OOM and the retry gate (an
   `unpin_table`, a CN dropping another query's parked slot, a concurrent standalone query's
   `QueryEnd` on a context with no HOST tier), the retry could have succeeded and we abort instead.
   Bounded: today such a task has 5-9 s of patience (100 x ~50-90 ms) and then fails with a less
   useful message; on the CN path fragments run one at a time per engine thread so no other
   query's release can arrive mid-fragment. If reviewers want hysteresis, a
   `kStalledRetriesBeforeFailFast = 2` (abort on the second consecutive stalled retry gate) costs
   one extra round (~1 s in q05) and is a two-line addition (a counter in the local state,
   inherited like the floor). Recommended default: 1.
2. **Dependence on cucascade's `IDLE` semantics.** The proof leans on `make_reservation` granting
   partially only when `_n_active_notifiers == 0`. Pinned by unit case 2; if cucascade changes
   (e.g. a timed wait), that test fails loudly before the behaviour ships.
3. **`live_allocated` underestimates need when `_allocator` is null or the OOM is a plain
   `rmm::out_of_memory`** (requested falls back to 1 MiB). Then `required` is a weaker bound and the
   predicate may not fire: the change is dormant, never wrong. Measured OOMs here all carry
   `cucascade_out_of_memory` (requested and global usage are printed).
4. **`break` stops the manager loop until `drain_after_error`.** Same as the four existing gate
   failure paths; on the CN the failing fragment's `execute()` catch drains immediately
   (`sirius_engine.cpp:267-278`, measured 75 ms to `fragment run failed`). A caller that bypasses
   that catch would leave the executor idle until the next drain; not new to this change.
5. **Message churn.** The client-visible text changes; only notes reference the old text.
6. **Multi-GPU regression coverage.** The single-GPU fix worktree cannot run the mgpu tests that
   exercise the reschedule path; the reasoning in 5.3 is source-based (inference), so ask for a
   2-GPU CI run or a manual `num_gpus=2` SF100 q11 `cache=table_gpu` check before merge.

## 11. Effort

About half a day: header + gate (~1 h), three Catch2 cases (~2-3 h including the downgrade-executor
fixture), docs (~30 min), incremental `pixi run make` and `sirius_unittest "[gpu_pipeline_executor]"`
(minutes). SF1000 arms: ~3 min for the six, ~15 min for the fifteen (orchestrator time, GPU 0).

## 12. Dependencies and interactions with fixes 2, 3, 4

- No dependency on the other three. Lands first: pure engine change, smallest PR, and it makes
  every later SF1000 arm ~12 minutes shorter.
- Fix 2 (parked bookkeeping, Rust): independent code; behaviourally complementary (the dead
  query's queued senders still run after the abort, that leak is fix 2's job). Fail-fast changes
  the timing of the CN's `Err` arm (`engine.rs:291-316`) from +100 s to +2 s; fix 2's tests should
  not assume the failure arrives after the other senders have parked.
- Fix 4b (spillable parked repositories): once parked output is visible to TIER 1, these gates see
  `freed > 0` and the predicate does not fire; the retry proceeds and the query may pass slowly.
  MAX_RETRIES stays as the backstop for a spill that frees too little each round. Fix 4a
  (fusion) removes the OOM altogether; fail-fast becomes dormant for these six.
- Fix 3 (FE cardinalities): no interaction.

## 13. Alternatives considered

- **Abort in the reschedule path (worker thread) instead of the gate.** At the OOM we know
  `requested` and `global usage == limit` but not whether a downgrade could free anything nor
  whether other tasks are in flight; both would need plumbing. The gate already has `freed`,
  the grant, and (through `IDLE`) the in-flight fact. Rejected.
- **Compare the grant against the retry floor (2 x reservation).** Fires in the measured case
  (6.64 GB vs 3.3 GB) but the doubling is policy, not need: a task that OOMed at 1.0 GB live +
  0.1 GB request with a 1.5 GB grant available would be aborted although it may fit. Rejected in
  favour of `live + requested`, which is a hard bound; costs one header field.
- **Progress-based (no successful task since this task's previous attempt).** `_tasks_executed`
  (`gpu_pipeline_executor.cpp:310`) is per executor; in the measured windows exactly one fresh
  task finalised after the first OOM, which would have reset such a marker once, and on multi-GPU
  the memory that matters may be released by the other GPU's executor. Rejected.
- **Lower MAX_RETRIES / add a knob.** Trades the #732 and #1253 patience for a shorter storm; still
  wastes 100% of the remaining budget on a certain failure. Rejected.
- **Watchdog.** Cannot see it (2.4). Rejected.
- **`has_viable_downgrade_target()`.** True here (HOST has room); the measured `freed == 0` is the
  right signal. Rejected.

## 14. Open questions

1. The exact `live_allocated` at the failing allocation is not in the log (only peak); the margin of
   the predicate is `requested` (0.33-1.75 GB) if the accounting argument in 5.1 holds. Arm A
   verifies it; if a query survived round 1, the fallback is to also compare against the recorded
   peak minus materialisation bytes for tasks resumed at operator index 0 (valid lower bound only
   when the resume index is 0), still a one-line change.
2. Whether upstream wants the message to carry the OOM'ing operator name (available in the
   reschedule exception, not at the gate); it could be stored in the local state at reschedule
   time for one more line.
3. Hysteresis default (risk 1): 1 or 2 stalled retries.
