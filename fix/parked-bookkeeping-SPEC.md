# Fix 2 spec: per-query parked-output bookkeeping in the CN

Implementation spec for issue 2 of `~/.claude/plans/sf1000-top4-fixes-plan.md`. Written 2026-09-03 from
the three designs (`parked-bookkeeping-{surgical,robust,upstream}.md`) and the two judges' verdicts.
Every `file:line` below was re-read in `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be`;
the implementation worktree `/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping`
(branch `fix/parked-bookkeeping`) is at the same commit, so the anchors hold there. Paths without a
directory are under `experimental/starrocks/src/`. "Measured" means copied from a named evidence file
under the session scratchpad `perf/sf1000/`; everything else is inference and says so.

## 0. What was decided and why

**Base: the upstream design.** Judge 1 picked it outright; judge 2 picked the surgical design but made
two merges from upstream mandatory (the dead-query check inside `run_fragment`, and a CN-side gate in
`run_ready_fragment` that releases staged leases), which is the upstream mechanism. Both judges agree
on the same core: one query-scoped registry, a shared retired set that refuses already-queued runs in
both dispatch modes, the refusal placed so the remote-input lease sweep still runs, and a CN-side gate
that emits no `fragment run started` for a dead query.

**Merged in, as the judges asked:**
- from robust: `release` of a retired slot returns `Ok` exactly once (`AlreadyTornDown`), so the
  transport's post-eos `drop_parked` (`nixl_transport.rs:790`) and its failure-path `drop_parked`
  (`:412`) after a retire are not errors; structured retire log fields (`trigger`, `fragments`, `slots`,
  `still_parked`); an INFO `skipping fragment of a retired query` line as the acceptance grep target.
- from surgical: `RetiredQueries` capacity 1024 (every finished query now enters it, so 64 was too
  small); the unlabelled-failure GPU test; the cancel-reason matrix test; the acceptance arithmetic
  (`base@start`, `QueryBegin allocated` at q17 == q16, client-start-to-first-fragment gaps).
- implementation notes from judge 1: a `HashSet` twin for the exchange's retired FIFO so `is_retired` is
  O(1) per frame; both refusals release staged batches; QUERY_FINISHED retires parked state but never
  touches the `ResultStore`, guarded by a test that a repeat `fetch_data` still reports EOS; the 4-CN
  cancel path is verified by a fault-injection arm (q16 between two passing queries).

**Deliberately not merged:** the TTL sweep and its timer thread, the three knobs, the `Inventory`
request, `fail_query_all`, the three-PR split (all robust). The FE already cancels every multi-instance
query (measured `cancels=N` on all 45 passing runs, `cn1-cnlog.txt`), so the event-driven sweep exists.

**One correction to the upstream design's commit split:** `fail_fragment` (commit 1) calls
`executor.retire_query`, which for the engine sends the `RetireQuery` request. Upstream listed that
request under commit 2; it belongs in commit 1. Commit 2 is the cancel teardown only.

**Decisions left to the user** are in section 12. Where the spec had to pick, it says so inline.

## 1. Goal and non-goals

### Goal

When a StarRocks query dies on this CN (its fragment failed on the engine, failed before reaching the
engine, or the FE cancelled it), everything the CN holds for that query is released and no further
fragment of that query runs here, without touching any other query's state. Concretely, on the 1-CN
SF1000 sweep in campaign order:

- `[gpu_pool] GPU:0 QueryBegin ... allocated=` at the first fragment of every run returns to the
  pre-q05 baseline (a few KB). Measured today (`agent-notes/refute-oom5/headroom.txt`, `base@start`):
  q06.r0 1.32 GB, q06.r1/r2 and q07 4.69, q09 0.08, q10.r1..q16 31.56 (seven queries), q17 38.16,
  q22.r0 5.94, q22.r1/r2 36.64.
- Zero engine-thread time spent on a dead query's fragments after its failure. Measured today
  (`refute-oom5/notes.md`): q05->q06 0.72 s, q08->q09 1.04, q09->q10 0.39, q17->q18 6.42, q21->q22 3.35
  (11.9 s of the 910 s sweep); q22 cold 4110 ms vs 971/915 warm.
- A failure that never reaches the engine (q16: translation of a receiver, `cn1/cluster.log:16593`)
  drops the 6.60 GB its senders had parked instead of carrying it into q17.
- The 15 passing queries take an unchanged path while alive (one hash lookup per fragment) and keep
  their result set and warm medians.

This is hygiene and order independence, not a pass/fail change: B4 in the findings report established
that none of the six 1-CN OOM failures was decided by the leak (each failing sender exceeds the
107.37 GB cap on its own).

### Non-goals

- Aborting a fragment already inside `Fragment::run()`. The engine has no abort (single-flight
  lifecycle, B1/B3); the fix stops the *next* fragment and drops output *after* a run ends.
- Forwarding a sender failure to the receiver's node (the documented reason
  `SIRIUS_CN_ASYNC_SENDER_DISPATCH` is off by default, `compute_node_service.rs:284-288`).
- A periodic sweep, a TTL, or any new environment knob. Capacities are `const`.
- Eviction of drained `ResultStore` entries or the descriptor-table cache (pre-existing TODO at
  `result_store.rs:250-254`, `compute_node_service.rs:196-197`).
- Any change under `src/` (engine C++), the FE, `patches/`, or the wire protocol.

## 2. Today's mechanism (the defect, with anchors)

1. **Process-wide wipe on any engine error.** `engine.rs:44-47` (`ParkedOutput` has no owner);
   `:267-275` the three loose maps `parked`, `parked_slots`, `poisoned`; `:291-316` the `Err` arm clears
   all of them and poisons every slot with the failing query's error. The wipe is also the only place a
   dead query's parked output is ever released.
2. **Dead queries keep running.** `dispatch_worker` (`compute_node_service.rs:380-390`) pops the inbox
   FIFO with no liveness check; `run_ready_fragment` (`:864-911`) records the failure with
   `results.fail_query` (`:896`) but nothing on the dispatch or arrival path reads
   `query_failures` (`result_store.rs:96-98`; consulted only by `reserve`, `:127`). Measured: after q05's
   lineitem sender failed at 13:05:24.627 (`cluster.log:6051`), senders `5773` (started `:6072`, 5 ms
   later and 0.25 ms before the first FE cancel `:6073`), `5777`, `5776` and the receiver `5775`
   (`:6291-6305`, relayed 11 batches and parked 4.69 GB) ran to completion; q06 started at `:6329`.
3. **Cancel is a result-store stub.** `compute_node_service.rs:457-479` only calls `results.cancel(id)`
   (fails a `Waiting` entry, `result_store.rs:205-212`). No engine call, no rendezvous removal. The FE
   sends one cancel per deployed instance with `query_id` always set (`BackendServiceClient.java:192-212`,
   `FragmentInstanceExecState.java:383-415`), reason `INTERNAL_ERROR`/`TIMEOUT` on failure
   (`DefaultCoordinator.java:764-784`), `QUERY_FINISHED`/`LIMIT_REACH` after eos (`:1022-1035`), a
   dummy `finst_id (0,0)` for phased schedules (`ExecutionDAG.java:620-646`). Measured: 321 cancel RPCs
   in `cn1/cluster.log` (each logged twice), reasons 5/1/3 = 192/72/57; 11 for q05 (`:6073-6094`), 5 for
   q16 (`:16597-16605`), 9 for q21 (`:21350-21367`).
4. **A pre-run failure loses the slots and leases in hand.** `execute_ready_fragment`
   (`compute_node_service.rs:1325-1394`) translates at `:1360-1361` and only then extracts the
   `LocalParked` slots and `Remote` staged batches from `ready.inputs` (`:1362-1392`); an `Err` from the
   names check (`:1347`), translation (`:1361`) or the open-remote check (`:1377-1383`) drops them on the
   floor. Nothing calls `drop_parked` for the slots or `staging_release` for the leases.
5. **Rendezvous state is removed only in `take_ready`** (`local_exchange.rs:248-313`, removals at
   `:282-308`). A `SenderSource::Remote { batches }` (`:36-47`) holds staging-arena leases released only
   when the receiver runs (`engine.rs:671-679`), so a dead query's staged frames pin the arena for the
   process lifetime on N CNs (arena exhaustion is a hard failure, B6). Not measured: cn4 had no failures.

## 3. Design in one paragraph

