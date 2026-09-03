# Implementation spec: same-node fragment fusion (fix 4, "make the six run on 1 CN")

Written 2026-09-03 for an engineer implementing in the worktree
`/home/prestouser/aocsa/sirius-stacks-wt/fix-fragment-fusion` (branch `fix/fragment-fusion`, off
`perf/profile-sf1000` = `45dab3be`; engine and CN already built there, see `fix/prep-worktrees.log`).
Every `file:line` below was read from that tree at `45dab3be`. "Measured" means copied from a named
file under the evidence bundle `scratchpad/perf/sf1000/` (abbreviated `perf/`); everything else is
marked inference. This spec is the judges' merge: the **surgical** rule as the shipped default, the
**upstream** module shape, refusal enum, `LocalPlan { params, inputs }` carrier and acceptance
discipline, and the **robust** three-valued knob and skip-reason logging (robust's `forget_query` was
dropped at integration: fix 2 owns cancellation, see 4.6 and INTEGRATION.md).

Do not modify anything under `src/` (engine), `experimental/starrocks/patches/`, the FE, or the
protos. This fix is Rust-only: the translator crate and the CN.

---

## 1. Goal and non-goals

### Goal

On one CN, when a data-stream sender fragment's **only** destination is a receiver **on this CN**
whose `EXCHANGE_NODE` expects **exactly one** sender, do not run the sender. Park its *plan* in the
rendezvous instead of its *rows*; when the receiver becomes ready, splice the sender's `TPlan.nodes`
in place of the receiver's `EXCHANGE_NODE`, merge the exec params the translator reads, and run the
fused fragment as one plan. The engine's DuckDB optimizer, which already runs on every fragment
(`src/sirius_ffi.cpp:134-138`, with `JOIN_ORDER` and `BUILD_SIDE_PROBE_SIDE` enabled: the disabled set
at `:233-243` is `IN_CLAUSE, COMPRESSED_MATERIALIZATION, STATISTICS_PROPAGATION, COLUMN_LIFETIME,
LATE_MATERIALIZATION`), then sees the lineitem scan as an ordinary `ReadRel` inside the join fragment
and puts it on the probe side, so lineitem streams through the join instead of being parked whole.

Measured target (perf/cn1-cnlog.txt, perf/cn1/dump): q05 q08 q09 q17 q18 q21 die on 1 CN at SF1000
with a 100 GiB pool after 54-232 s of OOM reschedules (775 s of a 910 s sweep); each is a bare
lineitem `FILE_SCAN_NODE` alone in a fragment whose `DATA_STREAM_SINK` is `HASH_PARTITIONED` with one
local destination (`fragment-0141.txt` node 8 -> exchange 9; `0208` 3 -> 4; `0220` 3 -> 4; `0427`
0 -> 1; `0437` 9 -> 10; `0504` 12 -> 13). Standalone Sirius on the same GPU and pool runs all six in
2.7-6.5 s warm and matches the oracle exactly (perf/compare-standalone-failed.txt, perf/results-failed.md).

### Shipped in this PR (PR 1)

- Fusion of **leaf** senders (no `EXCHANGE_NODE` of their own) with a `HASH_PARTITIONED`
  single-destination local sink into a plain exchange whose parent is not an aggregation. Knob
  `SIRIUS_CN_FRAGMENT_FUSION = off | leaf | leaf-any`, default `leaf`.
- Complete fallback: every decline is today's run-and-park path. No decision is taken after a
  sender has been deferred that could fail the query for a shape that passes today.
- Predicted effect (inference, sections 8-9): q05 q08 q17 q18 pass (high confidence), q09 passes
  with little headroom (medium), **q21 most likely still OOMs** (medium; the leaf rule keeps the
  F03 stream declared exactly while l3 becomes an estimate, which is the mixed information set
  that makes DuckDB build on the 3.8e9-row side). The q21 outcome is recorded, not required, by
  this PR; PR 2 (`all` mode) is the follow-up that gives q21 a real chance.

### Non-goals (explicitly out of this PR)

