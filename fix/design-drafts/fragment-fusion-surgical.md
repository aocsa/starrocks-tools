# Fix 4, surgical angle: fuse single-destination local leaf senders into their receiver at the TPlan level

Design for "make the six failing queries run on 1 CN", written 2026-09-03 against worktree
`/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be` (all `file:line` below are that tree; evidence
paths are relative to the session scratchpad `perf/sf1000/`). Every number is **measured** (with its file) or
marked **inference**.

## 0. Summary

One CN-side change, no engine, translator, FE, proto or wire change:

> When a **leaf** sender fragment (no `EXCHANGE_NODE` of its own) arrives with a `DATA_STREAM_SINK` whose
> partitioning is `HASH_PARTITIONED` and whose **only** destination is a receiver **on this CN** that is already
> registered and expects **exactly one** sender on that exchange, do not run the sender. Store its
> `TExecPlanFragmentParams` in the rendezvous as a new `SenderSource::LocalFused`. When the receiver's sender set
> completes, splice the sender's `TPlan.nodes` into the receiver's `TPlan` in place of the `EXCHANGE_NODE`, merge
> its scan ranges, drop the exchange from `per_exch_num_senders`, and translate/run the fused plan as one fragment.

The six failing fragments are exactly this shape (P1): a bare lineitem `FileScanNode` under a one-destination
`HASH_PARTITIONED` sink. A shuffle to one destination is a degenerate partitioning; the FE meant a partitioned
join, and on one node a partitioned join is a local join. After fusion the lineitem scan sits inside the join
fragment, the engine's own optimizer puts it on the probe side (it already does this today for streams, P5/q04),
and lineitem streams through the join instead of being parked whole. Broadcast (`UNPARTITIONED`) senders, merge
aggregations, sorted/limited exchanges and non-leaf senders are deliberately left exactly as they are today.

Predicted outcome at SF1000 / 1 CN / 100 GiB pool: **q05, q08, q17, q18 pass** (high confidence), **q09 passes
with little headroom** (medium), **q21 most likely still OOMs** (medium confidence; the reason and the number are
in section 6). Of the 15 passing queries, 11 are plan-for-plan identical; q02, q07, q13 and q20 lose 8 parked
leaves between them (q07: 59.5 GB less parked) and are expected to get faster. 4-CN behaviour is untouched
(a shuffle there has 4 destinations).

## 1. What is measured about the failure (facts relied on)

1. Six 1-CN failures, one shape. `survey/plan-summary.txt` lines 10, 16, 18, 34, 36, 42 and the dumps
   `cn1/dump/fragment-0141` (q05 node 8 -> dest 9), `0208` (q08 node 3 -> 4), `0220` (q09 node 3 -> 4), `0427`
   (q17 node 0 -> 1), `0437` (q18 node 9 -> 10), `0504` (q21 node 12 -> 13). My parse of those dumps
   (`fix/designs/all-dumps.txt`, produced by `fix/designs/parse_dumps.py`) shows for all six:
   `sink=T0` (DATA_STREAM_SINK), `optype=2` (= `HASH_PARTITIONED`, `Partitions.thrift:41-49`), `ndest=1`,
   `sender_id=0`, `output_exprs=None`, `output_columns=None`, a single `FILE_SCAN_NODE` (`T17/c0`) with
   conjuncts and `limit -1`, and `per_exch_num_senders={}` (a leaf).
2. The receiving exchange in each case expects one sender and is a plain exchange: `per_exch` entries are all
   `:1`, the exchange nodes are `T9/c0 ... lim-1/noconj ... nosort off=0 ptype=2` (q05 fragment-0137 node 9;
   q08 0206 node 4; q09 0214 node 4; q17 0426 node 1 and 4; q18 0433 node 10, 1, 4; q21 0500 node 13, 2, 9).
   The exchange's `row_tuples` equals its `input_row_tuples` equals the sender root's `row_tuples` in every
   case (e.g. q05: exchange `9:T9 rt[6] irt=[6]`, sender root `8:T17 rt[6]`).
3. The sender must park its whole projection because nothing streams across a fragment boundary (B1):
   `engine.rs:278-280` one request at a time; `run_fragment_inner` (`engine.rs:486-720`) does declare ->
   `build()` -> `relay_from` -> `run()` -> park; `sirius_ffi.cpp:739-745` `relay_from` requires the source to
   have run; `local_exchange.rs:248-283 take_ready` releases a receiver only when every sender is complete.
   Parked bytes at death 104.30 / 99.54 / 96.42 / 66.26 / 35.87 / 25.37 GB against a 107.4 GB cap (P1 table;
   `cn1-quent.txt` q05 `STREAMING_SINK(1) n=40 in=104.30GB` at line ~352, q17 `66.26GB` at ~1072, q18 `35.87GB`
   at ~1093, q21 `25.37GB` at ~1258).
4. Parked batches are invisible to the downgrade sweep (B2): `src/exec/streaming_fragment.cpp:103-112`
   ("Repositories escape data_repository_manager_ cleanup"); `src/downgrade/downgrade_executor.cpp:223-232`
   TIER 1 iterates registered managers only, `:294-300` TIER 2 the task queue; 17,917 "0 bytes freed" lines.
   `src/sirius_ffi.cpp:1062-1066 output_row_count` and `:822-825 export_packed` throw on non-GPU batches.