*The engine holds parked state; the CN decides when a query is over.* Parked output is keyed by the
query id every run already carries (`FragmentLabel.query_id`, `fragment_executor.rs:57-63`, filled by
`fragment_label` at `compute_node_service.rs:1584-1589`). A query is declared over by exactly three
events, all terminal on the FE side: the engine's own `Err` for a run of that query (marked on the engine
thread before its next dequeue), a CN-side failure of a fragment of that query (`fail_fragment`), or an
FE `cancel_plan_fragment` carrying that query id. Each event retires the query: drops its parked
fragments, poisons its slots with the cause, marks it in a shared `RetiredQueries` set, and (from the
CN) purges its rendezvous state and releases its staged leases. Four gates consult the marks: the engine
refuses a `Run` of a retired query as the first statement inside `run_fragment_inner` (so the lease
sweep at `engine.rs:462-478` still runs); the engine refuses to *park* output of a query retired while
the fragment ran; the dispatch worker skips a queued fragment of a failed query before translating it
(releasing its staged batches, emitting no `fragment run started`); `process_fragment` refuses a late
arrival of a failed query before translation. No knobs, no timer, no wire change.

## 4. Mechanism by file

### 4.1 New module `parked_registry.rs` (not feature-gated; CI-compiled and CI-tested)

`engine.rs` is behind `#[cfg(feature = "sirius-engine")]` (`lib.rs:51-52`) and CI runs
`--no-default-features` (`Cargo.toml:44-50`; `.github/workflows/experimental.yml:76-79`), so today none
of the park/claim/release logic runs in CI. The bookkeeping moves into a generic struct that a CI test
drives with a drop-counting `F`.

```rust
//! Query-scoped bookkeeping of parked sender output. Generic over the fragment handle so the
//! logic is unit-tested without a GPU; the engine thread instantiates it with
//! `sirius::Fragment<'ctx>`.
use std::collections::{HashMap, HashSet, VecDeque};
use crate::fragment_executor::SenderSlot;
use crate::result_store::FragmentInstanceId;

pub(crate) type QueryId = FragmentInstanceId;

/// Why a query was retired; rendered into the log line and the poisoned-slot message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum RetireTrigger {
    /// A run of the query returned `Err` on the engine thread.
    EngineErr,
    /// A fragment of the query failed on the CN before or around the engine (translation, sink
    /// validation, remote drain).
    CnErr,
    /// `cancel_plan_fragment` with the FE's reason name (`PPlanFragmentCancelReason::as_str_name`,
    /// or "none" when the field was absent).                       -- added in commit 2
    Cancel(String),
}
impl std::fmt::Display for RetireTrigger { /* "engine_err" | "cn_err" | "cancel:{name}" */ }

struct Parked<F> {
    fragment: F,
    /// The StarRocks query this output belongs to; `None` for unlabeled runs (test fixtures).
    query_id: Option<QueryId>,
    /// Destinations that have not yet released their stream.
    outstanding: usize,
}

/// Outcome of releasing one destination's claim.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Release {
    /// Other destinations still hold the fragment.
    Outstanding(usize),
    /// Last claim: the fragment (and its GPU batches) dropped.
    Freed,
    /// The slot was retired with its query before this release; forgotten now, so a second
    /// release is a loud error again.
    AlreadyTornDown,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct Retired { pub fragments: usize, pub slots: usize }

pub(crate) struct ParkedRegistry<F> {
    parked: HashMap<u64, Parked<F>>,
    slots: HashMap<SenderSlot, (u64, u64)>,
    /// Why a slot's output went away, so the next export/relay/drop of it names the query's real
    /// error instead of the generic message (the TPC-H q08 lesson from engine.rs:270-275).
    /// Bounded FIFO; an entry is removed when the slot is parked again or released once.
    torn_down: HashMap<SenderSlot, String>,
    torn_down_order: VecDeque<SenderSlot>,
    next_id: u64,
}

impl<F> ParkedRegistry<F> {
    pub const TORN_DOWN_CAPACITY: usize = 4096;
    pub fn new() -> Self;
    /// Parks once; destination i claims (id, stream i). Refuses a duplicate slot before inserting
    /// anything (today's engine.rs:701-707 contract, same message:
    /// "duplicate destination slot {slot:?} in one sender fan-out"). Re-parking a slot forgets its
    /// torn_down entry.
    pub fn park(&mut self, query_id: Option<QueryId>, outputs: &[SenderSlot], fragment: F) -> Result<(), String>;
    /// The parked fragment and the stream a slot names, for relay (engine.rs:625-631) and export
    /// (:386-392). `verb` is "relay" | "export" for the missing-slot message.
    pub fn claim(&mut self, slot: &SenderSlot, verb: &str) -> Result<(&mut F, u64), String>;
    /// Read-only lookup for the cardinality declaration (engine.rs:559-566).
    pub fn peek(&self, slot: &SenderSlot) -> Option<(&F, u64)>;
    /// One destination's release (engine.rs:405-425). A live slot released twice is a loud error;
    /// a retired slot returns `AlreadyTornDown` exactly once.
    pub fn release(&mut self, slot: &SenderSlot) -> Result<Release, String>;
    /// Drops every parked fragment of `query_id` (`None` matches only unlabeled output), records
    /// `cause` for each of its slots, returns the counts. Idempotent: a second call returns zeros.
    pub fn retire(&mut self, query_id: Option<QueryId>, cause: &str) -> Retired;
    pub fn fragments(&self) -> usize;
    pub fn slots(&self) -> usize;
    fn missing(&self, slot: &SenderSlot, verb: &str) -> String;
}

/// Queries this CN has declared over. A `Run` already queued for one of them is refused instead of
/// re-parking output nobody will consume. Bounded FIFO: StarRocks query ids never recur, so
/// eviction only forgets an explanation, never a live query. First cause wins (matches
/// `ResultStore::fail_query`).
#[derive(Debug, Default)]
pub(crate) struct RetiredQueries {
    order: VecDeque<QueryId>,
    causes: HashMap<QueryId, String>,
}
impl RetiredQueries {
    pub const CAPACITY: usize = 1024;
    /// Returns `false` when the query was already marked (the earlier cause is kept).
    pub fn mark(&mut self, query_id: QueryId, cause: &str) -> bool;
    pub fn cause(&self, query_id: QueryId) -> Option<&str>;
    pub fn len(&self) -> usize;
}
```

Exact texts:

| situation | text |
|---|---|
| slot missing, no torn_down entry | `no parked sender output to {verb} for {slot:?}` (today's `engine.rs:376`) |
| slot missing, torn_down | `sender output for {slot:?} was discarded when its query was retired: {cause}` (replaces `engine.rs:372-374`'s "when another fragment on this CN failed") |
| duplicate park | `duplicate destination slot {slot:?} in one sender fan-out` (unchanged, `engine.rs:704-706`) |

Clippy note: with `--no-default-features` the engine is not compiled, so the registry's runtime API is
only reached from tests. Put `#[cfg_attr(not(feature = "sirius-engine"), allow(dead_code))]` on the
items (precedent: `result_store.rs:32`, `prpc.rs:154`), or CI's `-D warnings` clippy step fails.

### 4.2 `engine.rs`

1. **State** (`:262-275`): replace the three maps with `let mut registry: ParkedRegistry<sirius::Fragment<'_>> = ParkedRegistry::new();`,
   declared after `context` as the comment at `:262-266` requires (the fragments borrow the context and
   must drop first; `drop(registry)` replaces `drop(parked_slots); drop(parked);` at `:362-363`).
2. **Shared retired set.** `SiriusEngine::start` (`:148-169`) creates
   `let retired: Arc<Mutex<RetiredQueries>> = Arc::default();`, clones it into the thread closure
   (`engine_thread(settings.config, request_rx, ready_tx, retired.clone())`) and stores it on the handle
   (`SiriusEngine { requests, thread, staging, retired }`). Same shape as the `staging` handle served
   off-thread (`:129-138`): the set is what lets a cancel refuse runs already queued in the FIFO request
   channel (`std::sync::mpsc`, `:151`) ahead of the retire message.
3. **Err arm** (`:291-316`) becomes:

```rust
if let Err(err) = &result {
    retire(&mut registry, &retired, request.label.query_id, &RetireTrigger::EngineErr, err);
}
```

```rust
/// Retires one query on the engine thread: drops its parked fragments, poisons its slots, marks
/// it so later runs are refused. `None` retires only unlabeled output (test fixtures).
fn retire<F>(
    registry: &mut ParkedRegistry<F>,
    retired: &Mutex<RetiredQueries>,
    query_id: Option<QueryId>,
    trigger: &RetireTrigger,
    cause: &str,
) {
    let dropped = registry.retire(query_id, cause);
    if let Some(query_id) = query_id {
        retired.lock().unwrap_or_else(PoisonError::into_inner).mark(query_id, cause);
    }
    if dropped.fragments > 0 {
        warn!(query_id = ?query_id, trigger = %trigger, fragments = dropped.fragments,
              slots = dropped.slots, still_parked = registry.fragments(), cause,
              "retired a query's parked sender outputs");
    } else {
        debug!(query_id = ?query_id, trigger = %trigger, "retire found nothing parked");
    }
}
```

   The process-wide wipe and its `warn!("discarding every parked sender output ...")` are gone.

