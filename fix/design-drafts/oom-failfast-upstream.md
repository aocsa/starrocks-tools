# Fix 1 design (upstream-acceptable angle): fail an OOM retry chain that cannot make progress

Written 2026-09-03 from the worktree `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be`
(engine source cited as `src/...`, cucascade as `cucascade/...`) and the evidence bundle
`scratchpad/perf/sf1000/` (cited as `E/...`). Every number below is **measured** from a named file
unless marked **inference**.

## 0. One-paragraph summary

Keep `MAX_RETRIES = 100` and the 50 ms backoff exactly as they are. Add one new terminal condition
next to the existing retry-cap check in the executor's reschedule path: an OOM retry is **futile**
when (1) the task is already a retry, (2) at this attempt's reservation gate the downgrade freed
0 bytes and the space could grant less than the floor the previous OOM established, and (3) no task
has completed on this executor since the retry was scheduled. The gate records what it saw on the
task's local state (three integers); the reschedule path applies a pure predicate and fails the
query through the same `completion_handler->report_error` path the retry cap uses, with a message
that names the bytes held outside any reservation vs the pool limit. Engine-only (`src/`), no new
configuration key, one Self-contained PR against `dev`, tested by a GPU-free Catch2 predicate test
plus two new cases in the existing `test_oom_reschedule.cpp`. Predicted SF1000 effect: the six
failing queries die 2-5 s after their first OOM instead of 52-230 s later; the 15 passing queries
never enter the changed code path (0 partial-reservation lines in their windows on cn1 and cn4).

## 1. The measured failure (what the code must recognise)

**The loop.** `src/pipeline/gpu_pipeline_executor.cpp:348-349` `static constexpr uint32_t
MAX_RETRIES = 100; if (next_retry_count > MAX_RETRIES) {...report_error...}`; `:409`
`std::this_thread::sleep_for(std::chrono::milliseconds(50));`; the only other exit is
`:312-316` (completion handler already in error). Each retry re-enters `manager_loop()` and the
reservation gate (`:143-296`).

**The gate proceeds on a partial grant.** `:196-272`: when `reservation->size() < bytes_needs`
the executor releases the partial reservation, calls `_downgrade_executor->request_downgrade(...)`
(`:224-233`, returns `size_t freed`), re-reserves, and if still short logs
```
:263  SIRIUS_LOG_WARN("GPU Pipeline Executor: after downgrade ({} bytes freed), reservation "
:265                  "still partial ({}/{} bytes) for pipeline {} task {} -- proceeding "
:266                  "with partial reservation", ...)
```
and dispatches anyway (`:284-301`). `freed`, `reservation->size()`, `bytes_needs`, and
`reservation_info.retry_reservation_floor` are all locals at that point.

**Why the grant is partial and why it never changes.** `cucascade/src/memory/memory_space.cpp:257-267`
```
std::unique_ptr<reservation> memory_space::make_reservation(size_t size)
{
  std::unique_ptr<reservation> res = make_reservation_or_null(size);
  while (!res) {
    auto status = _notification_channel->wait();
    if (status == notification_channel::wait_status::SHUTDOWN) { return nullptr; }
    if (status == notification_channel::wait_status::IDLE) { return make_reservation_upto(size); }
    res = make_reservation_or_null(size);
  }
```
`notification_channel::wait()` returns `IDLE` only when `_n_active_notifiers == 0`
(`cucascade/src/memory/notification_channel.cpp:49-60`), and a notifier lives exactly as long as a
reservation arena (`notification_channel.cpp:27-33`, arenas created with the notifier at
`reservation_aware_resource_adaptor.cpp:365-381`). So **a partial grant means no other reservation
is live on this memory space**: everything else that fills the pool is unreserved allocation.
`reserve_upto` grants `limit - total_allocated` (`reservation_aware_resource_adaptor.cpp:535-543`,
`do_reserve_upto` = `add_bounded(size, _memory_limit)`), so `limit - grant` is the bytes held
outside reservations.

