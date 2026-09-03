# Fix 2, surgical variant: per-query parked-output bookkeeping in the CN

Angle: the smallest change that removes the measured leak and the measured stolen engine time, with
provably zero behaviour change for the 15 passing queries. Everything lives in the CN crate
(`experimental/starrocks/src`), nothing in the engine C++, the FE, `local_exchange.rs` or
`result_store.rs`.

Source tree read: `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be` (all `file:line` below
are from it). Evidence root: `scratchpad/perf/sf1000`. "Measured" = copied from a named evidence file;
"inference" is marked.

---

## 1. The defect, restated from the evidence

### 1.1 What the code does today (verified)

1. The engine thread serves one request at a time (`engine.rs:278-280`). When a `Run` returns `Err` it
   wipes **every** parked sender output on the CN and poisons every slot with the error
   (`engine.rs:291-316`). Nothing remembers that the query failed: the next `Run` of the same query
   is executed normally.
2. `cancel_plan_fragment` only flips a `Waiting` result entry to `Failed`
   (`compute_node_service.rs:457-479`, `result_store.rs:205-212`). No engine call, no parked drop.
3. `run_ready_fragment` (dispatch worker) on `Err` calls `results.fail_query` and returns
   (`compute_node_service.rs:864-911`). `execute_ready_fragment` translates **before** it extracts
   the `LocalParked` slots (`:1361` then `:1365-1392`); a translation failure returns with those
   slots still parked in the engine and never referenced again.
4. `ResultStore.query_failures` is recorded (`result_store.rs:98`, `:174-179`) but only consulted by
   `reserve` (`:127`); no sender path reads it.

### 1.2 What the FE sends (verified, code + log)

- The FE cancels **every** fragment instance of **every** query, including successful ones:
  `DefaultCoordinator.java:1028-1034` sends `LIMIT_REACH` (1) when a LIMIT was satisfied, else
  `QUERY_FINISHED` (5); on a failure `handleErrorExecution` sends `INTERNAL_ERROR` (3) or `TIMEOUT`
  (4) (`:764-786`), and `cancelRemoteFragmentsAsync` attaches `queryStatus.getErrorMsg()` only for
  `INTERNAL_ERROR` (`:1161-1167`). Per instance: `FragmentInstanceExecState.java:383-415` passes
  `query_id`, `instanceId`, `cancelReason`, `errorMessage`. Proto: `internal_service.proto:464-482`.
- Measured in `cn1/cluster.log`: 642 `cancel_plan_fragment` lines for 66 runs. q01 gets 3 cancels
  with `cancel_reason=5` (lines 1131-1136), q02 13 cancels with `cancel_reason=1` (1934-1959);
  q05's 11 cancels carry `cancel_reason=3 error_message=Some("fragment instance ... failed: ...")`
  (6073-6094); q16's 5 cancels likewise (16597-16605); q21's 9 (21350-21367).

  Consequence for the design: any cancel-path change must be gated on the reason, or it runs on
  every passing query.

### 1.3 What happens after a failure (measured)

q05, `cn1/cluster.log` (ANSI stripped, times UTC):

| time | line | event |
|---|---|---|
| 13:05:24.626889 | 6050 | `discarding every parked sender output on this CN after a fragment failure slots=2` |
| 13:05:24.627064 | 6051 | `fragment run failed ... 5774 role="sender" elapsed_ms=101193` (lineitem, OOM at GPU_SCAN) |
| 13:05:24.632012 | 6072 | `fragment run started ... 5773` (dead query, 0.25 ms before the first cancel) |
| 13:05:24.632266-.633083 | 6073-6094 | 11 x `cancel_plan_fragment ... cancel_reason=3` |
| 13:05:24.640784 | 6097 | 5773 finished, 8 ms |
| 13:05:24.650830-.684200 | 6181-6237 | 5777 ran, 33 ms |
| 13:05:24.696997-25.279686 | 6258-6275 | 5776 ran, 582 ms |
| 13:05:25.282255-25.355984 | 6291-6305 | 5775 (receiver, `inputs=2`) declared streams 1 and 4 (150,000,000 and 227,571,151 rows), relayed 1 + 10 batches, ran 73 ms and parked its output for a receiver that never runs |
| 13:05:25.372453 | 6329 | q06's first fragment starts; the q06 client started 13:05:24.652 (`cn1/runs/runs.csv`) = 0.72 s stolen |

Pool (`cn1/engine-cn0.log` 61738-61889): `QueryEnd q=219 allocated=104297693440` -> `QueryBegin
q=221 allocated=0` (the wipe) -> after 5773/5777/5776/5775: `allocated=4690817280` = **4.69 GB**
that q06 and q07 then ran under (`agent-notes/refute-oom5/headroom.txt` base@start).

