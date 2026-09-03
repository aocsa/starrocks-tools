# Fix 2, robust design: query-scoped teardown of parked output on the CN

Angle: the design a senior engineer ships for the long run. Correct under cancellation and
concurrent queries, observable, configurable where it must be, with the failure modes written
down. Source tree read at `/home/prestouser/aocsa/sirius-stacks-wt/perf` (branch
`perf/profile-sf1000`, 45dab3be); evidence at `scratchpad/perf/sf1000`. Every number is
**measured** (file named) or marked **inference**.

## 0. Summary

Today a query's parked sender output on a CN is released only by the receiver that consumes it.
When the query dies, three things go wrong (all measured on the 1-CN SF1000 sweep):

1. The engine thread's `Err` path wipes **every** parked output process-wide
   (`engine.rs:291-316`), which is collateral damage for any concurrent query and does not even
   help the failing one on 1 CN (see 4.1).
2. The dead query's not-yet-run senders keep running and park output nobody will consume
   (`dispatch_worker` has no notion of a dead query, `compute_node_service.rs:380-390`):
   0-36.64 GB leaked per failure, 0.39-6.42 s of engine-thread time stolen from the next query.
3. `cancel_plan_fragment` touches only the result store (`compute_node_service.rs:449-476`), and
   a failure that never enters the engine (translation, q16) drops nothing.

The design replaces the process-wide wipe with **one query-scoped teardown**,
`ServiceCore::terminate_query(query_id, cause)`, reachable from three triggers (a fragment
failure, an FE cancel, a TTL sweep) and acting on four subsystems (the engine's parked
registry, the dispatch path, the exchange rendezvous, the result store). Teardown is idempotent,
tolerates the transport's late `drop_parked`, refuses late `Run`s of the dead query at two gates,
logs one structured report per teardown, and has two knobs. It changes nothing on the hot path of
a healthy query beyond a hash lookup per fragment run.

Predicted effect on the SF1000 1-CN sweep: the six post-failure leaks (4.69 / 8.83 / 31.56 /
+6.60 / 4.00 / 36.64 GB) become 0; the ~11.9 s of leftover-sender time (q05->q06 0.72,
q08->q09 1.04, q09->q10 0.39, q17->q18 6.42, q21->q22 3.35 s) disappears; q22 cold drops from
4110 ms to about 1.0-1.2 s; the 15 passing queries are unchanged (their QUERY_FINISHED cancels
tear down an empty registry).

## 1. Measured facts this design rests on

| fact | evidence |
|---|---|
| Six 1-CN failures (q05 q08 q09 q17 q18 q21) each end in `discarding every parked sender output on this CN after a fragment failure slots=N` (N = 2, 3, 7, 6, 5, 2) | `cn1/cluster.log` lines 6050, 9470, 11962, 17711, 18482, 21344 (ANSI stripped) |
| After each failure the dead query's remaining senders start and finish on the engine thread: q05 5773/5777/5776/5775 at 13:05:24.632-25.355 (the first 0.25 ms **before** the FE's first cancel ack at .632266); q08 7cb2..7cad 13:08:29.792-30.680; q09 947..944 13:12:22.333-22.721; q17 aca2 (60 ms) + aca1 (6343 ms) 13:14:56.819-13:15:03.233; q21 2acc (179 ms) + 2acb (`inputs=1`, 3153 ms) 13:18:07.604-10.954; q18 none | `cn1/cluster.log` (same windows; excerpts in section 9); `agent-notes/refute-oom5/notes.md` |
| Pool bytes left behind at the next query's start: q05 -> 4.69 GB, q08 -> 8.83, q09 -> 31.56 (through q10..q16, 7 queries), q16 -> +6.60 (38.16 at q17's start), q17 -> 4.00, q18 -> 0, q21 -> 36.64 (q22 warm runs) | `agent-notes/refute-oom5/headroom.txt` `base@start`; `pool-trace.txt` |
| q22 cold 4110 ms vs warm 971/915: q21's leftovers held the engine thread 13:18:07.604-10.954, q22's own fragments ran 13:18:10.960-11.715 (0.755 s) | `cn1/runs/runs.csv` lines 51-53; `cn1/cluster.log` 21467-21744 |
| q16 failed at **translation** of a receiver (`malformed plan: two-phase aggregation ... multi_distinct_count`); its two senders (partsupp 6.6 GB, supplier) had already parked and were never dropped until q17's wipe 104 s later | `cn1/cluster.log` 16593; `headroom.txt` q16.r0 6.60; `notes.md` |
| The FE sends one `cancel_plan_fragment` per deployed instance with `query_id` set; on failure `cancel_reason=3` (INTERNAL_ERROR) with `error_message` = the CN's own error; on success `cancel_reason=5` (QUERY_FINISHED); on LIMIT `1`. q05: 11 cancels at 13:05:24.632-.633, 5 ms after the `fragment run failed` line | `cn1/cluster.log` 6073-6095; `cn1-cnlog.txt` `cancels=` column; FE source in section 2.5 |
| The reservation gate blocked 47-178 ms per run in q10/q12/q14 at 1 CN while 31.6 GB of leaked output sat in the pool; 0 at 4 CNs and standalone | report C3 / BJ-6 correction (`agent-notes/refute-bj6/slow_reserving.py`) |
| q07's own boundary-sampled peak is 79.17 GB; 79.17 + 31.56 = 110.7 > 107.37 GB cap | `headroom.txt` q07 rows; **inference** that q07 would have failed between q09 and q17 |
| No pass/fail in the campaign was decided by the leak; the leak is a hygiene, order-independence and multi-query-correctness problem, not the cause of the six failures | report B4 objection; `notes.md` "Causal claim ... REFUTED" |

## 2. Today's mechanism (code, with the lines the design changes)

### 2.1 Engine thread: park once, release per destination, wipe everything on Err

`experimental/starrocks/src/engine.rs`

```rust
41  /// A sender fragment's parked output, shared by its destinations: stream i belongs to
42  /// destination i. `outstanding` counts destinations that have not yet released their stream
44  struct ParkedOutput<'ctx> {
45      fragment: sirius::Fragment<'ctx>,
46      outstanding: usize,
47  }
...
267     let mut parked: HashMap<u64, ParkedOutput<'_>> = HashMap::new();
268     let mut parked_slots: HashMap<SenderSlot, (u64, u64)> = HashMap::new();
269     let mut next_park_id: u64 = 0;
...
275     let mut poisoned: HashMap<SenderSlot, String> = HashMap::new();
...
280     while let Ok(request) = requests.recv() {
281         match request {
282             EngineRequest::Run(request) => {
283                 let result = run_fragment(&context, &mut parked, &mut parked_slots, &poisoned, &mut next_park_id, &request);
291                 if let Err(err) = &result {
297                     // The wipe is process-wide, so it also destroys OTHER in-flight fragments'
298                     // parked output. Record why, and say so out loud: ...
301                     if !parked_slots.is_empty() {
302                         warn!(slots = parked_slots.len(), error = %err,
305                             "discarding every parked sender output on this CN after a fragment failure");
307                     }
310                     poisoned.clear();
311                     for slot in parked_slots.keys() { poisoned.insert(slot.clone(), err.clone()); }
314                     parked.clear();
315                     parked_slots.clear();
316                 }
317                 let _ = request.respond.send(result);
```