4. **Gate 1, dequeue refusal inside `run_fragment_inner`** (`:486-496`), as its first statement, before
   `context.fragment()`:

```rust
if let Some(query_id) = request.label.query_id
    && let Some(cause) = retired.lock().unwrap_or_else(PoisonError::into_inner).cause(query_id).map(str::to_owned)
{
    return Err(format!("query {query_id} was already retired on this CN: {cause}"));
}
```

   Placement is the point: `run_fragment` (`:427-481`) sees `result.is_err()` and its sweep at `:462-478`
   releases every `remote_inputs` lease the refused run carried. A refusal at the `Run` dequeue (the
   surgical and robust designs) would bypass that sweep and leak staging-arena leases on N CNs. `run_fragment`
   and `run_fragment_inner` gain `registry: &mut ParkedRegistry<Fragment<'ctx>>, retired: &Mutex<RetiredQueries>`
   in place of the three map parameters.

5. **Gate 2, park-time refusal** (`:696-717`, right before `registry.park`): re-check the set; if the
   query was retired while this fragment ran (a cancel mid-run, or a sibling failing on another CN),
   drop the fragment instead of parking it:

```rust
if let Some(query_id) = request.label.query_id
    && let Some(cause) = /* retired.cause(query_id) cloned */
{
    return Err(format!("fragment of query {query_id} finished after the query was retired on this CN; \
                        its output was dropped instead of parked: {cause}"));
}
registry.park(request.label.query_id, &request.outputs, fragment)?;
return Ok(None);
```

   Without this, output parked after a retire has no owner-triggered release and stays for the process
   lifetime (the accidental wipe that used to free it is gone). See decision 3 in section 12.

6. **Registry call sites**: cardinality declaration `:559-566` -> `registry.peek(slot)`; relay
   `:625-636` -> `registry.claim(slot, "relay")` then `registry.release(slot)?` (map `Ok(_)` to `()`);
   `export_next` `:379-403` -> `registry.claim(&slot, "export")`; `release_slot` `:405-425` ->
   `registry.release(slot)` mapping every `Ok` variant to `Ok(())` and logging
   `debug!(?slot, "drop_parked for a slot already retired with its query")` on `AlreadyTornDown`. Delete
   `missing_slot`, `export_next`, `release_slot` as free functions (their bodies move into the registry).
7. **New request** next to `DropParked` (`:98-104`), fire-and-forget because the cancel RPC and the
   dispatch worker must never wait behind a running fragment (the rule the module doc at `:16-21` records):

```rust
/// Drop every parked output of `query_id` and refuse its later runs. Sent by the CN when it
/// learns a query is over by a route the engine cannot see: a pre-run failure or an FE cancel.
/// No respond channel: the sender already marked the shared `RetiredQueries`, so the refusal is
/// visible to the very next dequeue; the drop happens when the engine thread is free.
RetireQuery { query_id: QueryId, trigger: RetireTrigger, cause: String },
```

   Handler: `retire(&mut registry, &retired, Some(query_id), &trigger, &cause)`.

8. **Handle methods** (`impl SiriusEngine`, next to `engine_call` `:750-765`):

```rust
/// Sends one request without waiting for an answer.
fn engine_send(&self, request: EngineRequest) -> Result<(), String> {
    self.requests.lock().unwrap_or_else(PoisonError::into_inner).as_ref()
        .ok_or_else(|| "sirius-engine is shutting down".to_string())?
        .send(request).map_err(|_| "sirius-engine thread is not running".to_string())
}
```

   and in `impl FragmentExecutor for SiriusEngine`:

```rust
fn retire_query(&self, query_id: QueryId, trigger: RetireTrigger, cause: &str) -> Result<(), String> {
    // Mark first: a Run already queued ahead of the RetireQuery is refused at its dequeue.
    self.retired.lock().unwrap_or_else(PoisonError::into_inner).mark(query_id, cause);
    self.engine_send(EngineRequest::RetireQuery { query_id, trigger, cause: cause.to_string() })
}
```

9. `Drop for SiriusEngine` (`:832-849`) is unchanged: no new thread, the channel still closes on drop.
10. Module doc: add one sentence after the staging paragraph: "Parked output is owned per query; a
    query is retired (dropped, later runs refused) by its own engine error or by the CN's
    `retire_query`, never by another query's failure."

### 4.3 `fragment_executor.rs`

One trait method with a default, next to `drop_parked` (`:217-223`):

```rust
/// Drops every parked output of `query_id` and refuses later runs for it. Non-blocking and
/// idempotent. Called when the CN learns the query is over: a fragment failed before the engine
/// ran it, or the FE cancelled. Unlike `drop_parked` this is a sweep, not an exactly-once release,
/// so an executor that parks nothing succeeds trivially.
fn retire_query(&self, query_id: FragmentInstanceId, trigger: RetireTrigger, cause: &str) -> Result<(), String> {
    let _ = (query_id, trigger, cause);
    Ok(())
}
```

`StubExecutor` and every test executor inherit it. Re-export `RetireTrigger` from here for callers.

### 4.4 `compute_node_service.rs`

**Commit 1**

a. `ServiceCore::fail_fragment(&self, id: FragmentInstanceId, query_id: FragmentInstanceId, error: String)`:
   `self.results.fail_query(query_id, id, error.clone())` (unchanged semantics, `:896`), then
   `self.executor.retire_query(query_id, RetireTrigger::CnErr, &error)`; on `Err`
   `warn!(%query_id, error = %err, "could not retire the failed query's parked output")`. Replaces the
   bare `fail_query` in `run_ready_fragment`'s `(Some(id), Some(query_id))` arm (`:877-897`). This is the
   q16 fix (translation at `:1361`) and the belt for an engine `Err` (idempotent: the engine already
   retired).

b. `ServiceCore::release_staged(&self, inputs: Vec<ReadyExchangeInput>) -> usize`: for every
   `SenderSource::Remote { batches, .. }` and every batch with `len > 0` (the `StagedBatch` contract,
   `fragment_executor.rs:96-100`), `self.executor.staging_release(batch.offset)`; warn on error
   (`"failed to release a staged lease of a retired query"`), return the count released. `LocalParked`
   slots need nothing here: the engine's retire drops them by query.

c. **Gate 3, dispatch refusal** at the top of `run_ready_fragment` (`:864`), before
   `execute_ready_fragment`:

```rust
if let Some(query_id) = query_id
    && let Some(cause) = self.results.failure_of(query_id)
{
    let released_leases = self.release_staged(ready.inputs);
    info!(%query_id, fragment_instance_id = ?id, %cause, released_leases,
          "skipping fragment of a retired query");
    return Vec::new();
}
```

   No translation, no `fragment run started`, no GPU work, no follow-on fragments. This is where the
   measured q05/q08/q09/q17/q21 leftovers were sitting (the worker inbox, async dispatch on in the
   campaign, `perf/sf1000/capture-cn.sh:12`).

d. **Gate 4, arrival refusal** in `process_fragment` (`:918-951`), after the translate-only block
   (`:927-933`, survey mode keeps accepting everything) and before `receiver_exchanges`:

```rust
if let Some(query_id) = Self::query_id(&params)
    && let Some(cause) = self.results.failure_of(query_id)
{
    if Self::is_mysql_result_sink(&params)? && let Some(id) = Self::fragment_instance_id(&params) {
        // Keep the reserve-then-fail contract: the FE's fetch_data long-poll on this id reports
        // the cause on its first poll (result_store.rs:121-135) instead of an RPC error.
        self.results.reserve(id, query_id);
        return Ok(FragmentOutcome::default());
    }
    return Err(format!("query {query_id} already failed on this CN: {cause}"));
}
```

   The result-sink branch keeps `intermediate_failure_before_result_registration_still_fails_the_result_poll`
   (`:3376-3411`) green and does not register a receiver that can never complete.

e. **Staged leases on every pre-run error path.** Restructure `execute_ready_fragment` (`:1325-1394`)
   so the split of `ready.inputs` into `inputs`/`remote_inputs` happens before translation and the
   remote batches live in a guard:

