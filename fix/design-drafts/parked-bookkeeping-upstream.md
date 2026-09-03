# Fix 2, upstream angle: per-query parked-output bookkeeping in the CN

Design written 2026-09-03 for the SF1000 plan (`~/.claude/plans/sf1000-top4-fixes-plan.md`, issue 2) from
the angle "what the maintainers of sirius-db/sirius would accept as one reviewable CN PR". Source tree read
at `/home/prestouser/aocsa/sirius-stacks-wt/perf` (branch `perf/profile-sf1000` = 45dab3be); every
`file:line` below is from that tree. Evidence root: session scratchpad `perf/sf1000/`. "Measured" means
copied from a named evidence file; everything else is marked inference.

## 1. The defect, restated from the code and the log

Four independent mechanisms let a dead query's output survive on the GPU and let its fragments keep
running after the query has already failed.

### 1.1 The engine thread wipes every parked output, process-wide, when any run fails

`experimental/starrocks/src/engine.rs:44-47` (the parked record has no owner):

```rust
struct ParkedOutput<'ctx> {
    fragment: sirius::Fragment<'ctx>,
    outstanding: usize,
}
```

`engine.rs:267-268, 291-316` (the Err arm):

```rust
let mut parked: HashMap<u64, ParkedOutput<'_>> = HashMap::new();
let mut parked_slots: HashMap<SenderSlot, (u64, u64)> = HashMap::new();
...
if let Err(err) = &result {
    // ... The wipe is process-wide, so it also destroys OTHER in-flight fragments'
    // parked output. Record why, and say so out loud ...
    poisoned.clear();
    for slot in parked_slots.keys() {
        poisoned.insert(slot.clone(), err.clone());
    }
    parked.clear();
    parked_slots.clear();
}
```

The wipe is also the *only* place parked memory of a failed query is released, so anything parked
after the wipe by the same dead query is never released (1.2).

### 1.2 Fragments of a failed query keep running and re-park output nobody consumes

`compute_node_service.rs:380-390` (dispatch worker: FIFO, one at a time, no liveness check):

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