q21 -> q22 (`cluster.log` 21344-21617): failed 13:18:07.593; leftover `acc` ran 179 ms, `acb`
(`inputs=1`, relayed 8 batches = 730,806,711 rows of stream 6) ran **3153 ms** and parked; q22's first
fragment started 13:18:10.961 against a client start of 13:18:07.610: **3.35 s stolen, 36.64 GB
parked** into q22 (cold 4110 ms vs warm 971/915).

q16 (`cluster.log` 16571-16605): senders `ff87` (partsupp, 108 ms) and `ff86` (25 ms) parked, then
the receiver `ff85` failed **translation** on the dispatch worker
(`dispatched intermediate receiver fragment failed ... malformed plan: two-phase aggregation ...`).
No `Run` returned `Err`, so no wipe: **+6.60 GB** (31.56 -> 38.16 GB, headroom.txt) that q17 started under.

All six failures (`headroom.txt` base@start of the next run): q05 -> 4.69, q08 -> 8.83, q09 -> 31.56
(carried through q10..q16), q16 -> +6.60, q17 -> 4.00, q18 -> 0, q21 -> 36.64 GB. Stolen engine
time (`refute-oom5/notes.md`): q05->q06 0.72 s, q08->q09 1.04, q09->q10 0.39, q17->q18 6.42,
q21->q22 3.35 = **11.9 s**.

Not caused by the leak (both skeptics, `refute-oom5/notes.md`): no pass/fail flipped; every failing
sender exceeds the 107.37 GB cap on its own. The leak only moved where q17 died (25 vs ~40 batches).
Order dependence is inference: q07's own peak 79.17 GB + 31.56 GB leak = 110.7 > 107.37.

### 1.4 What the 15 passing queries do (measured)

`cn1-cnlog.txt`: `fail=0` for all 45 passing runs; cancels are reason 5 or 1 only; the engine
`Err` arm never executed for them; `parked_slots` is empty at every query boundary
(`pool-trace.txt`: alloc returns to the pre-query baseline after each result fragment).

---

## 2. Mechanism