```rust
/// Staged remote batches this CN holds for one fragment. Released on drop unless handed to the
/// engine (which releases each lease after pushing it, or in its own sweep on a failed run).
struct StagedLeases<'e> { batches: Vec<(i32, i32, Vec<StagedBatch>)>, executor: &'e dyn FragmentExecutor }
impl StagedLeases<'_> {
    fn push(&mut self, node_id: i32, sender_id: i32, batches: Vec<StagedBatch>);
    /// Hands the batches to the caller; the guard then releases nothing.
    fn take(&mut self) -> Vec<(i32, i32, Vec<StagedBatch>)> { std::mem::take(&mut self.batches) }
}
impl Drop for StagedLeases<'_> { /* staging_release every len > 0 offset; warn on error */ }
```

   Order inside `execute_ready_fragment`: build `exchange_inputs` borrowing `ready.inputs` (the names
   check at `:1347` can `Err`; nothing extracted yet, so on that `Err` call `release_staged(ready.inputs)`
   and return); split `ready.inputs` pushing each `Remote` batch list into the guard *before* the
   `closed` check (`:1377-1383`); translate (`:1360-1361`); call
   `execute_fragment_with_inputs(&params, translated, inputs, leases)`. In
   `execute_fragment_with_inputs` (`:1020-1245`) take the batches out of the guard only when building the
   `FragmentRun` for `run_labeled` (`:1030-1040` result, `:1176-1187` sender), so the sink validations
   (`:1055-1093`), destination checks (`:1094-1140`) and routing (`:1142-1172`) release on `Err` for free.
   `execute_fragment` (`:1007-1013`) passes an empty guard.

f. `results.failure_of` is section 4.5's accessor.

**Commit 2**

g. `cancel_plan_fragment` (`:457-479`) keeps its immediate OK reply (the jprotobuf-channel reason in the
   docstring `:449-455` stays) and becomes a teardown:

```rust
let id = FragmentInstanceId::from(&request.finst_id);
let query_id = request.query_id.as_ref().map(FragmentInstanceId::from);
let reason_code = request.cancel_reason.and_then(|code| PPlanFragmentCancelReason::try_from(code).ok());
let reason_name = reason_code.map_or("none", PPlanFragmentCancelReason::as_str_name);
let mut reason = format!("fragment instance {id} was cancelled by the FE");        // unchanged text
if let Some(message) = request.error_message.as_ref().filter(|m| !m.is_empty()) {
    reason = format!("{reason}: {message}");
}
self.core.results.cancel(id, reason.clone());                                          // unchanged
if let Some(query_id) = query_id {
    let finished = matches!(reason_code, Some(PPlanFragmentCancelReason::QueryFinished | PPlanFragmentCancelReason::LimitReach));
    if !finished {
        self.core.results.cancel_query(query_id, reason.clone());
    }
    let released_leases = if id == FragmentInstanceId::from_halves(0, 0) { 0 }   // phased-schedule dummy
                          else { self.core.release_sources(self.core.exchanges.retire_receiver(id)) };
    if let Err(err) = self.core.executor.retire_query(query_id, RetireTrigger::Cancel(reason_name.to_string()), &reason) {
        warn!(%query_id, error = %err, "could not retire the cancelled query's parked output");
    }
    info!(%query_id, fragment_instance_id = %id, reason = reason_name, released_leases,
          "cancel_plan_fragment retired the query on this CN");
}
Ok(PCancelPlanFragmentResult { status: Self::ok_status() }.into())
```

   `release_sources(Vec<SenderSource>) -> usize` is `release_staged`'s sibling over a flat source list.
   Reason names come from the generated prost enum (`PPlanFragmentCancelReason::{LimitReach, UserCancel,
   InternalError, Timeout, QueryFinished}`, `target/.../out/starrocks.rs:3266-3272`, exposed through
   `crate::proto::starrocks`). Every reason retires parked output and rendezvous state; only the
   non-finished reasons record a query-level failure, so `query_failures` does not grow by one entry per
   successful query and a repeat `fetch_data` after QUERY_FINISHED still reports EOS. The phased dummy
   `(0,0)` retires nothing by instance and everything by query. See decision 2 in section 12.

h. **Late-frame refusal** in `handle_transmit_packed` (`:721-803`), after the key is built (`:746-749`)
   and before `push_remote_frame` (`:793-800`):

```rust
if self.core.exchanges.is_retired(key.fragment_instance_id) {
    if let Some(batch) = &batch && batch.len > 0 {
        self.core.executor.staging_release(batch.offset)?;
    }
    info!(receiver_fragment_instance_id = %key.fragment_instance_id, stream_id = key.node_id, sender_id, seq, eos,
          "released a remote frame for a retired receiver");
    return Ok(());
}
```

   The peer's drain completes quietly (it gets OK), and its own `drop_parked` frees the sender side.

i. **Inline-path query-level recording.** Today an inline failure (the default dispatch mode) is
   reported only as the RPC status: `exec_single_attachment` (`:704-717`) and the batch loop
   (`:1661-1675`). Wrap both in one helper that, on `Err` from `try_dispatch_sender`, `process_fragment`
   or `dispatch_then_join`, calls `fail_fragment(id, query_id, err.clone())` when the params carry both
   ids, then returns the `Err`. Any RPC error fails the query on the FE (`FragmentInstanceExecState`
   deployment wait), so marking is never premature. Result-store effect: a result instance of that
   query reserved on this CN reports the real cause on its first poll instead of the 600 s `wait_ready`
   timeout (`result_store.rs:219-243`). Survey mode returns `Ok` and is unaffected.

j. Docstring rewrite for `cancel_plan_fragment` (`:449-455`): it now retires parked output, purges the
   receiver's rendezvous state and releases its staged leases; it still cannot abort a fragment inside
   `run()`.

### 4.5 `result_store.rs`

```rust
/// The first failure recorded for `query_id` (by `fail_query` or `cancel_query`), if any.
pub(crate) fn failure_of(&self, query_id: FragmentInstanceId) -> Option<String>;   // commit 1

/// Records an FE cancel at query level: later fragments of the query are refused on arrival and
/// its still-`Waiting` result entries fail now with `reason`. Delivered, drained and failed entries
/// keep their state (the rule `cancel` follows, :205-212). First cause wins.
pub(crate) fn cancel_query(&self, query_id: FragmentInstanceId, reason: String);      // commit 2
```

`cancel_query` walks `query_results[query_id]` (`:92-95`), flips only `Waiting` entries, inserts into
`query_failures` with `entry().or_insert`, and `notify_all`s when it changed anything.

### 4.6 `local_exchange.rs` (commit 2)

`ExchangeState` (`:88-96`) gains `retired: HashSet<FragmentInstanceId>` and
`retired_order: VecDeque<FragmentInstanceId>` (`const RETIRED_CAPACITY: usize = 1024`; evict oldest from
both). New methods on `LocalExchange`:

```rust
/// Forgets a receiver the FE cancelled: its pending registration, every sender source recorded
/// for it (all exchange nodes), and its remote sequence tracking. Returns the removed sources so
/// the caller releases the staging leases of the remote ones. Remembers the id (bounded) so a
/// frame from a peer's still-draining sender is refused instead of re-creating the entry.
pub(crate) fn retire_receiver(&self, fragment_instance_id: FragmentInstanceId) -> Vec<SenderSource>;
/// Whether `retire_receiver` was called for this receiver (O(1)).
pub(crate) fn is_retired(&self, fragment_instance_id: FragmentInstanceId) -> bool;
```

`retire_receiver` mirrors the removals in `take_ready` (`:282-308`): `receivers.remove`, every
`sources` entry whose key's `fragment_instance_id` matches, `remote_seq.retain(...)`. No query id enters
the exchange and the wire frame is untouched (`PTransmitPackedParams`, `internal_service.proto:812-827`,
has no `query_id`): the FE cancels per deployed instance, and a sender exists only for a receiver that
was deployed (receiver-first dispatch, module doc `:1-6`).

### 4.7 Comments and docs

- `nixl_transport.rs:410-411`: "(per-query GC is cut from Path B)" -> "(per-query retirement makes a
  late drop idempotent: a slot already retired with its query returns Ok once)".
- `DEMO.md:37-42` "What it does not exercise yet": add "**Cancellation mid-run.** `cancel_plan_fragment`
  retires the query's parked output and rendezvous state on this CN, but a fragment already inside
  `run()` finishes first; its output is dropped when it ends."
- `benchmarks/tpch/README.md:42`, `benchmarks/8GPU-NVLINK-RUNBOOK.md:435,531`, `2NODE-REPLICATE.md:381`,
  `2NODE-ENGINE-B-TUTORIAL.md:819` say the CN does not implement `cancel_plan_fragment`. Update the
  README caveat in commit 2; leave the runbooks' `RESTART_CMD` advice (an engine wedge inside `run()`
  is still not cancellable) but correct the wording to "cannot abort a running fragment".
- `benchmarks/tpch/QUERY-DEVIATIONS.md:42-43` describes the blanket `parked.clear()`; add one line
  that it is now scoped to the failing query.
- `docs/TUNABLES.md`: no change (no knob). `docs/super-sirius/`: no change (no engine C++ change).

### 4.8 Log lines and error texts (the contract the acceptance greps and tests pin)