**The floor.** `src/include/pipeline/gpu_pipeline_task.hpp:99-108`
```
void update_retry_reservation_floor_after_oom(std::size_t current_reservation_bytes,
                                              std::size_t live_allocated_bytes,
                                              std::optional<std::size_t> requested_bytes) noexcept
{
  auto next_floor = memory::saturating_mul(current_reservation_bytes, 2);
  next_floor = std::max(next_floor, memory::saturating_add(live_allocated_bytes, request_bytes));
  ...
  _retry_reservation_floor = std::max(_retry_reservation_floor, next_floor);
}
```
called from every OOM catch site (`src/pipeline/gpu_pipeline_task.cpp:456-457`, `:646-647`),
inherited across reschedule (`gpu_pipeline_executor.cpp:389 inherit_retry_reservation_floor`),
and fed into the request: `gpu_pipeline_task.cpp:886 info.reservation_size =
std::max(normal_reservation, info.retry_reservation_floor);` then clamped to the space maximum
(`gpu_pipeline_executor.cpp:165-175`).

**The OOM itself.** `cucascade/src/memory/reservation_aware_resource_adaptor.cpp:477-482`
```
throw cucascade_out_of_memory("not enough capacity to allocate memory",
                              MemoryError::LIMIT_EXCEEDED, allocation_bytes,
                              post_allocation_size, _pool_handle);
```
is the text in every SF1000 OOM line (`E/cn1/engine-cn0.log:49221` "std::bad_alloc: out_of_memory:
not enough capacity to allocate memory"); `global_usage` (`cucascade/include/cucascade/memory/error.hpp:43-54`)
is the pool counter after the failed add = the 100 GiB limit.

**The measured chain (q05, original task 2567; `E/cn1/engine-cn0.log`, extracted with
`scratchpad/fix/designs/_chain2567.py`).** 100 reschedule lines, 100 partial-reservation lines, one
`exceeded` line:

| retry | task | time | OOM request (MB) | global usage (GB) | peak (GB) | reservation (GB) | freed | wanted (GB) | granted (GB) |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 0 (first attempt) | 2567 | 13:03:44.795 | 748.9 | 107.37 | 3.38 | 3.32 | 0 | 5.80 | 3.32 |
| 1 | 2593 | 13:03:45.847 | 748.9 | 107.37 | 3.38 | 3.08 | 0 | 6.64 | 3.08 |
| 2 | 2618 | 13:03:46.811 | 748.9 | 107.37 | 3.38 | 3.08 | 0 | 6.64 | 3.08 |
| ... | | | identical | | | | | | |
| 100 | 5043 | 13:05:23.587 | 748.9 | 107.37 | 3.38 | 3.08 | 0 | 6.64 | 3.08 |
| exceeded | 5068 | 13:05:24.553 | `task 5068 (original task 2567) exceeded 100 retries at operator index 0` | | | | | | |

From retry 1 on the tuple (wanted 6.64 GB = 2 x 3.32 GB floor, granted 3.08 GB, freed 0, usage
107,374,182,400 B) is byte-identical; `limit - granted` = 104.3 GB, which is the sink's parked
lineitem (B2: "104.30 GB ... the running leaf fragment's own STREAMING_SINK output"). Retry rounds
are ~1 s apart because the 25 stuck tasks serialise through the single manager thread blocking in
`make_reservation` (B3: Reserving 99.9 s of the 101.2 s window).

**Per query (B3 table, `E/agent-notes/skeptic-oom4/retry_stats.py`).** stuck tasks / reschedules /
first OOM / wall to die: q05 25/2500/+1.61 s/101.5 s; q08 36/3600/+1.73/168.3; q09 56/5600/+2.38/232.5;
q17 21/2100/+1.20/103.9; q18 23/2300/+9.29/54.2; q21 18/1800/+2.68/114.5. Exactly one task
finalised successfully after the first OOM in each window. 774.9 s of the 910.3 s sweep.

**What reaches the user today** (`E/cn1/runs/q05.r0.err`):
```
ERROR 1064 (HY000) at line 1: fragment instance 01a0675e-...-576d failed: fragment instance
01a0675e-...-5774 failed: failed to execute fragment: GPU pipeline task exceeded maximum retry
limit (100) for original task 2567: OOM at operator GPU_SCAN (index 0)
```
Path: `completion_handler::report_error` (`src/include/pipeline/completion_handler.hpp:51-62`,
first call wins) -> `sirius_engine.cpp:151 future.get()` -> `streaming_fragment::run()`
(`src/exec/streaming_fragment.cpp:213-226`, poisons outputs, rethrows) -> `ffi::Fragment::run()`
(`src/sirius_ffi.cpp:1010-1023`) -> CN `engine.rs:694-695 .run().map_err(|err| format!("failed to
execute fragment: {err}"))` -> `compute_node_service.rs:523-524 format!("fragment instance {id}
failed: {cause}")` -> FE -> client. The engine's `what()` text is carried verbatim to the user, so
the engine alone can make the error name the cause; no CN or FE change is needed.

**Blast radius baseline (measured with `scratchpad/fix/designs/_bucket_oom.py`).** On cn1 the
strings `proceeding with partial reservation`, `reschedule (retry` and `exceeded ... retries` occur
only inside the six failing queries' windows (q05 2503/2500/1, q08 3603/3600/1, q09 5603/5600/1,
q17 2103/2100/1, q18 2302/2300/1, q21 1803/1800/1) and in none of the 15 passing queries' windows.
On cn4, across all four engine logs, none of the 45 query windows contains any of them. The
standalone capture logs carry no engine lines, so standalone is not measurable from the bundle.