5. Standalone Sirius on the same GPU and pool runs all six (`results-failed.md`, `compare-standalone-failed.txt`
   all MATCH): warm 2786 / 3674 / 5427 / 3786 / 2691 / 6401 ms. It streams lineitem as the probe of hash joins
   built on the small side (`standalone-failed/runs/q05.explain.txt:50-61`; `standalone-failed-quent.txt`:
   q05 `GPU_SCAN(27) n=65 in=171.00GB`, q17 `HASH_JOIN(18) n=46 in=120.54GB`).
6. The engine already flips join sides inside a fragment from cardinalities it can see: q04 F00
   `HASH_JOIN (id=6) type: RIGHT_SEMI` with build = orders scan and probe = the 3.79B-row lineitem stream
   (`cn1/engine-cn0.log:48480`, query begun 13:03:38.958 at `:48424`), i.e. DuckDB's build/probe optimizer runs
   on the CN path (`duckdb/src/optimizer/build_probe_side_optimizer.cpp:160-212` `TryFlipJoinChildren`, swap
   when `right_side_build_cost > left_side_build_cost` at `:185-188`; SEMI/ANTI flipped to RIGHT_SEMI/RIGHT_ANTI
   at `:226-240`; `FlipChildren`/`InverseJoinType` at `:55-60`), and the Sirius planner accepts RIGHT_SEMI/
   RIGHT_ANTI (`src/planner/sirius_plan_comparison_join.cpp:242-246`). The FFI path disables only IN_CLAUSE,
   COMPRESSED_MATERIALIZATION, STATISTICS_PROPAGATION, COLUMN_LIFETIME, LATE_MATERIALIZATION
   (`src/sirius_ffi.cpp:233-240`); join ordering and build/probe selection stay on.
7. Receivers are dispatched before their senders. The FE deploys fragment groups root-first
   (`ExecutionDAG.java:169-200` javadoc: "All the upstream fragments of the fragments in a group must belong to
   the previous groups. Each group should be delivered sequentially"; `Deployer.java:208-215` `deployAsync` then
   `waitForDeploymentCompletion` per stage). Measured for q05: the first four `exec_plan_fragment` RPCs closed at
   13:03:43.358-43.373 with sub-ms busy time (receivers registering), the nation/region leaves ran at 43.375 and
   43.384, the lineitem leaf started at 43.434 (`cn1/cluster.log:4142-4202`). The CN's own module doc says the
   same: `local_exchange.rs:3` "StarRocks' receiver-first dispatch".

## 2. The CN path today, and where the change goes

```rust
// experimental/starrocks/src/compute_node_service.rs:705-717
fn exec_single_attachment(&self, protocol: Option<&str>, attachment: &[u8]) -> Result<(), String> {
    Self::ensure_binary_protocol(protocol)?;
    let params = Self::deserialize_binary::<TExecPlanFragmentParams>(attachment)?;
    if self.try_dispatch_sender(&params)? {          // async gate (SIRIUS_CN_ASYNC_SENDER_DISPATCH), :309-331
        return Ok(());
    }
    self.dispatch_then_join(self.core.process_fragment(&params)?)
}
```

```rust
// compute_node_service.rs:918-950  process_fragment
let expected_senders = Self::receiver_exchanges(&params)?;            // :1397-1435, one (node_id, n) per EXCHANGE_NODE
if !expected_senders.is_empty() {
    ...
    return Ok(FragmentOutcome::from_ready(
        self.exchanges.register_receiver(fragment_instance_id, expected_senders, params)?   // :943
            .into_iter().collect()));
}
let translated = self.translate_fragment_logged(&params, dump_seq)?;   // :949  <-- leaf senders translate here
self.execute_fragment(&params, translated)
```

```rust
// compute_node_service.rs:1020-1215  execute_fragment_with_inputs (sender half)
if sink.type_ != TDataSinkType::DATA_STREAM_SINK { return Err(...) }     // :1066-1071
let destinations = exec.destinations ... ;                               // :1093-1097
if destinations.len() > 1 { match stream_sink.output_partition.type_ { UNPARTITIONED | HASH_PARTITIONED ... } } // :1109-1131
let hash_keys = if destinations.len() > 1 { ... } else { Vec::new() };   // :1132  one destination = gather, keys ignored
let broadcast = destinations.len() > 1 && hash_keys.is_empty();          // :1140
for destination in destinations { ... let route = self.route_destination(destination)?; ... }   // :1147-1170, Local vs Remote by brpc address
self.run_labeled("sender", FragmentRun { plan: &translated, inputs, remote_inputs, outputs: slots.clone(), ... })?;  // :1175-1187  <-- the park
// Local destinations first: their rendezvous is immediate bookkeeping and fails fast.    // :1189
let ready = self.exchanges.push_sender(ExchangeKey { .. }, sender_id, SenderSource::LocalParked { names, slot })?;  // :1193
```

```rust
// compute_node_service.rs:1325-1385  execute_ready_fragment (receiver half)
let exchange_inputs = ready.inputs.iter().map(|input| { let names = input.sources.first()...names(); ... ExchangeInput { node_id, stream_view: sirius_stream_view_name(stream_id), names } })...;
let dump_seq = Self::dump_fragment(&ready.params);                      // :1360
let translated = self.translate_fragment_logged_with_inputs(&ready.params, &exchange_inputs, dump_seq)?;
for input in ready.inputs { for source in input.sources { match source { LocalParked { slot, .. } => slots.push(slot), Remote {..} => remote_inputs.push(..) } } }
self.execute_fragment_with_inputs(&ready.params, translated, inputs, remote_inputs)
```