| where | level | text and fields |
|---|---|---|
| engine retire with drops | WARN | `retired a query's parked sender outputs` `query_id` `trigger=engine_err\|cn_err\|cancel:<NAME>` `fragments` `slots` `still_parked` `cause` |
| engine retire, nothing parked | DEBUG | `retire found nothing parked` |
| engine gate 1 | Err | `query {q} was already retired on this CN: {cause}` |
| engine gate 2 | Err | `fragment of query {q} finished after the query was retired on this CN; its output was dropped instead of parked: {cause}` |
| poisoned slot | Err | `sender output for {slot:?} was discarded when its query was retired: {cause}` |
| CN gate 3 | INFO | `skipping fragment of a retired query` `query_id` `fragment_instance_id` `cause` `released_leases` |
| CN gate 4 | Err | `query {q} already failed on this CN: {cause}` |
| cancel | INFO | `cancel_plan_fragment retired the query on this CN` `query_id` `fragment_instance_id` `reason` `released_leases` |
| late frame | INFO | `released a remote frame for a retired receiver` |
| retire failed | WARN | `could not retire the failed query's parked output` / `could not retire the cancelled query's parked output` |

Log `query_id` with `%` (Display, the plain UUID) wherever it is not an `Option`; `cnlog_extract.py`'s
regex accepts both the plain and the `Some(FragmentInstanceId(..))` forms.

## 5. Behaviour

### 5.1 The measured 1-CN cases (campaign configuration: async dispatch on)

- **q05** (`cluster.log:6050-6329`): the lineitem sender `5774` returns `Err` at 13:05:24.626; the engine
  retires query `...576c` on its own thread (drops the 2 parked slots it already had, `slots=2` as
  today's wipe reported, marks retired). The worker's `Err` arm calls `fail_fragment` (records
  `failure_of`, sends an empty `RetireQuery`). The next inbox items `5773`, `5777`, `5776` hit gate 3 and
  are skipped: three `skipping fragment of a retired query` lines, no `fragment run started`, nothing
  parked. `5775` never becomes ready (its senders never pushed). The 11 `INTERNAL_ERROR` cancels each
  `cancel_query` (first wins), `retire_receiver` (the never-ready receivers of streams 7 and 9 leave the
  rendezvous), `retire_query` (nothing left). q06.r0's first `QueryBegin allocated=` is the ~3 KB
  baseline instead of 1.32 GB, and q06 starts ~0.7 s earlier.
- **q08, q09, q17, q21**: same shape; q09's 31.56 GB no longer sits under q10..q16; q17's 6.34 s leftover
  `aca1` and q21's 3.15 s `acb` (which today relays 730,806,711 rows of stream 6 and parks 36.64 GB) are
  skipped; q18 and q22 start 6.42 s / 3.35 s earlier.
- **q16** (`cluster.log:16593`): the receiver `ff85`'s translation fails on the worker after `ff87`
  (partsupp, 6.6 GB) and `ff86` parked. `fail_fragment` -> `RetireQuery` -> the engine (idle) drops both
  fragments within the millisecond: `retired ... trigger=cn_err fragments=2 slots=2 still_parked=0`. q17
  starts at 0 GB instead of 38.16 (with q09's leak also gone).
- **q18**: no leftovers today, unchanged.

Note that on 1 CN the process-wide wipe was not itself the leak: at each wipe only the failing query's
slots were parked, so today's `clear()` and the new `retire` free the same bytes. What changes is that the
leftovers never run and q16's slots are dropped at all.

### 5.2 Default dispatch mode (async off, not the campaign setting; tested in CI, smoke-tested at SF1000)

Senders run inside their RPC threads and queue in the engine channel behind the failing `Run`. The
engine marks the query at its own `Err` before responding, so every queued `Run` of it is refused at gate
1 with its leases swept, and logs `fragment run started` / `fragment run failed ... already retired`
with `elapsed_ms` of a channel round trip. A sender arriving after the failure is refused at gate 4 before
translation (RPC `INTERNAL_ERROR`, which the FE ignores: it is already cancelling,
`DefaultCoordinator.java:1054-1057`). Commit 2's inline recording makes `failure_of` true on this node
for an inline failure too.

### 5.3 The 15 passing queries

While alive: one `HashMap` lookup under the result-store mutex per fragment arrival and per dispatch;
one small mutex lock per run at gate 1 and one at gate 2 (microseconds against fragment runs of 4 ms to
8 s, `cn1-cnlog.txt`). At query end the FE's `QUERY_FINISHED`/`LIMIT_REACH` cancels (already sent today,
`cancels=N`) each cost one non-blocking channel send, one `RetiredQueries::mark`, one empty
`retire_receiver` and one empty engine `retire` (DEBUG line). `results.cancel` is unchanged, so delivered
rows are never clobbered (`cancel_fails_only_a_waiting_entry`, `result_store.rs:490-510`). No plan, memory
or ordering change; warm medians should be within run-to-run noise (6%).

### 5.4 N CNs (not measured this campaign: cn4 had zero failures)

A query failing on CN A: A retires it locally; the FE's `INTERNAL_ERROR` cancels reach every CN; each
peer `cancel_query`s (later arrivals refused), `retire_receiver`s (staged frames for the dead receiver
leave the arena) and `retire_query`s (parked senders of the dead query drop; a drain in flight fails at
its next `export_packed_next` with the retiring cause and its `drop_parked` returns `AlreadyTornDown`).
A late frame from a peer still draining is released and acked. `QUERY_FINISHED` on a sender CN racing the
transport's post-eos `drop_parked` (`nixl_transport.rs:790`) is the one theoretical race; `AlreadyTornDown`
makes it a success (it is a microsecond window against an FE round trip, and impossible in inline mode,
where the FE cannot finish while the sender RPC is open).

## 6. Tests to write first

All Rust, in the CN crate (`experimental/starrocks`). No Catch2 (no engine C++ change) and no FE JUnit
(no FE change). "CI" tests run under `cargo test --no-default-features`; "GPU" tests need the engine
feature and hold `crate::GPU_ENGINE_TEST_LOCK` (`lib.rs:80-81`).

### 6.1 CI: `parked_registry.rs` `#[cfg(test)]` (use `F = Rc<Cell<usize>>`-style drop counter)

| test | asserts |
|---|---|
| `park_then_release_last_claim_drops_the_fragment` | two slots, one fragment; first release `Outstanding(1)`, second `Freed`, drop count 1 |
| `second_release_of_a_live_slot_is_a_loud_error` | after `Freed`, `release` is `Err` containing "no parked sender output to drop" |
| `duplicate_slot_is_refused_before_anything_is_inserted` | park with a repeated slot -> `Err("duplicate destination slot ...")`, `slots() == 0`, fragment dropped |
| `retire_drops_only_that_query_and_poisons_its_slots` | park A (2 slots) and B (3); `retire(Some(A))` -> `{fragments:1, slots:2}`; B's `claim` works; A's `claim` errs with "was discarded when its query was retired: <cause>"; `retire(Some(A))` again -> zeros |
| `retire_of_the_unlabeled_bucket_leaves_labeled_output_alone` | park `None` and `Some(Q)`; `retire(None)` drops only the unlabeled one |
| `release_after_retire_is_ok_exactly_once` | `retire(A)`; `release(a_slot)` -> `AlreadyTornDown`; again -> `Err` |
| `repark_of_a_retired_slot_forgets_its_torn_down_entry` | after retire, park the same slot again; `claim` succeeds; no stale cause |
| `torn_down_is_bounded` | retire `TORN_DOWN_CAPACITY + 1` slots; the oldest slot's `claim` gives the generic message |
| `retired_queries_keep_the_first_cause_and_evict_fifo` | `mark(q, "a")` true, `mark(q, "b")` false, `cause(q) == "a"`; `CAPACITY + 1` marks evict the first |

### 6.2 CI: `result_store.rs`

| test | asserts |
|---|---|
| `failure_of_reports_the_first_recorded_failure` | `None` before; after two `fail_query`s the first cause; after `cancel_query` on a fresh query, the cancel reason |
| `cancel_query_fails_waiting_entries_only_and_is_visible_to_failure_of` (commit 2) | Waiting -> Failed(reason); Pending stays Pending (extends `cancel_fails_only_a_waiting_entry` `:490-510`); a later `reserve` for the query lands Failed on arrival (pattern of `:451-470`) |

### 6.3 CI: `local_exchange.rs` (commit 2)

| test | asserts |
|---|---|
| `retire_receiver_returns_its_sources_and_refuses_later_frames` | register receiver R (node 7, 2 senders); `push_sender` one local; `push_remote_frame` one open remote batch; `retire_receiver(R)` returns both sources (the remote one with its batch); `is_retired(R)`; `register_receiver(R2)`/`push_sender` for another receiver unaffected; `remote_seq` for R is gone (a fresh frame seq 0 for R would not be a gap; assert via `is_retired` path in the service test instead) |
| `retired_receivers_are_bounded` | `RETIRED_CAPACITY + 1` retires; the first is no longer `is_retired` |

### 6.4 CI: `compute_node_service.rs` tests (existing fixtures: `propagation_chain` `:3311-3343`,
`assert_exec_ok` `:3774`, `fetch_error_eventually` `:2078-2102`, `fetch_rows_eventually` `:2041`,
`wait_until` `:2028`, `route` `:3751`, `sink_of_type` `:3799`, `transmit_params` `:2936`,
`RecordingExecutor` `:2914-2932`, `FailingIntermediateExecutor` `:2000-2010`,
`CountingExecutor` `:1810-1820`, `set_async_sender_dispatch` `:297-302`)