## 2. Mechanism

### 2.1 Decision rule

A reschedule request for `oom_reschedule_exception` is **futile** iff all of:

1. `retry_count >= 1` for the task that just failed (it was created by a reschedule; a first
   attempt never fails fast, so the `+3` first-attempt partial lines per query are unaffected).
2. The gate observation recorded for this attempt says `freed_by_downgrade == 0` and
   `granted < min(retry_reservation_floor, space_max)` (the space could not grant what the
   previous OOM said the task needs, and spilling freed nothing).
3. `_tasks_executed.load() == progress_mark`, where `progress_mark` is `_tasks_executed` at the
   moment this retry was created. `_tasks_executed` is per executor and success-only
   (`gpu_pipeline_executor.cpp:310`). If any task on this device completed in the meantime,
   memory may have been released and the attempt is not futile.

Conditions 2 and 3 are the reason this is safe for the contention case the 100-retry budget was
raised for (comment at `gpu_pipeline_executor.cpp:339-347`, from `2ac106b2` #732): that OOM comes
from the cross-GPU clone allocation inside `lock_or_prepare_batch`
(`src/include/pipeline/batch_lock_utils.hpp:122-124` `read_accessor.clone_to<...>`; the shared
lock itself never throws OOM, `cucascade/src/data/data_batch.cpp:218-223`) while other probe tasks
on the same device keep completing, so condition 3 fails and retries continue up to 100 exactly as
today. `cuda_launch_reschedule_exception` is excluded from the rule entirely (it is not a memory
shortfall; the existing 100-retry budget applies unchanged).

Why the gate observation and not `has_disk_tier()`: `src/include/downgrade/downgrade_executor.hpp:155-161`
already documents `has_disk_tier()` as "used by callers (e.g. the GPU pipeline executor) to decide
whether an unsatisfiable reservation can ever be relieved by spilling, or whether retrying is
futile", but no caller exists (`grep -rn has_disk_tier src` -> only its definition and
`has_viable_downgrade_target()` at `downgrade_executor.cpp:401-411`). The design uses the
stronger, already-computed signal `freed == 0` (correct with or without a disk tier: 0 freed with a
disk tier means there was no convertible candidate at all) and reports the disk-tier state in the
message via `has_disk_tier()`, which finally gives that hook its intended caller.

### 2.2 Where each piece lives

**(a) `src/include/pipeline/sirius_pipeline_task_states.hpp`** next to `reservation_size_info`
(`:43-52`): a plain struct the executor fills at the gate.
```cpp
/// What the GPU executor's reservation gate saw for one attempt. Recorded on the task's local
/// state before dispatch; read by the reschedule path to decide whether another retry can succeed.
struct retry_gate_observation {
  std::size_t requested_bytes   = 0;  ///< bytes_needs after the space-max clamp
  std::size_t granted_bytes     = 0;  ///< reservation->size() actually handed to the task
  std::size_t freed_by_downgrade = 0; ///< bytes the predicate-based downgrade freed (0 if none ran)
  bool downgrade_available      = false;  ///< a downgrade executor exists for this space
  bool disk_tier_configured     = false;  ///< downgrade_executor::has_disk_tier()
  std::size_t space_max_bytes   = 0;  ///< memory_space::get_max_memory()
};
```