One idea: **the engine thread remembers which queries failed and owns "abandon query" as a
primitive**; the two CN call sites that learn about a failure (the dispatch worker's `Err` arm and
the FE's non-success cancel) invoke that primitive. Everything else follows from FIFO ordering on
the existing engine channel.

### 2.1 `engine.rs`

**(a) Tag parked output with its query.** `ParkedOutput` (`engine.rs:44-47`) gains
`query_id: Option<FragmentInstanceId>`, copied from `request.label.query_id` at park time
(`engine.rs:710-716`). `FragmentLabel.query_id` is already carried on every `ExecuteRequest`
(`engine.rs:72`, `fragment_executor.rs:58-63`) and set by `fragment_label` for every FE-dispatched
fragment (`compute_node_service.rs:1584-1589`).

**(b) A failed-query memory on the engine thread.** Next to `parked` / `parked_slots` / `poisoned`
(`engine.rs:267-275`), a `FailedQueries` value: `HashMap<FragmentInstanceId, String>` (id -> first
cause) plus a `VecDeque` for FIFO eviction at 1024 entries. Query ids are 128-bit unique, so a stale
entry can only ever match a fragment of the query that failed, and an evicted entry can only matter
if a fragment of a query that failed 1024 failures ago is still pending, which the FE's 300 s query
timeout rules out.

**(c) Skip pending runs of a failed query.** In the `Run` arm (`engine.rs:282-318`), before
`run_fragment`:

```rust
if let Some(query_id) = request.label.query_id
    && let Some(cause) = failed_queries.get(&query_id)
{
    let _ = request.respond.send(Err(format!(
        "fragment of query {query_id} skipped: the query already failed on this CN: {cause}"
    )));
    continue;
}
```

No `context.fragment()`, no `build()`, no GPU work, no park. The error text carries the original
cause, so a result fragment skipped this way still names the real failure through `fetch_data`
(and `ResultStore::fail_query` keeps the first cause anyway, `result_store.rs:174-179`).

**(d) Drop only the failed query's parked output.** Replace the body of the `Err` arm
(`engine.rs:301-315`) with a call to one function that both the `Err` arm and the new request use:

```rust
/// Drops every parked output of `query_id` (all of them when the failure carried no query id,
/// which is today's process-wide wipe) and remembers the failure so later `Run`s of the query
/// are skipped. Returns the number of parked fragments dropped.
fn abandon_query<'ctx>(
    parked: &mut HashMap<u64, ParkedOutput<'ctx>>,
    parked_slots: &mut HashMap<SenderSlot, (u64, u64)>,
    poisoned: &mut HashMap<SenderSlot, String>,
    failed_queries: &mut FailedQueries,
    query_id: Option<FragmentInstanceId>,
    cause: &str,
) -> usize {
    if let Some(query_id) = query_id {
        failed_queries.insert(query_id, cause.to_string());
    }
    let doomed: HashSet<u64> = parked
        .iter()
        .filter(|(_, output)| query_id.is_none() || output.query_id == query_id)
        .map(|(park_id, _)| *park_id)
        .collect();
    if doomed.is_empty() {
        return 0;
    }
    // Same bound as today: only the most recent abandonment explains a missing slot.
    poisoned.clear();
    parked_slots.retain(|slot, (park_id, _)| {
        let keep = !doomed.contains(park_id);
        if !keep {
            poisoned.insert(*slot, cause.to_string());
        }
        keep
    });
    parked.retain(|park_id, _| !doomed.contains(park_id));
    doomed.len()
}
```

`Err` arm becomes: log at `warn!` with `query_id`, `slots`, `error` ("discarding the failed
query's parked sender outputs"; the old wording stays for the `query_id: None` wipe), then
`abandon_query(..., request.label.query_id, &err)`. The `missing_slot` attribution
(`engine.rs:365-377`) is unchanged and still names the culprit.

**(e) One new request.** `EngineRequest::AbandonQuery { query_id: FragmentInstanceId, cause: String }`
with **no respond channel**: its two callers must never wait behind a running fragment (the
cancel RPC runs on the BRPC current-thread runtime; the module doc at `engine.rs:16-21` records the
q02 wedge that motivates the rule). Handler: `abandon_query(..., Some(query_id), &cause)` plus the
same `warn!`. FIFO on the existing channel gives the ordering the design relies on: a `Run` queued
before the `AbandonQuery` executes and parks, then gets dropped; a `Run` queued after is skipped.

### 2.2 `fragment_executor.rs`

One trait method with a default:

```rust
/// Best-effort, non-blocking: forget every parked output of `query_id` and refuse its later
/// runs. Called when the CN learns a query is dead (a fragment failed before or during its
/// run, or the FE cancelled it for a non-success reason). Unlike `drop_parked` this is a
/// sweep, not an exactly-once release, so an executor that parks nothing succeeds trivially.
fn abandon_query(&self, query_id: FragmentInstanceId, cause: String) -> Result<(), String> {
    let _ = (query_id, cause);
    Ok(())
}
```

`SiriusEngine` implements it by sending `EngineRequest::AbandonQuery` and returning without
receiving (an `Err` only when the channel is closed, i.e. shutdown). `StubExecutor` and every test
executor inherit the default.

### 2.3 `compute_node_service.rs`

**(f) Dispatch-worker failure.** In `run_ready_fragment`'s `(Some(id), Some(query_id))` arm
(`compute_node_service.rs:877-897`), right after `self.results.fail_query(query_id, id, error)`:

```rust
if let Err(err) = self.executor.abandon_query(query_id, error) {
    tracing::warn!(query_id = %query_id, error = %err, "could not abandon the failed query's parked output");
}
```

This covers every CN-side pre-run failure that leaves inputs parked: translation
(`:1361-1362`, the q16 case), "exchange senders produced different output names" (`:1347`), the
sink validations (`:1066-1131`), a posted-drain failure (`:872`, `:160-163`). For an engine-side
`Err` it is redundant and idempotent.

**(g) FE cancel for a non-success reason.** In `cancel_plan_fragment` (`:457-479`), keep the
existing `results.cancel(id, reason)` and add:

```rust
use crate::proto::starrocks::PPlanFragmentCancelReason as CancelReason;
let reason_code = request
    .cancel_reason
    .and_then(|code| CancelReason::try_from(code).ok());
let query_finished = matches!(
    reason_code,
    Some(CancelReason::QueryFinished | CancelReason::LimitReach)
);
if !query_finished
    && let Some(query_id) = request.query_id.as_ref().map(FragmentInstanceId::from)
    && let Err(err) = self.core.executor.abandon_query(query_id, reason.clone())
{
    warn!(query_id = %query_id, error = %err, "could not abandon the cancelled query's parked output");
}
```

(prost variant names per the generated enum, `target/.../out/starrocks.rs:3266`.) `QUERY_FINISHED`
and `LIMIT_REACH` are the two reasons the FE sends for a query whose rows it has; a missing or
unknown reason is treated as a failure (the existing cancel test at `:3414-3460` sends
`cancel_reason: None` with an "exceed big query cpu limit" message and `query_id: None`, so it
still reaches no engine call). The FE sends one cancel per instance, so a dead 11-fragment query
produces 11 `AbandonQuery` messages; each is a `HashMap` scan of a map that is empty after the first,
microseconds on the engine thread.

Why the gate is safe rather than merely conservative: in this CN's single-batch result model
(`result_store.rs:245-283`) the FE can only be at `QUERY_FINISHED`/`LIMIT_REACH` after the result
fragment ran, and a receiver runs only after every sender it reads has parked or staged
(`local_exchange.rs:248-280`), so at that moment nothing of the query is parked on any CN; an
`AbandonQuery` would find nothing. Gating simply avoids the engine message on every passing query.

### 2.4 Behaviour under the measured cases

**q05 (engine-side failure on the worker).** 5774's `Run` returns `Err` at 13:05:24.627 ->
`abandon_query(Some(q05), ...)` drops the 2 parked slots (as today) and records q05. 5773's `Run`
(sent 5 ms later by the worker) is skipped without touching the GPU; so are 5777, 5776 and the
receiver 5775 (which today relayed 11 batches and parked 4.69 GB). `run_ready_fragment` logs each
skip and calls `fail_query` (no-op: first failure wins) and `abandon_query` (no-op: nothing
parked). The 11 FE cancels with reason 3 each queue an `AbandonQuery` that finds nothing. q06's
first fragment starts as soon as the worker reaches it: `QueryBegin allocated=0` instead of
4,690,817,280 bytes, and ~0.7 s earlier.

**q16 (CN-side translation failure).** `ff87` and `ff86` park (6.6 GB). `ff85`'s translation fails
on the worker -> `fail_query` -> `abandon_query(Some(q16), "malformed plan ...")` -> the engine drops
both parked outputs and records q16. Pool returns to the pre-q16 baseline before q17 starts (today
+6.60 GB).

**q21 -> q22.** `acc` (179 ms) and `acb` (3153 ms, 36.6 GB parked) are skipped; q22's first
fragment starts ~3.35 s earlier with `allocated=0` instead of 36.64 GB.

**Sync dispatch mode (default, `SIRIUS_CN_ASYNC_SENDER_DISPATCH` unset).** Senders run inside
their RPC threads and may already be queued on the engine channel ahead of the failing `Run`'s
return. Those already-queued `Run`s still execute and park (as today), then the `AbandonQuery`
sent by the FE cancel (or the worker's `Err` arm) drops what they parked. End state is clean; the
stolen time is bounded by fragments that were already queued at the failure, and every `Run` queued
after the `Err` is skipped by the failed-query memory. The campaign ran with async dispatch on
(`capture-cn.sh:12`), where the worker sends one `Run` at a time and nothing is queued ahead.

**The 15 passing queries.** The `Run` arm does one `HashMap::get` on an empty map; the park stores
one `Option<FragmentInstanceId>`; the `Err` arm never runs; `run_ready_fragment`'s `Err` arm never
runs; every cancel carries reason 5 or 1 and takes the unchanged path. No engine message is added,
no ordering changes, no determinism change. The only visible difference is the cancel log line
gaining the decoded reason.

### 2.5 Explicit non-goals (and why they are safe to leave)

- **`LocalExchange` removal by query.** A dead query's `PendingReceiver` (a cloned
  `TExecPlanFragmentParams`, KB-scale host memory) and its `SenderSource::LocalParked` references
  stay in the rendezvous maps (`local_exchange.rs:87-95`). The GPU bytes they refer to are gone
  (dropped by query id), the receiver can never become ready (its failed sender never pushed), and
  if a leftover sender of the dead query did complete a set, the readied receiver's `Run` is
  skipped by the failed-query memory before it relays anything. Host-side KBs per failed query;
  not the measured GB-scale defect. Can be a follow-up with a `query_id` in `PendingReceiver`.
- **Aborting a fragment that is currently running.** The engine's single-flight lifecycle
  (`sirius_context.cpp:1555-1596`, per report B1) has no abort; that is fix 1's territory
  (fail-fast). This design only stops the next fragment.
- **`ResultStore` changes.** `cancel` keeps failing only `Waiting` entries; `fail_query` is not
  called from the cancel path (a `Pending` result must not be clobbered, `result_store.rs:490-504`).

---

## 3. Files to touch

| file | change | approx. lines |
|---|---|---|
| `experimental/starrocks/src/engine.rs` | `ParkedOutput.query_id`; `FailedQueries`; skip in the `Run` arm; `abandon_query` replacing the wipe body; `EngineRequest::AbandonQuery` + handler; `FragmentExecutor::abandon_query` impl; two GPU tests | ~80 + ~120 test |
| `experimental/starrocks/src/fragment_executor.rs` | trait method with default | ~10 |
| `experimental/starrocks/src/compute_node_service.rs` | one call in `run_ready_fragment`'s `Err` arm; reason-gated call in `cancel_plan_fragment`; a `RecordingAbandonExecutor` + three tests | ~20 + ~120 test |

Untouched: `local_exchange.rs`, `result_store.rs`, `nixl_transport.rs`, engine C++, translator, FE,
patches. Carve-plan layer: CN (`experimental/starrocks`) -> fork PR, one commit.

---

## 4. Tests

### 4.1 No-GPU unit tests (`pixi run cn-test-no-engine`, what CI runs; `pixi.toml:148`)

`compute_node_service.rs` tests, using the existing fixtures (`propagation_chain` `:3311-3343`,
`assert_exec_ok`, `fetch_error_eventually` `:2078-2102`, `route`, `sink_of_type` `:3799`) and a new
`RecordingAbandonExecutor` (pattern of `RecordingExecutor`, `:2914-2932`) that records
`(query_id, cause)` per `abandon_query` call and otherwise behaves as `StubExecutor`:

1. `receiver_pre_run_failure_abandons_the_querys_parked_output`: the `propagation_chain` with the
   middle fragment's sink replaced by an unsupported type (`sink_of_type(TDataSinkType::OLAP_TABLE_SINK)`),
   so the leaf parks, the middle becomes ready, translates, then fails at `:1066` before any run.
   Assert: `fetch_error_eventually(root)` carries the sink error, and the executor recorded exactly
   one `abandon_query` with the chain's `query_id` and that cause. (A second variant with two
   senders of different `output_names` exercises the `:1347` path.)
2. `cancel_for_a_non_success_reason_abandons_the_query`: send `PCancelPlanFragmentRequest` with
   `query_id: Some(..)` and `cancel_reason` = `INTERNAL_ERROR`, `TIMEOUT`, `USER_CANCEL`, `None`:
   each records one call whose cause contains the request's `error_message`; with `QUERY_FINISHED`
   and `LIMIT_REACH`: no call. With `query_id: None`: no call. The two existing cancel tests
   (`:3414-3495`) must pass unchanged.
3. `engine_failure_on_the_worker_abandons_the_query`: `FailingIntermediateExecutor` chain
   (`:3346-3373`) wrapped to also record `abandon_query`: one call with the middle's error.

### 4.2 GPU tests (`pixi run cn-test`, `pixi.toml:136-140`; under `GPU_ENGINE_TEST_LOCK`,
`lib.rs:81`, like `engine_executes_local_files_and_sequential_exchange`, `engine.rs:1284`)