Parking (`engine.rs:697-717`) keys nothing by query even though the request carries the query
id (`request.label.query_id`, `fragment_executor.rs:58-63`):

```rust
697     if !request.outputs.is_empty() {
700         let park_id = *next_park_id;
702         for (stream, slot) in request.outputs.iter().enumerate() {
708             parked_slots.insert(slot.clone(), (park_id, stream as u64));
709         }
710         parked.insert(park_id, ParkedOutput { fragment, outstanding: request.outputs.len() });
717         return Ok(None);
```

Release is exactly-once per slot and loud on a second release (`engine.rs:405-425`); the
transport relies on that contract (`nixl_transport.rs:790 self.executor.drop_parked(spec.slot)?`,
`:412` on a failed transmit; `compute_node_service.rs:110-117` documents it). The engine thread is
the only place a `sirius::Fragment<'ctx>` may live (`engine.rs:1-7`; `rust/crates/sirius/src/lib.rs:244-248`
`UniquePtr<sirius_sys::Fragment>` + `PhantomData<&'ctx SiriusContext>`), so any query-scoped drop
must execute there. Dropping a `Fragment` frees its output repositories
(`src/sirius_ffi.cpp:566-568`, `src/exec/streaming_fragment.cpp:103-113` "Repositories escape
data_repository_manager_ cleanup so sender output outlives this fragment").

### 2.2 Dispatch: nothing knows a query is dead

`compute_node_service.rs`

```rust
380 fn dispatch_worker(core: Arc<ServiceCore>, inbox: mpsc::Receiver<ReadyFragment>) {
381     while let Ok(ready) = inbox.recv() {
385         let mut queue = vec![ready];
386         while let Some(ready) = queue.pop() {
387             queue.extend(core.run_ready_fragment(ready));
```

```rust
864     fn run_ready_fragment(&self, ready: ReadyFragment) -> Vec<ReadyFragment> {
865         let id = Self::fragment_instance_id(&ready.params);
866         let query_id = Self::query_id(&ready.params);
870         match self.execute_ready_fragment(ready).and_then(FragmentOutcome::join_into_ready) {
874             Ok(next) => next,
875             Err(error) => {
876                 match (id, query_id) {
877                     (Some(id), Some(query_id)) => {
896                         self.results.fail_query(query_id, id, error);
```

With `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` (the campaign setting, `capture-cn.sh:12`) every sender
of a query is queued in this worker's inbox while the current one runs
(`try_dispatch_sender`, `:309-332`); after a failure the worker simply pops the next one. With the
default (off), each sender runs inside its own `exec_plan_fragment` RPC on a `spawn_blocking`
thread (`:397-421`), and several such threads queue in the engine's request channel
(`engine.rs:750-765 engine_call`). Either way the leftover senders run.

A receiver that fails **before** `run` (translation, `execute_ready_fragment` `:1361-1362`) has
its `LocalParked` slots in hand (`ready.inputs`, `:1365-1392`) and drops them on the floor: the
engine never sees an `Err`, so nothing is released (q16).

### 2.3 Cancel is result-store only

```rust
457     async fn cancel_plan_fragment(&self, request: PCancelPlanFragmentRequest, _attachment: Vec<u8>)
462         let id = FragmentInstanceId::from(&request.finst_id);
463         info!(fragment_instance_id = %id, query_id = ?request.query_id.as_ref().map(FragmentInstanceId::from),
466             cancel_reason = request.cancel_reason, error_message = ?request.error_message,
468             "acknowledging cancel_plan_fragment (best-effort: no engine-side abort yet)");
474         self.core.results.cancel(id, reason);
```

`ResultStore::cancel` flips one `Waiting` entry to `Failed` (`result_store.rs:205-212`);
`query_failures` (`:98`, `:163-200`) is recorded by `fail_query` but consulted nowhere on the
sender/dispatch path.

### 2.4 Rendezvous: removals only when a receiver becomes ready

`local_exchange.rs:248-313 take_ready` is the only place `receivers`, `sources` and `remote_seq`
shrink. A receiver whose senders never all arrive, or a sender whose receiver never registers,
stays forever; a `Remote` source's `StagedBatch`es hold staging-arena leases
(`SenderSource::Remote { batches }`, `:36-47`; leases released only after `push_packed`,
`engine.rs:671-679`).

### 2.5 What the FE sends

`fe/fe-core/.../rpc/BackendServiceClient.java:192-209`: every cancel carries `finst_id`,
`cancel_reason`, `query_id` (always set) and `error_message` (non-empty only for
INTERNAL_ERROR). `qe/DefaultCoordinator.java:1003-1035`: on `getNext` failure ->
`dealStatusToTryRetry` -> `cancelInternal(INTERNAL_ERROR)`; on eos with >1 instance ->
`cancelInternal(QUERY_FINISHED)` or `LIMIT_REACH` (`:1028-1034`); `:1161-1177
cancelRemoteFragmentsAsync` calls `cancelFragmentInstance` for **every** deployed execution
(`FragmentInstanceExecState.java:383-415`: only DEPLOYING/EXECUTING instances are sent; the CN
accepts every dispatch immediately, so all are EXECUTING). Phased schedule uses
`ExecutionDAG.cancelQueryContext` (`:620-646`) with a dummy `finst_id (0,0)` per worker.
Proto: `gensrc/proto/internal_service.proto:464-482` (`LIMIT_REACH=1, USER_CANCEL=2,
INTERNAL_ERROR=3, TIMEOUT=4, QUERY_FINISHED=5`). The FE does not wait for the cancel reply
(`cancelPlanFragmentAsync` returns a `Future` that `cancelFragmentInstance` discards), but a slow
reply on the shared jprotobuf channel misattributes later replies (docstring at
`compute_node_service.rs:449-454`), so the cancel handler must keep returning immediately.

Consequence for the design: on 1 CN, cancel arrives **after** the next leftover sender has
already started (0.25 ms late in q05), so Err-time bookkeeping on the CN is mandatory and cancel
is the second line; on N CNs, cancel is the only signal a *sender-side* CN gets that a query
failing on another CN is dead.

## 3. The design

### 3.1 One entry point, three triggers, four subsystems

```
trigger                     ServiceCore::terminate_query(query_id, cause)      subsystem action
------------------------    -------------------------------------------      --------------------------------
run_ready_fragment Err  ->  1. lifecycle.mark(query_id, cause)          ->  dispatch gate: later Runs skipped
process_fragment Err        2. executor.terminate_query(query_id,cause) ->  engine: mark + DropQuery (async)
cancel_plan_fragment    ->  3. exchanges.remove_query(query_id)         ->  rendezvous purged; staged leases
   (INTERNAL_ERROR,            + remove_receiver_instance(finst_id)         released via staging_release
    TIMEOUT, USER_CANCEL)   4. results.fail_query / nothing              ->  FE-polled ids fail (or untouched)
cancel_plan_fragment    ->  same, with cause = Finished (release-only)
   (QUERY_FINISHED,
    LIMIT_REACH)
TTL sweep (engine)      ->  engine-local drop_query + warn; CN lifecycle learns it on next touch
```

`TerminationCause` is an enum, not a string: `Failed { fragment: FragmentInstanceId, error }`,
`Cancelled { reason: PPlanFragmentCancelReason, message: Option<String> }`,
`Finished { reason }` (QUERY_FINISHED / LIMIT_REACH), `Expired { idle: Duration }`. `Display`
renders the text that today's `missing_slot` (`engine.rs:369-377`) and the FE error carry.

### 3.2 Engine thread: `ParkedRegistry`, a terminated set, two gates

Extract the three maps at `engine.rs:267-275` into `experimental/starrocks/src/parked.rs`:

```rust
pub(crate) type QueryId = FragmentInstanceId;

/// Everything the engine thread knows about parked sender output. Generic over the fragment
/// handle so the bookkeeping is unit-tested without a GPU (`F = ()` or a drop counter).
pub(crate) struct ParkedRegistry<F> {
    parked: HashMap<u64, ParkedOutput<F>>,
    slots: HashMap<SenderSlot, (u64, u64)>,
    by_query: HashMap<Option<QueryId>, Vec<u64>>,           // park ids per query
    torn_down: HashMap<SenderSlot, TornDown>,               // replaces `poisoned`
    next_park_id: u64,
}
struct ParkedOutput<F> { fragment: F, outstanding: usize, query_id: Option<QueryId>, parked_at: Instant, last_touch: Instant, label: FragmentLabel }
pub(crate) struct TornDown { pub query_id: Option<QueryId>, pub cause: Arc<TerminationCause>, pub at: Instant }

impl<F> ParkedRegistry<F> {
    pub fn park(&mut self, query_id: Option<QueryId>, label: FragmentLabel, slots: &[SenderSlot], fragment: F) -> Result<(), String>; // duplicate-slot refusal as today (engine.rs:703-707)
    pub fn get_mut(&mut self, slot: &SenderSlot) -> Result<(&mut F, u64 /*stream*/), SlotError>;  // SlotError::TornDown(cause) | Missing
    pub fn release(&mut self, slot: &SenderSlot) -> Result<Release, String>; // Release::Freed | Release::Outstanding(n) | Release::AlreadyTornDown
    pub fn drop_query(&mut self, query_id: Option<QueryId>, cause: Arc<TerminationCause>) -> DropReport; // moves slots to torn_down
    pub fn expire(&mut self, ttl: Duration, now: Instant) -> Vec<(Option<QueryId>, DropReport)>;
    pub fn inventory(&self) -> Inventory; // queries, slots, batches (via a caller-supplied fn on F)
    pub fn forget_torn_down_older_than(&mut self, retain: Duration, now: Instant);
}
pub(crate) struct DropReport { pub slots: usize, pub fragments: usize, pub batches: Option<usize>, pub rows: Option<u64>, pub oldest_parked_for: Option<Duration> }
```

Semantics that differ from today, each deliberate:

- **`release` on a torn-down slot returns `Ok(Release::AlreadyTornDown)`** and removes the
  `torn_down` entry, so the transport's post-eos `drop_parked` (`nixl_transport.rs:790`) after a
  QUERY_FINISHED teardown, or its failure-path `drop_parked` (`:412`) after an INTERNAL_ERROR
  teardown, is not an error. A second release of a *live* slot stays a loud error (the
  exactly-once contract the transport tests pin, `compute_node_service.rs:2703-2757`).
- **`get_mut` (export / relay) on a torn-down slot returns `SlotError::TornDown(cause)`**,
  rendered as today's "sender output for {slot} was discarded when ... : {cause}" text so the
  q08-for-weeks diagnosis lesson (`engine.rs:270-275`) is kept, now naming the query and trigger.
- `torn_down` is bounded by time (`forget_torn_down_older_than(retain)`, default 600 s, run from
  the sweep) instead of "replace on next wipe".

The engine thread (`engine_thread`, `engine.rs:242-363`) gets:

```rust
let terminated: Arc<Mutex<HashMap<QueryId, Arc<TerminationCause>>>>  // shared with SiriusEngine
...
EngineRequest::Run(request) => {
    // GATE 1 (dequeue): a Run queued behind the failure is refused before any GPU work.
    if let Some(cause) = request.label.query_id.and_then(|q| terminated.lock().get(&q).cloned()) {
        let _ = request.respond.send(Err(format!("fragment {} of terminated query {}: {cause}", ...)));
        continue;
    }
    let result = run_fragment(&context, &mut registry, &terminated, &request);
    if let Err(err) = &result {
        // Err-time marking on the engine thread itself (the CN's terminate arrives later).
        if let Some(q) = request.label.query_id { terminated.lock().insert(q, Arc::new(TerminationCause::Failed{..})); }
        let report = registry.drop_query(request.label.query_id, cause.clone());
        warn!(query_id, trigger = "engine_err", slots = report.slots, batches = ?report.batches, rows = ?report.rows, error = %err,
              "dropped the failed query's parked sender output");
    }
    let _ = request.respond.send(result);
}
EngineRequest::DropQuery { query_id, cause } => {           // posted by SiriusEngine::terminate_query; no respond channel
    let report = registry.drop_query(Some(query_id), cause.clone());
    if report.slots > 0 { warn!(query_id, trigger = %cause.trigger(), slots, batches, rows, "dropped a terminated query's parked sender output"); }
    else { debug!(query_id, "query teardown found nothing parked"); }
}
EngineRequest::Sweep => { for (q, report) in registry.expire(ttl, now) { warn!(...) } ; registry.forget_torn_down_older_than(retain, now); terminated.retain(|_, c| c.at.elapsed() < retain); info!(inventory) }
```

`run_fragment_inner` (`engine.rs:697-717`) gets **GATE 2 (park)**: right before `registry.park`,
re-check `terminated`; if the query was terminated while this fragment ran (user cancel mid-run,
or a sibling failed on another CN), drop the fragment instead of parking and return
`Err("... terminated while running: {cause}")`, so the CN never calls `push_sender` for output
that no longer exists. The relay/`release_slot` at `:625-639` and `export_next` at `:380-403`
switch to `registry.get_mut` / `registry.release`. The lease sweep at `:462-479` is unchanged.

`SiriusEngine` (`engine.rs:123-139`) holds the `Arc` of `terminated` and implements the new trait
method:

```rust
fn terminate_query(&self, query_id: QueryId, cause: Arc<TerminationCause>) -> Result<(), String> {
    self.terminated.lock().insert(query_id, cause.clone());               // immediate: gates see it now
    self.send_nowait(EngineRequest::DropQuery { query_id, cause })          // no respond: never blocks behind a running fragment
}
```

Why not wait for the report: the engine thread may be inside a 100 s fragment of *this* query
(the engine has no abort; B3), and the cancel RPC must reply immediately (2.5). The report lands
in the log; callers that want it synchronously (tests) use `EngineRequest::Inventory`.

Why a second map instead of sharing `ServiceCore`'s: `FragmentExecutor` is the seam the CN
tests substitute (`StubExecutor`, `CountingExecutor`, `RecordingExecutor`,
`compute_node_service.rs:1810-1820, 2913-2932`); keeping the executor self-contained preserves that.
Both maps are fed from the single `ServiceCore::terminate_query` and expire on the same retention.

### 3.3 `FragmentExecutor` trait (`fragment_executor.rs:178-241`)

```rust
/// Marks `query_id` dead for this executor: every parked output it owns is dropped as soon as
/// the engine thread is free, every later `run` for it is refused, and a `drop_parked` for one
/// of its slots is a no-op. Non-blocking. Idempotent.
fn terminate_query(&self, query_id: FragmentInstanceId, cause: Arc<TerminationCause>) -> Result<(), String> { let _ = (query_id, cause); Ok(()) }
/// Test/diagnostic: what is parked right now (engine round-trip; may wait behind a run).
fn parked_inventory(&self) -> Result<Inventory, String> { Ok(Inventory::default()) }
```

`StubExecutor` keeps the defaults. A test `RecordingParkExecutor` records `run(outputs)` as parked
slots, `drop_parked`, `terminate_query`, and can be told to fail a given fragment.

### 3.4 `ServiceCore::terminate_query` and the lifecycle map

`ServiceCore` (`compute_node_service.rs:186-211`) gains `lifecycle: QueryLifecycle`:

```rust
struct QueryLifecycle { inner: Mutex<HashMap<QueryId, Terminated>> }   // Terminated { cause: Arc<TerminationCause>, at: Instant, skipped_runs: u32 }
impl QueryLifecycle {
    fn mark(&self, q, cause) -> bool;            // false if already marked (first cause wins, like ResultStore::fail_query)
    fn check(&self, q) -> Option<Arc<TerminationCause>>;   // bumps skipped_runs
    fn prune(&self, retain: Duration);
}
```

```rust
fn terminate_query(&self, query_id: QueryId, cause: Arc<TerminationCause>, finst_ids: &[FragmentInstanceId]) {
    let first = self.lifecycle.mark(query_id, cause.clone());
    if let Err(err) = self.executor.terminate_query(query_id, cause.clone()) { error!(...) }   // engine is authoritative for GPU bytes
    let purged = self.exchanges.remove_query(query_id);                                          // receivers registered for q + their sources
    let purged_instances: Vec<_> = finst_ids.iter().flat_map(|id| self.exchanges.remove_receiver_instance(*id)).collect();
    for source in purged.sources.into_iter().chain(purged_instances) {
        match source {
            SenderSource::LocalParked { slot, .. } => { /* engine drop_query covers it; nothing to do */ }
            SenderSource::Remote { batches, .. } => for b in batches.iter().filter(|b| b.len > 0) {
                if let Err(err) = self.executor.staging_release(b.offset) { warn!(offset = b.offset, %err, "failed to release a staged lease of a terminated query") }
            }
        }
    }
    match &*cause {
        TerminationCause::Failed { fragment, error } => self.results.fail_query(query_id, *fragment, error.clone()),
        TerminationCause::Cancelled { message, .. } => self.results.fail_query_all(query_id, cause.to_string()),   // new: fail every reserved result id, record query_failures
        TerminationCause::Finished { .. } | TerminationCause::Expired { .. } => {}                                // release-only
    }
    if first { info!(query_id, trigger = %cause.trigger(), receivers = purged.receivers, remote_sources = .., staged_leases = .., "query teardown") }
}
```

Call sites:

1. `run_ready_fragment` `Err` arm (`:875-909`): replace `self.results.fail_query(...)` with
   `self.terminate_query(query_id, Failed{fragment: id, error}, &[])`. Covers engine failures
   (also already marked by the engine), translation failures with slots in hand (q16 -- the
   engine's `drop_query` releases them), remote drain failures (`join_into_ready`), rendezvous
   errors.
2. `exec_single_attachment` / `translate_batch_attachment` `Err` (the inline RPC path,
   `:705-720, 1624+`): when `process_fragment` fails for a fragment that carries a query id,
   call `terminate_query` before returning the error to the FE. Any RPC error fails the query on
   the FE (`FragmentInstanceExecState.waitForDeploymentCompletion`, seen at `cluster.log` 6186),
   so marking is never premature. Survey mode (`SIRIUS_CN_TRANSLATE_ONLY`) returns Ok and is
   unaffected.
3. `cancel_plan_fragment` (`:457-482`): see 3.6.

### 3.5 Dispatch gates on the CN side

- `run_ready_fragment` start: `if let Some(cause) = self.lifecycle.check(query_id) { info!(query_id, fragment_instance_id, %cause, "skipping fragment of a terminated query"); return Vec::new(); }`.
  This is what removes the leftover senders in async mode (they sit in the worker's inbox).
- `run_labeled` (`:1540-1581`), just before `self.executor.run(run)`: the same check, so a
  sender running inline in its RPC (default mode) is refused with
  `Err("fragment of terminated query ...")` instead of touching the engine. RPC threads already
  blocked in `engine_call` are caught by engine GATE 1.
- `process_fragment` for a **new** fragment of a terminated query (the FE's deploy of a late
  level racing its own cancel): refuse with the cause. The FE ignores the error (its
  `queryStatus` is already non-OK, `DefaultCoordinator.java:1056-1058`).

### 3.6 Cancel semantics by reason

| `cancel_reason` | FE meaning | CN action |
|---|---|---|
| 3 INTERNAL_ERROR, 4 TIMEOUT, 2 USER_CANCEL | query is dead; `error_message` set for 3 | `terminate_query(q, Cancelled{reason, message}, &[finst_id])`; result store: `fail_query_all` with the FE's message (keeps today's "cancelled by the FE: {message}" text that the test at `:3414-3460` pins) |
| 5 QUERY_FINISHED, 1 LIMIT_REACH | FE has all rows | `terminate_query(q, Finished{reason}, &[finst_id])`: release-only. Result store untouched (a `Pending` entry the FE has not drained must not be failed). Optional, separate commit: evict `Drained` entries and the query's `descriptor_tables` cache entry (`:985`; result_store.rs TODO `:250-254`) |
| none / unknown | old FE | treat as INTERNAL_ERROR without a message |
| `finst_id == (0,0)` | phased-schedule query-level cancel (`ExecutionDAG.cancelQueryContext`) | same path keyed by `query_id` only |
| `query_id` absent | pre-field FE | `results.cancel(finst_id)` + `exchanges.remove_receiver_instance(finst_id)` only (today's behaviour plus the purge) |

The handler still returns OK immediately: everything it calls is a mutex insert, a channel send,
or a rendezvous map operation; `staging_release` is the arena's own mutex (`engine.rs:793-811`).
Knob `SIRIUS_CN_TEARDOWN_ON_CANCEL=0` reduces cancel to today's result-store mark (escape hatch
if a StarRocks version ever sends cancels for live queries).

### 3.7 Rendezvous: `LocalExchange::remove_query` / `remove_receiver_instance`

`local_exchange.rs`: `PendingReceiver` gains `query_id: Option<QueryId>` (from
`params.params.query_id`, computed by the caller and passed to `register_receiver`);
`push_sender` gains a `query_id` argument (the sender's own, available at
`compute_node_service.rs:1193-1203`) recorded in `receiver_query: HashMap<FragmentInstanceId, QueryId>`.
Remote frames carry no query id on the wire (`internal_service.proto:812-827`), and none is
needed: the FE deploys receiver-first and waits for the level's replies before deploying senders
(`Deployer`, report P4), so the receiver is registered before its first frame; and every cancel
names the instance, so `remove_receiver_instance(finst_id)` purges any orphan `sources` entry
without a query mapping. `take_ready` keeps its removals; `remote_seq` is purged alongside.

```rust
pub(crate) struct PurgedRendezvous { pub receivers: usize, pub sources: Vec<SenderSource> }
pub(crate) fn remove_query(&self, query_id: QueryId) -> PurgedRendezvous;
pub(crate) fn remove_receiver_instance(&self, id: FragmentInstanceId) -> Vec<SenderSource>;
```

### 3.8 Sweep: the safety net for a silent FE

A `std::thread` timer posts `EngineRequest::Sweep` every `min(ttl/4, 60 s)`; the engine thread
drops parked output whose query has had no touch (park, relay, export, frame) for
`SIRIUS_CN_PARKED_TTL_SECS` (default 1800; 0 disables) and warns once at `ttl/2`. The TTL must
exceed the FE's `query_timeout` (300 s default) by a wide margin because a receiver legitimately
waits for the slowest sender of its query. The sweep is the only path that frees memory when the
FE dies without cancelling, and it also prunes `torn_down`/`terminated` retention
(`SIRIUS_CN_TERMINATED_RETAIN_SECS`, default 600, bounded so a late deploy of a dead query is still
refused). Knobs go through `tunable.rs` (`Knob` with name/default/min/max, reject-not-clamp,
logged at bring-up; `tunable.rs:1-60`).

### 3.9 Observability

- One `query teardown` line per (query, trigger) at INFO when nothing was parked, WARN when
  something was: `query_id trigger=engine_err|cn_err|cancel:<reason>|finished|sweep slots
  fragments batches rows oldest_parked_ms receivers_purged staged_leases_released skipped_runs`.
  Rows/batches come from `Fragment::output_row_count` / `output_batch_count`
  (`rust/crates/sirius/src/lib.rs:375-386`) with `.ok()` (a spilled batch throws,
  `src/sirius_ffi.cpp:1062-1066`, same tolerance as `engine.rs:559-566`).
- `skipping fragment of a terminated query` at INFO with the cause: this is the line the SF1000
  acceptance test greps for absence of `fragment run started` after `fragment run failed`.
- The engine-thread `fragment` span (`engine.rs:440-451`) records `parked_queries` and
  `parked_slots` at entry, so every run's close event carries the standing inventory; the sweep
  logs the inventory at INFO. Bytes stay where they are measured today: the engine's
  `[gpu_pool] QueryBegin/QueryEnd allocated=` lines (`src/sirius_context.cpp:271`).
- Optional (engine `src/` follow-up, not required): `Fragment::output_bytes(stream)` so the
  teardown line can carry bytes.

## 4. Behaviour under the measured cases

### 4.1 The 1-CN SF1000 sweep, failure by failure

The process-wide wipe was **not** the leak on 1 CN: at every wipe only the failing query's own
slots were parked (one query at a time), so today's `parked.clear()` and the new `drop_query`
free the same bytes. What changes is what happens next:

| transition | today (measured) | with the design |
|---|---|---|
| q05 -> q06 | 5773/5777/5776/5775 run 13:05:24.632-25.355 (0.72 s), 5775 relays streams 1+4 and parks 4.69 GB that survives q06/q07 | engine `Err` at 13:05:24.626 marks q05 terminated on the engine thread; `run_ready_fragment` marks it on the CN and the worker's next pop (5773) is skipped with `skipping fragment of a terminated query`; 5777/5776/5775 likewise; the 11 cancels at .632 find q05 already torn down (idempotent). q06 starts at once; pool at q06 QueryBegin = 0.00 GB |
| q08 -> q09 | 6 leftovers 13:08:29.792-30.680 (1.04 s), 8.83 GB into q09 | skipped; 0 GB; q09's lineitem sender starts with a clean pool |
| q09 -> q10..q16 | 4 leftovers (0.39 s), 31.56 GB parked through 7 queries (21 runs); 47-178 ms reservation-gate blocks in q10/q12/q14 | skipped; 0 GB; the gate blocks in C3 lose their cause (**inference**: up to 0.18 s per q10/q12/q14 warm run recovered) |
| q16 (translation failure of receiver ff85) | senders ff86/ff87 parked 6.6 GB never dropped (no engine `Err`); +6.6 GB into q17 | `run_ready_fragment` `Err` -> `terminate_query(q16, Failed)` -> engine `DropQuery` frees ff86/ff87 within the same millisecond (engine idle) |
| q17 -> q18 | aca2 + aca1 (6.34 s scan) run after the failure, 4.0 GB into q18 | skipped: q18's timer starts 13:14:56.823 and its first fragment can start at ~.83 instead of 13:15:03.243 (6.42 s earlier) |
| q21 -> q22 | 2acc + 2acb (3.15 s, parks 36.64 GB) run after the failure; q22 cold 4110 ms, warm runs carry 36.64 GB | skipped; q22 cold = its own 0.755 s of fragments + FE head (~0.2 s, P4) + first-touch: predicted 1.0-1.2 s; warm 943 ms unchanged |

Note the 0.25 ms race in q05: sender 5773 started **before** the first cancel ack. The design
does not depend on cancel for this; the CN marks the query at `run_ready_fragment`'s `Err`, which
executes on the dispatch worker *before* it pops 5773. In default (inline) mode the same senders
are RPC threads blocked in `engine_call`; engine GATE 1 refuses them because the engine thread
marked the query at its own `Err` before responding.

### 4.2 The 15 passing queries (1 CN and 4 CN)

Per fragment run: one `HashMap` lookup in `run_ready_fragment`/`run_labeled`, one in engine
GATE 1, one in GATE 2, one `by_query` insert on park. Per query: 2-13 QUERY_FINISHED cancels
(`cn1-cnlog.txt cancels=`), each a `terminate_query(Finished)` that finds an empty registry
(every parked output was consumed before the result fragment could run, by construction of the
receiver-first rendezvous, B1) and logs at INFO. On 4 CNs a QUERY_FINISHED cancel can arrive on a
sender CN while the transport thread has transmitted eos but not yet called `drop_parked`
(`nixl_transport.rs:774-790`); `release` returns `AlreadyTornDown` and the drain reports success.
No plan, memory, or ordering change for these queries; warm medians should be within noise
(compare.py set unchanged).

### 4.3 Cases the campaign did not exercise

- **User `KILL` mid-run** (USER_CANCEL while the engine is inside the query's fragment): cancel
  marks + posts `DropQuery`; the running fragment cannot be aborted (engine limitation, B3), but
  GATE 2 drops its output instead of parking, the CN never registers it, and every other fragment
  of the query is skipped. Memory returns as soon as the fragment ends.
- **Failure on another CN**: that CN's `run_ready_fragment` `Err` -> `fail_query` there; FE
  cancels every instance everywhere with INTERNAL_ERROR; this CN's parked output for the query
  (waiting on a drain, or already drained but the peer receiver died) is dropped by the cancel;
  an in-flight drain's next `export` fails with the teardown cause; staged remote frames for a
  receiver of the dead query on this CN are purged and their leases released (today they pin the
  arena forever, B6-adjacent).
- **FE dies, no cancel**: the sweep frees the output after `SIRIUS_CN_PARKED_TTL_SECS` and warns
  at half that; `parked_inventory` shows what is waiting.
- **Late deploy of a dead query** (FE level in flight when the failure happened): refused at
  `process_fragment` while the retention entry lives (600 s); after retention, it would register
  a receiver that never completes and be swept by the TTL.

## 5. Failure modes of the design itself

| # | scenario | what happens | why acceptable |
|---|---|---|---|
| F1 | QUERY_FINISHED cancel races the transport's post-eos `drop_parked` | `release` -> `AlreadyTornDown` (Ok) | test 7.5; without it a finished query's sender RPC would report an error |
| F2 | INTERNAL_ERROR cancel during an in-flight drain | next `export_packed_next` -> `Err(TornDown cause)`; drain fails; sender RPC / `fail_query` reports the teardown cause, not "no parked sender output" | the query is dead; the message names the culprit (the q08 lesson) |
| F3 | `terminate_query` for a query with a fragment mid-run on the engine | cannot abort; output dropped at GATE 2 when it ends | documented limitation; engine abort is fix-1/B3 territory |
| F4 | FE never sends cancel (crash) | TTL sweep frees after `SIRIUS_CN_PARKED_TTL_SECS` | default 1800 s >> FE query_timeout 300 s; knob |
| F5 | TTL too low for a legitimately long query (many-minute sender at SF10000) | its receiver fails with "sender output ... expired after N s" | loud, names the knob; warn at ttl/2 gives operators a signal first |
| F6 | Two failures of the same query (engine `Err` then CN `Err` then cancel) | `mark` keeps the first cause; later triggers log at DEBUG with `already_terminated=true` | idempotent by construction |
| F7 | Query id absent on the run (translate-only fixtures) | parked under `None`; only an `Err` of a `None` run drops the `None` bucket; never swept by query | preserves today's semantics for anonymous fragments |
| F8 | `DropQuery` posted while the engine is shutting down | `send_nowait` returns Err, logged; `Drop for SiriusEngine` drops the registry with the context (`engine.rs:359-361`) | nothing leaks past process exit |
| F9 | `staging_release` of a purged remote batch fails (arena gone) | warned, teardown continues | same tolerance as `engine.rs:470-476` |
| F10 | `terminated`/`torn_down`/`lifecycle` growth | bounded by retention (600 s) and by the sweep | one entry per query, pruned |
| F11 | A CN with async dispatch OFF and a sender RPC that failed at `route_destination` before running | `process_fragment` `Err` -> `terminate_query` | consistent: the FE fails the query on any RPC error |
| F12 | Knob `SIRIUS_CN_TEARDOWN_ON_CANCEL=0` | cancel reverts to `results.cancel`; Err-time and sweep paths still active | escape hatch only for the cancel contract |

## 6. Files to touch

| file | change |
|---|---|
| `experimental/starrocks/src/parked.rs` (new) | `ParkedRegistry<F>`, `ParkedOutput<F>`, `TornDown`, `DropReport`, `Inventory`, `TerminationCause`; pure Rust, unit-tested without a GPU |
| `experimental/starrocks/src/engine.rs` | `:41-47, 262-275` replace maps with `ParkedRegistry<sirius::Fragment<'ctx>>` + `terminated: Arc<Mutex<..>>`; `:280-318` GATE 1 and `drop_query` on `Err`; `:321-328` `export_next`/`release_slot` via the registry; new `EngineRequest::{DropQuery, Sweep, Inventory}`; `:697-717` GATE 2 before park; `:440-451` span fields; `:123-139, 748-830` `SiriusEngine::terminate_query`, `parked_inventory`, sweep timer thread; `:365-377` `missing_slot` text from `TornDown` |
| `experimental/starrocks/src/fragment_executor.rs` | `:178-241` trait: `terminate_query`, `parked_inventory` defaults; re-export `TerminationCause` |
| `experimental/starrocks/src/compute_node_service.rs` | `:186-211` `ServiceCore.lifecycle`; new `terminate_query`; `:380-390`/`:864-909` gate + `Err` arm; `:705-720, 1624+` inline `Err`; `:457-482` cancel by reason; `:1193-1203` `push_sender(query_id)`; `:1540-1581` `run_labeled` gate; `:932-946` `register_receiver(query_id)` |
| `experimental/starrocks/src/local_exchange.rs` | `PendingReceiver.query_id`, `receiver_query` map, `remove_query`, `remove_receiver_instance`; `push_sender`/`register_receiver` signatures |
| `experimental/starrocks/src/result_store.rs` | `fail_query_all(query_id, cause)` (cancel with INTERNAL_ERROR); optional `evict_query` |
| `experimental/starrocks/src/tunable.rs` | `SIRIUS_CN_PARKED_TTL_SECS` (1800, 0..86400), `SIRIUS_CN_TERMINATED_RETAIN_SECS` (600, 1..86400), `SIRIUS_CN_TEARDOWN_ON_CANCEL` (bool) |
| `experimental/starrocks/src/nixl_transport.rs` | no logic change; `:410-418` comment ("per-query GC is cut from Path B") becomes "per-query teardown makes this idempotent" |
| `experimental/starrocks/docs/TUNABLES.md`, `experimental/starrocks/DEMO.md` (`:14-20` parked-output paragraph) | document teardown, knobs, log lines |
| `scratchpad`/bench tooling: `cnlog_extract.py` | count `skipping fragment of a terminated query` and `query teardown` lines per run (acceptance grep) |

Nothing under `src/` (engine C++) or the FE changes. No proto change.

## 7. Tests

### 7.1 Unit, no GPU (`parked.rs`)

1. `park_then_release_each_destination_frees_on_last`: two slots, release both, `Freed` on the
   second; a third release is `Err` (exactly-once kept).
2. `drop_query_removes_only_that_querys_slots`: park q1 (2 slots) and q2 (3 slots); `drop_query(q1)`
   -> report slots=2; q2 slots still `get_mut`-able; `F` drop counter = 1 fragment.
3. `release_after_teardown_is_ok_and_forgets`: `drop_query(q1)`; `release(slot_of_q1)` ->
   `AlreadyTornDown`; second `release` -> `Err(Missing)`.
4. `get_mut_after_teardown_names_the_cause`: `SlotError::TornDown(cause)` renders
   "was discarded when query q1 was terminated (cancel: INTERNAL_ERROR): ...".
5. `drop_query_none_bucket_only_touches_anonymous_output`.
6. `expire_drops_only_idle_queries`: two queries, touch one, `expire(ttl)` drops the other.
7. `forget_torn_down_older_than_bounds_the_map`.

### 7.2 Component, stub executor (`compute_node_service.rs` tests, existing fixtures
`exec_params`, `fragment_params`, `data_stream_sink`, `local_destination`, `route`)

Add `RecordingParkExecutor { parked: Mutex<HashMap<SenderSlot, QueryId>>, dropped, terminated: Mutex<Vec<(QueryId, String)>>, fail_fragments: HashSet<FragmentInstanceId> }`.

1. `failure_terminates_only_that_query`: two queries interleaved (q1 receiver + 2 senders, q2
   receiver + 1 sender); q1's receiver fails; assert `terminated == [q1]`, q2's rows still fetch.
2. `queued_senders_of_a_failed_query_are_skipped` (async dispatch on): gated first sender fails
   after two more senders of the same query were queued; assert the executor saw exactly one
   `run` for that query and the log/lifecycle shows `skipped_runs == 2`; a later
   `exec_plan_fragment` for the same query is refused with the cause.
3. `inline_sender_of_a_failed_query_is_refused` (async off): same with RPC threads.
4. `translation_failure_terminates_and_drops_slots_in_hand`: sender parks, receiver has an
   untranslatable plan (reuse the unsupported-node fixture), assert `terminate_query(q)` was
   called and `fetch_data` reports the translation error (q16 shape).
5. `cancel_internal_error_tears_down_and_fails_result_polls`: receiver waiting; cancel with
   reason 3 + message; assert `terminated`, rendezvous empty, `fetch_data` error contains the
   message (keeps `cancel_plan_fragment_returns_ok_and_unblocks_a_waiting_result_poll`).
6. `cancel_query_finished_is_release_only`: result delivered, then cancel reason 5; the
   `Pending`/`Drained` entry still answers; `terminate_query` called with `Finished`.
7. `cancel_purges_staged_remote_frames_and_releases_their_leases`: `transmit_packed` frames for a
   receiver, then cancel; `RecordingExecutor.released` contains the offsets (`:2913-2932`).
8. `drop_parked_after_teardown_does_not_fail_the_drain`: fake transport answers the drain after
   a QUERY_FINISHED cancel; the sender RPC is OK (extends `drain_specs`, `:2613-2634`).
9. `cancel_for_unknown_instance_still_fabricates_nothing` (existing test, kept).
10. `local_exchange::remove_query_returns_sources_and_clears_seq`, `remove_receiver_instance_purges_orphans`.
11. `result_store::fail_query_all_marks_every_reserved_id_and_records_the_query_failure`.

### 7.3 GPU (feature `sirius-engine`, under `GPU_ENGINE_TEST_LOCK`, `engine.rs` tests)

1. `terminating_one_query_leaves_the_others_parked_output_relayable`: park the users fixture under
   q1 and q2 (`write_users_parquet`, `local_files_plan`, `stream_plan`, `:1129-1143, 874-983`);
   `terminate_query(q1)`; receiver for q2 returns 3 rows; receiver for q1 fails with the teardown
   text; `parked_inventory` shows 0 after both.
2. `run_of_a_terminated_query_is_refused_before_build`: terminate then `run` -> `Err` containing
   "terminated"; `engine_replays`-style check that no `[gpu_pool] QueryBegin` was logged is out of
   scope; assert via `parked_inventory` and the error text.
3. `drop_parked_after_terminate_is_ok_once_then_err`.

### 7.4 SF1000 acceptance (orchestrator-run, 1 CN, same order as the campaign)

From the plan table plus these greps over the new run's `cluster.log`/`engine-cn0.log`:

- After each `fragment run failed` of query Q: zero `fragment run started ... query_id=Q` lines
  later; at least one `skipping fragment of a terminated query ... query_id=Q`; exactly one
  `query teardown ... trigger=engine_err|cn_err` WARN for Q (plus INFO/DEBUG for the cancels).
- `[gpu_pool] QueryBegin allocated=` at the first fragment of q06, q09, q10, q17, q18, q22 is
  <= 0.01 GB (was 1.32-4.69 / 8.83 / 0.08->31.56 / 38.16 / 4.00 / 5.94->36.64).
- q22 cold <= 1.5 s (was 4110), q06 cold <= 2.0 s (was 2453); first-fragment start of q18 within
  0.5 s of its client start (was 6.42 s).
- q16: `query teardown` WARN with `slots=2` immediately after the translation error; q17's
  `QueryBegin allocated` = 0.
- The 15 passing queries: warm medians within noise of `results.md`; `compare-cn1.txt` set
  unchanged; `cancels=` per query unchanged; no `query teardown` WARN for a passing query.
- 4-CN smoke (15 queries): no drain reports "no parked sender output"; every QUERY_FINISHED
  teardown logs `slots=0`.

## 8. Predicted effect (numbers)

| metric (1 CN, SF1000, campaign order) | today (measured) | predicted | basis |
|---|---|---|---|
| bytes left in the pool after a failure | 4.69 / 8.83 / 31.56 / +6.60 / 4.00 / 0 / 36.64 GB | 0 in all seven | leftover senders never run; q16's parked inputs dropped at the CN `Err` |
| engine-thread time spent on dead queries' fragments | 0.72 + 1.04 + 0.39 + 6.42 + 3.35 = 11.9 s of the 910 s sweep | 0 | skipped at the dispatch gate |
| q22 cold | 4110 ms | ~1000-1200 ms (own fragments 755 ms + ~200 ms FE head + first-touch) | `cluster.log` 21617-21744 |
| q06 cold | 2453 ms | ~1750-1950 ms | 0.72 s removed; warm 1916 |
| q18 time-to-first-fragment | 6.42 s | < 0.3 s | aca1 skipped |
| reservation-gate blocks in q10/q12/q14 warm runs | 177 / 155 / 128-139 ms | 0-50 ms (**inference**; C3 ties them to the 31.6 GB leak) | 3 tasks x 20 GB + 31.6 GB leak reached the cap |
| q10..q16 and q22 warm timings | taken with 31.6 / 36.6 GB occupied (report 5.7) | valid on a clean pool | removes the re-run caveat |
| order dependence | q07 after q09 would need 110.7 GB > 107.37 (**inference**) | q07 passes in any order | headroom.txt |
| failing queries' wall | 54-232 s | unchanged by this fix (fix 1 owns it); the FE sees the same error 5 ms earlier because no leftover run precedes the result-store failure | |
| passing queries' wall | 1 CN parity 0.95-1.4 | unchanged (hash lookups; empty teardowns) | 4.2 |
| after fix 1 lands (failures die in ~3 s) | leftover runs would then be 25-68% of a failure's wall (q17: 3 s + 6.4 s) | 0 | ordering argument for landing fix 2 with or before fix 1 |

Multi-CN correctness effect (not measured in this campaign; the `poisoned` comment at
`engine.rs:270-275` records the symptom): a query on CN A no longer loses its parked output when
an unrelated query fails on CN A.

## 9. Code excerpts relied on (beyond section 2)

`compute_node_service.rs:1174-1206` (sender parks, then rendezvous):
```rust
1176        self.run_labeled("sender", FragmentRun { plan: &translated, inputs, remote_inputs, outputs: slots.clone(), broadcast, hash_keys, label: Self::fragment_label(params) })?;
1191        for (slot, route) in slots.iter().zip(&routes) {
1192            if matches!(route, DestinationRoute::Local) {
1193                let ready = self.exchanges.push_sender(ExchangeKey { fragment_instance_id: slot.fragment_instance_id, node_id: slot.node_id }, sender_id,
1199                    SenderSource::LocalParked { names: translated.output_names.clone(), slot: *slot })?;
```
`compute_node_service.rs:1361-1393` (receiver translates after its senders parked; `Err` here
leaves `ready.inputs` slots unreleased):
```rust
1361        let translated = self.translate_fragment_logged_with_inputs(&ready.params, &exchange_inputs, dump_seq)?;
1365        for input in ready.inputs { ... SenderSource::LocalParked { slot, .. } => slots.push(slot), ... }
1393        self.execute_fragment_with_inputs(&ready.params, translated, inputs, remote_inputs)
```
`nixl_transport.rs:786-790` (drop after eos) and `:407-419` (drop after a failed transmit):
```rust
790            self.executor.drop_parked(spec.slot)?;
...
409                    if result.is_err() {
410                        // Best-effort GPU cleanup: without it a failed transmit pins the parked
411                        // output for the process lifetime (per-query GC is cut from Path B).
412                        if let Err(drop_err) = state.executor.drop_parked(spec.slot) {
```
`local_exchange.rs:282-308` (the only removals today):
```rust
282        let receiver = state.receivers.remove(&fragment_instance_id).expect("receiver checked above");
295                let mut senders = state.sources.remove(&key).unwrap_or_default();
306        state.remote_seq.retain(|(key, _), _| key.fragment_instance_id != fragment_instance_id);
```
`result_store.rs:163-200` (`fail_query`: first failure wins; reserved result ids fail; recorded
in `query_failures` for late `reserve`), `:205-212` (`cancel`: one `Waiting` entry only).
`engine.rs:750-765` (`engine_call` blocks the caller for its answer -- why `terminate_query` must
not use it), `:793-811` (staging calls served off-thread -- why `staging_release` in the cancel
handler is safe).
`fe/.../FragmentInstanceExecState.java:390-406`, `DefaultCoordinator.java:1003-1035, 1085-1099,
1161-1177`, `BackendServiceClient.java:192-209` (section 2.5).
`cn1/cluster.log` (ANSI stripped) 6050-6052, 6072-6073, 6097, 6181, 6237, 6258, 6275, 6291,
6305 (q05 -> q06); 9470-9789 (q08 -> q09); 11962-12221 (q09 -> q10); 16593-16608 (q16);
17711-17999 (q17 -> q18); 21344-21617 (q21 -> q22).

## 10. Risks

1. **Cancel contract drift.** The design assumes StarRocks sends QUERY_FINISHED only after the FE
   has every row (`DefaultCoordinator.java:1022-1035`). It does, and the CN's single-batch model
   means all parked output is consumed by then; the knob `SIRIUS_CN_TEARDOWN_ON_CANCEL` is the
   escape hatch. Mitigation: test 7.2.6/7.2.8 and the 4-CN smoke assertion `slots=0` on finished
   teardowns.
2. **Refusing a fragment the FE still expects.** `process_fragment` refusal of a late deploy of a
   terminated query returns an RPC error to an FE that has already failed the query -- harmless.
   If retention were shorter than the FE's deploy window the late fragment would register and be
   swept by the TTL instead; 600 s is far above any deploy.
3. **GATE 2 discards work.** Output of a fragment that finished after its query was terminated is
   dropped rather than parked; that is the intent, but a mis-marked query (a bug in a trigger)
   would lose a live query's output loudly ("terminated while running"). First-cause-wins and
   the cause text make such a bug diagnosable in one log line.
4. **TTL misconfiguration** (F5) can fail a long-running legitimate query; the default is 6x the
   FE query timeout and the warn-at-half gives a signal first. Set to 0 to disable in bring-up
   experiments.
5. **Engine backlog.** `DropQuery` is FIFO behind whatever the engine thread is doing; for a
   cancel during a 100 s fragment of the same query, the release happens when that fragment ends
   (F3). No new blocking anywhere.
6. **Interaction with fix 4(b) (spillable parked repositories).** If parked repositories get
   registered with the downgrade executor, dropping a `Fragment` must unregister them; flag to
   that design. Row counting already tolerates a spilled batch (`.ok()`).
7. **Behavioural change for the inline sender path**: a queued sender of a dead query now returns
   an RPC error instead of running; the FE already treats the query as failed, and
   `dispatched_receiver_failure_surfaces_through_fetch_data`-style tests cover the surfaced text.

## 11. Effort and PR slicing

Stack of three PRs against the CN (fork PR per the carve plan; `experimental/starrocks` only):

1. **Registry + Err-time per-query drop** (`parked.rs`, `engine.rs`, `fragment_executor.rs`
   trait defaults, unit tests 7.1 + GPU tests 7.3): pure hygiene, no dispatch-contract change.
   ~1 day.
2. **Query lifecycle: terminate on Err and cancel, dispatch gates, rendezvous purge, idempotent
   release** (`compute_node_service.rs`, `local_exchange.rs`, `result_store.rs`, tests 7.2):
   touches the dispatch contract; this is the PR that fixes the measured leak. ~1.5 days.
3. **Sweep, retention, knobs, telemetry fields, docs** (`tunable.rs`, sweep thread, span fields,
   `TUNABLES.md`, `DEMO.md`, `cnlog_extract.py`): ~1 day.

SF1000 verification: one 1-CN sweep in campaign order (~16 min of GPU time with today's
54-232 s failures; ~4 min if fix 1 is already in) plus a 4-CN 15-query smoke. Total 3.5-4
engineer-days.

## 12. Dependencies and interactions with the other three fixes

- **Fix 1 (fail fast in the reschedule loop, engine `src/`)**: independent code. Ordering: land
  fix 2 first or together; with fix 1 alone the leftover-sender time becomes the dominant part of
  each failure's wall (q17: 3 s to fail, 6.4 s of leftovers). Fix 1's new error text lands in
  `TerminationCause::Failed { error }` and is what the teardown line and the FE message carry.
- **Fix 3 (FE cardinalities)**: no interaction; fewer failures means fewer teardowns.
- **Fix 4(a) (fragment fusion)**: fused fragments are ordinary runs; fewer exchanges, fewer parked
  outputs; no interaction. **Fix 4(b) (spillable parked repositories)**: `Fragment` drop must
  unregister from the downgrade registry (risk 6); `DropReport.rows` stays optional because
  `output_row_count` throws on a spilled batch until 4(b) adds host-side counts.
- No dependency on a proto or FE change; the FE already sends everything the design consumes.