**(b) `src/include/pipeline/gpu_pipeline_task.hpp`** in `gpu_pipeline_task_local_state`
(after `retry_count`/`original_task_id`, `:91-94`): `std::optional<retry_gate_observation>
gate_observation;` and `std::size_t executor_progress_mark = 0;` with the same doc-comment style
("only meaningful when retry_count > 0"). No inheritance needed: the executor sets the mark on the
new local state at reschedule time and the gate fills the observation on every attempt.

**(c) `src/include/pipeline/retry_futility.hpp`** (new, header-only, no CUDA includes, same
pattern as `batch_lock_utils.hpp` + `test_batch_lock_utils.cpp`):
```cpp
namespace sirius::pipeline {
struct retry_futility_input {
  uint32_t retry_count;                           // of the task that just OOM'd
  std::size_t retry_reservation_floor;            // from its local state
  std::optional<retry_gate_observation> gate;     // from its local state
  std::size_t progress_mark;                      // _tasks_executed when the retry was created
  std::size_t tasks_executed_now;                 // _tasks_executed now
};
/// Returns std::nullopt when another retry may succeed, otherwise the reason it cannot.
std::optional<std::string> assess_retry_futility(const retry_futility_input& in, int device_id);
}
```
Rule as in 2.1; the returned string is the human-readable reason used in the error. Pure function,
so it is unit-tested without a GPU.

**(d) `src/pipeline/gpu_pipeline_executor.cpp`**, three edits:

1. Gate records the observation. Immediately before `local_state->set_reservation(...)` (`:284-286`),
   in both the downgrade branch (`:196-272`) and the no-downgrade-executor branch (`:273-283`):
   ```cpp
   local_state->gate_observation = retry_gate_observation{
     .requested_bytes = bytes_needs, .granted_bytes = reservation->size(),
     .freed_by_downgrade = freed, .downgrade_available = _downgrade_executor != nullptr,
     .disk_tier_configured = _downgrade_executor && _downgrade_executor->has_disk_tier(),
     .space_max_bytes = _memory_space->get_max_memory()};
   ```
   (`freed` is hoisted to the enclosing scope, default 0.) The `:263` WARN keeps its text.

2. Reschedule path marks progress. At `:387-389` where the new local state is built:
   ```cpp
   new_local_state->executor_progress_mark = _tasks_executed.load(std::memory_order_relaxed);
   ```

3. Reschedule path decides. Directly after the `MAX_RETRIES` block (`:349-364`), guarded by
   `dynamic_cast<oom_reschedule_exception*>(&ex)`:
   ```cpp
   if (auto reason = assess_retry_futility({...cur_local fields..., _tasks_executed.load()},
                                           _memory_space->get_device_id())) {
     SIRIUS_LOG_ERROR("GPU Pipeline Executor: task {} (original task {}) giving up after {} OOM "
                      "retries -- {}: {}", ..., *reason, ex.what());
     if (_completion_handler) {
       _completion_handler->report_error(std::make_exception_ptr(std::runtime_error(
         "GPU pipeline task gave up after " + std::to_string(cur_local->retry_count) +
         " OOM retries with no progress for original task " + std::to_string(orig_task_id) +
         ": " + ex.what() + "; " + *reason)));
     }
     return;   // same shape as the MAX_RETRIES path: no task_creator->stop(), no drain here
   }
   ```
   Everything after (`release_intermediate_data`, `create_rescheduled_task`, sleep, `schedule`)
   is unchanged. Other stuck tasks that OOM afterwards return at `:312-316` because
   `_completion_handler->has_error()` is now true, exactly as they do after the cap trips today.

**The message** (built from the observation; q05 numbers):
```
GPU pipeline task gave up after 1 OOM retries with no progress for original task 2567: OOM at
operator GPU_SCAN (index 0); [GPU:0] could grant 3,076,488,960 of the 6,637,897,728 bytes the
previous OOM established as this task's floor; 104,297,693,440 bytes (97.1% of the
107,374,182,400-byte limit) are held outside any task reservation and the downgrade executor freed
0 bytes (disk tier not configured); no task completed on this device since the retry was scheduled
```
`held = space_max - granted` is exact under the IDLE semantics of 1.: a partial grant implies no
other live reservation, so the remainder is unreserved data (parked sink output, pinned tables).
The engine does not know the word "parked"; the CN already logs `discarding every parked sender
output on this CN after a fragment failure slots=2 error=...` (`engine.rs:301-307`,
`E/cn1/cluster.log` at 13:05:24.626) next to it, and fix 2's per-query bookkeeping is the place to
attribute those bytes to fragments.