New fixture: `Retiring<E: FragmentExecutor>` wrapping any executor, recording
`Vec<(FragmentInstanceId, RetireTrigger, String)>` from `retire_query`, delegating everything else
(sibling of `RecordingPinExecutor` `:1823-1849`).

| test | mode | asserts |
|---|---|---|
| `queued_fragments_of_a_failed_query_are_skipped` | async on | `Retiring<CountingFailingIntermediate>` (counts runs, fails intermediate ones); `propagation_chain` root+middle+leaf, then a fourth sender-only fragment of the same query, then a sentinel sender-only fragment of another query. `wait_until` run count == 3 (leaf, middle, sentinel); the fourth never ran; `failure_of(q)` is `Some`; `retire_query` recorded once with `CnErr` and the middle's error |
| `queued_fragments_of_a_failed_query_are_skipped_inline` | async off | same chain; the fourth sender's `exec_plan_fragment` returns `INTERNAL_ERROR` containing "already failed on this CN"; run count did not move |
| `a_fragment_failure_retires_the_query_on_the_executor` | either | `Retiring<FailingIntermediateExecutor>`: after `fetch_error_eventually(root)`, exactly one recorded retire with the chain's query id and the error text |
| `translation_failure_retires_the_slots_in_hand_and_releases_remote_leases` | async on | receiver `per_exch_num_senders(7) = 2`; sender 0 local (StubExecutor parks nothing but pushes `LocalParked`); sender 1 remote via `transmit_params` frames with a batch (`offset 4096, len 512`) and eos, column names differing from sender 0 so `:1347` errs before translation; `Retiring<RecordingExecutor>`: `released` contains 4096, `retire_query` recorded, `fetch_error_eventually(root)` carries "different output names" |
| `a_result_fragment_arriving_after_the_failure_still_reports_the_cause` | inline | extends `:3376-3411`: with gate 4 present the late result fragment's RPC is still OK and `fetch_data` reports the cause (the reserve branch) |
| `an_inline_sender_failure_is_recorded_at_query_level` (commit 2) | async off | sender with `sink_of_type(TDataSinkType::OLAP_TABLE_SINK)` returns an RPC error (`exec_plan_fragment_rejects_unhandled_output_sink` `:3566` shape); a result fragment of the same query reserved afterwards fails on its first poll with that cause |
| `cancel_reason_matrix_retires_and_records_by_reason` (commit 2) | either | for `INTERNAL_ERROR`, `TIMEOUT`, `USER_CANCEL`, `None`: one recorded retire with `Cancel(<name or "none">)`, cause contains the request's `error_message`, `failure_of(q)` is `Some`. For `QUERY_FINISHED`, `LIMIT_REACH`: retire recorded, `failure_of(q)` is `None`. With `query_id: None`: no retire call (today's path). The two existing cancel tests `:3414-3496` pass unchanged |
| `query_finished_cancel_keeps_delivered_rows` (commit 2) | either | full chain + `fetch_rows_eventually`; cancel reason 5 for each instance; a repeat `fetch_data` reports EOS; `failure_of` is `None` |
| `cancel_purges_the_receivers_staged_frames_and_refuses_late_ones` (commit 2) | either | `Retiring<RecordingExecutor>`: two `transmit_packed` frames (offsets 1024, 2048) for receiver R, no eos; cancel R with `INTERNAL_ERROR` and the query id; `released` contains 1024 and 2048; a third frame (offset 3072) returns OK and `released` gains 3072; nothing dispatched |
| `cancel_for_the_phased_dummy_instance_still_retires_the_query` (commit 2) | either | `finst_id (0,0)` with a query id: retire recorded; no panic; unknown-instance behaviour of `:3463-3496` kept |

The mode-dependent tests run twice (a loop over `[false, true]` calling `set_async_sender_dispatch`).

### 6.5 GPU: `engine.rs` tests (`pixi run cn-test` on one GPU; reuse `write_users_parquet` `:1131`,
`local_files_plan` `:876`, `stream_plan` `:916`, `test_settings` `:866`; the arena env dance from
`engine_pushes_staged_remote_batches` `:1157-1159, 1233-1235`)

| test | asserts |
|---|---|
| `a_failed_run_retires_only_its_own_query` | park the fixture under slot A1 labelled `{query A, instance a1}` and under B1 labelled `{B, b1}`; run a labelled-A `stream_plan(99, ..)` with `inputs: []` (fails at `:532-538` before `build`): `Err`. Then: any run labelled A -> `Err` containing "already retired"; an unlabeled receiver over A1 -> `Err` containing "was discarded when its query was retired"; `drop_parked(A1)` -> `Ok` then `Err`; a receiver over B1 -> 3 rows. With the 64 MiB arena: `staging_lease(4096)` -> `o`; a run labelled A carrying `remote_inputs: vec![(9, 0, vec![StagedBatch{ metadata: vec![1], offset: o, len: 4096, rows: Some(1) }])]` -> `Err` "already retired"; `staging_lease(1024)` -> `0` (the sweep at `:462-478` released `o`, mirrors `:1222-1225`) |
| `retire_query_drops_parked_output_and_refuses_later_runs` | park under C; `engine.retire_query(C, RetireTrigger::CnErr, "translation failed")`; a run labelled C -> `Err` containing "translation failed"; an unlabeled receiver over C's slot -> `Err` containing "retired: translation failed" (FIFO makes the fire-and-forget deterministic here); `staging_lease(1024) == 0` |
| `an_unlabeled_failure_retires_only_the_unlabeled_bucket` | park U1, U2 with `FragmentLabel::default()` and L labelled Q; fail an unlabeled run; receivers over U1/U2 -> "was discarded when its query was retired"; receiver labelled Q over L -> 3 rows |
| `output_parked_after_a_retire_is_dropped_not_parked` (gate 2) | `retire_query(D, ..)` first, then run a sender labelled D -> `Err` containing "dropped instead of parked"; `drop_parked(D's slot)` -> `Err` generic (never parked) |

The two existing GPU tests `engine_executes_local_files_and_sequential_exchange` (`:1283-1417`) and
`engine_pushes_staged_remote_batches` (`:1151-1236`, including the double-drop assertion at
`:1200-1203`) guard the registry refactor and must pass unchanged.

## 7. Build and test commands

Worktree: `/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping` (branch
`fix/parked-bookkeeping` off `perf/profile-sf1000` = 45dab3be; CN release binary built 15:51 today; the
plan assigns GPU 1 to this fix). Never the shared clone `/home/prestouser/aocsa/sirius`. Submodules are
already initialised there; if the tree is fresh, `git submodule update --init --recursive` first.

```bash
WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping
cd $WT/experimental/starrocks

# CI trio, exactly what .github/workflows/experimental.yml:68-79 runs (no GPU, no engine build):
pixi run -e cn cargo fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check
pixi run -e cn cargo clippy --all-targets --no-default-features -- -D warnings
pixi run cn-test-no-engine                     # = cargo test -p sirius-starrocks-cn --no-default-features (pixi.toml:148)

# Engine-linked build and the GPU tests (pixi.toml:131-140; cn-build/cn-test depend on engine-build
# = `pixi run make` at the repo root, and on apply-starrocks-patches):
ls $WT/build/release/extension/parquet/parquet.duckdb_extension   # engine artefacts the CN links/loads (engine.rs:172-190)
pixi run cn-build                              # release binary for the SF1000 arm
CUDA_VISIBLE_DEVICES=1 pixi run cn-test        # whole suite incl. the GPU tests, one device
# targeted GPU tests:
CUDA_VISIBLE_DEVICES=1 pixi run bash -lc 'source scripts/cn-env.sh && cargo test -p sirius-starrocks-cn -- engine::tests --nocapture'

# Repo-root hooks before committing:
cd $WT && pixi run pre-commit run -a
```

`pixi run make` (repo root) is only needed if `build/release` is missing; this fix changes no engine C++.