```rust
// experimental/starrocks/src/local_exchange.rs:26-63
pub(crate) enum SenderSource {
    LocalParked { names: Vec<String>, slot: SenderSlot },
    Remote { names: Vec<String>, sender_id: i32, batches: Vec<StagedBatch>, closed: bool },
}
impl SenderSource { fn is_complete(&self) -> bool { match self { LocalParked{..} => true, Remote{closed,..} => *closed } } }
// :105-140 register_receiver(fragment_instance_id, expected_senders, params) -> Option<ReadyFragment>
// :142-155 push_sender(key, sender_id, source) -> Option<ReadyFragment>
// :248-310 take_ready: complete iff every expected exchange has `expected` complete sources; returns ReadyFragment { params, inputs }
```

```rust
// experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs:637-720  translate_exchange
let input = ctx.exchange_inputs.get(&node.node_id).ok_or(UnsupportedPlanNode { reason: "exchange node requires a bound same-node input stream" })?;
let mut schema = ctx.desc.named_struct_for_tuples(&exchange.input_row_tuples)?;
...
if input.names.len() != output_width { return Err(descriptor("row layout {:?} has {} fields but exchange input has {} names")) }  // :706
// :438-470 merge_exchange_overrides: a Merge AGGREGATION_NODE's next preorder node MUST be an EXCHANGE_NODE, else
//   "a merge aggregation must read its partial states directly from an exchange" (:461)
```

```rust
// crates/starrocks-plan-translator/src/scan_paths.rs:62-90  ScanFilePaths::from_fragment
for (node_id, ranges) in &exec_params.per_node_scan_ranges { paths.add_ranges(*node_id, ranges, desc)?; }
if let Some(per_driver) = exec_params.node_to_per_driver_seq_scan_ranges.as_ref() { ... }   // keyed by scan node id
```

```java
// FE planner/ExchangeNode.java:183-186
public final void computeTupleIds() { clearTupleIds(); tupleIds.addAll(getChild(0).getTupleIds()); nullableTupleIds.addAll(getChild(0).getNullableTupleIds()); }
```

The descriptor table is per query (StarRocks sends it once and references it as cached;
`resolve_descriptor_table`, `compute_node_service.rs:959-995`), so the receiver's resolved `desc_tbl` already
describes the sender's tuples and slots. Node ids are query-global. Those two facts are what make a TPlan-level
splice sound: slot references resolve identically before and after the splice.

## 3. Mechanism

### 3.1 Fusability predicate (pure, on thrift only)

`fusable_leaf_sender(sender: &TExecPlanFragmentParams) -> Result<Option<(ExchangeKey, i32 /*sender_id*/)>, String>`
returns `Some` iff all of:

- `SIRIUS_CN_FUSE_LOCAL_LEAVES` is not `0|false|off` (read once at bring-up, same pattern as
  `async_sender_dispatch_from_env`, `compute_node_service.rs:289-296`; `#[cfg(test)]` setter);
- `receiver_exchanges(sender)?.is_empty()` (a leaf: no `EXCHANGE_NODE` in its plan);
- the sink is `DATA_STREAM_SINK` with `stream_sink.limit` unset/negative and `output_columns` None/empty;
- `stream_sink.output_partition.type_ == TPartitionType::HASH_PARTITIONED`;
- `destinations.len() == 1` and `route_destination(&destinations[0])? == Local`;
- `fragment.output_exprs` is None/empty (the six leaves have none; a sender with output exprs would need a
  PROJECT splice, decline instead).

`fusable_receiver_exchange(receiver: &TExecPlanFragmentParams, node_id, sender_root: &TPlanNode) -> Result<(), &'static str>`
(evaluated under the rendezvous lock, on the registered receiver's params):

- an `EXCHANGE_NODE` with `node_id` exists, `num_children == 0`, `limit == -1`, no `conjuncts`,
  `exchange_node.sort_info.is_none()`, `offset.unwrap_or(0) == 0` (merging/limited exchanges stay streams);
- `exchange.row_tuples == sender_root.row_tuples` (the ExchangeNode.java:183-186 guarantee, checked rather
  than assumed);
- the exchange's parent in preorder is **not** an `AGGREGATION_NODE` (conservative superset of "merge
  aggregation": keeps `merge_exchange_overrides` (node_translator.rs:438-470) satisfied and leaves two-phase
  aggregation, which is cheap (AGG-5, 3-24 ms), exactly as today). Parent lookup is the standard preorder
  ancestor stack already used by `common_slots_consumed_above` (node_translator.rs:504-560); ~15 lines.