### 2.3 Behaviour under the measured SF1000 case

q05: first OOM of task 2567 at 13:03:44.795 (retry 0, not eligible). Retry 1 (task 2593) is created
with `progress_mark = _tasks_executed` at 13:03:44.795; its gate at ~13:03:45.8 grants 3.08 GB
against a 6.64 GB floor with freed 0 -> observation recorded; it OOMs at 13:03:45.847. If the one
successful task in the window finalised in that ~1 s round, condition 3 fails once and retry 2
(13:03:46.811) is the terminal one; otherwise retry 1 is. Query error at +2.7 to +3.7 s from the
client start instead of +101.5 s. The remaining 24 stuck tasks hit `:312-316` and return; the CN
wipes the parked slots (`engine.rs:314-315`) ~100 s earlier than today, which is also why fix 2's
"engine-thread stolen by dead senders" numbers shrink slightly but are otherwise unaffected.

Per query (**inference** from the B3 offsets and measured round periods = reschedules / retry
window: q05 40 ms x 25 tasks ~1.0 s/round, q08 ~1.7 s, q09 ~2.3 s, q17 ~1.0 s, q18 ~0.5 s, q21
~1.1 s; plus ~0.2 s FE/CN propagation):

| query | first OOM (from client start) | predicted time to fail | today |
|---|---:|---:|---:|
| q05 | +1.61 s | 3-4 s | 101.5 s |
| q08 | +1.73 s | 4-5 s | 168.3 s |
| q09 | +2.38 s | 5-7 s | 232.5 s |
| q17 | +1.20 s | 2.5-3.5 s | 103.9 s |
| q18 | +9.29 s | 10-11 s | 54.2 s |
| q21 | +2.68 s | 4-5 s | 114.5 s |

Six failures: ~30-35 s total vs 774.9 s (saves ~740 s of the 910 s sweep, 81%). Log volume: the
five per-retry lines (89,580 of 152,097 engine-log lines) drop to <= 2 rounds per stuck task
(~250-600 lines per query). The plan's acceptance "time-to-fail <= 5 s each" should read "<= 5 s
after the first OOM": q18's first OOM is at +9.3 s because its own sender fragments run first (B3),
and q09's 56 stuck tasks make its round ~2.3 s.

### 2.4 Behaviour for the 15 passing queries

Unchanged by construction: the rule is evaluated only inside the `task_reschedule_exception`
catch, and the recorded observation only matters when `retry_count >= 1`. On cn1 and cn4 at SF1000
no passing query produced a reschedule or a partial-reservation line (section 1), so neither the
gate branch nor the reschedule path runs for them. The only added work on the hot path is filling
five integers per gate pass.

## 3. Files to touch (engine only; carve layer: `src/` -> Self-contained fork PR against `dev`)

| file | change |
|---|---|
| `src/include/pipeline/sirius_pipeline_task_states.hpp` | add `struct retry_gate_observation` after `reservation_size_info` (`:43-52`) |
| `src/include/pipeline/gpu_pipeline_task.hpp` | two fields on `gpu_pipeline_task_local_state` (`:91-94` block) |
| `src/include/pipeline/retry_futility.hpp` | new header-only `assess_retry_futility` |
| `src/pipeline/gpu_pipeline_executor.cpp` | record observation before `:286`; set `executor_progress_mark` at `:387-389`; futility check after `:364`; include the new header |
| `test/cpp/pipeline/test_retry_futility.cpp` | new, GPU-free predicate table (section 4.1) |
| `test/cpp/pipeline/test_oom_reschedule.cpp` | fixture gains an optional real `downgrade_executor`; two new TEST_CASEs (4.2, 4.3) |
| `CMakeLists.txt` | add the new test source next to `test/cpp/pipeline/test_oom_reschedule.cpp` (`:850`) |
| `docs/super-sirius/pipeline-execution.md` | "Reschedule Handling" list (`:426-436`): new step between 1 and 2; "If max retries are exceeded" sentence gains the no-progress case; update the stale "retry up to 10 times with 5ms backoff" at `execution-flow.md:150` while there |
| `docs/super-sirius/memory-management.md` | `:84` bullet: one clause that a retry chain with no progress fails the query |