**FE patch delivery, for the record: not exercised by this fix.** No FE change, so nothing is added to
`experimental/starrocks/patches/` (today: `files-query-whole-file-ranges.patch`,
`nixl-exchange-proto.patch`, applied idempotently by `scripts/apply-starrocks-patches.sh`, which every
`cn-*` recipe depends on) and `pixi run fe-build` (`pixi.toml:197-258`, a long Maven build) is not
rerun. The packaged FE under `starrocks/output/fe` stays as is.

## 8. Acceptance at SF1000 (orchestrator-run, one arm at a time, GPU box idle, outside 02:00-03:50 UTC)

Tooling: `perf/sf1000/capture-cn.sh` hardcodes `WT=.../sirius-stacks-wt/perf`. Copy it to
`capture-cn-fix2.sh` with `WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping`
(`run-queries.sh` reads the queries from the perf tree; same SQL). Add to `cnlog_extract.py` two
counters next to `cancels` (`skips`: lines containing `skipping fragment of a retired query`;
`retired`: lines containing `retired a query's parked sender outputs`) and print them.

`base@start` per run = `allocated=` of the first `[gpu_pool] GPU:0 QueryBegin` line
(`sirius_context.cpp:271` format, `cn1/engine-cn0.log`) whose timestamp falls in the run's window
(`runs.csv start_utc` to the next start), the `headroom.txt` recipe.

### Arm A: smoke, campaign configuration (async dispatch on), ~7 min

`bash capture-cn-fix2.sh 1 fix2-smoke 300 2 q05 q06 q16 q17 q21 q22`

| # | check | must be | was |
|---|---|---|---|
| A1 | `base@start` of q06.r0/r1/r2, q17.r0, q22.r0/r1/r2 | <= 1 MiB (baseline is 3-4 KB) | 1.32 / 4.69 / 4.69, 38.16, 5.94 / 36.64 / 36.64 GB |
| A2 | for each failed query id: `fragment run started` lines after its `fragment run failed` | 0 | q05 4, q17 2, q21 2 |
| A3 | `skips=` for q05, q17, q21 | >= 1 each (predicted 3, 2, >= 1) | n/a |
| A4 | `retired a query's parked sender outputs` per failure | exactly one WARN per failed query with `still_parked=0`; q05 `trigger=engine_err fragments=2 slots=2`; q16 `trigger=cn_err fragments=2 slots=2` | n/a |
| A5 | `relayed native batches` for a dead query after its failure | 0 | q05 `5775` relayed 11 |
| A6 | client start (`runs.csv start_utc`) to first `fragment run started` of q06.r0 and q22.r0 | <= 100 ms | 720 ms, 3350 ms |
| A7 | q22.r0 wall | 1.0-1.3 s | 4110 ms |
| A8 | `QueryBegin allocated` at q17's first fragment == at q16's first fragment | equal (within 1 MiB) | +6.60 GB |
| A9 | `WARN ... discarding every parked` | 0 lines (string removed) | 6 |
| A10 | `failed to release` / `could not retire` warnings | 0 | n/a |

### Arm B: smoke, default dispatch mode (`SIRIUS_CN_ASYNC_SENDER_DISPATCH` unset), ~3 min

Same script with the export removed: `q05 q06`. Checks: A1 for q06; A4; every `fragment run failed`
of q05's id after the first has `elapsed_ms <= 5` and an error containing "already retired"; no
`fragment run finished` for q05's id after the failure; `cancel_plan_fragment retired the query` lines
= 11 with `reason=INTERNAL_ERROR`.

### Arm C: full 22-query sweep, campaign order and configuration, ~16 min today (~4 min once fix 1 lands)

`bash capture-cn-fix2.sh 1 fix2-full 300 2 q01 ... q22`, then `results_table.py`, `compare.py`,
`cnlog_extract.py`, `headroom.txt`.