`engine.rs` tests, reusing `local_files_plan`, `stream_plan` (`:916-983`) and the parquet fixture:

4. `failure_drops_only_the_failed_querys_parked_output_and_skips_its_later_runs`:
   - park sender A under `slot_a` with label `query_id = QA`; park senders B and C under `slot_b`,
     `slot_c` with label `QB`;
   - run a fragment labelled `QA` that fails before touching the GPU: `stream_plan(9, ..)` with
     `inputs: []` returns `Err("exchange node 9 is read as a stream but no sender output ... exists")`
     at `engine.rs:532-538`;
   - assert `drop_parked(slot_a)` errs with `"was discarded when another fragment on this CN failed"`
     (poisoned; `missing_slot`, `engine.rs:369-377`);
   - assert a receiver labelled `QB` over `slot_b` returns 3 rows (B survived the failure);
   - assert any `run` labelled `QA` returns `Err` containing `"skipped"` and the original cause;
   - call `abandon_query(QB, "test")`, then run any blocking request (FIFO makes the abandonment
     visible), then assert `drop_parked(slot_c)` errs as poisoned and a `run` labelled `QB` is skipped.
5. `unlabelled_failure_keeps_the_process_wide_wipe`: park under two slots with
   `FragmentLabel::default()`, fail an unlabelled fragment, assert both slots are gone (today's
   behaviour, kept for fixtures that carry no ids).