No change to `exec/config.hpp` (no new key, so no `configuration.md` row; `sirius.executor.pipeline`
stays "Thread pool only ... No extra keys", `configuration.md:294`), none to the CN
(`experimental/starrocks`), none to the FE patch set, none to cucascade, none to `MAX_RETRIES` or
the 50 ms sleep. Estimated diff: ~120 lines engine, ~250 lines tests, ~15 lines docs.

## 4. Tests

### 4.1 Predicate table, no GPU: `test/cpp/pipeline/test_retry_futility.cpp` (`[retry_futility]`)

Rows (expect futile / not futile):
- retry 0, any observation -> not futile (first attempt).
- retry 1, no observation -> not futile (gate never recorded; defensive).
- retry 1, granted < floor, freed 0, marks equal -> **futile**; reason names held bytes, floor,
  grant, freed, disk-tier state.
- same but freed > 0 -> not futile.
- same but granted >= floor -> not futile.
- same but `tasks_executed_now != progress_mark` -> not futile (progress).
- floor > space_max, granted == space_max -> not futile (clamp: the floor is unreachable by design,
  the existing clamp comment at `gpu_pipeline_executor.cpp:153-164` owns that case).
- floor > space_max, granted < space_max, freed 0, marks equal -> futile.
- q05 numbers from section 1 -> futile with the exact expected message substring.

### 4.2 Storm fails fast: `test_oom_reschedule.cpp` new TEST_CASE `"GPU pipeline executor gives up
when an OOM retry cannot make progress"` (`[gpu_pipeline_executor][oom][futile]`)

Uses the existing fixture (1100 MB software limit, `kReservationSize` pattern, `:44-121`) with two
additions: the fixture constructs a real `downgrade_executor` the way
`test/cpp/downgrade/test_downgrade_executor.cpp:126-134` does (`monitor_period = 0`, empty
`data_repository_manager_registry`, no task queue) so `request_downgrade` runs the real sweep and
returns 0; and a `hog`: an unreserved allocation of ~900 MB from
`mem_space->get_default_allocator()` (`cucascade/include/cucascade/memory/memory_space.hpp:119`, the
same unreserved path `make_gpu_batch` uses at `test_downgrade_executor.cpp:110`), which mimics
parked sink output: it charges the pool but holds no notifier, so the gate goes IDLE and grants
partially. One `floor_task` (new class next to `xl_task`, `:304-339`) whose
`get_estimated_reservation_size_info` returns `max(50 MB, floor)` like the real
`gpu_pipeline_task.cpp:886`, and whose `execute` allocates 300 MB and on `rmm::out_of_memory` calls
`update_retry_reservation_floor_after_oom(reservation_bytes, live, requested)` before throwing
`oom_reschedule_exception`. Assertions: `completion.has_error()` within 5 s; the error text contains
"gave up after" and "held outside any task reservation"; `oom_count <= 3` (was >= 100 for the
XL task in the existing max-retry test); `is_task_queue_empty()` after `drain_and_wait()`.

### 4.3 Contention keeps retrying: TEST_CASE `"GPU pipeline executor keeps retrying an OOM while
other tasks complete"` (`[gpu_pipeline_executor][oom][progress]`)

Same hog and `floor_task`, plus a `releaser_task` scheduled after it that sleeps 200 ms, frees the
hog, and completes (bumping `_tasks_executed`). Assertions: no error; `floor_task` completes;
`oom_count >= 1`; the executor's `get_metrics().tasks_executed == 2`. This pins the guard that
protects the #732 cross-GPU contention scenario.

### 4.4 Existing tests unchanged

`"GPU pipeline executor fails after max OOM retries"` (`:446-536`) still needs 100 retries: its
`xl_task` never raises the floor and its 50 MB reservation is granted in full, so condition 2 is
false on every attempt. `"GPU pipeline executor reschedules tasks on OOM"` (`:358-429`) completes
on the first retry because other tasks finish (condition 3 false). Run:
`pixi run build/release/extension/sirius/test/cpp/sirius_unittest "[gpu_pipeline_executor]"` and
`"[retry_futility]"`; `pixi run make test` for the SQLLogic suite (no plan shape changes).

### 4.5 SF1000 acceptance (orchestrator-run, GPU 0, 1 CN, 100 GiB pool, same env as `E/capture-cn1.log`)