- Middle-fragment fusion (`all` mode; q21 F03, q09's broadcast parking): PR 2, stacked on this
  one. This PR's data structures (`LocalPlan { params, inputs }`, the recursive fold) are shaped
  so PR 2 adds a call site and a knob value, not a redesign (section 4.9).
- Fusing merge-fed exchanges (two-phase aggregation boundaries), merging exchanges
  (`sort_info`), limited/offset/filtered exchanges, multi-sender exchanges, remote destinations.
- Anything on 4 CNs: every shuffle/gather receiver there expects 4 senders and every broadcast
  sink has 4 destinations (`perf/survey/plan-summary-4cn.txt`), so nothing fuses by construction.
- Spillable parked repositories (plan option 4b), streaming receivers, engine changes, the
  `RIGHT_SEMI_JOIN` translator arm (fix 3 companion; not needed here, see section 3.4).
- Fixing the pre-existing ~1e-3 decimal-lowering deviation on revenue sums (perf/compare-cn1.txt
  q01/q03/q07/q19); the acceptance criteria account for it (section 8).

---

## 2. Mechanism today (verified anchors)

| step | where | what it does |
|---|---|---|
| RPC arrival | `experimental/starrocks/src/compute_node_service.rs:705-717 exec_single_attachment` | deserializes, calls `try_dispatch_sender` (`:309-329`, async gate, off by default) then `process_fragment` (`:918-959`) |
| batch RPC | `:1624-1679 translate_batch_attachment` | same two calls per instance, in FE order |
| receiver registration | `process_fragment :934-949` -> `local_exchange.rs:105-139 register_receiver` | a fragment with any `EXCHANGE_NODE` (`receiver_exchanges :1397-1431` reads `per_exch_num_senders`) is stored as `PendingReceiver { params, expected_senders }` with its descriptor table already resolved (`resolve_descriptor_table :954-988` runs first at `:922`) |
| leaf sender run | `process_fragment :958-959` -> `execute_fragment_with_inputs :1020-1246` | translates, runs (`run_labeled("sender") :1176-1187`), parks output on the GPU, then `push_sender(.., SenderSource::LocalParked{names, slot}) :1189-1206` for each local destination |
| one destination is a gather | `:1104-1111` | "one destination is a gather regardless of the partition label"; `hash_keys` empty when `destinations.len() == 1` (`:1132-1139`). This is why a single-destination `HASH_PARTITIONED` sink is the identity on rows |
| sink checks | `:1079-1094` | `stream_sink.limit >= 0` refused; non-identity `output_columns` refused |
| routing | `:1251-1275 route_destination` | `Local` iff `brpc_server` matches this CN's `ExchangeIdentity` (`:61-86`) |
| readiness | `local_exchange.rs:248-313 take_ready` | receiver released only when every exchange has `expected` complete sources; `is_complete` (`:59-64`) is `true` for `LocalParked` |
| receiver run | `compute_node_service.rs:1325-1394 execute_ready_fragment` | builds one `ExchangeInput { node_id, stream_view, names }` per exchange (`:1329-1356`, calls `source.names()`), re-dumps params (`:1358-1360`), translates with inputs (`:1360-1362`), runs with parked slots / staged batches |
| engine | `engine.rs:486-729 run_fragment_inner` | declares only `request.stream_inputs` (`:508-549`), declares the **exact** parked row count per stream (`:551-587`), relays parked senders by `SenderSlot` key (`:616-647`, `parked_slots.get(slot)`), runs, parks |
| translator: exchange -> stream read | `crates/starrocks-plan-translator/src/node_translator.rs:637-763 translate_exchange` | requires a bound `ExchangeInput` (`:656-663`), pushes a `StreamInputSchema` (`:729-733`), applies `sort_info` (`:746-761`) and `apply_conjuncts` (`:762`); `apply_fetch` (`:258-297`) honours `limit`/`offset` |
| translator pre-passes | `node_translator.rs:390-428 translate_plan`, `:438-483 merge_exchange_overrides`, `:499-556 common_slots_consumed_above` | a `Merge` aggregation's next preorder node must be an `EXCHANGE_NODE` (`:453-463`); a partial avg must be the fragment root (`:404-414`); common-slot consumers are found by an ancestor stack over the flat list |
| translator: scans | `scan_paths.rs:62-93 ScanFilePaths::from_fragment`, `node_translator.rs:341-356 translate_scan` | scan ranges keyed by **scan node id** from both `per_node_scan_ranges` and `node_to_per_driver_seq_scan_ranges`; a node in both is refused (`:75-88`) |
| translator: plan shape | `node_translator.rs:164-223` (`TranslatePlan for TPlan`, `PlanNodeCursor`) | flat preorder with `num_children`; `ensure_consumed` refuses trailing nodes |
| translator: joins | `node_translator.rs:1722-1862 translate_hash_join` | plain `JoinRel`; `refuse_carried_join_child` (`:1880-1895`); `distribution_mode` is never read (grep empty) |
| FE row-layout guarantee | `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks/planner/ExchangeNode.java:181-184` | `computeTupleIds()` copies the child's tuple ids; shipped as `TExchangeNode.input_row_tuples`. Measured: sender root `row_tuples: [6]` (`perf/cn1/dump/fragment-0141.txt:11-19`) = receiver exchange 9 `input_row_tuples: [6]` (`fragment-0137.txt:3020-3022`) |
| FE deploy order | `fe-core/.../qe/scheduler/Deployer.java:208-215` | stage by stage from the root: `deployAsync` then `waitForDeploymentCompletion` per stage, so a receiver is registered before its sender arrives (measured for q05: `perf/cn1/cluster.log:4142-4202`) |
| DuckDB build side | `duckdb/src/optimizer/build_probe_side_optimizer.cpp:160-213 TryFlipJoinChildren` | swaps when `right_side_build_cost > left_side_build_cost` (`:184-188`, row width x estimated cardinality); SEMI/ANTI with an equality also flip (`:225-246`). Measured on this engine: q04 `HASH_JOIN (id=6) type: RIGHT_SEMI` built on the orders scan, probed the 3.79e9-row lineitem stream (`perf/cn1/engine-cn0.log:48480`) |
| filter selectivity | `duckdb/src/include/duckdb/optimizer/join_order/relation_statistics_helper.hpp:57` | `DEFAULT_SELECTIVITY = 0.2` applied once per relation with filters; each of the six leaves carries exactly one conjunct (`is_not_null_pred`), so the inline scan is estimated at footer rows x 0.2 |

Two facts that make a `TPlan`-level splice sound: the descriptor table is per query (the receiver's
resolved `desc_tbl` already describes the sender's tuples and slots), and plan node ids are
query-global (`fe-core/.../sql/plan/ExecPlan.java:67 IdGenerator<PlanNodeId>`), so slot references
resolve identically before and after the splice and scan-range keys cannot collide.

---

## 3. Design decisions (with the judges' reasoning)

1. **Leaf, HASH_PARTITIONED, non-aggregation parent is the shipped default.** It is a strict subset
   of the upstream behaviour, keeps 11 of the 15 passing 1-CN plans byte-identical, keeps exact
   stream cardinalities at every non-leaf boundary (the BJ-4 hazard, perf report C2, is bounded to
   leaves whose relative sizes are >= 5x apart), and needs no remote-path change.
2. **All fusability checks run at defer time with both sender and receiver params in hand.** A
   refusal is a logged decline to today's path, never a query failure. This fixes the one
   as-written flaw the judges found in the upstream design (sender-side refusals evaluated at
   fold time after the sender had been deferred).
3. **Pure splice lives in the translator crate** (`fusion.rs`, `FusionRefusal` enum), because that
   crate already owns `TPlan -> Substrait` and its invariants, and it is testable without an
   engine. The CN owns policy (mode), routing and the rendezvous.
4. **`SenderSource::LocalPlan { params, inputs }`** carries the deferred sender's own exchange
   inputs so PR 2 (middle fragments) is a fold extension, not an alias map. In PR 1 `inputs` is
   always empty.
5. **Three-valued knob through `tunable.rs`** (rejected on a bad value, logged at bring-up):
   `off | leaf | leaf-any`. `leaf-any` (all single-destination local leaves, any partition type) is
   the experiment arm for q09's ~31 GB of broadcast parking; it is not the default.
6. **No `RIGHT_SEMI` translator arm is needed here**: the flip happens inside the engine after
   fusion, as it already does today for q04 (section 2, last rows).
7. **Conservative aggregation rule**: decline when the exchange's parent is *any*
   `AGGREGATION_NODE`, not only `AggPhase::Merge`. No TPC-H exchange has a non-merge aggregation
   parent (`perf/survey/plan-summary.txt`), so the broader rule buys nothing and the narrower one
   is easier to reason about. Also decline when the **sender root** is a `Partial` aggregation
   (`agg_phase::classify`, `agg_phase.rs:44-80`): partial states must cross a real boundary, and
   the "partial avg must be the fragment root" invariant (`node_translator.rs:404-414`) would
   otherwise be violated by the splice.

---

## 4. Exact mechanism and code shape

### 4.1 Translator crate: new module `fusion.rs`

File: `experimental/starrocks/crates/starrocks-plan-translator/src/fusion.rs` (new, pure functions
over thrift structs, no I/O). Add `pub mod fusion;` to `crates/starrocks-plan-translator/src/lib.rs`
next to the other modules and re-export `pub use fusion::{FusionRefusal, SenderShape};`.

```rust
//! Same-node fragment fusion: splicing a sender fragment's plan over the receiver's EXCHANGE_NODE.
//!
//! The FE cuts one query into fragments and connects them with exchanges. When a sender has one
//! destination on this CN and the receiving exchange expects one sender, the exchange is the
//! identity on rows (compute_node_service.rs:1104-1111), so the sender's node list can replace the
//! exchange node in the receiver's flat preorder plan. Everything the translator reads per fragment
//! (`per_node_scan_ranges`, `node_to_per_driver_seq_scan_ranges`, `per_exch_num_senders`) is
//! unioned by query-global node id. The row layout is guaranteed by the FE: an ExchangeNode's
//! tuple ids are its child's (ExchangeNode.java:181-184), shipped as `input_row_tuples`.

use starrocks_thrift::internal_service::TExecPlanFragmentParams;
use starrocks_thrift::partitions::TPartitionType;
use starrocks_thrift::plan_nodes::{TPlanNode, TPlanNodeType};

/// Why an exchange keeps its stream boundary instead of absorbing its sender. Every variant is a
/// logged decline on the CN, never a query failure.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FusionRefusal {
    // Sender side (known from the sender alone)
    SenderMissingField(&'static str),            // fragment / plan / params / output_sink / stream_sink
    NotDataStreamSink,
    NotSingleDestination { destinations: usize },
    SinkLimit,                                    // stream_sink.limit >= 0
    SinkOutputColumns,                            // stream_sink.output_columns non-empty
    SenderOutputExprs,                            // fragment.output_exprs non-empty
    MalformedSenderPlan(String),                  // empty, or preorder span != nodes.len()
    CommonSlotProjection { node_id: i32 },        // any PROJECT_NODE with non-empty common_slot_map
    PartialAggregationRoot { node_id: i32 },      // sender root classifies as AggPhase::Partial
    // Receiver side (needs the receiver's params)
    ExchangeMissing { node_id: i32 },             // no EXCHANGE_NODE with that id in the receiver plan
    ExchangeHasChildren { node_id: i32 },         // num_children != 0 (malformed)
    ExchangeLimit { node_id: i32 },               // node.limit >= 0
    ExchangeOffset { node_id: i32 },              // exchange_node.offset > 0
    ExchangeConjuncts { node_id: i32 },
    SortedExchange { node_id: i32 },              // exchange_node.sort_info.is_some()
    AggregationParent { exchange: i32, parent: i32 },
    RowTuplesDiffer { exchange: Vec<i32>, sender_root: Vec<i32> },
    ReceiverDescriptorUnresolved,                 // desc_tbl None or empty tuple_descriptors
    DescriptorMissingTuple { tuple_id: i32 },     // a sender node's row tuple is not in the receiver's desc_tbl
    ScanRangeCollision { node_id: i32 },
    ExchangeIdCollision { node_id: i32 },
}
impl std::fmt::Display for FusionRefusal { /* one line each, e.g. "exchange 9 feeds aggregation node 10" */ }

/// What the CN needs from a sender to apply its policy and routing. Borrowed from the params.
pub struct SenderShape<'a> {
    pub dest_node_id: i32,
    pub destination: &'a starrocks_thrift::data_sinks::TPlanFragmentDestination,
    pub sender_id: i32,                           // params.sender_id.unwrap_or(0), as :1103
    pub partition: TPartitionType,                // stream_sink.output_partition.type_
    pub is_leaf: bool,                            // no EXCHANGE_NODE in the sender plan
}

/// Sender-only checks: sink shape, output_exprs, plan well-formedness, common-slot projections,
/// partial-aggregation root. Does not look at any receiver.
pub fn sender_shape(sender: &TExecPlanFragmentParams) -> Result<SenderShape<'_>, FusionRefusal>;

/// Receiver-and-sender checks for the edge `sender -> receiver.EXCHANGE_NODE(exchange_node_id)`.
/// Pure; called under the rendezvous lock with the registered receiver's (descriptor-resolved) params.
pub fn fusable_edge(
    receiver: &TExecPlanFragmentParams,
    exchange_node_id: i32,
    sender: &TExecPlanFragmentParams,
) -> Result<(), FusionRefusal>;

/// The splice. Precondition: `fusable_edge` passed for the same three arguments. Returns the fused
/// params with the RECEIVER's identity (query_id, fragment_instance_id, output_sink, destinations,
/// sender_id, desc_tbl) and:
///   plan.nodes:  the EXCHANGE_NODE replaced by `sender.fragment.plan.nodes` (a complete preorder subtree)
///   per_exch_num_senders:  minus `exchange_node_id`, plus the sender's own entries (collision -> refusal)
///   per_node_scan_ranges / node_to_per_driver_seq_scan_ranges:  union by node id (collision -> refusal)
pub fn splice(
    receiver: TExecPlanFragmentParams,
    exchange_node_id: i32,
    sender: &TExecPlanFragmentParams,
) -> Result<TExecPlanFragmentParams, FusionRefusal>;

/// Index of the preorder parent of `nodes[idx]`, via the same ancestor stack
/// `common_slots_consumed_above` uses (node_translator.rs:499-556). `None` for the root.
pub(crate) fn preorder_parent(nodes: &[TPlanNode], idx: usize) -> Option<usize>;

/// Number of nodes in the preorder subtree rooted at `nodes[start]`; `Err` on underrun.
pub(crate) fn preorder_span(nodes: &[TPlanNode], start: usize) -> Result<usize, FusionRefusal>;
```

`fusable_edge` in order: locate the exchange (`ExchangeMissing`); `num_children == 0`
(`ExchangeHasChildren`); `node.limit < 0` (`ExchangeLimit`); `exchange_node.offset.unwrap_or(0) == 0`
(`ExchangeOffset`); `conjuncts` empty (`ExchangeConjuncts`); `sort_info.is_none()`
(`SortedExchange`); `preorder_parent` is not `AGGREGATION_NODE` (`AggregationParent`);
`exchange_node.input_row_tuples == sender.nodes[0].row_tuples` (`RowTuplesDiffer`); receiver
`desc_tbl` resolved and covering every `row_tuples` id of every sender node
(`ReceiverDescriptorUnresolved`, `DescriptorMissingTuple`); no key of the sender's
`per_node_scan_ranges` / `node_to_per_driver_seq_scan_ranges` present in either of the receiver's
two maps (`ScanRangeCollision`); no key of the sender's `per_exch_num_senders` present in the
receiver's (`ExchangeIdCollision`). `sender_shape` must also have passed (the CN calls it first).

The splice itself is `plan.nodes.splice(pos..=pos, sender_nodes.iter().cloned())`. Because the
exchange is a preorder leaf (`num_children: 0`, e.g. `fragment-0137.txt:2541, 3003`) and the sender
list is a complete preorder subtree rooted at index 0 (checked by `preorder_span == len`), every
ancestor's `num_children` stays valid and `PlanNodeCursor::ensure_consumed` (`node_translator.rs:
216-223`) is satisfied.

The thrift field types (generated `thrift_gen/internal_service.rs:3319-3336`, `plan_nodes.rs:
11997-12011, 9691-9698, 10462-10465`, `data_sinks.rs:623-631`, `planner.rs:423-435`):
`per_node_scan_ranges: BTreeMap<i32, Vec<TScanRangeParams>>`, `per_exch_num_senders: BTreeMap<i32,
i32>`, `node_to_per_driver_seq_scan_ranges: Option<BTreeMap<i32, BTreeMap<i32, Vec<TScanRangeParams>>>>`,
`destinations: Option<Vec<TPlanFragmentDestination>>`, `sender_id: Option<i32>`; `TPlanNode { node_id,
node_type, num_children: i32, limit: i64, row_tuples: Vec<i32>, conjuncts: Option<Vec<TExpr>>,
exchange_node: Option<TExchangeNode>, project_node: Option<TProjectNode>, agg_node, .. }`;
`TExchangeNode { input_row_tuples: Vec<i32>, sort_info: Option<TSortInfo>, offset: Option<i64>,
partition_type, .. }`; `TProjectNode { slot_map, common_slot_map: Option<BTreeMap<i32, TExpr>> }`;
`TDataStreamSink { dest_node_id, output_partition: TDataPartition, .., output_columns: Option<Vec<i32>>,
limit: Option<i64> }`; `TPlanFragment { plan, output_exprs: Option<Vec<TExpr>>, output_sink, .. }`.
`TPartitionType::UNPARTITIONED = 0`, `HASH_PARTITIONED = 2` (`partitions.rs:37-41`).

### 4.2 CN knob: `tunable.rs`

```rust
// experimental/starrocks/src/tunable.rs (registry rules at :1-20: reject, log, unset = default)
/// Which same-node senders are fused into their receiver plan instead of running and parking.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FusionMode {
    /// Every sender runs and parks (the behaviour before this module existed).
    Off,
    /// Leaf senders (no exchange input) with a HASH_PARTITIONED single local destination whose
    /// receiving exchange is plain and not under an aggregation. The shipped default.
    Leaf,
    /// Every single-local-destination leaf, any partition type (experiment: removes broadcast
    /// parking of dimension tables at the price of estimated instead of exact cardinalities).
    LeafAny,
    // PR 2 adds `All` (middle fragments too).
}
const FUSION_MODE_NAME: &str = "SIRIUS_CN_FRAGMENT_FUSION";
impl FusionMode {
    const DEFAULT: Self = Self::Leaf;
    fn read() -> Result<Self, String>   // env_value(FUSION_MODE_NAME); trim; case-insensitive:
    // "off" | "0" | "false" -> Off; "leaf" -> Leaf; "leaf-any" -> LeafAny;
    // anything else -> Err(format!("{FUSION_MODE_NAME}={raw:?} rejected: expected one of off, leaf, leaf-any"))
    // (same wording family as Knob::rejected, tunable.rs:116-150)
}
pub struct Tunables { .., pub(crate) fusion_mode: FusionMode }   // DEFAULTS: FusionMode::DEFAULT
```

`Tunables::from_env` (`:231-249`) reads it; `Tunables::resolve` (`:259-288`) adds
`fusion_mode = ?published.fusion_mode` to its existing `"resolved CN transport tunables"` info line
(keep the message text). `main.rs:171` already calls `Tunables::resolve()` before the service is
built, so a bad value fails bring-up.

`ServiceCore` (`compute_node_service.rs:186-211`) gains `fragment_fusion: AtomicU8` initialised from
`Tunables::get().fusion_mode` in `with_transport` (`:238-269`), plus
`#[cfg(test)] pub(crate) fn set_fragment_fusion(&self, mode: FusionMode)` mirroring
`set_async_sender_dispatch` (`:299-303`). Tests see `DEFAULTS` (= `Leaf`) unless they call the setter.

### 4.3 Rendezvous: `local_exchange.rs`

```rust
// experimental/starrocks/src/local_exchange.rs
use starrocks_plan_translator::FusionRefusal;

/// A same-node sender whose PLAN was deferred into its receiver instead of running. Holds thrift
/// only (no GPU memory). `inputs` are the exchange inputs the deferred sender itself consumed
/// (parked slots or staged remote batches; never another `LocalPlan`, see the fold in 4.6) so the
/// fused fragment relays them under their own stream ids.
#[derive(Clone)]
pub(crate) struct LocalPlan {
    pub(crate) params: TExecPlanFragmentParams,
    pub(crate) inputs: Vec<ReadyExchangeInput>,
}
// Manual `Debug`: query id, fragment instance id, plan node count, inputs.len(). The derived
// Debug of TExecPlanFragmentParams is the 23,000-line fragment dump; it must never reach an
// error string or a log line.

pub(crate) enum SenderSource {
    LocalParked { names: Vec<String>, slot: SenderSlot },      // unchanged
    Remote { .. },                                             // unchanged
    LocalPlan(LocalPlan),
}
// names(): LocalPlan => &[] (documented: the fold in execute_ready_fragment consumes every
//          LocalPlan before names() is read for the remaining inputs).
// is_complete(): LocalPlan => true.
// `ReadyExchangeInput` gains `#[derive(Clone)]` (LocalPlan carries a Vec of them).

/// Why a sender was not deferred. Logged, never an error.
#[derive(Debug)]
pub(crate) enum FuseSkip {
    NoPendingReceiver,
    ExchangeNotDeclared,            // receiver pending but per_exch_num_senders lacks this node id (malformed dispatch)
    ReceiverExpectsMany(usize),
    ExchangeAlreadySourced,         // cannot happen with expected == 1; kept as a guard
    Structural(FusionRefusal),
}
impl std::fmt::Display for FuseSkip { .. }

pub(crate) enum FuseOffer {
    Fused(Option<ReadyFragment>),
    Declined { plan: LocalPlan, skip: FuseSkip },   // the plan is handed back so the caller runs it
}

impl LocalExchange {
    /// Offers a sender's plan for fusion into the pending receiver `key.fragment_instance_id` at
    /// exchange `key.node_id`. Under the lock: the receiver must be pending, declare the exchange,
    /// expect exactly one sender there, have no source for it yet, and `verdict(&receiver.params)`
    /// must pass. On success the plan is recorded as `SenderSource::LocalPlan` and `take_ready`
    /// decides whether the receiver is complete. Nothing is cloned but the verdict.
    pub(crate) fn offer_local_plan(
        &self,
        key: ExchangeKey,
        sender_id: i32,
        plan: LocalPlan,
        verdict: impl FnOnce(&TExecPlanFragmentParams) -> Result<(), FusionRefusal>,
    ) -> Result<FuseOffer, String>;

    /// Drops every pending receiver of `query_id` with its sources and remote sequence tracking.
    /// Returns what was dropped so the caller can log it. Deferred plans hold no GPU memory; the
    /// GPU memory behind returned parked slots and the arena leases behind staged batches are NOT
    /// released here (that is fix 2's engine-side work); today they would have lived just as long.
    pub(crate) fn forget_query(&self, query_id: FragmentInstanceId) -> ForgottenQuery;
}
pub(crate) struct ForgottenQuery { pub receivers: usize, pub deferred_plans: usize,
                                   pub parked_slots: Vec<SenderSlot>, pub staged_batches: usize }
```

**`forget_query` / `ForgottenQuery` are not shipped** (INTEGRATION.md 2.3, 2.7 item 1): fix 2 lands
first and its per-instance `retire_receiver` (fix 2 spec 4.6) removes every `sources` entry of a
cancelled receiver, deferred `LocalPlan`s included. The declaration above is kept only as the
fallback if fix 2 were ever dropped from the stack.

`take_ready` (`:248-313`) is unchanged: a `LocalPlan` counts as complete, so a receiver whose only
pending sender was deferred becomes ready the moment the offer is accepted. `push_sender` and
`push_remote_frame` are unchanged (no alias map; nothing on the remote path changes).

### 4.4 CN policy and the defer hook: `compute_node_service.rs`

```rust
impl ServiceCore {
    /// Tries to defer a sender into its local receiver instead of running it. Returns the
    /// receivers this completed (to dispatch) or `None` when the sender must take today's path.
    /// Every check runs here, before anything is deferred; a decline is logged and never an error.
    fn try_defer_sender(
        &self,
        params: &TExecPlanFragmentParams,          // descriptor-resolved
    ) -> std::result::Result<Option<Vec<ReadyFragment>>, String> {
        let mode = self.fragment_fusion();                     // AtomicU8 -> FusionMode
        if mode == FusionMode::Off { return Ok(None); }        // debug!("fragment fusion skipped", reason="off")
        // Integration with fix 2 (parked bookkeeping; INTEGRATION.md 2.3): a leaf of a query this CN
        // has already declared dead is never deferred. This check cannot be left to fix 2's arrival
        // gate 4 in `process_fragment`: on the async path `exec_single_attachment`
        // (compute_node_service.rs:705-717) calls `try_dispatch_sender` (:309-329), and this hook,
        // BEFORE `process_fragment`. Declining sends the leaf down today's path, where fix 2's
        // gate 3 in `run_ready_fragment` skips it without translating.
        if let Some(query_id) = Self::query_id(params)
            && let Some(cause) = self.results.failure_of(query_id)
        {
            debug!(%query_id, %cause, reason = "query already failed on this CN", "fragment fusion skipped");
            return Ok(None);
        }
        let shape = match fusion::sender_shape(params) {
            Ok(shape) => shape,
            Err(refusal) => { debug!(..reason = %refusal, "fragment fusion skipped"); return Ok(None); }
        };
        // Policy (PR 1: leaves only)
        if !shape.is_leaf { debug!(..reason = "sender has exchange inputs", ..); return Ok(None); }
        if mode == FusionMode::Leaf && shape.partition != TPartitionType::HASH_PARTITIONED {
            debug!(..reason = "leaf mode fuses HASH_PARTITIONED sinks only", ..); return Ok(None);
        }
        if !matches!(self.route_destination(shape.destination)?, DestinationRoute::Local) {
            debug!(..reason = "remote destination", ..); return Ok(None);
        }
        let key = ExchangeKey {
            fragment_instance_id: FragmentInstanceId::from(&shape.destination.fragment_instance_id),
            node_id: shape.dest_node_id,
        };
        let plan = LocalPlan { params: params.clone(), inputs: Vec::new() };
        match self.exchanges.offer_local_plan(key, shape.sender_id, plan,
                |receiver| fusion::fusable_edge(receiver, key.node_id, params))? {
            FuseOffer::Fused(ready) => {
                info!(%query_id, %sender_fragment_instance_id, %receiver_fragment_instance_id,
                      exchange = key.node_id, mode = ?mode, "fused sender fragment into its local receiver");
                Ok(Some(ready.into_iter().collect()))
            }
            FuseOffer::Declined { skip, .. } => {
                info!(%query_id, %sender_fragment_instance_id, %receiver_fragment_instance_id,
                      exchange = key.node_id, reason = %skip, "fragment fusion skipped");
                Ok(None)
            }
        }
    }
}
```

Logging levels, deliberately: policy exclusions (`Off`, not a leaf, partition type, remote,
multi-destination, not a data-stream sink) are `debug!` because every broadcast leaf hits one on
every query; rendezvous/structural declines (`NoPendingReceiver`, `ExchangeNotDeclared`,
`ReceiverExpectsMany`, `ExchangeAlreadySourced`, `Structural`) are `info!` because they are the
arrival-order and plan-shape regressions the acceptance arm counts (section 8: `fragment fusion
skipped` must be 0 for the six).

Two call sites, so the inline, async and batch RPC paths all get it:

- `process_fragment` (`:918-959`): after the survey-mode return (`:927-934`) and after the
  receiver branch (`:935-949`; a leaf has no exchanges so it falls through), **before**
  `translate_fragment_logged` at `:958`:
  ```rust
  if let Some(ready) = self.try_defer_sender(&params)? {
      return Ok(FragmentOutcome::from_ready(ready));
  }
  ```
- `try_dispatch_sender` (`:309-329`): after `resolve_descriptor_table`, before `self.dispatch(..)`:
  ```rust
  if let Some(ready) = self.core.try_defer_sender(&params)? {
      self.dispatch_then_join(FragmentOutcome::from_ready(ready))?;   // no drains; dispatches the readied receivers
      return Ok(true);
  }
  ```
  A fused leaf never touches the dispatch worker and its RPC returns immediately.

`translate_batch_attachment` (`:1624-1679`) calls both per instance and needs no change.

### 4.5 Fold at receiver ready time

In `execute_ready_fragment` (`:1325-1394`), before the `ExchangeInput` construction at `:1329`:

```rust
let (params, inputs, fused_senders) = Self::fold_deferred_plans(ready)?;
if !fused_senders.is_empty() {
    info!(%query_id, %fragment_instance_id, fused = fused_senders.len(),
          senders = ?fused_senders, "fused deferred sender plans into receiver");
}
// then exactly today's code over `inputs` and `&params`; `dump_fragment(&params)` at :1358 now
// dumps the FUSED params, which pairs with the plan-NNNN.substrait the engine runs.

/// Splices every deferred sender plan into the receiver's params. Returns the fused params, the
/// remaining (streamed) inputs, and the absorbed sender instance ids. A refusal here is a bug:
/// the same checks passed at defer time on the same two params.
fn fold_deferred_plans(ready: ReadyFragment)
    -> std::result::Result<(TExecPlanFragmentParams, Vec<ReadyExchangeInput>, Vec<FragmentInstanceId>), String>
{
    let mut params = ready.params;
    let mut streamed = Vec::new();
    let mut fused = Vec::new();
    let mut worklist = ready.inputs;                       // PR 2: a deferred plan's own inputs join this list
    while let Some(input) = worklist.pop() {
        match input.sources.as_slice() {
            [SenderSource::LocalPlan(_)] => {
                let SenderSource::LocalPlan(plan) = input.sources.into_iter().next().unwrap() else { unreachable!() };
                let sender_id = Self::fragment_instance_id(&plan.params);
                params = fusion::splice(params, input.node_id, &plan.params).map_err(|refusal| format!(
                    "fragment fusion: splicing deferred sender {sender} into receiver {receiver} at exchange {} \
                     failed after passing the defer-time checks: {refusal}", input.node_id, ..))?;
                fused.extend(sender_id);
                worklist.extend(plan.inputs);              // empty in PR 1
            }
            sources if sources.iter().any(|s| matches!(s, SenderSource::LocalPlan(_))) => {
                return Err(format!("exchange node {} mixes a deferred sender plan with {} other sender source(s)",
                                   input.node_id, sources.len() - 1));
            }
            _ => streamed.push(input),
        }
    }
    streamed.sort_by_key(|input| input.node_id);           // keep take_ready's deterministic order
    Ok((params, streamed, fused))
}
```

After the fold, `translate_fragment_with_exchange_inputs` (`lib.rs:247-437`) sees a plan whose
remaining `EXCHANGE_NODE`s are exactly the streamed inputs, emits a `ReadRel(LocalFiles)` (+
`Filter` for the conjunct) for the spliced `FILE_SCAN_NODE`, and `TranslatedPlan.stream_inputs`
lists only the streamed exchanges, so `run_fragment_inner` declares and relays only those
(`engine.rs:508-587, 616-647`). Nothing in `engine.rs` changes.

### 4.6 Cancellation: owned by fix 2 (nothing added here)

Landing order (INTEGRATION.md section 3) puts fix 2 before this PR, and fix 2 rewrites
`cancel_plan_fragment` (`:457-479`) into a full teardown: `results.cancel_query`,
`exchanges.retire_receiver(id)` -> `release_sources`, `executor.retire_query` (fix 2 spec 4.4g). This PR
adds **nothing** to `cancel_plan_fragment`. What it must guarantee instead, so fix 2's teardown covers
deferred plans:

- `retire_receiver` (fix 2 spec 4.6) removes every `sources` entry of the cancelled receiver, so a
  `SenderSource::LocalPlan` recorded by `offer_local_plan` leaves the rendezvous with the other sources.
  Nothing else holds a deferred plan.
- A `LocalPlan` holds thrift only (no GPU memory, no staging lease), so fix 2's `release_sources` /
  `release_staged` have nothing to release for it. Fix 2 writes those loops as
  `if let SenderSource::Remote { batches, .. }` (non-exhaustive), so adding the third variant compiles
  without touching fix 2's code; if a reviewer prefers an exhaustive `match`, add
  `SenderSource::LocalPlan(_) => {}` with the comment "deferred plan: nothing to release".
- A fused receiver reaches `run_ready_fragment` through `dispatch` / `dispatch_then_join` like any ready
  receiver, so fix 2's gate 3 (`failure_of` -> skip) covers it; the leaf of a dead query is declined by the
  `failure_of` check at the top of `try_defer_sender` (4.4).

Historical note: the surgical/robust designs carried a by-query `forget_query`; it was dropped at
integration because fix 2's per-instance removal is the one the FE's per-instance cancel RPCs drive.

### 4.7 Docs

- `experimental/starrocks/docs/TUNABLES.md`: new table "Dispatch" (next to "Engine-side") with
  `SIRIUS_CN_FRAGMENT_FUSION` (`off | leaf | leaf-any`, default `leaf`, validated at bring-up,
  what each value fuses, that `off` restores run-and-park without a rebuild) and a pointer to
  `SIRIUS_CN_ASYNC_SENDER_DISPATCH`.
- `experimental/starrocks/DEMO.md:39` "Sequential fragments" paragraph: add two sentences: a
  single-destination same-node leaf sender is fused into its receiver's plan before translation
  (so the demo's `fragment run started` lines are fewer than the FE's fragments), and how to turn
  it off.
- Module docs: `local_exchange.rs:1-6` (mention the third source kind), `fusion.rs` header.
- Not shipped, but update alongside: `perf/sf1000/cnlog_extract.py` should count `fused sender
  fragment into its local receiver` per run so `frags=` reads "engine runs" and a new `fused=`
  reads "FE fragments absorbed".

### 4.8 What does not change

Engine `src/`, FE, protos, wire format, `engine.rs` (test only), the remote (nixl) path
(`push_remote_frame`, `handle_transmit_packed`, `nixl_transport.rs`), `results.fail_query`,
`SIRIUS_CN_TRANSLATE_ONLY` survey mode (returns before the hook, `:927-934`), failure attribution
(a fused receiver fails under the receiver's ids through `run_ready_fragment :864-911` as any
receiver does today; a deferred sender never ran, so `engine.rs:291-316` has nothing of it to wipe).

### 4.9 PR 2 shape (not in this PR; recorded so PR 1 does not paint itself into a corner)

Knob value `all`. Third call site: in `execute_ready_fragment`, **after** `fold_deferred_plans`
and before translation, a ready fragment that is itself a data-stream sender with one local
destination is offered as `LocalPlan { params: fused_params, inputs: streamed }` to its receiver
(same `offer_local_plan`, same `fusable_edge`, `is_leaf` no longer required). Because the fold runs
first, a `LocalPlan`'s `inputs` never contain another `LocalPlan` (assert it in the fold). Sender
slots keep the middle fragment's instance id; that is fine because the engine looks parked output
up by `SenderSlot` (`engine.rs:625-628`) and the stream id is the exchange node id
(`stream_id_of`, `engine.rs:733-735`), both already query-global. This is what fuses q21's F03
(stream 9) and q09's `UNPARTITIONED` single-destination broadcasts.

---

## 5. Tests to write first

No Catch2 tests (no engine change) and no FE JUnit (no FE change). Everything below is Rust.

### 5.1 Translator, `crates/starrocks-plan-translator/src/fusion.rs` `#[cfg(test)] mod tests`

Hand-built `TPlanNode`s through a local helper `node(id, TPlanNodeType, num_children, row_tuples)`
(fill the 50-argument `TPlanNode::new` once, like `scan_node` in `compute_node_service.rs:4076`),
plus `exchange(id, input_row_tuples)`, `params(plan, exec)`.

| test | asserts |
|---|---|
| `splice_replaces_the_exchange_leaf_with_the_sender_preorder` | receiver `[HASH_JOIN(2,c2), EXCHANGE(7), FILE_SCAN(1)]`, sender `[PROJECT(3,c1), FILE_SCAN(0)]` -> node ids `[2,3,0,1]`; `per_exch_num_senders` loses 7; `per_node_scan_ranges` has 0 (from the sender) and 1 (receiver's own); receiver identity fields untouched |
| `splice_merges_per_driver_scan_ranges_and_sender_exchanges` | sender ranges under `node_to_per_driver_seq_scan_ranges` land in the receiver's map (created if `None`); a sender `per_exch_num_senders {5: 1}` is added (PR 2 shape, cheap now) |
| `fusable_edge_refuses_scan_range_collision` / `_exchange_id_collision` | `ScanRangeCollision{0}` when both maps hold node 0 (either receiver map); `ExchangeIdCollision{5}` |
| `fusable_edge_refuses_aggregation_parent` | receiver `[AGGREGATION(4,c1), EXCHANGE(3)]` -> `AggregationParent{exchange: 3, parent: 4}` (the q01 F02 -> merge shape, `all-dumps.txt` fragment-0004) |
| `fusable_edge_refuses_sorted_limited_offset_or_filtered_exchange` | four cases -> `SortedExchange`, `ExchangeLimit`, `ExchangeOffset`, `ExchangeConjuncts` |
| `fusable_edge_refuses_row_tuples_mismatch` | exchange `[6]` vs sender root `[3]` -> `RowTuplesDiffer` |
| `fusable_edge_refuses_missing_descriptor_tuple` | receiver desc_tbl lacks tuple 6 -> `DescriptorMissingTuple{6}`; cached-reference desc_tbl (`is_cached: Some(true)`, empty tuples) -> `ReceiverDescriptorUnresolved` |
| `sender_shape_refuses_output_exprs_sink_limit_output_columns_and_fan_out` | four cases -> `SenderOutputExprs`, `SinkLimit`, `SinkOutputColumns`, `NotSingleDestination{2}` |
| `sender_shape_refuses_common_slot_projection_and_partial_aggregation_root` | PROJECT with non-empty `common_slot_map` -> `CommonSlotProjection`; root `AGGREGATION(need_finalize false, no merge flags)` -> `PartialAggregationRoot` |
| `sender_shape_refuses_malformed_preorder` | `[PROJECT(c1)]` alone (underrun) and `[FILE_SCAN, FILE_SCAN]` (trailing) -> `MalformedSenderPlan` |
| `sender_shape_reports_leaf_and_partition_type` | leaf `HASH_PARTITIONED` -> `is_leaf: true, partition: HASH_PARTITIONED, sender_id, dest_node_id`; a plan containing an `EXCHANGE_NODE` -> `is_leaf: false` |
| `preorder_parent_matches_the_ancestor_stack` | on a 7-node tree the parent of every node matches a hand computation |

### 5.2 Translator, `crates/starrocks-plan-translator/tests/translate.rs`

Fixtures exist: `scan_node` (`:294`), `params` (`:301`), `desc_table` (`:107`), `broker_scan_range`
(`:440`), `params_with_scan_range` (`:505-538`), `parquet_query_range` (`:923`), and the hash-join
fixtures used by the join tests (added in `e4112409`).

| test | asserts |
|---|---|
| `spliced_leaf_translates_to_a_local_files_scan_with_no_stream_inputs` | receiver `[EXCHANGE(7, tuples [0])]` with `per_exch {7: 1}`; sender `params_with_scan_range([FILE_SCAN(0, tuple 0)], .., 0, parquet_query_range(..))`; `fusion::splice` then `PlanTranslator::translate_fragment` -> `stream_inputs.is_empty()`, root is a `ReadRel` with `local_files` |
| `fused_plan_equals_the_hand_built_single_fragment_plan` | the fused translation's Substrait `Plan` equals translating the same `[FILE_SCAN(0)]` fragment built directly (proto equality) |
| `spliced_receiver_keeps_its_other_exchange_as_a_stream_input` | receiver `[HASH_JOIN, EXCHANGE(7), EXCHANGE(8)]`; splice 7 only; translate with an `ExchangeInput` for 8 -> exactly one `StreamInputSchema { node_id: 8 }` |

### 5.3 CN, `src/local_exchange.rs` `mod tests` (fixtures `key`, `params` exist from `:322`)

| test | asserts |
|---|---|
| `offer_local_plan_completes_a_single_sender_receiver` | register `{7: 1}`; offer with an `Ok` verdict -> `Fused(Some(ready))`, `ready.inputs[0].sources` is `[LocalPlan]`, receiver removed |
| `offer_local_plan_declines_without_pending_receiver` | -> `Declined { skip: NoPendingReceiver, plan }` and the plan is the one offered (ids equal) |
| `offer_local_plan_declines_when_receiver_expects_many` | `{7: 2}` -> `ReceiverExpectsMany(2)`; a later `push_sender` still works |
| `offer_local_plan_declines_on_structural_verdict_and_leaves_receiver_untouched` | verdict `Err(SortedExchange)` -> `Structural(..)`; `sources` empty; `push_sender` then readies as today |
| `offer_local_plan_declines_an_undeclared_exchange` | receiver declares `{7: 1}`, offer at node 8 -> `ExchangeNotDeclared` |
| `mixed_deferred_and_parked_inputs_become_ready_together` | `{7: 1, 8: 1}`; offer 7, push 8 parked -> ready with both inputs, node ids sorted |
| ~~`forget_query_drops_pending_receivers_and_their_sources`~~ | not shipped (4.6: fix 2's `retire_receiver` owns removal). Replace with `retire_receiver_returns_a_deferred_plan_with_the_other_sources`: register `{7: 1, 8: 1}`, offer a `LocalPlan` at 7, push a parked source at 8 (or leave 8 empty so the receiver stays pending), then fix 2's `retire_receiver(R)` returns the `LocalPlan` among its sources and `is_retired(R)` is true; a later `offer_local_plan` for R declines with `NoPendingReceiver` |

### 5.4 CN, `src/compute_node_service.rs` `mod tests`

Existing fixtures: `CountingExecutor` (`:1810`), `local_destination` (`:2018`), `remote_destination`
(`:2584`), `wait_until` (`:2028`), `fetch_rows_eventually` (`:2041`), `data_stream_sink` (`:3806`,
UNPARTITIONED), `exec_params` (`:3835`), `fragment_params` (`:3907`), `scan_plan`/`exchange_plan`
(`:3955-3973`), `desc_table` (`:4042`), `scan_node` (`:4076`), the self-exchange tests (`:2323-2427`).
Add: `hash_partitioned_data_stream_sink(dest_node_id, slot_id, tuple_id)` whose
`TDataPartition::new(HASH_PARTITIONED, Some(vec![slot_ref_expr]), None, None)` carries one bare
`SLOT_REF` (the translator requires bare slot refs for a hash-partitioned sink when the leaf does
run, `lib.rs:347-410`; copy the `slot_ref` builder from `translate.rs:171`); a
`RecordingRunExecutor` capturing `(inputs.len(), remote_inputs.len(), outputs.len(),
plan.stream_inputs.len())` per call; a `FailingExecutor`; and a minimal two-exchange join receiver
plan (`[HASH_JOIN(c2), EXCHANGE(7), EXCHANGE(8)]` with one equality conjunct, copied from the
translator's join fixtures) for the partial-fusion case.

| test | asserts |
|---|---|
| `hash_partitioned_single_destination_leaf_fuses_into_its_receiver` | the `:2323` shape with a hash-partitioned sink: executor called **once**, `inputs == 0`, `stream_inputs == 0`; `fetch_data` returns rows; log contains `fused sender fragment into its local receiver` |
| `broadcast_leaf_still_parks_in_leaf_mode` | same with `data_stream_sink` (UNPARTITIONED) -> 2 calls, second with `inputs == 1, stream_inputs == 1` |
| `broadcast_leaf_fuses_in_leaf_any_mode` | `set_fragment_fusion(LeafAny)` -> 1 call |
| `fusion_off_restores_two_runs` | `set_fragment_fusion(Off)` -> 2 calls |
| `leaf_arriving_before_its_receiver_falls_back_to_parking` | sender RPC first -> 2 calls, rows fetched, log has `fragment fusion skipped` with `NoPendingReceiver` |
| `receiver_expecting_two_senders_keeps_the_parked_path` | `{7: 2}`, two leaves -> 3 calls, log has `ReceiverExpectsMany(2)` |
| `partial_fusion_keeps_the_other_exchange_as_a_stream` | join receiver over 7 (hash-partitioned leaf) and 8 (broadcast leaf) -> 2 calls; the receiver run has `inputs == 1, stream_inputs == 1` |
| `middle_fragment_is_not_deferred_in_leaf_mode` | the `:2384` root <- middle <- leaf chain, leaf hash-partitioned -> 2 calls (leaf fused into middle; middle parks; root streams), cached descriptor references still resolve |
| `fusion_applies_on_the_async_sender_path` | `set_async_sender_dispatch(true)` -> 1 call; the leaf's RPC status is OK |
| `fusion_applies_on_the_batch_path` | `exec_batch_plan_fragments` with receiver + leaf instances -> 1 call |
| `remote_single_destination_is_never_fused` | `remote_destination` fixture: behaviour of `:2439`/`:2482` unchanged |
| `fused_receiver_failure_fails_the_fe_polled_result` | `FailingExecutor` -> `fetch_data` returns the error for the receiver id |
| `fused_receiver_dump_is_the_fused_plan` | with `SIRIUS_CN_DUMP_FRAGMENTS` set to a tempdir, the receiver's ready-time dump contains no `EXCHANGE_NODE` for the fused id and contains the scan node (guard the env var with the test lock pattern from `tunable.rs` tests) |
| `cancel_drops_deferred_plans` | receiver `{7: 1, 8: 1}`, leaf 7 fused (deferred), then `cancel_plan_fragment` with `INTERNAL_ERROR` and the query id (fix 2's teardown): log shows fix 2's `cancel_plan_fragment retired the query on this CN`; `exchanges.is_retired(receiver)` is true and no `LocalPlan` remains for exchange 7 (add a `#[cfg(test)]` accessor or assert through `offer_local_plan` -> `NoPendingReceiver` for a second leaf 7); a late leaf 8 is refused by fix 2's gate 4 (RPC error containing "already failed on this CN") on the inline path or skipped by gate 3 on the async path, and the executor is never called. (Only if fix 2 is absent from the stack does the old assertion `deferred_plans = 1` from `forget_query` apply.) |

### 5.5 CN, `src/tunable.rs` tests (use `with_env`, `:321`)

`fusion_mode_parses_off_leaf_leaf_any_and_rejects_others`: unset -> `Leaf`; `off`/`0`/`false` ->
`Off`; `LEAF` -> `Leaf`; `leaf-any` -> `LeafAny`; `all` and `on` -> `Err` naming the variable, the
value and the accepted set.

### 5.6 CN, `src/engine.rs` (feature `sirius-engine`, real GPU; run on GPU 2 per the plan)

`engine_executes_a_fused_leaf_like_the_sender_receiver_pair`, next to
`engine_executes_local_files_and_sequential_exchange` (`:1284`): the same two parquet fixtures once
as a sender + receiver pair (existing path) and once as one fused fragment (translate the spliced
params through `PlanTranslator`, no `stream_inputs`), asserting identical rows. Pins "fused plan
== single-fragment plan" on a real engine without touching engine code.

---

## 6. Build and test commands

Worktree `WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-fragment-fusion`; clone
`CLONE=/home/prestouser/aocsa/sirius-stacks`; scratchpad `SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad`.
`$WT/.pixi` is a symlink to the clone's; submodules and StarRocks patches are applied; the FE is
an rsync of the perf worktree's `starrocks/output/fe` (`fix/prep-worktrees.sh`).

```bash
# Engine (libsirius) -- unchanged by this fix; already built by prep. Rebuild only if the tree moves:
pixi run --manifest-path $CLONE/pixi.toml bash -c "cd $WT && make release"      # CLAUDE.md's `pixi run make`

# CN and translator: what CI runs (.github/workflows/experimental.yml:69-79), pure Rust, no GPU:
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn \
    bash $SP/cn-build.sh $WT/experimental/starrocks fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn \
    bash $SP/cn-build.sh $WT/experimental/starrocks clippy --all-targets --no-default-features -- -D warnings
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn \
    bash $SP/cn-build.sh $WT/experimental/starrocks test --workspace --no-default-features
# (equivalent to `pixi run cn-test-no-engine` in experimental/starrocks/pixi.toml:148, run from the worktree;
#  $SP/cn-build.sh sets TOOLS_DIR and the nvidia-ml link shims, then sources scripts/cn-env.sh and execs cargo)

# Engine-linked CN tests (GPU 2 only; default features = sirius-engine + nixl-transport):
CUDA_VISIBLE_DEVICES=2 env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn \
    bash $SP/cn-build.sh $WT/experimental/starrocks test -p sirius-starrocks-cn --release -- engine_

# Release CN binary for the SF1000 arm:
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn \
    bash $SP/cn-build.sh $WT/experimental/starrocks build --release
ls -la $WT/experimental/starrocks/target/release/sirius-starrocks-cn

# Formatting/lint hooks for the Markdown edits (rumdl) before committing:
pixi run --manifest-path $CLONE/pixi.toml bash -c "cd $WT && pre-commit run -a"
```

FE: **no FE change in this fix**, so `pixi run fe-build` is not needed and
`experimental/starrocks/patches/` is untouched. For the record, FE changes in this repo are
delivered as patches under `experimental/starrocks/patches/*.patch`, applied to the vendored
submodule by `experimental/starrocks/scripts/apply-starrocks-patches.sh` (idempotent; also run by
the `apply-starrocks-patches` pixi task), and then rebuilt with `pixi run fe-build`
(`experimental/starrocks/pixi.toml:196-260`, hours).

Do not start clusters or use GPUs other than GPU 2 from the implementation worktree (plan: fix 4 ->
GPU 2). Never measure during the box's nightly CI window (02:00-03:50 UTC).

---

## 7. Behaviour on the 22 plans (what the arm must see)

Fusable edges in `leaf` mode, computed from every 1-CN dump (`fix/designs/all-dumps.txt`, parsed
with the predicate of section 4: leaf sender, `sink=T0`, `ndest=1`, `optype=2`, receiver
`per_exch[E] == 1`, plain exchange, parent not `T6`, `row_tuples` equal):

| query | fused exchanges (sender -> exchange, receiver parent) | FE fragments -> engine runs | note |
|---|---|---|---|
| q05 | 1 customer, 4 orders -> join 5; 9 lineitem -> join 10; 18 nation -> join 19 | 11 -> 7 | declined (broadcast): 13 supplier, 23 region |
| q08 | 2 part, 4 lineitem -> join 5 | 11 -> 9 | six broadcasts stay parked (8, 12, 16, 20, 24, 29) |
| q09 | 2 part, 4 lineitem -> join 5 | 9 -> 7 | broadcasts 8 supplier, 12 partsupp (~13 GB), 16 orders (~18 GB), 20 nation stay parked |
| q17 | 1 lineitem, 4 part -> join 5 | 5 -> 3 | 9 -> merge agg 10 declined (`AggregationParent`) |
| q18 | 1 orders -> semi 7; 10 lineitem -> join 11; 15 customer -> join 16 | 7 -> 4 | 4 -> merge agg 5 declined (24.0 GB parked, measured P1) |
| q21 | 2 l3 -> anti 10 (left/probe child); 13 l2 -> semi 14 | 9 -> 7 | 9 (F03 output, middle fragment) and 6 orders (broadcast) stay parked |
| q02 | 8 supplier -> join 9; 30 region -> join 31 | 13 -> 11 | **correction to the surgical design**: the senders to exchanges 6 (part) and 28 (partsupp) are middle fragments (`per_exch {3:1}` and `{21:1, 25:1}`, `all-dumps.txt` fragment-0022/0029), not leaves; they fuse only in PR 2 |
| q07 | 1 supplier, 3 nation -> join 4; 8 lineitem -> join 9 | 10 -> 7 | the 59.47 GB lineitem parking (B1) disappears |
| q13 | 2 orders, 4 customer -> join 5 (RIGHT OUTER) | 5 -> 3 | 18.18 GB orders parking disappears |
| q20 | 12 partsupp, 15 part -> join 16 (LEFT SEMI) | 8 -> 6 | 800M-row partsupp parking disappears |
| q01 q03 q04 q06 q10 q11 q12 q14 q15 q16 q19 q22 | none | unchanged (3, 5, 4, 2, 6, 9, 4, 3, 7, 5, 3, 6) | only broadcasts, gathers, or shuffles into merge aggregations; q01's F02 -> exchange 3 is declined `AggregationParent` (`all-dumps.txt` fragment-0004) |

`all-dumps.txt`'s `out_cols=Some` on some fragments is a regex hit on `TPlanNode.output_columns`;
the sink-level `TDataStreamSink.output_columns` is `None` on every TPC-H fragment
(`fragment-0137.txt:3661`, `fragment-0141.txt:~821`), so `SinkOutputColumns` never fires here.

4 CNs: every shuffle sink has 4 destinations and every gather receiver expects 4 senders
(`perf/survey/plan-summary-4cn.txt`), so `NotSingleDestination` / `ReceiverExpectsMany(4)` decline
everything; the 4-CN plans and the remote path are untouched.

---

## 8. Acceptance at SF1000 (orchestrator-run, one arm at a time)

Harness: the campaign's `perf/sf1000/capture-cn.sh` (100 GiB pool, 16 GiB staging, 160 GiB host,
watchdog 300 s, Quent on, `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`, dumps on for 1 CN) with `WT` pointed
at the fusion worktree, `run-queries.sh` (cold + N warm, `/* tag qNN rK */` marker),
`cnlog_extract.py`, `card_compare.py`, oracle compare against `../../oracle/tpch_sf1000/qNN.tsv` at
rel tol 1e-6. The knob is an environment variable read by the CN process; export
`SIRIUS_CN_FRAGMENT_FUSION` in the capture script's environment block (it is inherited through
`start-cluster.sh` -> `cluster8.sh` like `SIRIUS_CN_ENABLE_QUENT`). The CN's bring-up log line
`resolved CN transport tunables ... fusion_mode=Leaf` must be present in `cluster.log` for every arm.

**FE precondition for every golden count below (INTEGRATION.md 2.4).** The `fused` counts, the
`fragment run started` counts and the "11 of 15 plans byte-identical" statement were computed from
`perf/cn1/dump`, i.e. today's FE plans (every FILES() scan at cardinality 1). They hold only while the
FE runs without fix 3's patch or with `files_scan_estimate_row_count=false`. Arms A-E therefore run
**before** fix 3's FE is deployed (build fix 1+2+4a, today's FE); the integration sweep (INTEGRATION.md
V5a) records the new counts under fix 3's plans and those become the goldens from then on.

**Arm A. 1 CN, default (`leaf`), the six.** `q05 q08 q09 q17 q18 q21`, cold + 2 warm, timeout 300 s
(~6 min plus bring-up).

| check | must read |
|---|---|
| runs.csv | q05 q08 q17 q18 `pass` in all three runs; q09 `pass` (medium confidence, predicted peak 70-90 GB); q21 recorded either way (predicted: OOM at GPU_SCAN in the fused F07 with the build on l3). **Do not expect fix 1 to make it die in seconds** (INTEGRATION.md 2.2): a fused F07 holds its join build in inter-pipeline port repositories (`src/pipeline/repository_wiring_materializer.cpp:68-69`), which the downgrade sweep spills to the 160 GiB HOST tier (`downgrade_executor.cpp:223-232`), so the reschedule path sees `freed > 0` and fix 1's rule 5 keeps retrying; q21 may pass slowly through spilling or run up to 100 retries x spill rounds. Record: the `HASH_JOIN (id=10)` build port, the number of `after downgrade (N bytes freed)` lines with N > 0 and their sizes, the `[host_pool]` peak, and time-to-fail or pass. Expected warm medians (inference, standalone + fragment overhead): q05 3.2-4.0 s, q08 4-5 s, q09 6-7.5 s, q17 7-10 s (F05's partial avg over lineitem still runs first, 6.3 s today), q18 4.5-6.5 s; standalone reference 2786 / 3674 / 5427 / 3786 / 2691 / 6401 ms (perf/results-failed.md) |
| oracle | q17 q18 q21 `MATCH` exactly. q05 q08 q09: `MATCH`, or `VALUES-DIFFER` with identical row count, identical key columns and `maxreldiff <= 2e-3` confined to sum columns — the pre-existing FP64 decimal lowering seen on q01/q03/q07/q19 today (perf/compare-cn1.txt: 9.6e-4 .. 1.8e-3) while standalone matched exactly. Anything else is a fusion bug |
| cluster.log per query | `fused sender fragment into its local receiver` count = q05 4, q08 2, q09 2, q17 2, q18 3, q21 2; `fragment fusion skipped` = 0; `fragment run started` count = 7, 9, 7, 3, 4, 7 (section 7); one `fused deferred sender plans into receiver` line per fused receiver with `fused=` summing to the same counts |
| engine-cn0.log | no `oom_resched` for the five that pass (`cnlog_extract.py oom_resched=0`); the fused join fragment's pipeline print shows the lineitem `GPU_SCAN` feeding a `HASH_JOIN` on `port: default` (probe), as `engine-cn0.log:11572-11574` shows for a passing query today; for q21 read `HASH_JOIN (id=10) type:` and which child is `port: build` — this is the number the follow-up decision needs; `[gpu_pool] ... QueryEnd allocated=` returns to the pre-query baseline after each passing query (no leftover) |
| Quent (`quent_bp.py`) | no `STREAMING_SINK` of a lineitem leaf (the 104/99/96/66/36/25 GB parks of P1 are gone); the fused fragment is one Quent query labelled with the receiver's ids |

**Arm B. 1 CN, `leaf`, the 15 passing queries** (`q01 q02 q03 q04 q06 q07 q10 q11 q12 q13 q14 q15 q19 q20 q22`, cold + 2 warm, ~10 min).

| check | must read |
|---|---|
| oracle set | unchanged from perf/compare-cn1.txt: q02 q04 q06 q12 q13 q14 q20 q22 `MATCH`; q01 q03 q07 q19 the known 1e-3 diffs; q10 the known TOP-20 reorder; q15 flaky as before (fact 21); q11 `EMPTY` |
| fused counts | q02 2, q07 3, q13 2, q20 2; all others 0; `fragment fusion skipped` 0 |
| `fragment run started` | q02 11, q07 7, q13 3, q20 6; the other 11 exactly today's (`perf/cn1-cnlog.txt` `frags=`) |
| warm medians | the 11 unchanged queries within noise of perf/results.md (n=3 vs n=2: treat > 1.15x AND > 200 ms slower as a regression to triage, not as an automatic fail); q02/q07/q13/q20 within noise or faster; q07's largest parked `STREAMING_SINK in=` < 25 GB (was 59.47 + 18.38 GB, B1) |
| build sides | for q02 join 9/31, q07 join 4/9, q13 join 5, q20 join 16: `HASH_JOIN (id=N) type:` and the `port: build` child in engine-cn0.log are the same side as in perf/cn1/engine-cn0.log (customer build in q13, part build in q20, stream 6 build in q07's join 9); fused inline scans with conjuncts may now get `Wired dynamic filter` lines (`src/planner/sirius_plan_comparison_join.cpp:646-664`), which parked streams never did — record, not fail |

**Arm C. 1 CN, `SIRIUS_CN_FRAGMENT_FUSION=off`, q03 and q05** (cold only, timeout 300 s, ~4 min).
q03 passes with 5 fragment runs and 0 fused lines; q05 reproduces today's failure shape (OOM at
GPU_SCAN in the lineitem sender, `fragment run started` for the leaf present, no fused lines). This
proves the switch restores the old path without a rebuild.

**Arm D. 4 CNs, `leaf`, smoke `q03 q04 q07 q22`** (cold + 1 warm, ~6 min). Zero `fused sender
fragment` lines on every CN; `fragment run started` counts per CN identical to perf/cn4-cnlog.txt;
results and warm timings within noise of the cn4 campaign.

**Arm E (experiment, not gating). 1 CN, `leaf-any`, q09 q05 q08.** Record fused counts (q09 gains
8, 12, 16, 20), `[gpu_pool]` peak and warm medians against arm A. This is the evidence for whether
`leaf-any` should become the default; do not change the default in this PR.

**Definition of done for this PR**: unit/component tests green under `--no-default-features`,
clippy and fmt clean, the engine-backed test green on GPU 2, arms A-D as above (with q21 recorded
and q09's outcome written into the report either way), TUNABLES.md/DEMO.md updated, PR description
per `CONTRIBUTING.md` "PR reviewability" (Draft until then).

---

## 9. Risks and how the reviewer should probe them

1. **Estimated instead of exact cardinality for fused leaves (medium).** Today a receiver plans
   with exact parked row counts (`engine.rs:551-587`); after fusion a leaf carries the parquet
   footer count x 0.2. A wrong pick costs memory/time (OOM bounded by fix 1), never a wrong answer,
   because a single-destination sink is the identity on rows and every exchange decoration that
   could change results is refused (`sort_info`, `limit`, `offset`, `conjuncts`, aggregation
   parent). Probe: arm B's build-side check on q02/q07/q13/q20; arm C's A/B.
2. **q21 flips onto l3 (medium, expected).** Fused F07 is `LEFT SEMI(build l2 scan) over RIGHT
   ANTI(left = l3 scan, right = stream 9)` (`fragment-0500.txt` node 10 children in preorder:
   exchange 2 then exchange 9). The translator lowers `RIGHT_ANTI` to `Right` + `IS NULL`
   (`node_translator.rs:1750, 1837-1844`), and `TryFlipJoinChildren` compares stream 9 (exact
   ~1.85e9 rows) with the l3 estimate (6.0e9 x 0.2 = 1.2e9): the exact side looks bigger, DuckDB
   builds on l3 (actual 3.8e9 rows, the 77.76 GB measured in `cn1-quent.txt`) and OOMs. Probe: read
   the fused F07's `HASH_JOIN (id=10)` build port in engine-cn0.log before looking at the time.
   Route to a pass: PR 2 (`all`) makes stream 9 a join estimate too; fix 3 (real FE cardinalities)
   may reorder the supplier join below the anti as standalone does. Time-to-fail is governed by the
   downgrade's spill progress, not by fix 1 (arm A row; INTEGRATION.md 2.2).
3. **Translator invariants on the spliced plan (low).** `merge_exchange_overrides` is protected by
   `AggregationParent` (a leaf can never contain a `Merge` aggregation: it has no exchange to read
   from) and `PartialAggregationRoot`; `common_slots_consumed_above` and
   `refuse_carried_join_child` by `CommonSlotProjection` (verified no TPC-H exposure: the 10
   fragments with `common_slot_map: Some` in `perf/cn1/dump` are q01's F02 x3 and six q14/q08
   fragments, all sinking into merge-fed or broadcast exchanges that are declined anyway);
   `ensure_consumed` by `MalformedSenderPlan`. Probe: the translator tests in 5.1/5.2 and the
   `fused_receiver_dump_is_the_fused_plan` test; read one fused `fragment-NNNN.txt` from arm A.
4. **Arrival order (low).** Fusion needs the receiver registered before the leaf arrives. The FE
   deploys stage by stage from the root (`Deployer.java:208-215`) and this is measured for q05
   (`cluster.log:4142-4202`); under `enable_single_node_schedule` (default false,
   `SessionVariable.java:3337`) RPCs could race and the design declines, logging
   `NoPendingReceiver`. Probe: arm A's `fragment fusion skipped = 0`.
5. **Blast radius (low).** 11 of 15 passing 1-CN plans are byte-identical; 4 CNs untouched by
   construction (arm D). The only shared code touched on the remote path is the `SenderSource`
   enum gaining a variant; `push_remote_frame`'s `let SenderSource::Remote {..} = source else`
   (`local_exchange.rs:201-212`) already errors on a non-remote collision, so a `LocalPlan` under a
   remote frame's key is a loud error, not a silent misread.
6. **Host state after cancel (low).** A deferred plan is thrift (hundreds of KB), no GPU memory.
   Fix 2's `retire_receiver` drops it on cancel together with the receiver's other sources (4.6);
   until the cancel arrives it lives exactly as long as a parked slot does today. Probe:
   `cancel_drops_deferred_plans` and `retire_receiver_returns_a_deferred_plan_with_the_other_sources`.
7. **Telemetry and tooling (low).** A fused sender has no `fragment run started` line and no
   Quent query of its own; `cnlog_extract.py`'s `frags=` shrinks; arrival-time dumps are unchanged
   but the receiver's ready-time dump is the fused plan. The `fused sender fragment` line carries
   the sender's instance id so a stitch stays possible.
8. **Huge `Debug` output (low, easy to miss).** `TExecPlanFragmentParams`'s derived `Debug` is
   the 23k-line dump. `LocalPlan` must implement `Debug` by hand and no error string may embed
   params. Probe: grep the diff for `{:?}` / `?plan` on anything holding params.
9. **Sink identity assumptions (low).** `sender_shape` refuses `limit`, non-empty
   `output_columns` and `output_exprs`, the same three shapes `execute_fragment_with_inputs`
   (`:1079-1094`) and `translate_fragment_with_exchange_inputs` (`lib.rs:291-312`) treat
   specially today, so no sink-side transformation is ever dropped by the splice.

---

## 10. Landing order and dependencies

- **Builds and tests independently** of fixes 1-3 on `perf/profile-sf1000`, but **lands third**, after
  fix 1 and fix 2 (INTEGRATION.md section 3).
- **Fix 1 (fail fast, engine)**: lands first. It shortens arm A for anything that OOMs with
  `freed == 0`; for a fused q21 that spills it does not (arm A row, risk 2).
- **Fix 2 (parked bookkeeping, CN)**: lands before this PR; rebase onto it. Overlaps to expect
  (INTEGRATION.md 2.3, 4): `compute_node_service.rs` `cancel_plan_fragment` (take fix 2's version,
  nothing added here: 4.6), `execute_ready_fragment` (call `fold_deferred_plans` first, then fix 2's
  input split and `StagedLeases` guard over the streamed inputs), `process_fragment` (fix 2's gate 4
  sits before this PR's hook), `local_exchange.rs` (additive: `LocalPlan` variant + `offer_local_plan`
  next to fix 2's `retire_receiver` / `is_retired`; `forget_query` dropped), `DEMO.md:37-42` (adjacent
  bullets). Fix 2's `release_staged` / `release_sources` are non-exhaustive over `SenderSource`, so the
  new variant needs no arm. Fix 2's tests use UNPARTITIONED `data_stream_sink` fixtures and are not
  fused by the `Leaf` default. Fusion reduces fix 2's exposure at 1 CN (deferred senders park nothing).
- **Fix 3 (FILES() cardinalities, FE patch)**: complementary. If the FE then keeps lineitem inside
  the join fragment there is nothing to fuse; if the broadcast memory penalty still routes it
  through a single-destination exchange, fusion still applies. Note: with real statistics a
  1-CN lineitem broadcast becomes an `UNPARTITIONED` single-destination leaf, which `leaf` does
  not fuse and `leaf-any` does. Re-run arms A/B after fix 3 and revisit the default then.
- **PR 2 (`all` mode, middle fragments)**: stacked on this PR; gives q21 its real chance and
  removes q09's ~31 GB of broadcast parking; needs the `CONTRIBUTING.md` "Stacked PRs" flow
  (stacks merge bottom-up; never "Enqueue stack").
- Independent follow-ups the report lists: `RIGHT_SEMI_JOIN` translator arm (fix 3 companion,
  ~15 lines), not emitting the FE's join-key `IS NOT NULL` as a `Filter` (C2/BJ-4).

PR shape: **one fork PR against `dev`** (CN path = personal fork per the carve plan; open as Draft),
two commits: (1) translator `fusion.rs` + tests (no behaviour change alone), (2) CN wiring, knob,
tests, docs (no cancellation code: fix 2 owns it). Split into a stack of two only if the reviewer asks. Size: ~450 lines
of code, ~700 of tests (inference).

Commit / PR title (Conventional Commits, scope as in `perf(cn):`, `feat(cn):`, `docs(cn):` in the
log):

```
feat(cn): fuse single-destination local leaf senders into their receiver plan
```

Body, in the repo's unslop style: one paragraph on the mechanism (park the plan, not the rows;
splice at the `EXCHANGE_NODE`; DuckDB picks the build side), the knob and its default, what is
deliberately not fused, one short testing note (which cargo tests, which SF1000 arms and their
outcome including q21), and the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer.

---

## 11. Decisions that need the user

1. **Acceptance for q21.** The plan's row says all six must pass. This PR predicts 5/6 with q21
   still OOM (recorded, with the build side read from the engine log). Accept 5/6 + PR 2 as the
   route to q21, or hold "done" until PR 2 lands?
2. **Oracle wording for q05/q08/q09.** Accept the known ~1e-3 decimal-lowering deviation on sum
   columns (as the campaign already does for q01/q03/q07/q19) instead of the plan's "oracle
   MATCH"? Section 8 is written that way.
3. **Default mode.** `leaf` (HASH_PARTITIONED leaves only; 11 of 15 plans identical) as specified,
   or `leaf-any` if arm E shows q09 needs the headroom? Recommendation: ship `leaf`, decide from arm E.
4. ~~`forget_query` in this PR or leave cancellation entirely to fix 2?~~ **Resolved at integration**
   (INTEGRATION.md 2.7 item 1): fix 2 lands first and owns cancellation; `forget_query` is not shipped
   (4.3, 4.6).
5. **One PR with two commits vs a two-PR stack** (translator module, then CN). Recommendation: one PR.

---

## Appendix: evidence lines relied on

- Six-shape and sizes: `perf/cn1-cnlog.txt` (q05..q21 `fail ... oom_resched`), `perf/cn1/dump/fragment-0141.txt`
  (node 8 `row_tuples [6]`, `per_node_scan_ranges {8: ..}` at :4775, `node_to_per_driver_seq_scan_ranges Some({})`
  at :23178, `per_exch_num_senders {}` at :23135, `output_exprs: None` at :708), `fragment-0137.txt`
  (exchanges 7/9 `num_children 0, limit -1, conjuncts None, sort_info None, offset Some(0)` at :2537-2565 and
  :2999-3026; sink `output_columns: None, limit: None` at :3661; `per_exch_num_senders {7:1, 9:1, 13:1}` at :7703-7707),
  `fragment-0500.txt` (q21 receiver: node 10 `RIGHT ANTI` children exchange 2 then 9; `per_exch {2:1, 9:1, 13:1}` at :7570).
- Parsed dump summary: `fix/designs/all-dumps.txt` (per-fragment sink/partition/destinations/per_exch and node preorder).
- Standalone contrast: `perf/results-failed.md`, `perf/compare-standalone-failed.txt` (6/6 MATCH),
  `perf/standalone-failed/runs/qNN.explain.txt`.
- CN-path oracle deviations today: `perf/compare-cn1.txt`.
- q04 engine-side flip: `perf/cn1/engine-cn0.log:48480` (`type: RIGHT_SEMI`), pipeline print shape `:11572-11574`.
- Deploy order: `perf/cn1/cluster.log:4142-4202`; `fragment run started` line shape `:973`.
- Passing-query fragment counts: `perf/cn1-cnlog.txt` `frags=` (q02 13, q03 5, q04 4, q07 10, q10 6, q13 5, q20 8, q22 6).
- 4-CN plans: `perf/survey/plan-summary-4cn.txt`.