| # | check | must be |
|---|---|---|
| C1 | `base@start` on every row | <= 1 MiB (was 0.08-38.16 GB on 24 of 66 rows) |
| C2 | oracle compare set | identical to `compare-cn1.txt`: q02 q04 q06 q12 q13 q14 q20 q22 MATCH; the known FP64 diffs on q01 q03 q07 q10 q19 unchanged; q11 EMPTY; q15 flaky as before |
| C3 | warm medians of the 15 passing queries | within 6% of `results.md` (the campaign's run-to-run spread); q10/q12/q14 may improve by up to 0.18 s (C3 reservation-gate blocks tied to the leak; inference) |
| C4 | `cancels=` per run | unchanged (3, 13, 5, 4, 11, 2, 10, 11, 9, 6, 9, 4, 5, 3, 7, 5, 5, 7, 3, 8, 9, 6) |
| C5 | pass/fail set | unchanged: 15 pass, q05 q08 q09 q17 q18 q21 OOM, q16 translator |
| C6 | client start (`runs.csv start_utc`) to first `fragment run started`, every row | <= 100 ms on all 66 rows (was 720-3350 ms on the rows after a failure; the campaign's 11.9 s of stolen engine time is this gap summed). Not a sweep-wall criterion: with fix 1 in the build (INTEGRATION.md order) the sweep is ~745 s shorter for fix 1's reasons, which would swamp an 11.9 s comparison |
| C7 | `oom_resched` for the 15 passing queries | 0 (as today) |

### Arm D: 4-CN fault injection, ~8 min (needs all four GPUs; see decision 4)

`bash capture-cn-fix2.sh 4 fix2-cn4 300 1 q03 q16 q10` (q16 fails at translation on every CN that
hosts one of its receivers; its senders shuffle partsupp/supplier frames into peers' arenas first).

| # | check | must be |
|---|---|---|
| D1 | on every CN, `QueryBegin allocated` at q10.r0's first fragment | == the value at q03's last fragment (within 1 MiB) |
| D2 | for q16's id on every CN after its first `fragment run failed`/translation error | no `fragment run finished`; `skipping fragment of a retired query` or `released a remote frame for a retired receiver` or `already retired` refusals |
| D3 | `cancel_plan_fragment retired the query ... reason=INTERNAL_ERROR` | present on every CN with `released_leases` summing to the q16 frames that CN had staged (`received remote exchange frame` count for q16's receivers minus frames already consumed) |
| D4 | q03 and q10 results | MATCH the oracle (`compare.py`, `../../oracle/tpch_sf1000/`) |
| D5 | `no parked sender output` / `failed to release` / `skipped from frame seq` errors | 0 across the four CN logs |
| D6 | `QUERY_FINISHED` cancels of q03/q10 | every engine retire is the DEBUG "nothing parked" line, never a WARN |

## 9. Risks and how the reviewer should probe them

1. **Lease sweep placement.** Gate 1 must be inside `run_fragment_inner`, not at the dequeue. Probe: the
   GPU test's `staging_lease(1024) == 0` after a refused run that carried `remote_inputs`; read
   `engine.rs:462-478` and confirm the refusal returns through it.
2. **Refusing a run the FE still wants.** Only three events mark a query: its own engine `Err`, a CN
   `fail_fragment` for it, an FE cancel for it. Probe: `DefaultCoordinator.java:1046-1063` (a cancelled
   query cannot be cancelled twice and gets no new deploys), `:1022-1035` (QUERY_FINISHED/LIMIT_REACH only
   after eos). Confirm no other code path calls `mark`/`retire_query`.
3. **Gate 2 drops output of a fragment that ended after its query was retired.** That is the intent;
   the only failure mode is a bug in one of the three triggers, which would surface loudly as "dropped
   instead of parked" on the query's own id. Probe: the trigger enumeration above; the GPU test.
4. **`AlreadyTornDown` weakens exactly-once release.** Only slots in `torn_down` return `Ok` once; a live
   slot released twice stays a loud error. Probe: `second_release_of_a_live_slot_is_a_loud_error`,
   `release_after_retire_is_ok_exactly_once`, the unchanged double-drop assertion at
   `engine.rs:1200-1203`, and the transport tests `:2703-2757` staying green.
5. **Hot-path cost on the 15 passing queries.** Per fragment: three hash lookups; per query: N
   non-blocking sends and N `mark`s. Probe: C3/C4; confirm `engine_send` never `recv`s; confirm
   `retire_receiver` on a receiver `take_ready` already removed is a no-op plus one set insert.
6. **Bounded memories.** `RetiredQueries` 1024, exchange `retired` 1024, `torn_down` 4096: `const`s, not
   knobs; eviction forgets an explanation (a later drain sees the generic message) or lets a very late
   frame re-create a source (today's behaviour). Probe: the three bound tests; a 4-CN q07 (13 fragments)
   produces at most 13 entries per query.
7. **Registry refactor.** Mechanical move of `:369-425, :559-566, :625-639, :697-717`. Probe: the diff
   should show code moving, not changing; the two existing GPU tests unchanged.
8. **Inline-path recording is a behaviour change** (commit 2i): a default-mode sender failure now fails
   the query's result instances on this node with the real cause. Probe: `an_inline_sender_failure_is_recorded_at_query_level`;
   `SIRIUS_CN_TRANSLATE_ONLY` still returns OK for untranslatable fragments.
9. **Result-fragment arrival for a failed query keeps its contract** (gate 4's reserve branch). Probe:
   `:3376-3411` unchanged and the new arrival test.
10. **Both dispatch modes.** The campaign ran async on; the default is off. Probe: the mode-looped tests
    and arm B.
11. **CI clippy with `--no-default-features`.** The registry's engine-facing API is dead code there.
    Probe: the `cfg_attr(allow(dead_code))` and a local run of the clippy step.
12. **Phased-schedule dummy `(0,0)`.** `retire_receiver` is skipped for it; the query-level retire still
    runs. Probe: the dummy-instance test.
13. **Fire-and-forget ordering.** A `RetireQuery` queued behind a long `Run` of another query delays the
    drop (not the refusal) by that run; behind a `Run` of the *same* query it cannot happen (the run is
    refused). Probe: `retire_query_drops_parked_output_and_refuses_later_runs` relies on FIFO; the
    campaign's engine is idle after an `Err`, so the drop is immediate there.
14. **Mutex poisoning.** Every `retired.lock()` uses `unwrap_or_else(PoisonError::into_inner)` like
    `engine.rs:757, 839, 844`.

## 10. Landing order and dependencies on the other three fixes

- **No shared files with fix 1** (engine C++ `src/pipeline/gpu_pipeline_executor.cpp`). Code order is
  fix 1 then fix 2 (INTEGRATION.md section 3); the order of the SF1000 arms is free because fix 1's primary
  criterion is measured from the first OOM in the engine log, not from client start. The 0.7-6.4 s of
  leftover-fragment time (q17->q18 6.42 s) that this fix removes lands only in fix 1's secondary `runs.csv`
  wall bounds, which are re-measured on the fix 1+2 build in arm C (INTEGRATION.md V2c, the report's
  before/after row). Fix 1 moves the engine `Err` from ~+100 s to ~+3 s after the failing sender starts;
  which sibling senders had parked by then is decided by inbox order, not time, because the dispatch
  worker is one thread popping a FIFO (`compute_node_service.rs:380-391`, channel at `:259`), so A4's
  `q05 fragments=2 slots=2` still holds. Fix 1's ~600-character reason text becomes the `cause` in the
  retire WARN, the poisoned-slot message and `RetiredQueries` (1024 entries, < 1 MB worst case); nothing
  to coordinate.
- **Fix 3 (FE cardinalities)**: independent (FE + proto + `file_schema.rs`); different plan shapes change
  what parks where, not the bookkeeping.
- **Fix 4a (fragment fusion)** lands after this fix and rebases onto it (INTEGRATION.md 2.3, 3). The
  contract this fix must keep so that rebase is additive: (1) `release_staged` (4.4b) and
  `release_sources` (4.4g) iterate `SenderSource` **non-exhaustively** (`if let SenderSource::Remote {
  batches, .. }`), because fusion adds a third variant `LocalPlan` that holds no GPU memory and no lease;
  (2) `retire_receiver` (4.6) removes every `sources` entry of the cancelled receiver, which is how a
  deferred plan leaves the rendezvous (fusion ships no `forget_query`); (3) gate 3 in `run_ready_fragment`
  covers fused receivers unchanged (they arrive via `dispatch`); (4) gate 4 in `process_fragment` runs
  before fusion's hook on the inline and batch paths, but on the async path `exec_single_attachment`
  (`:705-717`) calls `try_dispatch_sender` (`:309-329`) first, so fusion carries its own `failure_of`
  check at the top of `try_defer_sender` (fusion spec 4.4); (5) this fix's tests keep using the
  UNPARTITIONED `data_stream_sink` fixtures (`:3806`, `propagation_chain :3311`), which fusion's `Leaf`
  default never fuses; any future test with a hash-partitioned single-destination local leaf must call
  `set_fragment_fusion(Off)`. Textual conflicts to expect: `cancel_plan_fragment` (this fix's version
  wins), `execute_ready_fragment` (fusion's fold goes first), `process_fragment`, `local_exchange.rs`,
  `DEMO.md:37-42`. **Fix 4b (spillable parked repositories)** must keep
  `Fragment` drop releasing HOST-tier batches too, or `retire` frees GPU but not host memory (note for
  4b's design; `src/exec/streaming_fragment.cpp:103-112`).
- **Carve plan**: this is CN-layer work (`experimental/starrocks` -> fork PR); against `dev` it sits after
  the C2a/C2b park/relay and C6b staged-lease PRs, and closes the carve plan's known limitation "a parked
  Fragment pinned for the process lifetime when a receiver never arrives". No pushes or PRs until asked
  (plan header).
- Base branch for the diff: `perf/profile-sf1000` (45dab3be). Nothing here depends on the three perf
  commits on that branch beyond `FragmentLabel` (which is in 45dab3be's parent chain via 784cf116).

## 11. Commits and PR

Two commits on `fix/parked-bookkeeping`, each Conventional Commits with the repo's trailer (matches
`git log` on the branch):

```
fix(cn): retire a failed query's parked output instead of wiping every query's

<body: what/why, ~8 lines, plain prose; name the measured leak numbers and the q16 case>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```
feat(cn): cancel_plan_fragment tears down the cancelled query on this CN

<body>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

Commit 1: `parked_registry.rs` (+ `mod parked_registry;` in `lib.rs`, not gated), `engine.rs` (registry,
`RetiredQueries`, gates 1 and 2, `RetireQuery`, `engine_send`, `retire_query`, four GPU tests),
`fragment_executor.rs` (trait method), `compute_node_service.rs` (`fail_fragment`, `release_staged`,
gates 3 and 4, `StagedLeases`, tests), `result_store.rs` (`failure_of`), comment fixes. Roughly 350
non-test + 500 test lines.

Commit 2: `compute_node_service.rs` (cancel teardown, late-frame refusal, inline recording, docstring,
tests), `local_exchange.rs` (`retire_receiver`, `is_retired`, bounded set, tests), `result_store.rs`
(`cancel_query`, test), `RetireTrigger::Cancel`, docs (`DEMO.md`, `benchmarks/tpch/README.md`,
`QUERY-DEVIATIONS.md`, runbook wording). Roughly 200 non-test + 300 test lines.

PR: one Draft against the branch the user names (see section 10), titled with commit 1's subject (the repo
squash-merges, `CONTRIBUTING.md:284-286`), body per the "PR reviewability" checklist
(`CONTRIBUTING.md:265-282`) and the user's unslop rule: motivation with the measured numbers, what
changed, a short plain testing note naming the CI trio, the GPU tests as GPU-only, and the SF1000 arms.
Commit 2 stands alone as a stacked PR if the reviewer asks (decision 1).

Effort: about two engineer-days plus one GPU session on GPU 1; SF1000 arms A+B+C ~26 min of box time
today (~14 min after fix 1), arm D ~8 min on all four GPUs.

## 12. Decisions that need the user

1. **Packaging.** One Draft PR with two commits (default here; ~550 non-test lines is at the edge of one
   sitting) or a stacked pair (commit 2 on commit 1, the carve plan's style). The code is identical.
2. **Cancel scope.** Default here (upstream, judge 1): every cancel reason retires parked output and
   rendezvous state, only failure reasons record a query-level failure. Alternative (surgical, judge 2):
   gate the whole teardown to `INTERNAL_ERROR`/`TIMEOUT`/`USER_CANCEL`/none, leaving the 15 passing
   queries' 264 `QUERY_FINISHED`/`LIMIT_REACH` cancels on today's path. The alternative is a one-line
   `if` and drops the theoretical post-eos race entirely, at the cost of not cleaning a finished query's
   rendezvous leftovers on N CNs (none are expected by construction). Bundled with this: commit 2's
   inline-path recording (4.4i) is a documented behaviour change on the default dispatch mode; say if it
   should wait for sender-failure forwarding instead.
3. **Gate 2 (park-time refusal, 4.2 item 5).** Default here: on. Judge 2 asked not to take it; judge 1
   did not object. Without it, a query cancelled while one of its fragments is inside `run()` parks that
   output with no owner-triggered release (the process-wide wipe that used to free it is gone). With it,
   the only failure mode is a trigger bug, which surfaces loudly on the query's own id.
4. **Arm D now or later.** The 4-CN fault-injection arm needs all four GPUs while fixes 1 and 4 hold
   GPUs 0 and 2 for their own tests; run it on the integration branch's final sweep unless the user
   wants the multi-CN cancel path proven before commit 2 is reviewed.