1. Cold-run q05 q08 q09 q17 q18 q21 once each. Pass criteria: each `qNN.r0.err` contains
   "gave up after" and "held outside any task reservation"; wall from client start to error <= first
   OOM + 5 s (expected values in 2.3); `_bucket_oom.py` shows <= 2 reschedule rounds per stuck task.
2. Warm sweep of the 15 passing queries in the campaign order: `compare.py` set unchanged
   (MATCH/VALUES-DIFFER pattern identical to `E/compare-cn1.txt`), warm medians within the 6% cn1
   run-to-run spread, and `_bucket_oom.py` reports 0 reschedule / partial lines in their windows.
3. Sanity on the contention budget: `pixi run` the multi-GPU SF100 q11 configuration from the
   `#732` comment (`cache=table_gpu`, `num_gpus=2`) if the box is free; expected identical
   pass/fail and no "gave up" text.

## 5. Risks

1. **False positive when memory would have appeared without any task completing on this device.**
   Sources considered: another query's tasks (counted, same executor), downgrade monitor freeing
   bytes (shows up as `freed > 0` or a fuller grant), CN relay/export of parked outputs (engine
   thread is one-request-at-a-time, `engine.rs:278-280`, and the stuck sender is the running
   request), cross-GPU release (frees memory on the other device). None identified; residual risk
   is bounded to memory freed by a path outside the executor, in which case the query fails at
   retry 1-2 with a precise message instead of after 100 retries.
2. **The floor over-asks.** `2 x reservation` is the existing floor policy, not this change; the
   measured chain shows the same 749 MB request OOMing at the same 3.38 GB peak on all 101
   attempts, so a smaller grant would not have helped. Multi-GPU configs with pinned tables filling
   a device could see the floor exceed what is grantable while the task might still fit; the
   progress guard is the protection, and 4.5 step 3 checks the known case.
3. **Executors without a downgrade executor** (`:273-283`, "should never happen" in production;
   the test fixture passes `nullptr`): the observation records `freed 0`, so the rule can fire
   there too. Intentional (nothing can free memory), called out in the PR description.
4. **Message text changes** for genuinely stuck queries: no code, script or test matches the old
   "exceeded maximum retry limit" text (grep of the tree; only `notes/` mentions it), and the
   100-retry message itself is kept for the cases that still reach it.
5. **Watchdog interplay**: `SIRIUS_QUERY_WATCHDOG_SECS` cannot see the storm because reschedules
   advance the fingerprint (`sirius_engine.cpp:116-127`); this fix removes the storm rather than
   changing the watchdog. No interaction.

## 6. Effort and dependencies

- Effort: S. Code ~0.5 day, tests ~0.5 day, docs ~1 h, SF1000 arm ~15 min of GPU time. One PR,
  reviewable in one sitting (`CONTRIBUTING.md` "PR reviewability"), title
  `fix(pipeline): fail an OOM retry chain that cannot make progress`, body states motivation
  (this campaign's 775 s), mechanism, "intentionally keeps MAX_RETRIES=100 for #732 contention",
  and the test/SF1000 notes.
- Dependencies on the other three fixes: none to land. Interactions: fix 2 (bookkeeping) sees the
  failure ~100 s earlier but the same leak; fix 3 (cardinalities) is orthogonal; fix 4b (spillable
  parked repositories) makes `freed > 0` for the six queries so this rule stays quiet while
  spilling makes progress and fires only once HOST is full and nothing else completes, which is the
  intended division of labour; fix 4a (fusion) makes the six pass on 1 CN and this fix remains the
  safety net at the next scale factor. Landing order recommended: fix 1 first (pure engine, tiny,
  makes every later SF1000 arm ~12 minutes shorter).

## 7. Open questions for the judges

- Whether to also emit a Quent telemetry event for the terminal decision (the reschedule path
  already emits `finalizing{success=false}` at `:414-419`; a dedicated event would need a probe in
  `telemetry/` and is not required for the acceptance test).
- Whether the CN should append its own parked-slot byte total to the error (belongs with fix 2's
  per-query bookkeeping; out of scope here).

## Appendix: helper scripts written for this design (evidence extraction only)

- `scratchpad/fix/designs/_bucket_oom.py`: buckets reschedule / partial / exceeded lines from the
  engine logs into `runs.csv` query windows per arm (result in section 1).
- `scratchpad/fix/designs/_chain2567.py`: walks the 101 attempts of q05's original task 2567
  (table in section 1).