`compute_node_service.rs:864-911` records the failure at query level (`self.results.fail_query(query_id,
id, error)` at :896) but nothing reads that record on the sender path: `run_ready_fragment`,
`process_fragment` (:918-951) and `run_labeled` (:1540-1581) never consult `results.query_failures`
(`result_store.rs:98`, documented as "First failure recorded per query. A result fragment that reserves
*after* the failure landed fails immediately" and used only in `reserve`, :127).

**Measured, q05 on 1 CN** (cn1/cluster.log after ANSI stripping; the capture ran with
`SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`, `perf/sf1000/capture-cn.sh:12`): the lineitem sender
`...5774` failed at 13:05:24.626 after 101,193 ms (`fragment run failed`, line 6051), the engine logged
`discarding every parked sender output on this CN after a fragment failure slots=2` (line 6050), the worker
attributed it with `fail_query` (line 6052), and then ran the same dead query's queued senders `5773`
(started 13:05:24.632012, line 6072, 8 ms), `5777` (33 ms), `5776` (582 ms) and the receiver `5775`
(`inputs=2 outputs=1`, 73 ms, lines 6181-6305). The FE's first `cancel_plan_fragment` for the query
arrived at 13:05:24.632266 (line 6073), 0.25 ms after `5773` had started. `5775` and `5776` parked output
that no receiver ever consumed; it survived until q08's failure wiped it.

### 1.3 Cancel is a result-store-only stub

`compute_node_service.rs:457-479`:

```rust
async fn cancel_plan_fragment(&self, request: PCancelPlanFragmentRequest, ...) {
    let id = FragmentInstanceId::from(&request.finst_id);
    info!(..., "acknowledging cancel_plan_fragment (best-effort: no engine-side abort yet)");
    ...
    self.core.results.cancel(id, reason);
    Ok(PCancelPlanFragmentResult { status: Self::ok_status() }.into())
}
```

The FE sends one cancel per fragment instance with the real instance id and the query id
(`fe-core/.../qe/scheduler/dag/FragmentInstanceExecState.java:404-406`,
`rpc/BackendServiceClient.java:192-206` fills `finstId`, `queryId`, `cancelReason`, `errorMessage`), with
reason `INTERNAL_ERROR` on failure (`qe/DefaultCoordinator.java:768-784 handleErrorExecution`) and
`QUERY_FINISHED` or `LIMIT_REACH` after the last result batch (`DefaultCoordinator.java:1027-1033`).
Measured: 11 cancels with `cancel_reason=3` for q05's 11 instances (cluster.log 6073-6094); `cancels=N`
equal to the fragment count for every passing query (`perf/sf1000/cn1-cnlog.txt`, e.g. `q02.r0 ...
frags=13 ... cancels=13`). On this CN a cancel frees nothing and dequeues nothing.

### 1.4 A pre-run failure loses the slots (and remote leases) already in hand

`compute_node_service.rs:1325-1394 execute_ready_fragment` translates first (:1361-1362) and only then
extracts the `LocalParked` slots and `Remote` staged batches out of `ready.inputs` (:1363-1392). A
translation error returns before the engine sees the slots, so nothing calls `drop_parked` for them and
nothing releases the remote batches' staging leases. `run_ready_fragment`'s Err arm (:875-909) only
records the failure.

**Measured**: q16 failed at translation ("multi_distinct_count has a partial state this translator does
not model", plan fact 5) after its partsupp scan had parked; `agent-notes/refute-oom5/headroom.txt` shows
`q16.r0 ... base@start 31.56 ... max_alloc 38.16` and `q17.r0 base@start 38.16`: the 6.60 GB parked by
q16 was carried into q17 and never released by anything but q17's own failure wipe.

### 1.5 What it cost at SF1000 (measured, `agent-notes/refute-oom5/headroom.txt`, GB at the first fragment of a run)

| after failure of | carried into | `base@start` |
|---|---|---|
| q05 | q06.r1..q07.r2 | 4.69 |
| q08 | q09 | 0.08 (q08's wipe cleared it; the 8.83 GB the report attributes lands inside q09) |
| q09 | q10.r1..q16 (seven queries) | 31.56 |
| q16 (translation) | q17 | 38.16 (= 31.56 + 6.60) |
| q17 | q18 | 0.00 at q18 start, 4.0 GB inside (B4) |
| q21 | q22.r0 5.94 during, q22.r1/r2 | 36.64 |

Engine-thread time spent on dead queries' fragments after their failure (B4): q17 -> q18 6.42 s, q21 -> q22
~3.35 s, q08 -> q09 1.04 s, q05 -> q06 0.70 s (8 + 33 + 582 + 73 ms from lines 6097-6305). q22 cold
4110 ms vs 971/915 ms warm (`cn1-cnlog.txt` lines 50-52).

B4's verdict stands: no pass/fail at SF1000 was decided by the leak. This fix is hygiene, order
independence of the sweep, and the missing cleanup for the multi-CN cancel path. It does not make any of
the six failing queries pass.

## 2. Design

One CN PR, no engine C++ change, no FE patch, no proto change, no new environment knob.

### 2.1 Principle

*The engine holds parked state; the CN decides when a query is over.* The engine thread learns that a
query is over from two sources only: its own `Err` for a run of that query (immediately, on the same
thread, before it dequeues the next request), and an explicit `retire` request from the CN (for
pre-run failures and FE cancels). Every retirement is scoped to one query id, taken from the
`FragmentLabel` every run already carries (`fragment_executor.rs:57-63`; set from the FE params in
`compute_node_service.rs:1584-1589 fragment_label`).

### 2.2 New module `experimental/starrocks/src/parked_registry.rs` (CI-compiled, no `sirius` dependency)

The three loose maps and free functions in `engine.rs` (`parked`, `parked_slots`, `poisoned` at
:267-275; `missing_slot` :369-377; `export_next` :380-403; `release_slot` :408-425; the park block
:697-717) move into one generic struct so the bookkeeping is unit-testable without a GPU. `engine.rs` is
behind `#[cfg(feature = "sirius-engine")]` (`src/lib.rs:51-52`) and CI builds `--no-default-features`
(`Cargo.toml:44-51`), so today none of this logic runs in CI.

```rust
/// One parked sender fragment and who still has a claim on it.
struct Parked<F> {
    fragment: F,
    /// The StarRocks query this output belongs to (`None` for unlabeled test runs).
    query_id: Option<FragmentInstanceId>,
    /// Destinations that have not yet released their stream.
    outstanding: usize,
}

pub(crate) struct ParkedRegistry<F> {
    parked: HashMap<u64, Parked<F>>,
    slots: HashMap<SenderSlot, (u64, u64)>,
    /// Why a slot's output went away: filled by `retire`, replaced (never accumulated) on the
    /// next retirement. Same bound and same message contract as today's `poisoned`.
    poisoned: HashMap<SenderSlot, String>,
    next_id: u64,
}

impl<F> ParkedRegistry<F> {
    /// Park once; each destination claims (id, stream i). Refuses a duplicate slot before
    /// inserting anything (today's engine.rs:697-717 contract).
    pub fn park(&mut self, query_id: Option<FragmentInstanceId>, outputs: &[SenderSlot], fragment: F)
        -> Result<(), String>;
    /// The parked fragment and stream a slot names (engine.rs:625-631 relay lookup, :386-392 export).
    pub fn claim(&mut self, slot: &SenderSlot) -> Result<(&mut F, u64), String>;
    /// Read-only lookup used by the cardinality declaration (engine.rs:559-566).
    pub fn peek(&self, slot: &SenderSlot) -> Option<(&F, u64)>;
    /// Exactly-once release of one destination's claim (engine.rs:408-425).
    pub fn release(&mut self, slot: &SenderSlot) -> Result<(), String>;
    /// Drops every parked fragment of `query_id`, poisons its slots with `cause`, returns
    /// (fragments dropped, slots dropped). Idempotent: a second call returns (0, 0).
    pub fn retire(&mut self, query_id: Option<FragmentInstanceId>, cause: &str) -> (usize, usize);
    pub fn len(&self) -> usize;
}

/// Queries this CN has declared over, so a run that was already queued for one of them is refused
/// instead of re-parking output nobody will consume. Bounded FIFO; a StarRocks query id is never
/// reused, so eviction only forgets an explanation, never a live query.
pub(crate) struct RetiredQueries {
    order: VecDeque<FragmentInstanceId>,
    causes: HashMap<FragmentInstanceId, String>,
}
impl RetiredQueries {
    pub const CAPACITY: usize = 64;
    pub fn mark(&mut self, query_id: FragmentInstanceId, cause: String);
    pub fn cause(&self, query_id: FragmentInstanceId) -> Option<&str>;
}
```

The `poisoned` message changes from "was discarded when another fragment on this CN failed" to
"was discarded when its query was retired: {cause}"; the generic "no parked sender output to {verb} for
{slot:?}" stays (engine.rs:369-377 contract, relied on by `engine_pushes_staged_remote_batches`
:1200-1203 which only asserts `is_err()`).

### 2.3 `engine.rs`: scoped retirement, refusal of dead runs, a retire request

* `engine_thread` (:242-363) owns `ParkedRegistry<sirius::Fragment<'_>>` (declared after `context`
  for the drop order the comment at :262-266 requires) and an `Arc<Mutex<RetiredQueries>>` shared with
  the `SiriusEngine` handle, in the same way the handle already serves `staging` off-thread (:129-138;
  the "one shared C++ allocator, two entry points" precedent). The shared set is what lets a cancel
  refuse runs that are already queued in the request channel ahead of it; the channel is FIFO
  (`std::sync::mpsc`, :151) and a queued `Run` of a dead query would otherwise execute for its full
  duration (a lineitem scan at SF1000: 101 s) before any retire message reached the thread.

* Err arm (:291-316) becomes:

```rust
if let Err(err) = &result {
    retire(&mut registry, &retired, request.label.query_id, err);
}
```

  with

```rust
fn retire(registry: &mut ParkedRegistry<_>, retired: &Mutex<RetiredQueries>,
          query_id: Option<FragmentInstanceId>, cause: &str) {
    let (fragments, slots) = registry.retire(query_id, cause);
    if let Some(query_id) = query_id {
        retired.lock().mark(query_id, cause.to_string());
    }
    if fragments > 0 {
        warn!(query_id = ?query_id, fragments, slots, still_parked = registry.len(), cause,
              "retired a query's parked sender outputs");
    }
}
```

  Unlabeled runs (`query_id == None`, test fixtures only) retire the unlabeled bucket, never other
  queries; the process-wide wipe is gone.

* `run_fragment` (:430-481) refuses a dead query before creating a fragment, *inside* the function whose
  Err path already sweeps un-pushed remote-input leases (:462-478), so a refused run leaks no lease:

```rust
if let Some(query_id) = request.label.query_id
    && let Some(cause) = retired.lock().cause(query_id)
{
    return Err(format!("query {query_id} was already retired on this CN: {cause}"));
}
```

  (placed as the first statement of the `run_fragment_inner` call chain; the lease sweep at :462-478
  runs because `result.is_err()`.)

* New request variant, fire-and-forget (the cancel RPC must not wait behind a running fragment, the same
  reason `staging_*` bypasses the channel, :16-21):

```rust
/// Drop every parked output of `query_id` and refuse its later runs. Sent by the CN when it learns
/// a query is over by a route the engine cannot see: a pre-run failure or an FE cancel.
RetireQuery { query_id: FragmentInstanceId, cause: String },
```

  `SiriusEngine::retire_query(query_id, cause)` marks the shared `RetiredQueries` first (so the refusal is
  visible to the very next dequeue) and then `send`s the request without a respond channel; the engine
  thread logs the outcome. `engine_call` (:750-765) gets a sibling `engine_send` (send only).

* `FragmentExecutor` (`fragment_executor.rs:178-241`) gains one method with a default that does nothing,
  next to `drop_parked` (:220-223):

```rust
/// Drops every parked output of `query_id` and refuses later runs for it. Called when the CN
/// learns the query is over: a fragment failed before the engine ran it, or the FE cancelled.
fn retire_query(&self, query_id: FragmentInstanceId, cause: &str) -> Result<(), String> {
    let _ = (query_id, cause);
    Ok(())
}
```

  `drop_parked(slot)` keeps its exactly-once contract for the transport (`nixl_transport.rs:790` on
  success, `:412` on a failed transmit); a slot retired first now reports the retiring cause through
  `poisoned`, exactly as the wipe did.

### 2.4 `compute_node_service.rs`: one function decides a query is over; three places consult it

* New `ServiceCore::fail_fragment(&self, id, query_id, error: String)`: `self.results.fail_query(query_id,
  id, error.clone())` (unchanged semantics, :896) followed by `self.executor.retire_query(query_id,
  &error)` (logged, never fatal). Called from:
  1. `run_ready_fragment`'s `(Some(id), Some(query_id))` arm (:877-897) in place of the bare
     `fail_query`. This is the fix for 1.4 (translation and schema-mismatch errors at :1337-1347,
     :1361-1362) and the belt for 1.1 (idempotent when the engine's own Err arm already retired).
  2. `exec_single_attachment` (:705-718) and `translate_batch_attachment` (:1662-1674) on a
     `process_fragment` error when the params carry ids: the inline path (the default; async dispatch is
     off unless `SIRIUS_CN_ASYNC_SENDER_DISPATCH` is set, :271-294) today reports an inline failure only
     as the RPC status and never at query level, so nothing on this CN knows the query is dead until the
     FE's cancel arrives. Recording it makes both dispatch paths behave the same on the failing node.
     Result-store effect: a result instance of that query reserved on this CN reports the real cause on
     its first poll instead of the 600 s `wait_ready` timeout (`result_store.rs:219-243`).

* New `ServiceCore::refuse_if_retired(&self, params) -> Result<(), String>`: `Err("query {q} already
  failed on this CN: {cause}")` when `results.failure_of(query_id)` is `Some`. Consulted at the top of
  `process_fragment` (:918, covers inline and batch arrivals, and refuses registering receivers of dead
  queries in `LocalExchange`) and at the top of `run_ready_fragment` (:864, covers the dispatch worker,
  which is where the measured q05 senders ran). In `run_ready_fragment` the refusal must also release the
  `SenderSource::Remote` staged batches still inside `ready.inputs` (`executor.staging_release(offset)`
  for `len > 0`; `StagedBatch` contract at `fragment_executor.rs:92-105`), logs
  `info!(query_id, fragment_instance_id, cause, "skipping fragment of a retired query")`, and returns
  no follow-on fragments. No `fragment run started` line is emitted, which is the acceptance signal.

* `cancel_plan_fragment` (:457-479) becomes a real teardown, still answering OK immediately (the
  jprotobuf-channel reason in its docstring is unchanged):

```rust
let id = FragmentInstanceId::from(&request.finst_id);
let query_id = request.query_id.as_ref().map(FragmentInstanceId::from);
let reason_text = ...;                 // today's message, unchanged
self.core.results.cancel(id, reason_text.clone());            // unchanged
if let Some(query_id) = query_id {
    if !is_internal_cancel(request.cancel_reason) {           // LIMIT_REACH, QUERY_FINISHED are internal
        self.core.results.cancel_query(query_id, reason_text.clone());
    }
    self.core.release_staged(self.core.exchanges.retire_receiver(id)); // 2.5
    if let Err(err) = self.core.executor.retire_query(query_id, &reason_text) { warn!(...) }
}
```

  Reason codes come from `PPlanFragmentCancelReason` (`internal_service.proto:464-471`: LIMIT_REACH=1,
  USER_CANCEL=2, INTERNAL_ERROR=3, TIMEOUT=4, QUERY_FINISHED=5). Every reason retires the query's parked
  outputs and rendezvous state on this CN (after LIMIT_REACH the FE has its rows and not-yet-run receivers
  would otherwise leak exactly like a failure); only the non-internal reasons record a query-level failure
  in the result store, so `results.query_failures` does not grow by one entry per successful query.
  `retire_query` is fire-and-forget, so N cancels per query cost N channel sends; the engine finds
  nothing to drop for a finished query and logs nothing. The phased-schedule variant
  (`ExecutionDAG.java:620-645 cancelQueryContext`, dummy instance `(0,0)`) is handled by the same code:
  the unknown receiver retires nothing, the query-level retire still frees the parked outputs.

### 2.5 `local_exchange.rs`: retire by receiver instance, refuse late frames

Removals happen only in `take_ready` (:282-308). Add, keyed by what the exchange already keys everything
by (the receiver's `FragmentInstanceId`, `ExchangeKey` :19-22):

```rust
/// Forgets a receiver the FE cancelled: its pending registration, every sender source recorded for
/// it, and its remote sequence tracking. Returns the removed sources so the caller can release the
/// staging leases of the remote ones. The receiver id is remembered (bounded) so a frame from a
/// peer's still-draining sender is refused instead of re-creating the entry.
pub(crate) fn retire_receiver(&self, fragment_instance_id: FragmentInstanceId) -> Vec<SenderSource>;
/// Whether `retire_receiver` was called for this receiver.
pub(crate) fn is_retired(&self, fragment_instance_id: FragmentInstanceId) -> bool;
```

`ExchangeState` (:87-95) gains `retired: VecDeque<FragmentInstanceId>` (const cap 1024, 16 bytes each).
`handle_transmit_packed` (:721-803) checks `is_retired(key.fragment_instance_id)` before `push_remote_frame`
and, for a retired receiver, releases the frame's lease (`executor.staging_release(offset)` when a batch is
present, the same shape as the canary branch :728-734) and returns `Ok(())`, so the peer's drain completes
quietly. No query id is needed in the exchange and the wire frame (`PTransmitPackedParams`,
`internal_service.proto:812-827`, no `query_id` field) is untouched: the FE sends one cancel per deployed
instance, and a sender can only exist for a receiver that was deployed (receiver-first dispatch, the
module doc at :1-6).

### 2.6 `result_store.rs`: two small additions

```rust
/// The first failure recorded for `query_id`, if any (`fail_query` or `cancel_query`).
pub(crate) fn failure_of(&self, query_id: FragmentInstanceId) -> Option<String>;
/// Records an FE cancel at query level: later fragments of the query are refused on arrival and
/// still-`Waiting` result entries of the query fail now. Delivered or drained entries keep their
/// state (same rule as `cancel`, :202-212).
pub(crate) fn cancel_query(&self, query_id: FragmentInstanceId, reason: String);
```

`cancel_query` uses `query_results` (:95) to find the query's result instances; it never touches
`Pending`/`Drained`. The pre-existing growth of `query_failures`/`fragments` over the process lifetime
(TODO at :250-254) is not addressed here; this change adds entries only for failed or externally
cancelled queries, as today.

### 2.7 Behaviour under the measured SF1000 case (1 CN, same order)

q05: the engine's Err arm retires query `...576c` at 13:05:24.626 (drops the 2 parked slots of that query,
marks it retired). The worker records `fail_query` + `retire_query` (no-op). Next inbox items `5773`,
`5777`, `5776`, `5775` hit `refuse_if_retired` in `run_ready_fragment` and are skipped: no
`fragment run started`, no GPU work, nothing parked. The FE's 11 cancels (`reason=3`) each call
`cancel_query` (first wins), `retire_receiver` (the two never-ready receivers of streams 7 and 9 leave the
rendezvous) and `retire_query` (nothing left to drop). `[gpu_pool] ... QueryBegin allocated=` at q06.r0's
first fragment returns to the ~3 KB baseline instead of 1.32 -> 4.69 GB.

q16: the receiver's translation fails on the worker -> `fail_fragment` -> `retire_query` drops the parked
partsupp scan (6.60 GB) immediately; q17 starts at 31.56 -> 0 GB (with q09's leak also gone).

q22: q21's four other senders are skipped after the lineitem failure; q22.r0 no longer queues behind them
(3.35 s) nor runs with 36.64 GB less headroom.

Unlabeled or out-of-order arrivals: a receiver of a dead query arriving after the failure is refused at
`process_fragment` (RPC error the FE ignores, it is already cancelling); a sender already queued in the
engine channel (inline path) is refused at `run_fragment` and its remote leases swept.

### 2.8 Behaviour for the 15 passing queries

While a query is alive nothing changes: `refuse_if_retired` is one `HashMap` lookup under the result
store mutex per fragment arrival and per dispatch (microseconds against fragment runs of 4 ms to 8 s,
`cn1-cnlog.txt`); `run_fragment`'s dead check is one small mutex lock per run. At query end the FE's
`QUERY_FINISHED` cancels (already sent today, `cancels=N`) each cost one non-blocking channel send and one
empty `retire` on the engine thread between fragments. `results.cancel` is unchanged, so delivered rows are
never clobbered (guarded by `cancel_fails_only_a_waiting_entry`, `result_store.rs:490-510`).

## 3. Files to touch

| file | change | lines (est.) |
|---|---|---|
| `experimental/starrocks/src/parked_registry.rs` | new: `ParkedRegistry<F>`, `RetiredQueries`, CI tests | +200 (+120 tests) |
| `experimental/starrocks/src/lib.rs` | `mod parked_registry;` (not feature-gated) | +1 |
| `experimental/starrocks/src/engine.rs` | replace maps/free functions with the registry (:267-275, :369-425, :559-566, :625-639, :697-717); Err arm -> `retire` (:291-316); dead check in `run_fragment`; `RetireQuery` variant + `engine_send`; module doc line on retirement; two GPU tests | -110/+120 |
| `experimental/starrocks/src/fragment_executor.rs` | `FragmentExecutor::retire_query` default method | +10 |
| `experimental/starrocks/src/compute_node_service.rs` | `fail_fragment`, `refuse_if_retired`, `release_staged`, inline-path recording, `cancel_plan_fragment` teardown, docstring rewrite (:450-455); tests | +110 (+300 tests) |
| `experimental/starrocks/src/local_exchange.rs` | `retire_receiver`, `is_retired`, `retired` FIFO; tests | +45 (+60 tests) |
| `experimental/starrocks/src/result_store.rs` | `failure_of`, `cancel_query`; tests | +30 (+40 tests) |
| `experimental/starrocks/DEMO.md` | "What it does not exercise yet" (:37-42): replace the implicit "no cleanup on cancel" with what a cancel now does; one sentence | +4/-1 |
| `experimental/starrocks/docs/TUNABLES.md` | no change: no new knob (the two capacities are `const`) | 0 |

No file under `src/` (engine C++), `patches/`, or the StarRocks FE changes. `nixl_transport.rs` is untouched
(its `drop_parked` calls keep their contract).

Split into two commits inside one PR (both Conventional Commits with the repo's `Co-Authored-By` trailer,
matching `git log` on the branch):

1. `fix(cn): retire a failed query's parked output instead of wiping every query's` (registry, engine Err
   arm, dead check, `fail_fragment` on both dispatch paths, `refuse_if_retired`; fixes 1.1, 1.2, 1.4).
2. `feat(cn): cancel_plan_fragment tears down the cancelled query on this CN` (`RetireQuery` request,
   `cancel_query`, `retire_receiver` + late-frame refusal; fixes 1.3).

If a reviewer asks for smaller units, commit 2 stands alone as a stacked PR on commit 1.

## 4. Tests

### 4.1 CI (`cargo test --no-default-features`, the `sirius-rust` gate)

`parked_registry.rs`:
* `park_then_release_last_claim_drops_the_fragment` (broadcast: two slots, one fragment; drop on the
  second release; second release of one slot is a loud error).
* `retire_drops_only_that_query_and_poisons_its_slots` (park for A and B; `retire(A)` -> B's claim still
  works, A's slot error names the cause, `retire(A)` again returns `(0, 0)`).
* `retired_queries_are_bounded_and_keep_the_cause` (65 marks evict the first; `cause` of the newest holds).

`result_store.rs`:
* `cancel_query_fails_waiting_entries_only_and_is_visible_to_failure_of` (Waiting -> Failed with the
  reason; Pending stays Pending; a later `reserve` for the query fails on arrival, reusing the pattern of
  `query_failure_recorded_before_reserve_fails_the_result_instance_on_arrival`, :451-470).

`local_exchange.rs`:
* `retire_receiver_returns_its_sources_and_refuses_later_frames` (register receiver, push one local and
  one open remote source; retire -> both sources returned, `is_retired` true, a later
  `register_receiver`/`push_sender` for another receiver is unaffected).

`compute_node_service.rs` (all on `StubExecutor`-family executors already in the module):
* `queued_fragments_of_a_failed_query_are_skipped` — `FailingIntermediateExecutor` (:1997-2009) wrapped
  with a call counter; `propagation_chain` (:3311-3343) plus a fourth sender-only fragment of the same
  query dispatched after the middle failed, once with `set_async_sender_dispatch(true)` (worker path: a
  sentinel fragment of another query proves FIFO progress, the count did not move) and once inline (RPC
  status `INTERNAL_ERROR` containing "already failed on this CN", count did not move).
* `a_fragment_failure_retires_the_query_on_the_executor` — a `RecordingRetireExecutor` (records
  `retire_query(query_id, cause)`; sibling of `RecordingPinExecutor` :1822-1849) sees exactly the failing
  query's id after the root's `fetch_error_eventually`.
* `translation_failure_retires_the_slots_in_hand_and_releases_remote_leases` — two senders with
  different output names into one exchange (`per_exch_num_senders = 2`) trip the pre-run check at
  :1342-1347; assert `retire_query` recorded and, with a staged remote frame as the second sender
  (fixture `transmit_params` :2936 and the release-recording executor :2920-2932), `staging_release`
  saw the frame's offset.
* `an_inline_sender_failure_is_recorded_at_query_level` — async off; a sender with an unsupported sink
  (`sink_of_type`, :3799) returns an RPC error; a result fragment of the same query reserved afterwards
  fails on its first poll with that cause.
* `cancel_with_internal_error_retires_the_query` — extends
  `cancel_plan_fragment_returns_ok_and_unblocks_a_waiting_result_poll` (:3414-3460): the cancel carries
  `query_id` and `cancel_reason = 3`; assert `retire_query` recorded with the FE's message, a subsequent
  sender RPC for the query is refused without a run, and a `transmit_packed` frame for the cancelled
  receiver is released, not stored.
* `query_finished_cancel_keeps_delivered_rows_and_records_no_failure` — after a full chain and fetch,
  cancel with `reason = 5` for each instance: `fetch_data` still reports EOS, `retire_query` recorded,
  `core.results.failure_of(query)` is `None`.
* `cancel_for_an_unknown_instance_with_a_query_id_still_retires_the_query` — the phased-schedule
  dummy `(0,0)` shape.

### 4.2 GPU (`cargo test` with the engine feature, under `GPU_ENGINE_TEST_LOCK`, not run in CI; the PR's
testing note names them as such)

`engine.rs`:
* `a_failed_run_retires_only_its_own_query` — park the parquet fixture (`write_users_parquet`, :1131) twice
  under labels A and B (`FragmentLabel { query_id: Some(..), .. }`); run a labelled-A `stream_plan` for a
  node with no parked sender (fails at :532-537 before `build`); assert the B receiver relays 3 rows, an
  unlabeled receiver on A's slot fails with "retired", `drop_parked(A's slot)` errs, and any run labelled
  A is refused with "already retired".
* `retire_query_drops_parked_output_and_refuses_later_runs` — park under C, `engine.retire_query(C,
  "cancelled")`, then a run reading C's slot fails naming "cancelled" (FIFO ordering makes the
  fire-and-forget deterministic here), and `staging_lease(1024)` after the drop lands at offset 0 as in
  `engine_pushes_staged_remote_batches` (:1222-1225), proving no lease survived.

The two existing GPU tests (`engine_executes_local_files_and_sequential_exchange`,
`engine_pushes_staged_remote_batches`) guard the refactor of the park/claim/release path.

### 4.3 SF1000 acceptance (orchestrator-run, 1 CN, 100 GiB pool, same order and same `capture-cn.sh`)

1. `grep '\[gpu_pool\] GPU:0 QueryBegin' cn1/engine-cn0.log` at the first fragment of q06.r0, q09.r0,
   q10.r1, q17.r0, q18.r0, q22.r0/r1: `allocated=` within 1 MB of the pre-q05 baseline (3-4 KB), against
   1.32/4.69, 0.08, 31.56, 38.16, 0.00, 5.94/36.64 GB measured (`headroom.txt`, `base@start`).
2. For each failed query id, `cluster.log` contains no `fragment run started` after its `fragment run
   failed`; instead `skipping fragment of a retired query` lines (add a `skips=` column to
   `cnlog_extract.py`).
3. `cn1-cnlog.txt` q22.r0 within the cold/warm ratio band of the other fragment-heavy queries (q02 1.35x,
   q11 1.08x, q20 1.07x): about 1.0-1.3 s, from 4110 ms.
4. `results.md` warm medians of the 15 passing queries within run-to-run noise of the campaign table;
   `compare.py` result set unchanged (q02 q04 q06 q12 q13 q14 q20 q22 MATCH; the known FP64 diffs on
   q01 q03 q07 q10 q19 unchanged).
5. Engine log for each retire: one `retired a query's parked sender outputs` line per failure carrying
   `fragments`, `slots`, `still_parked = 0` and the fail-fast cause once fix 1 lands.
6. A 4 CN arm with a deliberately failing query (q16, translator gap) between two passing queries: every
   CN's `QueryBegin allocated=` returns to baseline and the peers log `skipping fragment of a retired
   query` or refused runs, not `fragment run finished`, for q16's id.

## 5. Predicted effect (numbers derived from the evidence)

| what | today (measured) | after (prediction) |
|---|---|---|
| parked GB surviving a failure into later queries | 4.69, 8.83, 31.56 (x7 queries), 6.60, 4.0, 36.64 | 0 |
| engine time on dead-query fragments after failure | 0.70 s (q05), 1.04 s (q08), 6.42 s (q17), ~3.35 s (q21) | 0 |
| q22.r0 | 4110 ms | ~1.0-1.3 s |
| q18 time-to-fail | 54.2 s (includes ~6.4 s of q17's dead senders) | ~47.8 s; with fix 1, ~3-5 s |
| pass/fail set at SF1000 | 15 pass, 6 OOM, q16 translator | unchanged (B4: no pass/fail was decided by the leak) |
| q10/q13/q14 warm (ran under 31.56 GB leak) | 3697/2254/2588 ms | unchanged to slightly better; the C3 reservation-gate blocks (47-178 ms) needed the leak's headroom loss; inference, low confidence |
| 4 CN, a query failing on one CN | peers keep its parked output for the process lifetime (inference from 1.3; not measured, all 15 passed at 4 CNs) | peers drop it on the FE's INTERNAL_ERROR cancels and refuse its queued runs |

## 6. Risks and how the design bounds them

1. **Refusing a run the FE still wants.** Only three events mark a query retired: the engine's own Err for
   that query, a recorded CN failure of that query, or an FE cancel for it. All are terminal on the FE side
   (`DefaultCoordinator.java:1048-1066`: a cancelled query cannot be cancelled twice and receives no new
   dispatches). `QUERY_FINISHED`/`LIMIT_REACH` retire parked state but record no failure, so a repeat
   `fetch_data` still sees EOS.
2. **Retiring a query whose remote drains are in flight** (same query, this CN is a sender): the transport
   thread's next `export_packed_next` fails with the retiring cause, its `drop_parked` warns (:412-418),
   and the RPC reports a failure the FE already knows. Today's wipe does the same to *every* query's drain.
3. **Refactor of the park/claim/release path.** Mechanical move of existing code into `ParkedRegistry`;
   covered by the two existing GPU tests and the new CI tests. Keep the `poisoned` replace-never-accumulate
   rule and its bound (engine.rs:308-313).
4. **Loss of the accidental sweep.** A parked output whose receiver never arrives and whose query never
   fails was, until now, freed by the next unrelated failure. With per-query scoping it stays until the
   FE's end-of-query cancel, which every multi-instance query receives (`DefaultCoordinator.java:1027-1033`;
   measured `cancels=N` for all 15 passing queries). Single-instance queries get no cancel but also have
   no exchange and park nothing.
5. **Bounded memories.** `RetiredQueries` (64) and the exchange's `retired` FIFO (1024) are `const`s, not
   knobs; eviction forgets an explanation (a later drain gets the generic "no parked sender output") or, for
   the exchange, lets a very late frame re-create a source (today's behaviour). Query ids never recur.
6. **Inline-path recording is a behaviour change**: an inline sender failure now also fails the query's
   result instances reserved on this node with the real cause instead of the 600 s long-poll timeout. This
   is the documented reason `SIRIUS_CN_ASYNC_SENDER_DISPATCH` is off by default (:284-288); the fix does not
   forward failures to other nodes, so that note stays true.
7. **Both dispatch modes must be tested** (the campaign ran with async on; the default is off); the CI
   tests run each relevant case in both modes.
8. **Fire-and-forget `RetireQuery`** loses its error return; the engine thread logs failures. The only
   failure mode is a closed channel (shutdown), when there is nothing left to drop.

## 7. Effort

About two engineer-days plus one GPU session: 0.5 d registry + engine refactor, 0.5 d CN paths and
cancel, 0.5 d tests (CI trio `cargo fmt/clippy/test --no-default-features`, then the GPU tests on one
device), 0.5 d SF1000 acceptance arm and PR body. Roughly 400 non-test lines and 500 test lines in one PR,
well under the ~1500-line bar the carve plan applies to CN PRs.

## 8. Dependencies and interactions with the other three fixes

* **Fix 1 (OOM fail-fast, engine C++, `src/pipeline/gpu_pipeline_executor.cpp`)**: no shared files. Land
  fix 2 first or together: fix 1's "time-to-fail <= 5 s" measurement is otherwise polluted by the dead
  query's remaining senders (0.7-6.4 s stolen from the next query) and by leak-dependent headroom. Fix 1's
  error text becomes the `cause` in the retire log line and in `poisoned`.
* **Fix 3 (FE cardinalities)**: independent. Different plan shapes change which fragments park how much,
  not the bookkeeping.
* **Fix 4 (run the six on 1 CN)**: fusion (4a) reduces the number of parked outputs on 1 CN but does not
  remove the need for cleanup on failure or on multi-CN cancel. Spillable parked repositories (4b) must
  keep `Fragment` drop releasing HOST-tier batches too; verify when implementing 4b (inference: the
  repository is owned by the fragment, `src/exec/streaming_fragment.cpp:103-112`).
* **Carve plan placement**: touches the C2a/C2b layer (park/relay + dispatch) and the C6b remote half
  (staged leases), so as an upstream PR it lands after C6b; against the `perf/profile-sf1000` line it is a
  fork PR now. The carve plan's known limitation "a parked Fragment pinned for the process lifetime when a
  receiver never arrives (no GC in C2a; only `drop_parked` on failure)" (`i-want-to-create-golden-crane.md`
  ~:531) is closed by this PR's cancel path.
* **`SIRIUS_CN_ASYNC_SENDER_DISPATCH`**: unchanged default; the design is exercised in both modes.

## 9. What this design deliberately does not do

* No periodic sweep or timer (plan item 2e): the FE's per-instance end-of-query cancels already give a
  bounded, event-driven sweep; a timer would need a knob and a doc row.
* No `query_id` on the wire frame or on `ExchangeKey`: per-instance retirement matches the FE's per-instance
  cancels and the exchange's existing keying.
* No preemption of a fragment already executing inside the engine (`Fragment::run` is synchronous; the
  engine's single-flight lifecycle, `sirius_context.cpp:1555-1596`, is B1's problem, not this fix's).
* No eviction of drained `ResultStore` entries or cached descriptor tables (`result_store.rs:250-254`,
  `compute_node_service.rs:196-197`); pre-existing TODO, separate PR.
* No forwarding of a sender failure to the receiver's node (the reason async dispatch is off by default).

## Appendix: code excerpts relied on

`compute_node_service.rs:864-911` (the only failure attribution today):

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
                ...
            }
            Vec::new()
        }
    }
}
```

`compute_node_service.rs:1358-1362` (translation before the slots are taken out of `ready`):

```rust
let dump_seq = Self::dump_fragment(&ready.params);
let translated =
    self.translate_fragment_logged_with_inputs(&ready.params, &exchange_inputs, dump_seq)?;
let mut inputs: Vec<(i32, Vec<SenderSlot>)> = Vec::new();
let mut remote_inputs: Vec<(i32, i32, Vec<StagedBatch>)> = Vec::new();
for input in ready.inputs { ... }
```

`local_exchange.rs:282-308` (the only removal path):

```rust
let receiver = state.receivers.remove(&fragment_instance_id).expect("receiver checked above");
...
let mut senders = state.sources.remove(&key).unwrap_or_default();
...
state.remote_seq.retain(|(key, _), _| key.fragment_instance_id != fragment_instance_id);
```

`result_store.rs:87-99`:

```rust
struct StoreState {
    fragments: HashMap<FragmentInstanceId, FragmentState>,
    query_results: HashMap<FragmentInstanceId, Vec<FragmentInstanceId>>,
    /// First failure recorded per query. A result fragment that reserves *after* the failure
    /// landed fails immediately instead of waiting on senders that will never deliver.
    query_failures: HashMap<FragmentInstanceId, String>,
}
```

`engine.rs:462-478` (the lease sweep a refused run must still pass through):

```rust
if result.is_err() {
    for (_, _, batches) in &request.remote_inputs {
        for batch in batches {
            if batch.len == 0 || released.contains(&batch.offset) { continue; }
            if let Err(err) = context.staging_release(batch.offset) { warn!(...); }
        }
    }
}
```

`fragment_executor.rs:217-223` (the sibling the new method sits next to):

```rust
/// Drops the parked fragment under `slot`, releasing the GPU memory its batches hold. Called
/// after the drained output has been transmitted (or on a failed transmit, so a wedged
/// cross-node query does not pin its output for the process lifetime).
fn drop_parked(&self, slot: SenderSlot) -> Result<(), String> { ... }
```

FE cancel per instance, `FragmentInstanceExecState.java:402-406`:

```java
BackendServiceClient.getInstance().cancelPlanFragmentAsync(brpcAddress,
        jobSpec.getQueryId(), instanceId, cancelReason,
        jobSpec.isEnablePipeline(), errorMessage);
```

FE cancel reasons, `DefaultCoordinator.java:1027-1033` (end of query) and `:768-784` (failure):

```java
if (hasLimit && numReceivedRows >= numLimitRows) {
    cancelInternal(PPlanFragmentCancelReason.LIMIT_REACH);
} else {
    cancelInternal(PPlanFragmentCancelReason.QUERY_FINISHED);
}
...
default:
    cancelInternal(PPlanFragmentCancelReason.INTERNAL_ERROR);
```