### 4.3 SF1000 acceptance (orchestrator-run, 1 CN, same configuration as the campaign)

Reuse `perf/sf1000/capture-cn.sh` (100 GiB pool, 16 GiB staging, async dispatch on, Quent on) and
`run-queries.sh`. Two arms:

**Smoke, ~7 minutes including cluster bring-up:** `capture-cn.sh 1 fix2-smoke 300 2 q05 q06 q16 q17 q21 q22`
(today's durations: 101 + 2.5 + 0.4 + 104 + 114 + 4 s ~ 5.5 min; ~10 s each once fix 1 lands).
Checks, all mechanical on `cn1/cluster.log` and `cn1/engine-cn0.log` with the existing
`refute-oom5` scripts (`pool-trace.txt`, `headroom.txt`) and `cnlog_extract.py`:

1. `headroom.txt` `base@start` = 0.00 for q06, q17, q22 (was 4.69, 38.16, 36.64) and for every run
   after a failure.
2. After each `fragment run failed` of a query, no `fragment run finished` with that `query_id`;
   every later `fragment run failed` of it has `elapsed_ms` <= 5 and an error containing
   `skipped: the query already failed`.
3. No `relayed native batches` line for a dead query after its failure (q05's 5775 and q21's `acb`
   today).
4. Gap from the client start (`runs.csv start_utc`) to the next query's first
   `fragment run started` <= 100 ms for q06 and q22 (was 720 and 3350 ms).
5. The q16 case: `engine-cn0.log` `QueryBegin` allocated at q17's first fragment == the value at
   q16's first fragment (the 6.60 GB is gone).
6. `discarding` warn lines carry a `query_id` and the number of slots; none say "every parked".

**Full arm:** the 22-query sweep in the campaign order, then `results_table.py`,
`compare.py`/`compare-cn1.txt`, `headroom.txt`. Pass criteria: the MATCH/VALUES-DIFFER set for the
15 passing queries identical to `compare-cn1.txt`; warm medians within the campaign's run-to-run
spread (6%); `oom_resched=0` for the 15; `base@start` = 0.00 on every row; sweep wall shorter by
the stolen time (section 5).

---

## 5. Predicted effect (derived from the campaign numbers)

| what | today (measured) | after (inference from the mechanism) |
|---|---|---|
| parked bytes carried into the next query after q05 / q08 / q09 / q16 / q17 / q21 | 4.69 / 8.83 / 31.56 / +6.60 / 4.00 / 36.64 GB | 0 in all six |
| engine-thread time stolen from the next query (q05->q06, q08->q09, q09->q10, q17->q18, q21->q22) | 0.72 + 1.04 + 0.39 + 6.42 + 3.35 = 11.9 s | ~0 (each skipped fragment costs one channel round trip, < 1 ms) |
| q06 cold / q22 cold | 2453 / 4110 ms | ~1.75 / ~0.95 s (warm 1916 / 943 ms plus the client's 17-57 ms) |
| sweep wall (910.3 s) | | -11.9 s (1.3%); the 775 s of OOM storms are fix 1's |
| headroom for q07 (own peak 79.17 GB) | 23.51 GB | 28.2 GB; q07 placed between q09 and q17 would fit (79.17 < 107.37) instead of the inferred 110.7 GB OOM |
| q10-q16, q22 timings | taken with 31.6 / 36.6 GB of the pool occupied (report section 5, item 7) | taken with a clean pool; C3's 47-178 ms reservation-gate blocks in q10/q12/q14 may shrink, otherwise within noise (`oom_resched=0` already) |
| pass/fail set | 15 pass, 6 OOM, 1 translator | unchanged (no failure was attributable to the leak); order-independence restored |
| oracle compare | as `compare-cn1.txt` | unchanged; no data path is touched |
| time-to-fail of the six | 54-232 s | unchanged (fix 1). Side effect without fix 1: a cleaner pool lets the failing lineitem sender park more batches before its first OOM (q17: 38 GB more at the measured 50-80 GB/s sink rate, +0.5-1 s), negligible against the 100-retry storm |

---

## 6. Risks

| # | risk | why it is bounded |
|---|---|---|
| R1 | Sync dispatch mode: `Run`s already queued on the engine channel at failure time still execute and park | Bounded to fragments whose RPCs arrived before the failure (all of a query's RPCs arrive within ~100 ms of each other, B1); they are dropped by the `AbandonQuery` that follows; every later `Run` is skipped. Measured config (async on) has nothing queued ahead. No regression vs today. |
| R2 | Replacing the process-wide wipe removes the accidental "next failure cleans the last leak" safety net | Every path that can leave parked output unreachable now abandons the query explicitly: engine `Err` (d), worker pre-run `Err` (f), FE cancel for failures detected anywhere (g). Enumerated in section 2.4. The unlabelled case keeps the wipe. |
| R3 | Cancel gate misses a reason under which output is still parked | For `QUERY_FINISHED`/`LIMIT_REACH` nothing of the query is parked by construction (section 2.3); every other reason abandons. Regression-neutral: today nothing is dropped on any cancel. |
| R4 | `AbandonQuery` dropping a slot mid remote drain (4 CN) | Same as today's wipe: the next `export_packed_next` returns the poisoned cause, the drain fails, the transport's `drop_parked` warns (`nixl_transport.rs:409-418`). The FE already knows the query is dead. |
| R5 | Skipped result fragment reports "skipped ..." instead of the original error | `fail_query` keeps the first cause per query (`result_store.rs:174-179`) and the skip text embeds the original cause anyway. In sync mode a skipped sender's RPC returns INTERNAL_ERROR; the FE is already cancelling and ignores a second cancel (`DefaultCoordinator.java:1056-1058`). |
| R6 | `poisoned` growth | Cleared and refilled on every abandonment that drops something, exactly today's bound (`engine.rs:308-313`). |
| R7 | `FailedQueries` growth or a false skip | Capped at 1024 with FIFO eviction; ids are unique, so a false positive is impossible and a false negative needs a fragment pending across 1024 failures (> FE timeout). |
| R8 | Fire-and-forget send on a closed channel | `Err` returned and logged at warn; only at shutdown. |
| R9 | A `Run` interleaving with `AbandonQuery` | Impossible: both are handled by the single engine thread between requests; a running fragment lives on the stack, not in `parked`, and is abandoned when it parks (it is dropped by the later `AbandonQuery` or, if the failure came first, skipped). |
| R10 | Log noise: a skipped fragment still emits `fragment run started` / `fragment run failed` from `run_labeled` (`:1549-1578`) and an `error!` in `run_ready_fragment` | Cosmetic; the acceptance grep uses the `skipped` marker and `elapsed_ms`. A one-line `ResultStore::query_failure` check in `run_labeled` could silence it later; deliberately not in this change (second mechanism for the same predicate). |

---

## 7. Effort

Code ~110 lines, tests ~240 lines. Half a day to write and run the no-GPU tests and clippy/fmt;
the two GPU tests need the CN's engine build (`pixi run cn-test`, GPU 1 per the plan). SF1000
smoke arm ~7 min, full arm ~16 min today (~3 min once fix 1 is in). One commit, one fork PR.

## 8. Dependencies and interactions with the other three fixes

- **None required.** Touches only the CN; no proto, FE, or engine change.
- **Fix 1 (fail-fast):** complementary and independent. With a ~3 s failure more of the dead
  query's fragments are still queued, so more are skipped and the saving grows; fix 1's
  "time-to-fail <= 5 s" measurement is cleaner when the leftover fragments (q17->q18: 6.42 s) no
  longer run between the failure and the next query. Suggested landing order: this fix before fix 1's
  SF1000 arm.
- **Fix 3 (cardinalities):** no interaction.
- **Fix 4 (fusion or spill):** fusion reduces the number of fragments per 1-CN query, hence the
  number of leftovers, but does not remove the need (4 CNs, translator failures, FE cancels).
  Spillable parked repositories do not change who owns a parked output; `abandon_query` still drops it.

---

## Appendix A. Code excerpts relied on

`engine.rs:44-47`
```rust
struct ParkedOutput<'ctx> {
    fragment: sirius::Fragment<'ctx>,
    outstanding: usize,
}
```

`engine.rs:262-275` (engine-thread state; `parked` must drop before `context`)
```rust
    let mut parked: HashMap<u64, ParkedOutput<'_>> = HashMap::new();
    let mut parked_slots: HashMap<SenderSlot, (u64, u64)> = HashMap::new();
    let mut next_park_id: u64 = 0;
    ...
    let mut poisoned: HashMap<SenderSlot, String> = HashMap::new();
```

`engine.rs:278-318` (one request at a time; the process-wide wipe)
```rust
    while let Ok(request) = requests.recv() {
        match request {
            EngineRequest::Run(request) => {
                let result = run_fragment(&context, &mut parked, &mut parked_slots, &poisoned, &mut next_park_id, &request);
                if let Err(err) = &result {
                    // ... The wipe is process-wide, so it also destroys OTHER in-flight fragments' parked output.
                    if !parked_slots.is_empty() {
                        warn!(slots = parked_slots.len(), error = %err,
                              "discarding every parked sender output on this CN after a fragment failure");
                    }
                    poisoned.clear();
                    for slot in parked_slots.keys() { poisoned.insert(slot.clone(), err.clone()); }
                    parked.clear();
                    parked_slots.clear();
                }
                let _ = request.respond.send(result);
            }
```

`engine.rs:532-538` (the pre-GPU failure the GPU test uses)
```rust
        if senders.is_empty() && remote_senders.is_empty() {
            return Err(format!(
                "exchange node {} is read as a stream but no sender output — parked or remote — exists for it",
                schema.node_id));
        }
```

`engine.rs:697-717` (park once per fragment)
```rust
    if !request.outputs.is_empty() {
        let park_id = *next_park_id; *next_park_id += 1;
        for (stream, slot) in request.outputs.iter().enumerate() {
            if parked_slots.contains_key(slot) { return Err(...); }
            parked_slots.insert(slot.clone(), (park_id, stream as u64));
        }
        parked.insert(park_id, ParkedOutput { fragment, outstanding: request.outputs.len() });
        return Ok(None);
    }
```

`engine.rs:16-21` (the rule the fire-and-forget request follows)
```
//! Staging-arena calls deliberately BYPASS the request channel ... Funneling leases through the
//! engine thread turns any engine stall into a peer's exchange stall ... so leases must never wait
//! behind engine work.
```

`engine.rs:813-819`, `fragment_executor.rs:217-223` (existing exactly-once `drop_parked`)
```rust
    fn drop_parked(&self, slot: SenderSlot) -> Result<(), String> {
        self.engine_call(|respond| EngineRequest::DropParked { slot, respond })
    }
```

`compute_node_service.rs:380-390` (single dispatch worker; leftovers of a dead query sit in `queue`/inbox)
```rust
fn dispatch_worker(core: Arc<ServiceCore>, inbox: mpsc::Receiver<ReadyFragment>) {
    while let Ok(ready) = inbox.recv() {
        let mut queue = vec![ready];
        while let Some(ready) = queue.pop() {
            queue.extend(core.run_ready_fragment(ready));
        }
    }
}
```

`compute_node_service.rs:457-479` (cancel is result-store only)
```rust
    async fn cancel_plan_fragment(&self, request: PCancelPlanFragmentRequest, _attachment: Vec<u8>) -> ... {
        let id = FragmentInstanceId::from(&request.finst_id);
        info!(fragment_instance_id = %id, query_id = ?request.query_id.as_ref().map(FragmentInstanceId::from),
              cancel_reason = request.cancel_reason, error_message = ?request.error_message,
              "acknowledging cancel_plan_fragment (best-effort: no engine-side abort yet)");
        let mut reason = format!("fragment instance {id} was cancelled by the FE");
        if let Some(message) = request.error_message.as_ref().filter(|msg| !msg.is_empty()) {
            reason = format!("{reason}: {message}");
        }
        self.core.results.cancel(id, reason);
        Ok(PCancelPlanFragmentResult { status: Self::ok_status() }.into())
    }
```

`compute_node_service.rs:864-911` (worker failure arm; the insertion point for (f))
```rust
    fn run_ready_fragment(&self, ready: ReadyFragment) -> Vec<ReadyFragment> {
        let id = Self::fragment_instance_id(&ready.params);
        let query_id = Self::query_id(&ready.params);
        ...
        match self.execute_ready_fragment(ready).and_then(FragmentOutcome::join_into_ready) {
            Ok(next) => next,
            Err(error) => {
                match (id, query_id) {
                    (Some(id), Some(query_id)) => {
                        ...
                        self.results.fail_query(query_id, id, error);
                    }
```

`compute_node_service.rs:1358-1393` (translation happens before the parked slots are extracted)
```rust
        let translated = self.translate_fragment_logged_with_inputs(&ready.params, &exchange_inputs, dump_seq)?;
        let mut inputs: Vec<(i32, Vec<SenderSlot>)> = Vec::new();
        ...
        for input in ready.inputs {
            for source in input.sources {
                match source {
                    SenderSource::LocalParked { slot, .. } => slots.push(slot),
```

`compute_node_service.rs:1066-1072` (a pre-run validation failure after translation)
```rust
        if sink.type_ != TDataSinkType::DATA_STREAM_SINK {
            return Err(format!("{} carries a {} output sink, which this CN does not support", ...));
        }
```

`result_store.rs:163-200` (first failure wins; only `Waiting` entries are affected by cancel, `:205-212`)
```rust
    pub(crate) fn fail_query(&self, query_id: FragmentInstanceId, failed_id: FragmentInstanceId, error: String) {
        ...
        // First failure wins: later failures are usually downstream echoes of the first.
        let cause = state.query_failures.entry(query_id).or_insert(cause).clone();
```

`local_exchange.rs:248-280` (a receiver is released only when every sender is complete)
```rust
            if complete != expected {
                return Ok(None);
            }
```

`nixl_transport.rs:407-421` (transport-side best-effort drop on a failed drain)
```rust
                TransportRequest::SendFragment { spec, respond } => {
                    let result = state.send_fragment(&spec);
                    if result.is_err() {
                        if let Err(drop_err) = state.executor.drop_parked(spec.slot) {
                            warn!(slot = ?spec.slot, error = %drop_err,
                                  "failed to drop the parked output of a failed remote transmit");
                        }
                    }
```

`internal_service.proto:464-482`
```
enum PPlanFragmentCancelReason { LIMIT_REACH = 1; USER_CANCEL = 2; INTERNAL_ERROR = 3; TIMEOUT = 4; QUERY_FINISHED = 5; };
message PCancelPlanFragmentRequest {
    required PUniqueId finst_id = 1;
    optional PPlanFragmentCancelReason cancel_reason = 2;
    optional bool is_pipeline = 10;
    optional PUniqueId query_id = 11;
    optional string error_message = 12;
};
```

`DefaultCoordinator.java:1028-1034` (success-path cancels)
```java
            if (!jobSpec.isBlockQuery() && executionDAG.getInstanceIds().size() > 1) {
                if (hasLimit && numReceivedRows >= numLimitRows) {
                    cancelInternal(PPlanFragmentCancelReason.LIMIT_REACH);
                } else {
                    cancelInternal(PPlanFragmentCancelReason.QUERY_FINISHED);
                }
            }
```

`DefaultCoordinator.java:764-786` (failure-path cancels: TIMEOUT / INTERNAL_ERROR), `:1161-1177`
(error message attached only for INTERNAL_ERROR), `:1056-1058` ("we can't cancel twice"),
`FragmentInstanceExecState.java:402-406` (`cancelPlanFragmentAsync(brpcAddress, queryId, instanceId, cancelReason, isEnablePipeline, errorMessage)`).

## Appendix B. Evidence files used

- `perf/sf1000/cn1/cluster.log` lines 1131-1136, 1934-1959, 6050-6329, 16571-16605, 21344-21617
- `perf/sf1000/cn1/engine-cn0.log` lines 61738-61889 (`[gpu_pool] ... QueryBegin/QueryEnd allocated=`)
- `perf/sf1000/agent-notes/refute-oom5/{notes.md,headroom.txt,pool-trace.txt,cn-events.txt}`
- `perf/sf1000/cn1-cnlog.txt` (per-run `fail=`, `cancels=`, `oom_resched=`)
- `perf/sf1000/cn1/runs/runs.csv` (client start times), `results.md`, `compare-cn1.txt`
- `perf/sf1000/capture-cn.sh`, `run-queries.sh`, `cnlog_extract.py` (reused for the acceptance arm)
- `/home/prestouser/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md` B1, B4, section 4 item 2