Anything else is a *decline* (fall back to today's run-and-park), never an error: fusion is an optimisation
with a complete fallback, so a wrong guess costs performance, not correctness.

### 3.2 Rendezvous: a third source kind

```rust
// local_exchange.rs (new variant + one method)
pub(crate) enum SenderSource {
    LocalParked { .. }, Remote { .. },
    /// A same-node leaf sender whose plan will be spliced into the receiver instead of running on its own.
    /// Holds thrift only (tens of KB); no GPU memory.
    LocalFused { params: Box<TExecPlanFragmentParams> },
}
// is_complete(): LocalFused => true.  names(): LocalFused => &[] (never consulted; see 3.4).

pub(crate) enum FuseOffer { Fused(Option<ReadyFragment>), Declined(&'static str) }

/// Offers a leaf sender for fusion. Under the lock: the receiver must be registered, expect exactly one
/// sender on `key.node_id`, have no source for it yet, and its exchange node must pass
/// `fusable_receiver_exchange`. On success records `LocalFused` and runs `take_ready`.
pub(crate) fn offer_fused_sender(&self, key: ExchangeKey, sender_id: i32, params: TExecPlanFragmentParams) -> Result<FuseOffer, String>
```

### 3.3 Sender arrival: two call sites, one helper

`ServiceCore::try_fuse_leaf_sender(&self, params) -> Result<Option<FragmentOutcome>, String>`:
`fusable_leaf_sender` -> `offer_fused_sender` -> on `Fused(ready)` log
`info!(receiver, exchange = node_id, sender, "fused single-destination local leaf sender into its receiver")`
and return `Some(FragmentOutcome::from_ready(ready.into_iter().collect()))`; on `Declined(reason)` log at
info (`"leaf fusion declined"`) and return `None`.

Called (a) in `try_dispatch_sender` (compute_node_service.rs:309-331) after the cheap
`receiver_exchanges(params).is_empty()` test and `resolve_descriptor_table`, before queueing: a fused leaf never
touches the dispatch worker and its RPC returns immediately, which is also what the async gate wanted for the
deploy-wave problem; (b) in `process_fragment` (:918-950) between the receiver branch and
`translate_fragment_logged` (:949), so the inline path (async off, or `exec_batch_plan_fragments`) gets it too.
The returned outcome flows through the existing `dispatch_then_join` (:351-378) / worker.

### 3.4 Receiver ready: splice before translation

In `execute_ready_fragment` (compute_node_service.rs:1325-1385), before building `exchange_inputs`:

```rust
let mut params = ready.params;
let mut streamed_inputs = Vec::new();
for input in ready.inputs {
    match input.sources.as_slice() {
        [SenderSource::LocalFused { params: sender }] => {
            params = fragment_fusion::splice_leaf(params, input.node_id, sender)?;   // 3.5
        }
        _ => streamed_inputs.push(input),
    }
}
// then exactly today's code over `streamed_inputs` and `&params` (dump_fragment(&params) so the
// fragment-NNNN.txt dump is the fused plan that pairs with plan-NNNN.substrait)
```

`translate_fragment_with_exchange_inputs` (lib.rs:247-300) then sees a plan whose remaining `EXCHANGE_NODE`s are
exactly the streamed inputs, emits a `ReadRel(LocalFiles)` + `Filter` for the spliced `FILE_SCAN_NODE` (as it does
for every leaf today, e.g. `cluster.log:4185-4192`), and `TranslatedPlan.stream_inputs` lists only the streamed
exchanges, so `run_fragment_inner` declares and relays only those (`engine.rs:509-590, 618-645`).

### 3.5 The splice (pure function, new file `experimental/starrocks/src/fragment_fusion.rs`)

```rust
pub(crate) fn splice_leaf(mut receiver: TExecPlanFragmentParams, node_id: i32, sender: &TExecPlanFragmentParams)
    -> Result<TExecPlanFragmentParams, String>
{
    let plan = receiver.fragment.as_mut()?.plan.as_mut()?;
    let pos = plan.nodes.iter().position(|n| n.node_id == node_id && n.node_type == TPlanNodeType::EXCHANGE_NODE)?;
    let leaf = &sender.fragment.as_ref()?.plan.as_ref()?.nodes;
    // The exchange is a preorder leaf (num_children == 0) and `leaf` is a complete preorder subtree rooted at
    // leaf[0]; replacing one leaf entry by a subtree keeps every ancestor's `num_children` valid.
    plan.nodes.splice(pos..=pos, leaf.iter().cloned());

    let exec = receiver.params.as_mut()?; let sexec = sender.params.as_ref()?;
    exec.per_exch_num_senders.remove(&node_id);
    for (nid, ranges) in &sexec.per_node_scan_ranges {
        if exec.per_node_scan_ranges.insert(*nid, ranges.clone()).is_some() { return Err(format!("scan node {nid} already present in receiver")); }
    }
    if let Some(per_driver) = &sexec.node_to_per_driver_seq_scan_ranges { /* same merge into exec.node_to_per_driver_seq_scan_ranges */ }
    Ok(receiver)
}
```

Both maps are merged because `ScanFilePaths::from_fragment` reads both (`scan_paths.rs:70-90`) and enforces that a
node appears in exactly one; node ids are query-global so keys cannot collide with the receiver's own scans
(q21 F00 has its own `FileScanNode 0`).

### 3.6 What does not change

Engine (`src/`), translator crate, FE, protos, wire format, `engine.rs`, the remote (nixl) path,
`cancel_plan_fragment`, `results.fail_query`. A failed fused receiver is reported exactly as any receiver failure
today (`run_ready_fragment`, `compute_node_service.rs:864-916`). `SIRIUS_CN_TRANSLATE_ONLY` survey mode returns
before any of this (`:927-934`), so dumps taken in survey mode are unchanged.

## 4. Behaviour under the measured SF1000 q05 case (walk-through)

Plan (`survey/explain/q05.costs.txt`, dumps 0134-0147): F18 result <- 30 GATHER; F17 merge agg <- 27 SHUFFLE;
F14 = join 24 (23 BROADCAST region) over join 19 (18 SHUFFLE nation, 16 SHUFFLE <- F08); F08 = join 14 (13
BROADCAST supplier) over join 10 (9 SHUFFLE <- F06 lineitem, 7 SHUFFLE <- F04); F04 = join 5 (4 SHUFFLE <- F02
orders, 1 SHUFFLE <- F00 customer).

| arrival | fragment | today | with fusion |
|---|---|---|---|
| 43.358-43.373 | F18 F17 F14 F08 F04 (receivers) | register | register (unchanged) |
| 43.375 | F12 nation -> 18 (SHUFFLE, 1 dest, parent join 19) | run 6 ms, park | **fused into F14** |
| 43.384 | F15 region -> 23 (BROADCAST) | run 9 ms, park | run 9 ms, park (unchanged) |
| 43.4xx | F09 supplier -> 13 (BROADCAST) | run, park 0.08 GB | unchanged |
| 43.434 | F06 lineitem -> 9 (SHUFFLE) | run: parks 104.30 GB, OOM at +1.36 s, dies at +101 s | **fused into F08**, nothing runs |
| 43.4xx | F00 customer -> 1, F02 orders -> 4 (SHUFFLE) | run, park 1.24 + 2.79 GB | **fused into F04**; F04 now ready |
| | F04 fused: customer scan JOIN orders scan | (join of two streams, 73 ms) | one fragment: build customer (est 1.5e8), probe orders (est 1.5e9 x 0.2); parks ~2.7 GB to 7 (227,571,151 rows x ~12 B; rows measured `card-compare-cn1.txt:24`) |
| | F08 fused: lineitem scan JOIN stream 7 JOIN stream 13 | never ran | build stream 7 (227.6M exact, declared) and stream 13 (10M exact); **probe lineitem, streamed** (est 6e9 x 0.2 vs 2.3e8: no side flips it onto build) |
| | F14 fused (nation inline, 16 and 23 streams), F17, F18 | | as today |

Engine runs: 7 instead of 11. Peak GPU (inference): stream 7 ~2.7 GB + its hash table (~4 GB) + supplier
0.08 GB + 4 lineitem batches in flight (4 x 2.67 GB, `engine-cn0.log` batch size) + join outputs, well under
30 GB, against 104.3 GB parked today.

Why lineitem lands on the probe side: in the fused F08 both sides of join 10 have cardinalities DuckDB can see;
stream 7 is declared exactly (`engine.rs:555-590`, "declared input stream cardinality"), the inline scan
carries the parquet footer count (~6.0e9) times the flat 0.2 the FFI path applies per conjunct (C2), so
`TryFlipJoinChildren` (build_probe_side_optimizer.cpp:185-188) keeps the smaller side as build. This is the same
mechanism that produced the q04 RIGHT_SEMI flip (`engine-cn0.log:48480`). The FE's `PARTITIONED` label put
lineitem on the build side; the fused plan ignores that label, which is the point.

## 5. Behaviour under the 15 passing queries

Applying the predicate to `survey/plan-summary.txt` (1-CN plans) and the dumps: an exchange fuses iff it is
`SHUFFLE`, fed by a leaf, single sender, and its parent is not an aggregation.

| query | fusable exchanges | effect |
|---|---|---|
| q01 q03 q04 q06 q10 q11 q12 q14 q15 q19 q22 | none (broadcasts, gathers, or shuffles feeding merge aggregations) | plan-for-plan identical to today; q04 keeps its exact-cardinality RIGHT_SEMI flip on the parked stream |
| q02 | 6 part, 8 supplier (-> join 9); 28 partsupp, 30 region (-> join 31) | 13 -> 9 engine runs; the two partsupp shuffles (38.5 GB moved at 4 CNs, 800M rows parked at 1 CN) stop being parked; q02 is the 2.18x-slower fragment-heavy query, expected to improve |
| q07 | 1 supplier, 3 nation (-> join 4); 8 lineitem (-> join 9) | the 59.47 GB lineitem parking (B1, `cluster.log:6807-7005`) disappears; join 9 builds on stream 6 (799,746 rows exact) and probes lineitem inline |
| q13 | 2 orders, 4 customer (-> join 5 RIGHT OUTER) | 18.18 GB orders parking disappears; build customer (1.5e8) / probe orders (1.5e9 x 0.2) is today's side choice |
| q20 | 12 partsupp, 15 part (-> join 16 LEFT SEMI) | 800M-row partsupp parking disappears; build part (2e8 x 0.2) / probe partsupp (8e8), as today |

Risk in this table: for the four changed queries the receiver plans with DuckDB's estimate for the fused leaf
instead of the exact parked cardinality (C2). The relative sizes above are all at least 5x apart, so the side
choice is not expected to move; the acceptance arm (section 8) checks the four explicitly.

## 6. Which of the six each option rescues, and at what cost

Fusable exchanges per failing query (from the dumps; "(parked)" = stays a stream as today):

- q05: 1 customer, 4 orders -> F04; 9 lineitem -> F08; 18 nation -> F14. Parked: 7 (2.7 GB), 13 supplier, 16, 23, 27, 30.
- q08: 2 part, 4 lineitem -> F04. Parked (BROADCAST): 8 supplier 0.08 GB, 12 orders (~455M rows after the
  1995-1996 filter, ~7 GB), 16 customer 1.2 GB, 20/24 nation, 29 region; 33 -> merge agg.
- q09: 2 part, 4 lineitem -> F04. Parked: 8 supplier, 12 partsupp (800M x ~16 B ~ 13 GB), 16 orders
  (1.5e9 unfiltered x ~12 B ~ 18 GB), 20 nation; 24 -> merge agg.
- q17: 1 lineitem, 4 part -> F04. Parked: 9 (partial agg over lineitem -> merge agg 10; ~200M groups, ~4 GB).
- q18: 1 orders, 10 lineitem -> F03; 15 customer -> F09. Parked: 4 (partial agg by l_orderkey -> merge agg 5,
  **24.0 GB measured**, `cn1-quent.txt` q18 `MERGE_GROUP_BY(4) in=24.00GB`), 13 (F03 output, tiny).
- q21: 2 l3, 13 l2 -> F07. Parked: 9 = F03 output (l1 JOIN orders, non-leaf: `PARTITION(6) in=46.47GB`,
  `cn1-quent.txt` q21 fragment ...acb; sink ~1.85e9 rows x 12 B ~ 22 GB after its projection), 6 orders
  broadcast 5.94 GB, 16 (F07 output), 21 nation, 25 -> merge agg.

Predictions (all inference from the measured sizes above and the standalone runs):

| query | today (1 CN) | predicted with fusion | peak GPU estimate | confidence |
|---|---|---|---|---|
| q05 | OOM, 101.5 s | PASS, ~3.2-4.0 s warm (standalone 2.79; +F04 join fragment 0.7-1.0 s, +P4 head 0.12-0.27 s) | < 30 GB | high |
| q08 | OOM, 168.3 s | PASS, ~4-5 s (standalone 3.67; lineitem 195.75 GB scan ~3 s wall at the measured 12.2 s summed / 4 slots) | ~10 GB parked + hash tables on orders/customer ~ 25-35 GB | high |
| q09 | OOM, 232.5 s | PASS, ~6-7.5 s (standalone 5.43) | ~31 GB parked + a 1.5e9-row orders hash build (standalone shows the same: `HASH_JOIN(31) in=36.60GB`) + partsupp build ~ 70-90 GB | medium (least headroom of the five) |
| q17 | OOM, 103.9 s | PASS, ~7-10 s (standalone 3.79; the FE pre-aggregates all of lineitem in F05 first: Comp 24.3 s summed, ~6 s wall on 4 slots, then the fused F04 scans lineitem again ~3 s) | 4 GB parked + part build + lineitem streamed ~ 20 GB | high |
| q18 | OOM, 54.2 s | PASS, ~4.5-6.5 s (standalone 2.69; F02 partial agg ~2 s wall, fused F03 ~2-3 s) | 24 GB parked + merge-agg state (~36 GB, standalone `PARTITION(5) 36.19 GB`) + orders in flight ~ 65 GB | high |
| q21 | OOM, 114.5 s | **likely still OOM** (dies in ~3-5 s once fix 1 lands; 114 s without it) | see below | medium |

q21 reasoning. Fusing l3 (exchange 2) removes the 77.76 GB parking (`cn1-quent.txt` q21 fragment ...acd
`PROJECTION(2)/STREAMING_SINK(3) in=77.76GB`) and fusing l2 (exchange 13) removes the 73.5 GB projection that
died. The fused F07 is `LEFT SEMI(build = l2 scan, probe = RIGHT ANTI(build = stream 9, probe = l3 scan))`.
The semi flips correctly (build cost of l2, 6e9 x 12 B, exceeds the anti output's), so l2 streams. The anti is
the problem: DuckDB compares build cost of stream 9 (~1.85e9 exact rows x 12 B) with the l3 scan estimate
(6.0e9 x 0.2 = 1.2e9 x 12 B) and flips to ANTI with the l3 scan as build (build_probe_side_optimizer.cpp:185-188,
226-240). The actual l3 side after `l_receiptdate > l_commitdate` is ~3.8e9 rows (63%, the 77.76 GB above):
~45 GB of build rows plus a hash table of that order, plus the ~22 GB parked stream 9, exceeds 100 GiB. If the
flip does not happen (the estimate lands above 1.85e9), the build is stream 9 (~22 GB + ~30 GB table) and q21
passes at ~60-70 GB. Standalone passes because its planner joins supplier (nation = SAUDI ARABIA, 1/25 of
suppliers) below the semi/anti (`standalone-failed/runs/q21.explain.txt` RIGHT_DELIM_JOIN RIGHT_ANTI /
RIGHT_SEMI), which the FE plan does above them (F00, join 17/22). No CN-side fusion reproduces that; q21 needs
either real FE cardinalities (fix 3) so the FE reorders, or spill (option 4b, ~195 GB total per B2). The
acceptance run records which way it goes; nothing in this design depends on the answer.

Recursive fusion (also absorbing non-leaf single-destination senders such as q21 F03) was considered and
rejected for this angle: it needs an exchange-key alias map in `LocalExchange` (F04's destination is F03's
instance id) and does not fix q21's build choice either, since the supplier join still sits in F00.
Relaxing rule "HASH_PARTITIONED only" to all single-destination leaves is a one-line change; it would remove
q09's 31 GB of broadcast parking at the price of estimated instead of exact cardinalities for those dimension
streams, and is left as a follow-up experiment.

## 7. Files to touch

| file | change | size |
|---|---|---|
| `experimental/starrocks/src/local_exchange.rs` | `SenderSource::LocalFused`, `FuseOffer`, `offer_fused_sender` (calls the shape check on the stored receiver params), `is_complete`/`names` arms; module doc sentence | ~60 lines |
| `experimental/starrocks/src/fragment_fusion.rs` (new) | `fusable_leaf_sender`, `fusable_receiver_exchange`, `splice_leaf`, env gate reader; pure thrift, no I/O | ~150 lines |
| `experimental/starrocks/src/compute_node_service.rs` | `try_fuse_leaf_sender` helper; two call sites (`try_dispatch_sender` :309-331, `process_fragment` :949); `execute_ready_fragment` :1325-1385 partitions fused vs streamed inputs and dumps the fused params; `ServiceCore` gets the gate flag next to `async_sender_dispatch` | ~60 lines |
| `experimental/starrocks/src/lib.rs` | `mod fragment_fusion;` | 1 line |
| `experimental/starrocks/docs/TUNABLES.md` | `SIRIUS_CN_FUSE_LOCAL_LEAVES` (default on; `0` restores run-and-park) | ~10 lines |
| `perf/sf1000/cnlog_extract.py` (tooling, not shipped) | `frags=` counts drop for fused queries; count the new "fused ... leaf sender" line | optional |

Carve-plan layer: CN only (`experimental/starrocks`) -> fork PR. No `src/` (dev PR) and no FE patch.

## 8. Tests

Component/unit (pure Rust, no GPU; `pixi run cn-test-no-engine` = `cargo test -p sirius-starrocks-cn
--no-default-features`, `experimental/starrocks/pixi.toml:148`, what CI runs; minutes):

`fragment_fusion.rs`
1. `splices_leaf_in_place_of_exchange`: receiver preorder `[HASH_JOIN(c2), EXCHANGE(7), FILE_SCAN(1)]`, sender
   `[PROJECT(c1), FILE_SCAN(0)]` -> `[HASH_JOIN, PROJECT, FILE_SCAN(0), FILE_SCAN(1)]`; `per_exch_num_senders`
   loses 7; `per_node_scan_ranges` has 0 (from the sender) and 1 (receiver's own).
2. `declines_sorted_limited_or_filtered_exchange`, `declines_exchange_under_aggregation`,
   `declines_row_tuple_mismatch`, `declines_sender_with_output_exprs`, `declines_unpartitioned_sink`,
   `declines_multi_destination`, `duplicate_scan_node_is_an_error`.

`local_exchange.rs`
3. `offer_fused_sender_completes_the_set`: receiver expecting `{7: 1}`; offer -> `Fused(Some(ready))`;
   `ready.inputs[0].sources == [LocalFused]`.
4. `offer_declines_when_receiver_not_registered`, `offer_declines_when_exchange_expects_two_senders`,
   `mixed_fused_and_parked_inputs_become_ready_together`.

`compute_node_service.rs` (existing fixtures: `exchange_plan` :3960, `scan_plan`, `data_stream_sink` :3806 --
add a `hash_partitioned_data_stream_sink` variant -- `local_destination` :2018, `exec_params` :3835,
`CountingExecutor` :1810; add a `RecordingRunExecutor` capturing `FragmentRun.inputs.len()` and
`plan.stream_inputs.len()`)
5. `hash_partitioned_single_destination_leaf_fuses_into_its_receiver`: register receiver (exchange 7 -> result
   sink, `per_exch_num_senders {7: 1}`), then the leaf; executor called **once**, with `inputs.is_empty()` and
   `plan.stream_inputs.is_empty()`; `fetch_data` returns rows.
6. `broadcast_leaf_still_parks` (UNPARTITIONED sink -> two runs, second with one input): rule (ii) holds.
7. `leaf_arriving_before_its_receiver_falls_back_to_parking` (two runs; log says declined).
8. `fusion_also_applies_on_the_async_sender_path` (`set_async_sender_dispatch(true)`).
9. `partial_fusion_keeps_the_other_exchange_as_a_stream` (exchanges 7 fusable, 8 broadcast: one run of the
   receiver with `inputs == [(8, ..)]`, `stream_inputs.len() == 1`).
10. `gate_off_restores_run_and_park` (`set_fuse_local_leaves(false)` -> two runs).

Translator (`crates/starrocks-plan-translator/tests/translate.rs`, fixture `params_with_scan_range` :503-538):
11. `spliced_params_translate_to_a_local_files_scan_with_no_stream_inputs`: run `splice_leaf` output through
    `PlanTranslator::translate_fragment`; assert `stream_inputs.is_empty()` and a `LocalFiles` ReadRel.

SF1000 acceptance (orchestrator-run, 1 CN, `capture-cn.sh` configuration, gate on; ~5 min of queries):
- q05 q08 q09 q17 q18 q21, cold + 2 warm: expected PASS + oracle MATCH for q05 q08 q09 q17 q18; record q21
  (pass, or OOM with the time-to-fail). Report times against standalone 2786 / 3674 / 5427 / 3786 / 2691 / 6401.
- CN log: `fused single-destination local leaf sender` counts per query = q05 4, q08 2, q09 2, q17 2, q18 3,
  q21 2; `leaf fusion declined` = 0 for these; `fragment run started` count per query drops accordingly.
- `[gpu_pool] QueryEnd allocated` after each of the five returns to the pre-query baseline (no leftover; this
  is also fix 2's territory, but a passing query leaves nothing behind today either).
- The 15 passing queries, same order as the campaign: compare set unchanged (`compare.py`: q02 q04 q06 q12
  q13 q14 q20 q22 MATCH; q01 q03 q07 q19 q10 q15 the known decimal-lowering diffs; q11 EMPTY), warm medians
  within noise for the 11 unchanged queries, q02/q07/q13/q20 within noise or faster, q07's peak parked bytes
  < 25 GB (was 79.1 GB). A/B against the same binary with `SIRIUS_CN_FUSE_LOCAL_LEAVES=0` isolates the change.

## 9. Predicted effect (numbers)

- Sweep time: the six failures cost 774.9 s of the 910.3 s 1-CN sweep (B3). With five passing at 4-10 s each
  (~35 s total incl. cold runs' extra) and q21 either passing (~8-10 s) or dying fast with fix 1 (~5 s), the
  sweep drops to ~180-200 s; without fix 1 and with q21 still failing, ~300 s.
- Coverage: 15/22 -> 20/22 supported on 1 CN at SF1000 (21/22 if q21 passes; q16 is the translator gap).
- Memory: the six fragments that parked 66-104 GB each park nothing; q07 -59.5 GB, q13 -18.2 GB, q20 ~-12 GB,
  q02 ~-30 GB of transient parking.
- Engine runs per query: q05 11 -> 7, q08 11 -> 9, q09 9 -> 7, q17 5 -> 3, q18 7 -> 4, q21 9 -> 7, q02 13 -> 9,
  q07 10 -> 7, q13 5 -> 3, q20 8 -> 6 (each run costs a `Fragment` build/plan cycle and a rendezvous hop;
  q02-class queries lose ~100-300 ms of fixed cost, inference from MFS-4's 90 ms gaps + per-fragment plan time).

## 10. Risks

1. **Estimated instead of exact cardinality for fused leaves** (the q21 mechanism above). Bounded to shuffle
   leaves under non-aggregation parents; the only way it hurts is a wrong build side, which is a memory/time
   regression not a wrong answer. Mitigation: the gate, the A/B arm, and the per-query fused-count check.
2. **Order dependence**: fusion needs the receiver registered before the leaf arrives. Guaranteed by the FE's
   default stage-by-stage deploy (section 1 item 7) and measured for q05. Under `enable_single_node_schedule`
   (`DefaultCoordinator.java:697-704`, `Deployer.java:359-390` deploys everything at once) RPCs can race; the
   design then *declines* and behaves exactly as today (logged). An order-independent variant (hold the leaf's
   params under the exchange key, decide at `register_receiver`) is ~30 more lines and can follow if that
   scheduler is ever enabled.
3. **Translator invariants on the spliced plan**: `merge_exchange_overrides` (protected by the aggregation-parent
   decline), `common_slots_consumed_above`'s preorder ancestor stack (preserved by a leaf-for-subtree splice),
   `refuse_carried_join_child` (a spliced scan/project carries nothing a stream would not; both come from the
   same FE descriptor row). Test 11 exercises the translator on a spliced plan.
4. **Dumps/tooling**: `fragment-NNNN.txt` for a fused receiver is the fused plan (deliberate, pairs with the
   substrait); `cnlog_extract.py`'s `frags=` counts shrink. Note it in the report.
5. **Failure attribution**: a fused receiver's failure is attributed to the receiver's instance id (as any
   receiver failure); the absorbed leaf's id never appears in `fragment run started/finished`. Cancel is
   unaffected (`cancel_plan_fragment` is result-store only today, `compute_node_service.rs:449-476`).
6. **4 CNs**: unaffected by construction (`destinations.len() > 1`, or a remote route, declines).
7. **Nothing spills**: this design does not make the six robust to larger SFs or smaller pools; it removes one
   plan shape's need to park. q09 at 70-90 GB predicted peak is the canary.

## 11. Effort

~1 engineer-day for code + unit/component tests (~270 lines of code, ~300 of tests), 0.5 day for the SF1000
arm and the report row. Review surface: one new 150-line pure module, a 60-line rendezvous extension, ~60 lines
of call-site changes; everything else is unchanged.

## 12. Dependencies and interactions with the other three fixes

- **None required.** Builds and tests independently on `perf/profile-sf1000`.
- **Fix 1 (fail fast)**: complementary; makes the q21 residual (if any) cost ~3-5 s instead of 114 s.
- **Fix 2 (parked bookkeeping)**: `LocalFused` sources live in `LocalExchange.sources` next to `LocalParked`,
  so whatever by-query removal fix 2 adds to `LocalExchange` should drop them too (thrift only, no GPU memory;
  one extra match arm). Land order does not matter.
- **Fix 3 (real FILES() cardinalities)**: complementary. If the FE then keeps lineitem in the join's own
  fragment there is no exchange to fuse and both paths agree; if the broadcast memory penalty still routes
  lineitem through a single-destination shuffle (the report's skeptic caveat), fusion still applies. Fix 3 is
  also the most plausible route to q21 (supplier/nation join pushed below the semi/anti by the FE).
- **Option 4b (spill)**: independent; the two compose. Spill is the route to q21 and to headroom at larger SFs.

## 13. Open questions the acceptance run answers

1. Does q21's anti join flip (section 6)? Read the fused F07 plan in `engine-cn0.log` (`HASH_JOIN ... type:`)
   and the `[gpu_pool]` peak.
2. q09's real peak with orders (18 GB) and partsupp (13 GB) parked as broadcast streams plus their hash builds.
3. Whether the four changed passing queries (q02 q07 q13 q20) keep their build sides (compare `HASH_JOIN`
   types and `Wired/Not wiring dynamic filter` lines before/after; fused inline scans with conjuncts should
   now *get* dynamic filters, `sirius_plan_comparison_join.cpp:646-664`, which parked streams never did).
