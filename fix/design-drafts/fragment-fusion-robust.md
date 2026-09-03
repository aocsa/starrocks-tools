# Fix 4 design (robust angle): same-node single-sender fragment fusion at translation time

Written 2026-09-03 for the SF1000 top-4 plan (`sf1000-top4-fixes-plan.md`, issue 4). Source tree
read: `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be`. Evidence:
`scratchpad/perf/sf1000/` (cn1-cnlog.txt, cn1/dump, standalone-failed/, results-ratios.md) and the
report findings P1, P5, B1, B2 (`sf1000-planning-cardinality-backpressure.md`).

Every claim below is either **measured** (with the evidence file) or marked **inference**.

---

## 0. Decision in one paragraph

Fuse an exchange edge `S -> (R, E)` on the CN *before translation* whenever the sender fragment `S`
has exactly one destination, that destination is this CN, the receiver `R` is registered and still
pending, and `R.per_exch_num_senders[E] == 1`. Fusion is pure bookkeeping in `LocalExchange` (the
receiver keeps `S`'s thrift params under the exchange node id it replaces, and inherits `S`'s own
exchange inputs through an instance-id redirect) plus one translator extension (`translate_exchange`
translates the grafted sender plan in place of the stream read). No C++ engine change is needed:
the fused plan is an ordinary, larger Substrait plan that the engine's own DuckDB optimizer
re-plans with parquet cardinalities, which is exactly the standalone shape that passed all six
queries (`standalone-failed/runs/runs.csv`). On 1 CN every exchange is local and single-sender, so a
query collapses to one fragment per two-phase aggregation boundary (those boundaries are kept, see
section 3.4). The change is Rust-only (translator crate + CN), guarded by one enum knob
`SIRIUS_CN_FRAGMENT_FUSION = off | leaf | all` (default `all`), logs every fuse/skip decision with a
reason, and enumerates its failure modes in section 8.

The engine-side alternative (spillable parked repositories, plan item 4(b)) is not chosen: it keeps
the full materialisation that finding B1 identifies as the root cause, needs C++ changes in four
places (`streaming_fragment.cpp:103-113`, `downgrade_executor.cpp:203-232`, `sirius_ffi.cpp:822-826`
and `:1062-1066`), and even at 279 GB GPU+HOST rescues five of six slowly with q09 marginal
(B2 impact arithmetic). It stays the right tool for *multi*-CN shuffle/broadcast receivers, which
fusion by construction does not touch. Section 10 has the side-by-side.

---

## 1. The measured problem, restated as the fusion target

**Measured (P1, cn1-cnlog.txt).** All six 1-CN failures are one plan shape: an unfiltered lineitem
`FILE_SCAN_NODE` alone in a fragment whose `DATA_STREAM_SINK` has one destination on this CN. The
CN runs it as `role=sender inputs=0 outputs=1`, and because a receiver runs only after every sender
has parked (B1), the whole projection (73-245 GB, P1 table) must sit in the 100 GiB pool.

```
q05.r0: fail 101460ms frags=7 {'sender': 7} ... oom_resched=2500/{'GPU_SCAN': 2500}
q08.r0: fail 168285ms frags=8 {'sender': 8} ... oom_resched=3600/{'GPU_SCAN': 3600}
q09.r0: fail 232533ms frags=6 {'sender': 6} ... oom_resched=5600/{'GPU_SCAN': 5600}
q17.r0: fail 103894ms frags=3 {'sender': 3} ... oom_resched=2100/{'GPU_SCAN': 2100}
q18.r0: fail  54202ms frags=4 {'sender': 4} ... oom_resched=2300/{'GPU_SCAN': 2300}
q21.r0: fail 114485ms frags=5 {'sender': 5} ... oom_resched=1800/{'GPU_SCAN': 1800}
```
(`scratchpad/perf/sf1000/cn1-cnlog.txt`)

**Measured (standalone contrast).** The same SQL on the same GPU and pool passes because DuckDB's
plan streams lineitem as the probe of a hash join: q05 5191/2786 ms, q08 3835/3674, q09 6818/5427,
q17 4386/3786, q18 2764/2691, q21 6534/6401 (cold/warm, `standalone-failed/runs/runs.csv`), all
oracle MATCH (`compare-standalone-failed.txt`). `standalone-failed/runs/q05.explain.txt` shows
`READ_PARQUET lineitem ~6,110,827,680 rows` as the probe child of a `HASH_JOIN` whose build is the
~61.6M-row orders x customer chain.

**The exchange is the only reason the CN materialises.** Inside a fragment the engine streams:
q21's post-failure fragment streamed a 123 GB lineitem scan through `FILTER -> HASH_JOIN` in 3.15 s
(B1, cn1-quent.txt q21.r0). So the fix is to remove the exchange from the plan the engine sees when
both sides are on this CN.

**Why the engine will re-plan the fused tree the standalone way (measured mechanism).** Every
fragment is lowered through DuckDB's optimizer with join ordering and build/probe selection
enabled:

```cpp
// src/sirius_ffi.cpp:134-138
    auto logical_plan = std::move(planner.plan);
    if (client.config.enable_optimizer) {
      duckdb::Optimizer optimizer(*planner.binder, client);
      logical_plan = optimizer.Optimize(std::move(logical_plan));
    }
// src/sirius_ffi.cpp:233-243  (what is disabled: IN_CLAUSE, COMPRESSED_MATERIALIZATION,
//                              STATISTICS_PROPAGATION, COLUMN_LIFETIME, LATE_MATERIALIZATION)
```
`JOIN_ORDER` (`duckdb/src/optimizer/optimizer.cpp:205`) and `BUILD_SIDE_PROBE_SIDE` (`:247`) are
not disabled. WORKFLOW-PLAN fact 11 and finding PC-6 measured this re-planning inside fragments
(q19 built on filtered part and probed the 128.6M-row stream; q07 flipped three joins). The FE's
`PARTITIONED`/`BUCKET_SHUFFLE`/`BROADCAST` labels are irrelevant to the engine: `translate_hash_join`
emits a plain Substrait `JoinRel` (`node_translator.rs:1809-1823`), and the FE's `distribution_mode`
field (`PlanNodes.thrift:790`) is never read.

---

## 2. What the CN does today (the code the design changes)

### 2.1 Arrival: receivers register, senders run

```rust
// experimental/starrocks/src/compute_node_service.rs:918-951
    fn process_fragment(&self, params: &TExecPlanFragmentParams)
        -> std::result::Result<FragmentOutcome, String> {
        let params = self.resolve_descriptor_table(params)?;
        let dump_seq = Self::dump_fragment(&params);
        ...
        let expected_senders = Self::receiver_exchanges(&params)?;
        if !expected_senders.is_empty() {
            let fragment_instance_id = Self::fragment_instance_id(&params)...;
            if Self::is_mysql_result_sink(&params)? { ... self.results.reserve(fragment_instance_id, query_id); }
            return Ok(FragmentOutcome::from_ready(
                self.exchanges.register_receiver(fragment_instance_id, expected_senders, params)?
                    .into_iter().collect()));
        }
        let translated = self.translate_fragment_logged(&params, dump_seq)?;
        self.execute_fragment(&params, translated)
    }
```
`receiver_exchanges` (`:1397-1431`) lists every `EXCHANGE_NODE` with its
`params.per_exch_num_senders[node_id]`. A fragment with no exchange runs immediately; with the
opt-in `SIRIUS_CN_ASYNC_SENDER_DISPATCH` it is queued first (`try_dispatch_sender`, `:309-329`,
called from `exec_single_attachment` `:713` and `translate_batch_attachment` `:1661-1666`).

### 2.2 The sender parks and completes the rendezvous

```rust
// compute_node_service.rs:1099-1104, 1145-1172, 1189-1206
        let destinations = exec.destinations.as_ref().filter(|d| !d.is_empty())
            .ok_or_else(|| "DATA_STREAM_SINK fragment has no destinations".to_string())?;
        ...
        for destination in destinations {
            let slot = SenderSlot { fragment_instance_id: FragmentInstanceId::from(&destination.fragment_instance_id),
                                    node_id: stream_sink.dest_node_id, sender_id };
            ...
            let route = self.route_destination(destination)?;   // Local iff brpc_server == self.identity
            ...
        }
        self.run_labeled("sender", FragmentRun { plan: &translated, inputs, remote_inputs,
                                                  outputs: slots.clone(), broadcast, hash_keys, label })?;
        for (slot, route) in slots.iter().zip(&routes) {
            if matches!(route, DestinationRoute::Local) {
                let ready = self.exchanges.push_sender(ExchangeKey { fragment_instance_id: slot.fragment_instance_id,
                                                                     node_id: slot.node_id }, sender_id,
                                                       SenderSource::LocalParked { names: ..., slot: *slot })?;
                ready_receivers.extend(ready);
            }
        }
```
`route_destination` (`:1251-1275`) classifies against `ExchangeIdentity` (host + brpc port,
`:61-86`). One destination is a gather regardless of the partition label (`:1105-1106`), which is
why a `HASH_PARTITIONED` sink with one destination is semantically the identity.

### 2.3 The rendezvous

```rust
// experimental/starrocks/src/local_exchange.rs:82-95
struct PendingReceiver { params: TExecPlanFragmentParams, expected_senders: HashMap<i32, usize> }
struct ExchangeState {
    receivers: HashMap<FragmentInstanceId, PendingReceiver>,
    sources: HashMap<ExchangeKey, HashMap<i32, SenderSource>>,
    remote_seq: HashMap<(ExchangeKey, i32), i64>,
}
// :105-139 register_receiver  -> take_ready
// :142-155 push_sender        -> take_ready
// :159-246 push_remote_frame  -> take_ready
// :248-313 take_ready: returns the receiver only when every expected sender is complete,
//          then removes it and its sources and builds ReadyFragment { params, inputs }
```
The receiver is translated only when ready, binding each exchange to a stream view:

```rust
// compute_node_service.rs:1358-1362
        let dump_seq = Self::dump_fragment(&ready.params);
        let translated =
            self.translate_fragment_logged_with_inputs(&ready.params, &exchange_inputs, dump_seq)?;
```

### 2.4 The translator turns an exchange into a stream read

```rust
// crates/starrocks-plan-translator/src/node_translator.rs:637-663, 732-745
fn translate_exchange(node: &TPlanNode, children: Vec<TranslatedRel>, ctx: &mut PlanContext<'_>)
    -> Result<TranslatedRel> {
    expect_children(node, &children, 0)?;
    let exchange = node.exchange_node.as_ref().ok_or(...)?;
    if exchange.input_row_tuples.is_empty() { return Err(...) }
    let input = ctx.exchange_inputs.get(&node.node_id).ok_or(TranslateError::UnsupportedPlanNode {
        node_id: node.node_id, node_type: node.node_type,
        reason: "exchange node requires a bound same-node input stream" })?;
    let mut schema = ctx.desc.named_struct_for_tuples(&exchange.input_row_tuples)?;
    ...
    ctx.stream_inputs.push(StreamInputSchema { node_id: node.node_id, stream_view: input.stream_view.clone(), columns });
    let mut translated = TranslatedRel { rel: stream_read_rel(schema, &input.stream_view),
                                         row_tuples: exchange.input_row_tuples.clone(), output_width,
                                         carried_slots: Vec::new() };
    if let Some(sort_info) = &exchange.sort_info { ... SortRel over translated ... }
    apply_conjuncts(translated, node, ctx)
}
```
The caller wraps every node with `apply_fetch` (`node_translator.rs:248`, `:258-297`), which also
honours `exchange_node.offset`. Two pre-passes run over the flat node list before translation:
`merge_exchange_overrides` (`:438-483`, marks exchanges that feed a `Merge` aggregation and refuses
a merge aggregation whose child is not an exchange) and `common_slots_consumed_above` (`:499-556`).
The fragment root narrows carried common-expr columns (`lib.rs:320 confine_carried`,
`node_translator.rs:2338-2345`).

### 2.5 The row-layout guarantee fusion relies on

The FE builds an `ExchangeNode`'s tuple ids from its child's:

```java
// fe-core/src/main/java/com/starrocks/planner/ExchangeNode.java:180-185
    @Override
    public final void computeTupleIds() {
        clearTupleIds();
        tupleIds.addAll(getChild(0).getTupleIds());
        nullableTupleIds.addAll(getChild(0).getNullableTupleIds());
    }
```
and ships them as `TExchangeNode.input_row_tuples` ("The ExchangeNode's input rows form a prefix of
the output rows it produces", `PlanNodes.thrift:1170-1173`). **Measured on the q05 dumps:** the
receiver's exchange 9 has `input_row_tuples: [6]` (`cn1/dump/fragment-0137.txt:3003-3030`) and the
lineitem sender's root node 8 has `row_tuples: [6]` (`cn1/dump/fragment-0141.txt:11-27`). Slot
resolution in the translator is by `(tuple_id, slot_id)` over `row_tuples`
(`DescriptorTable::slot_global_index`, used at `node_translator.rs:1776-1779`), and the descriptor
table is per query (`resolve_descriptor_table`, `compute_node_service.rs:954-988`), so a grafted
subtree resolves identically to the stream read it replaces.

### 2.6 Dispatch order: receivers arrive before their senders (measured, with the FE mechanism)

`cn1/dump` sequence numbers are assigned at arrival (`dump_fragment`, `:992-1004`). For q05
(query hi 117207244681936527): 0134 (result, node 30) -> 0135 (29,28,27) -> 0136 (26..16,18) ->
0137 (15..7) -> 0138 (17 -> dest 18) -> 0139 (22,21 -> 23) -> 0140 (6,5,1,4 -> 7) -> 0141
(8 lineitem -> 9) -> 0142 (12 -> 13) -> 0143 (0 -> (0140, 1)) -> 0145 (3,2 -> (0140, 4)). Every
receiver precedes its senders. The FE deploys fragments in topological order from the root
(`ExecutionDAG.getFragmentsInTopologicalOrderFromRoot`, `qe/scheduler/dag/ExecutionDAG.java:195-218`)
and waits for each stage's RPCs before the next (`Deployer.java:208-215`):

```java
        for (List<FragmentInstanceExecState> executions : threeStageExecutionsToDeploy) {
            ... executions.forEach(FragmentInstanceExecState::deployAsync);
            ... waitForDeploymentCompletion(executions);
        }
```
The design does not depend on this order for correctness (section 3.6), only for fusion to trigger.

---

## 3. Mechanism

### 3.1 Vocabulary

- **Fusable edge**: sender fragment `S` -> receiver fragment `R` at exchange node `E`, where
  `S.destinations.len() == 1`, `route_destination(dest) == Local`, `R` is a pending receiver on
  this CN whose `expected_senders[E] == 1`, and the structural checks of 3.3 pass.
- **Graft**: `R` records `S.params` under `E`; `E` leaves `R.expected_senders`; `S`'s own
  exchange inputs (if any) join `R.expected_senders`; `S.instance -> R.instance` is recorded in an
  `absorbed` redirect map so anything later addressed to `S` (senders, remote frames, cancels)
  reaches `R`.
- **Fused translation**: when `R` becomes ready, the translator lowers `R`'s plan with
  `translate_exchange(E)` producing the translated sender subtree instead of a stream read.

### 3.2 Where the decision is made: sender arrival

New `ServiceCore::try_fuse(&params) -> Result<Option<FragmentOutcome>, String>` runs before
`try_dispatch_sender` in both entry points (`exec_single_attachment` `:705-717` and the per-instance
loop in `translate_batch_attachment` `:1646-1676`). Steps:

1. `resolve_descriptor_table` and `dump_fragment` exactly as today (the survey tooling keeps seeing
   every FE fragment; `SIRIUS_CN_TRANSLATE_ONLY` skips fusion entirely).
2. Read the knob. `off` -> return `None`. `leaf` -> only a sender with no `EXCHANGE_NODE` is a
   candidate. `all` -> any sender.
3. Sink shape: `output_sink.type_ == DATA_STREAM_SINK`, `destinations.len() == 1`, destination
   routes `Local`, `stream_sink.limit` unset, `stream_sink.output_columns` identity or absent,
   `fragment.output_exprs` empty. Anything else -> `None` with a logged reason (these are the same
   conditions `execute_fragment_with_inputs` already enforces or that would change the sink row,
   `:1079-1094`). **Measured:** none of the six leaf senders carries `output_exprs`
   (`grep -c "output_exprs: Some" fragment-{0141,0208,0220,0427,0437,0504}.txt` = 0; the 151
   fragments that do are result fragments, e.g. `fragment-0134.txt`).
4. Ask the translator for the structural verdict: `PlanTranslator::exchange_fusability(receiver,
   sender, E)` (3.3). This needs the receiver's params, so it is evaluated inside the
   `LocalExchange` lock via a closure (3.5).
5. `self.exchanges.try_fuse_sender(key, params, sender_exchanges, verdict_fn)`:
   - `Fused(None)`: `S` is absorbed, `R` still waits -> the RPC returns OK immediately, nothing
     runs. Log `fused sender fragment into its local receiver` with `query_id`, `sender_instance`,
     `receiver_instance`, `exchange_node_id`, `inherited_exchanges`.
   - `Fused(Some(ready))`: the graft completed `R` -> `dispatch_then_join(FragmentOutcome::from_ready(vec![ready]))`,
     the same path a completing sender takes today (`:716`).
   - `NotFused(reason)`: fall through to today's behaviour (async queue or run+park). Log
     `fragment fusion skipped` with the reason enum (`NoPendingReceiver`, `ReceiverExpectsMany(n)`,
     `ExchangeAlreadySourced`, `PolicyLeafOnly`, `Structural(<translator reason>)`).

`process_fragment` is otherwise unchanged; a sender that was not fused still registers as a
receiver (if it has exchanges) or runs.

### 3.3 Structural fusability (translator, pure function over thrift)

`exchange_fusability(receiver: &TExecPlanFragmentParams, sender: &TExecPlanFragmentParams, node_id: i32) -> Result<(), FusionRefusal>`:

| check | why | refusal |
|---|---|---|
| `node_id` is an `EXCHANGE_NODE` of `receiver.fragment.plan` | addressing sanity | `ExchangeMissing` |
| `node_id` is **not** a key of `merge_exchange_overrides(receiver_plan)` | an exchange feeding a `Merge` aggregation carries partial-state wire columns; the translator requires "a merge aggregation must read its partial states directly from an exchange" (`node_translator.rs:453-463`) and a partial aggregation with avg "must be the fragment root" (`:406-414`). Fusing it would need collapsing update+merge into one aggregation, which is real translator work and not needed for the six (the parked partial states are small: q05 25 groups, q17 ~200M groups, q18 ~24 GB measured P1) | `MergeFedExchange` |
| `sender.fragment.output_exprs` empty | the exchange's row is the sender root's descriptor row; a sink-side projection would change it (`lib.rs:291-312`) | `SenderHasOutputExprs` |
| `sender.plan.nodes[0].row_tuples == exchange.input_row_tuples` | the receiver resolves slots against the exchange's layout (2.5) | `RowLayoutMismatch{expected, got}` |
| sender plan non-empty, `nodes[0].num_children` consistent | malformed guard | `Malformed` |

Not refused (handled in translation instead): `sort_info` (the merging-exchange sort is still
applied on top of the fused subtree, 3.4), `offset`/`limit` (`apply_fetch` still wraps the exchange
node), `conjuncts` on the exchange (`apply_conjuncts` still runs), the FE's `distribution_mode`
and `partition_type` (never read).

### 3.4 Fused translation

`PlanTranslator::translate_fragment_with_inputs(params, exchange_inputs, fused: &[FusedFragment])`
generalises `translate_fragment_with_exchange_inputs` (`lib.rs:247-437`); the two-argument form
stays as a wrapper. `FusedFragment { node_id: i32, params: &TExecPlanFragmentParams }`. Changes in
`lib.rs`/`node_translator.rs`:

1. **Scan paths.** `ScanFilePaths::from_fragment(params, &desc)` (`scan_paths.rs:62-93`) is keyed
   by scan node id; node ids are query-global. Add `ScanFilePaths::extend_from_fragment(&mut self,
   params, desc)` that refuses a node id already present (a scan node lives in exactly one
   fragment) and call it for every fused fragment, recursively, before translation. One
   `ScanFilePaths` for the whole fused plan; `PlanContext.scan_paths` is unchanged.
2. **Pre-passes.** `PlanContext` gets `fused: HashMap<i32, &TPlan>`. `merge_exchange_overrides`
   and `common_slots_consumed_above` are run for `R`'s plan (as today) and, lazily when a fused
   exchange is translated, for the grafted plan, extending `exchange_state_overrides` and
   `consumed_above` (collision on a node id -> `Malformed`). The FE's boundary materialises
   anything an ancestor fragment needs, so the "consumed above" set of a sender plan is complete
   within that plan; running the pre-pass per fragment plan is the same computation the CN does
   today, just on the same `PlanContext`.
3. **`translate_exchange`.** Before the `ctx.exchange_inputs.get(...)` lookup:
   ```rust
   if let Some(sub_plan) = ctx.fused.get(&node.node_id).copied() {
       return translate_fused_exchange(node, exchange, sub_plan, ctx);
   }
   ```
   `translate_fused_exchange`: run the two pre-passes for `sub_plan`; `let mut sub = sub_plan.translate(ctx)?`
   (the `TranslatePlan for TPlan` cursor, `node_translator.rs:164-175`, which also enforces
   `ensure_consumed`); `sub = confine_carried(sub)` (what the sender's fragment root does today at
   `lib.rs:320`, so a carrying root never reaches `refuse_carried_join_child`, `:1880-1895`);
   assert `sub.row_tuples == exchange.input_row_tuples` and `sub.output_width ==
   desc.materialized width of those tuples` (loud `descriptor` error otherwise); assert
   `ctx.partial_expansion` did not change (a partial avg inside a grafted plan is impossible given
   the merge-fed refusal, so a change is a bug); then continue with the existing `sort_info` and
   `apply_conjuncts` code with `translated = sub`. No `StreamInputSchema` is pushed for a fused
   exchange, so `TranslatedPlan.stream_inputs` lists only real stream inputs, and the engine
   declares only those (`engine.rs:508-587`).
4. **Nesting.** A grafted plan's own `EXCHANGE_NODE`s are looked up in the same flat `fused` map
   (fused) or `exchange_inputs` (real stream) — no tree structure is needed because node ids are
   query-global.
5. **Output.** `output_names`, the root guard (`lib.rs:336-342`) and `output_partition_columns`
   (`lib.rs:347-410`) are computed from `R`'s root and sink exactly as today.

Result for q05 on 1 CN (`all`): two fragments instead of eleven. Fragment A = 0136's plan with
exchanges 16, 18, 23, 13, 9, 7, 1, 4 all grafted (0137, 0138, 0139, 0142, 0141, 0140, 0143, 0145),
i.e. the standalone-shaped six-table join tree under the partial aggregation 26, sink -> exchange
27. Exchange 27 feeds `28: merge finalize` and stays a boundary (partial state: 25 nation groups).
Fragment B = 0134's result plan with 0135 grafted at exchange 30 (a merging exchange:
`fragment-0134.txt:35 sort_info: Some(...)`; the `SortRel` is kept on top of the fused
`29 SORT -> 28 AGG -> 27 EXCHANGE(stream)` subtree).

### 3.5 `LocalExchange` changes (bookkeeping under the existing mutex)

```rust
struct PendingReceiver {
    params: TExecPlanFragmentParams,
    expected_senders: HashMap<i32, usize>,
    /// Sender fragments grafted into this receiver's plan, keyed by the EXCHANGE_NODE they replace.
    fused: HashMap<i32, FusedSender>,          // FusedSender { params, label: FragmentLabel }
}
struct ExchangeState {
    receivers, sources, remote_seq,            // unchanged
    /// Instances absorbed by fusion -> the receiver that now owns their exchanges. Flattened on
    /// insert (every entry points at a live receiver, never at another absorbed instance).
    absorbed: HashMap<FragmentInstanceId, FragmentInstanceId>,
}
pub(crate) struct ReadyFragment { params, inputs, fused: Vec<FusedSender> }   // fused added

pub(crate) enum FuseOutcome { Fused(Option<ReadyFragment>), NotFused(FuseSkip) }
pub(crate) enum FuseSkip { NoPendingReceiver, ReceiverExpectsMany(usize), ExchangeAlreadySourced,
                           Structural(String) }

pub(crate) fn try_fuse_sender(&self, key: ExchangeKey, sender: TExecPlanFragmentParams,
    sender_exchanges: Vec<(i32, usize)>,
    verdict: impl FnOnce(&TExecPlanFragmentParams) -> Result<(), String>,   // exchange_fusability(receiver, ...)
) -> Result<FuseOutcome, String>
```
`try_fuse_sender`, under `self.lock()`:

1. `owner = absorbed.get(&key.fragment_instance_id).unwrap_or(key.fragment_instance_id)`.
2. `receiver = receivers.get_mut(&owner)` else `NotFused(NoPendingReceiver)`.
3. `expected = receiver.expected_senders.get(&key.node_id)` else `Err("exchange {E} is not an input of receiver {owner}")`
   (the FE addressed a sink at an exchange the receiver does not declare: a malformed dispatch).
4. `expected != 1` -> `NotFused(ReceiverExpectsMany(expected))`.
5. `sources.get(&key)` non-empty -> `NotFused(ExchangeAlreadySourced)` (cannot happen with
   `expected == 1`; kept as a guard).
6. `verdict(&receiver.params)` -> `Err(reason)` -> `NotFused(Structural(reason))`.
7. Graft: `receiver.expected_senders.remove(E)`; `receiver.fused.insert(E, sender)` (duplicate ->
   `Err`); for `(e, n)` in `sender_exchanges`: `insert` into `receiver.expected_senders` (duplicate
   node id -> `Err`); `absorbed.insert(sender_instance, owner)` and re-point every entry that
   mapped to `sender_instance` (none exist if the sender was never a receiver, which is the
   receiver-first case; the loop keeps the map flat regardless); move any `sources`/`remote_seq`
   entries keyed by `(sender_instance, e)` to `(owner, e)` (defensive; empty under receiver-first).
8. `take_ready(state, owner)`: unchanged logic; with `expected_senders` empty it proceeds straight
   to building the `ReadyFragment` (the `for` at `:255-280` has nothing to wait on) and now moves
   `receiver.fused` into it. `register_receiver`'s "no exchange inputs" refusal (`:111-113`) stays
   — it applies to registration, not to a receiver emptied by fusion.

`push_sender` (`:142-155`) and `push_remote_frame` (`:159-246`) resolve `key.fragment_instance_id`
through `absorbed` first, so a later sender or remote frame addressed at an absorbed instance
completes the owner. This is what makes fusion of *intermediate* fragments safe on multi-CN
clusters too: if `S` (one local destination) is absorbed into `R` but `S`'s own exchange `e` has
four senders (three remote), the three remote `transmit_packed` frames carry `S.instance` in their
`finst_id` (`handle_transmit_packed`, `:721-748`) and are redirected to `R`, whose
`expected_senders[e] == 4` still counts them.

The `SenderSlot` a non-fused sender parks under keeps the *original* destination instance id
(`compute_node_service.rs:1148-1152`); it is an opaque key into the engine thread's `parked_slots`
(`engine.rs:267-268`, looked up at `:562-564` and `:625-628`), so no engine-side renaming is
needed. The engine stream id is the exchange node id (`stream_id_of`, `engine.rs:733-735`), also
unchanged.

### 3.6 Behaviour under the two dispatch orders

- **Receiver first (measured FE order, 2.6):** every fusable edge fuses at sender arrival. On 1 CN
  a query collapses to `1 + (#merge-fed exchanges)` fragments.
- **Sender first (not observed; defensive):** `NotFused(NoPendingReceiver)` -> the sender runs and
  parks exactly as today; the receiver, when it arrives, sees a parked source and reads a stream.
  Correct, slower, logged at `info` so a change in FE behaviour is visible in the CN log.

There is no third state: a sender is either absorbed (never runs on its own) or runs today's path.
Nothing is ever held waiting for a fragment that may not come, so fusion cannot introduce a hang.

### 3.7 Execution and attribution of a fused fragment

`execute_ready_fragment` (`:1325-1394`) passes `ready.fused` to the translator; `run_labeled`
(`:1540-1581`) gains a `fused = <n>` field and the `fragment run started/finished/failed` lines
list the absorbed instance ids (bounded: fragment instances per query are tens at most). The Quent
query label stays `<query id>:<receiver instance id>` (`FragmentLabel`, `fragment_executor.rs:58-84`)
so per-operator telemetry of the fused run is attributed to the receiver that owns the plan; the
fused instance ids are in the CN log line next to it.

Failure: an absorbed sender's RPC has already returned OK, exactly like a queued sender under
`SIRIUS_CN_ASYNC_SENDER_DISPATCH` (`:284-288`). A translation or run failure of the fused fragment
lands in `run_ready_fragment`'s `Err` arm (`:875-909`) -> `results.fail_query(query_id, id, error)`
(`result_store.rs:163`), which the FE observes through `fetch_data` — the same path an intermediate
receiver failure takes today (`:883-896`). The error text includes the fused exchange node id and
sender instance when the failure is a translation refusal (translator errors already name
`node_id`/`node_type`).

### 3.8 Cancellation and the interaction with fix 2

Fusion adds two pieces of per-query host state to `LocalExchange`: `PendingReceiver.fused`
(thrift params of absorbed senders, hundreds of KB each) and `absorbed` entries. Neither pins GPU
memory. Today `cancel_plan_fragment` (`:457-479`) only touches the result store, and
`LocalExchange` removes state only in `take_ready` (`:282-308`) — B4. Fix 2's design adds a
by-query removal (`LocalExchange::remove_query(query_id)` or equivalent). This design requires that
removal to also drop `fused` (it goes with the `PendingReceiver`) and `absorbed` entries whose
owner is that query (store `query_id` on `PendingReceiver`, or derive it from
`params.params.query_id` at removal time). If fusion lands before fix 2, it ships a minimal
`LocalExchange::forget_query(query_id)` for its own maps and `cancel_plan_fragment` calls it;
fix 2 then subsumes it. Without either, the cost of a cancelled query is a bounded host-memory
leak of thrift params, not a GPU leak — strictly better than today, where the same query's senders
would run and park GPU output nobody consumes (B4).

### 3.9 Configuration and observability

- `SIRIUS_CN_FRAGMENT_FUSION = off | leaf | all`, default `all`. Read once in
  `SiriusComputeNodeService::with_transport` (`:238-269`) into `ServiceCore.fusion_mode`
  (`AtomicU8`, with a `#[cfg(test)] set_fusion_mode` like `set_async_sender_dispatch`, `:299-303`).
  An unrecognised value fails bring-up (rule 1 of `tunable.rs:1-20`: rejected, never ignored); the
  resolved value is logged at bring-up with the other knobs; documented in
  `experimental/starrocks/docs/TUNABLES.md` under a new "Dispatch" table next to
  `SIRIUS_CN_ASYNC_SENDER_DISPATCH`.
- Per edge: one `info!` line for a fuse (ids + inherited exchange count) and one for a skip (reason
  enum). Per fused run: `fused = n` on `fragment run started`. The cnlog digest tool
  (`scratchpad/perf/sf1000/cnlog_extract.py`) gains the fused count per run so `frags=` in the
  digest becomes "FE fragments / engine runs".
- Dumps: arrival dumps unchanged (every FE fragment still dumped); the fused Substrait is dumped
  by `translate_fragment_logged_with_inputs` -> `dump_substrait` (`:1466-1478`) under the
  receiver's fresh `dump_seq`, so `plan-<seq>.substrait` is the plan the engine ran.

---

## 4. Behaviour on the six SF1000 failures (1 CN, `all`)

Fragment shapes from `survey/plan-summary.txt`; merge-fed exchanges from its `aggs=` lists; the
leaf sizes from P1 and `agent-notes/standalone-failed-quent.txt`.

| query | FE frags | merge-fed exchange kept as boundary (what it parks) | engine runs after fusion | lineitem in the fused plan | confidence it passes |
|---|---:|---|---:|---|---|
| q05 | 11 | 27 -> 28 merge (25 nation groups) | 2 | scan feeding join 10; DuckDB estimate 6.1e9 vs orders x customer chain -> probe side (standalone `q05.explain.txt`) | high |
| q08 | 11 | 33 -> 34 merge (2 year groups) | 2 | scan feeding join 5 with part (filtered) -> probe | high |
| q09 | 9 | 24 -> 25 merge (nation x year) | 2 | scan feeding join 5 with part (LIKE filter) -> probe | high |
| q17 | 5 | 9 -> 10 merge (avg per part: ~200M groups, inference ~5 GB); 14 -> 15 merge (1 row) | 3 | scan feeding join 5 with filtered part (~8M est) -> probe; standalone streams it in 46 batches (`HASH_JOIN(18) n=46 in=120.54 GB`) | high |
| q18 | 7 | 4 -> 5 merge (sum(l_quantity) by l_orderkey: 24.0 GB parked, P1 table) | 2 | scan feeding join 11 (build today) with the orders x semi-agg side -> DuckDB flips inner join sides by estimate | high; total parked 24 GB + standalone peak (`PARTITION(5) 36.19 GB`, P1) fits 100 GiB |
| q21 | 9 | 25 -> 26 merge (100 supplier groups) | 2 | three lineitem scans (2 l1, 9 l3, 13 l2); `14: LEFT SEMI` build = l2 scan, `10: RIGHT ANTI` build = l3 scan | medium (section 8.2) |

Mechanism that decides the build side in the fused plan (the one thing the FE got wrong that
fusion delegates to DuckDB): after `JOIN_ORDER`, `BuildProbeSideOptimizer` flips a join when the
right side is costlier to build than the left, for inner/outer joins and for `SEMI`/`ANTI` with an
equality:

```cpp
// duckdb/src/optimizer/build_probe_side_optimizer.cpp:225-244
	case LogicalOperatorType::LOGICAL_COMPARISON_JOIN: {
		auto &join = op.Cast<LogicalComparisonJoin>();
		switch (join.join_type) {
		case JoinType::SEMI:
		case JoinType::ANTI: {
			// if the conditions have no equality, do not flip the children.
			...
			if (op.type == LogicalOperatorType::LOGICAL_ANY_JOIN ||
			    (op.Cast<LogicalComparisonJoin>().HasEquality(has_range) && !prefer_range_joins)) {
				TryFlipJoinChildren(join);
			}
			break;
		}
		default:
			if (HasInverseJoinType(join.join_type)) {
				TryFlipJoinChildren(op);
			}
```
**Measured** that this fires on this engine with the translator's `LeftSemi`: q04 F00
`HASH_JOIN (id=6) type: RIGHT_SEMI`, build = orders scan, probe = the lineitem stream (P5,
cn1/engine-cn0.log), and the engine executes `RIGHT_SEMI` (`src/op/sirius_physical_hash_join.cpp:230,
825, 1352`). So the report's caveat "q21 needs RIGHT_SEMI in the translator" is not a hard
dependency for fusion: the flip happens below the translator. The translator arm
(`TJoinOp::RIGHT_SEMI_JOIN => (JoinType::RightSemi, JoinOutput::Right)`, `node_translator.rs:1743-1761`)
is still recommended as a companion (section 9) because fix 3 may make the FE emit it.

Predicted times (inference): standalone warm x the measured 1-CN/standalone parity band for
scan-heavy queries, 0.95-1.2 (`results-ratios.md`: q01 1.04, q03 1.02, q04 0.98, q06 0.97, q07 1.06,
q12 1.13, q14 0.95, q19 0.97):

| query | standalone warm (ms) | predicted fused 1-CN warm (ms) | today |
|---|---:|---:|---|
| q05 | 2786 | 2650-3350 | fail after 101 s |
| q08 | 3674 | 3500-4400 | fail after 168 s |
| q09 | 5427 | 5150-6500 | fail after 233 s |
| q17 | 3786 | 3600-4550 | fail after 104 s |
| q18 | 2691 | 2550-3250 | fail after 54 s |
| q21 | 6401 | 6100-7700 (medium confidence) | fail after 114 s |

Sweep effect: the six consumed 774.9 s of the 910.3 s 1-CN sweep (B3); with fusion they take
~25-30 s (inference), and 17,917 OOM-reschedule log lines disappear from the sweep regardless of
fix 1.

Under `leaf` the same six pass (inference, high confidence for q05/q08/q09, medium for the rest):
every failing park is a *leaf* lineitem scan (P1: `role=sender inputs=0`), so `leaf` removes them;
what stays parked are intermediate outputs of modest size (q05: 0140's customer x orders ~ a few GB
and 0137's ~244M-row join output; q18: the 24 GB partial state; q17: ~5 GB partial state; q21:
join-14 output, small). q05 in `leaf` mode runs 5 engine fragments instead of 11 (0134, 0135, 0136,
0137, 0140 remain; 0138/0139/0141/0142/0143/0145 fuse into their receivers).

---

## 5. Behaviour on the 15 passing queries (1 CN) and on 4 CNs

**1 CN, `all` (inference from `survey/plan-summary.txt`).** Engine runs per query become
`1 + #merge-fed exchanges`:

| query | FE frags | merge-fed exchanges | engine runs | what changes |
|---|---:|---|---:|---|
| q01 | 3 | 3 | 2 | gather 6 fused into the result fragment |
| q02 | 13 | 34 | 2 | the whole partsupp/supplier/nation/region chain becomes one plan; DuckDB re-plans the 800M-row shuffles P2 measured; fragment-heavy overhead (P4/MFS-4: 13 runs, 90 ms gaps, 2.7 of 4 slots busy) largely gone |
| q03 | 5 | 12 | 2 | |
| q04 | 4 | 8 | 2 | the 3.79B-row lineitem semi-join input (30.8 GB parked, P5) is no longer parked; DuckDB's flip (measured today) now happens on the scan |
| q06 | 2 | 3 | 2 | unchanged (the only exchange is merge-fed) |
| q07 | 10 | 24 | 2 | 59.5 GB lineitem + 18.4 GB orders no longer parked (B1 measured 79.1 GB, 74% of pool) |
| q10 | 6 | 16 | 2 | see risk 8.1 (BJ-4) |
| q11 | 9 | 10, 22 | 3 | |
| q12 | 4 | 7 | 2 | |
| q13 | 5 | 10 | 2 | 1.48B-row lineitem shuffle no longer parked |
| q14 | 3 | 7 | 2 | |
| q15 | 7 | 4, 13, 9 | 4 | still scans lineitem twice (P5 CTE inlining); the two scans are now one engine window |
| q19 | 3 | 7 | 2 | |
| q20 | 8 | 9 | 2 | 800M partsupp + 304M pre-agg no longer parked |
| q22 | 6 | 14, 4 | 3 | the 1.5B-row orders anti-join input is no longer parked |

Expected direction (inference): the fragment-heavy small queries move toward standalone
(today q02 2.18x, q11 2.88x, q15 1.78x, q22 1.90x of standalone, `results-ratios.md`) because the
per-fragment engine window, the receiver-first wait, and the relay steps disappear; the
scan-heavy ones (already at 0.95-1.2 parity) stay within noise. Peak pool usage drops for
q04/q07/q13/q20/q22 by the parked volumes listed. Correctness: the same relational plan is executed
(an exchange with one destination is the identity on its rows; merging exchanges keep their sort);
FP64 summation order changes batch to batch as it already does run to run (WORKFLOW-PLAN fact 21),
so the known q01/q03/q07/q19 1e-3 decimal deviations and the q15 flakiness are unchanged in kind.
The acceptance sweep (section 6.4) is what turns these expectations into measurements.

**4 CNs.** An exchange whose receiver expects one sender is rare: shuffles and gathers have one
sender per CN (`per_exch_num_senders == 4`), a broadcast sink has four destinations
(`destinations.len() > 1` fails the single-destination check). Candidates are single-instance
fragments with a single local destination (e.g. a one-file dimension scan feeding a local gather).
Fusion therefore leaves the 4-CN plans essentially unchanged; the smoke arm in 6.4 verifies "zero
or few `fused` lines, identical results and timings".

---

## 6. Tests

### 6.1 `local_exchange.rs` (unit, no GPU)

1. `fuse_single_local_sender_completes_pending_receiver`: register `R` with `[(7, 1)]`;
   `try_fuse_sender((R,7), S, [])` -> `Fused(Some(ready))`, `ready.inputs` empty,
   `ready.fused == [S at 7]`, receiver removed.
2. `fuse_inherits_sender_exchanges_and_redirects_later_senders`: `S` has `[(5, 1)]`; after
   fusion `R.expected == {5: 1}`; `push_sender((S.instance, 5), 0, LocalParked)` readies `R` with
   `inputs == [(5, [slot with S.instance])]` and `fused == [S at 9]`.
3. `nested_fusion_through_an_absorbed_instance`: leaf addressed at `(S.instance, 5)` after `S` is
   absorbed -> `Fused(Some(R))` with `fused == {9: S, 5: leaf}`; `absorbed` stays flat.
4. `remote_frame_for_an_absorbed_instance_reaches_the_owner`: `push_remote_frame((S.instance, 5), ...)`
   after absorption counts toward `R`.
5. `fuse_is_refused_when_the_receiver_expects_many` -> `NotFused(ReceiverExpectsMany(2))`;
   the sender then parks and `push_sender` works as before.
6. `fuse_is_refused_without_a_pending_receiver` -> `NotFused(NoPendingReceiver)`.
7. `fuse_is_refused_by_the_structural_verdict` -> `NotFused(Structural(..))`, receiver untouched.
8. `duplicate_exchange_ids_across_fused_fragments_are_an_error`.
9. `forget_query_drops_fused_params_and_redirects` (or the fix 2 equivalent).

### 6.2 Translator (`crates/starrocks-plan-translator/tests/translate.rs`, no GPU)

Fixtures: the existing `params`/`scan_node`/`desc_table`/`broker_scan_range` helpers
(`translate.rs:294-347, 440-540`) plus an `exchange_node(id, input_row_tuples)` and a
`join_node(...)` builder.

1. `fused_exchange_inlines_the_sender_plan`: receiver `[JOIN(SCAN a, EXCHANGE 7)]`, sender
   `[SCAN b]` with broker ranges -> Substrait has two `ReadRel` `local_files`, no stream read,
   `stream_inputs` empty, root names equal to the unfused translation of the same join over a
   stream (the output row is the same).
2. `fused_exchange_row_layout_mismatch_is_refused` (sender root `[3]` vs `input_row_tuples [6]`).
3. `merge_fed_exchange_is_not_fusable` (`exchange_fusability` -> `MergeFedExchange` for a
   `Merge` aggregation over an exchange; `Partial`/`OneShot` shapes from `agg_phase.rs:44-80`).
4. `sender_with_output_exprs_is_not_fusable`.
5. `fused_merging_exchange_keeps_sort_offset_and_limit`: exchange with `sort_info`, `offset 100`,
   `limit 10` over a fused `[SORT limit 110 -> SCAN]` -> `Fetch(offset 100, count 10)(Sort(Fetch(110)(Sort(Read))))`.
6. `fused_exchange_confines_carried_columns`: sender root is a `SELECT` over a `PROJECT` with a
   consumed common slot; the fused subtree is narrowed to the descriptor row and the join above it
   translates (no `refuse_carried_join_child` error).
7. `fused_scan_ranges_are_merged_by_node_id` and `duplicate_scan_node_across_fused_fragments_is_refused`.
8. `nested_fused_exchange` (sender plan contains `EXCHANGE 5`, itself fused) and
   `fused_plan_with_a_remaining_stream_input` (`EXCHANGE 5` not fused -> `stream_inputs == [5]`).
9. `right_semi_join_translates` (companion, section 9): `TJoinOp::RIGHT_SEMI_JOIN` -> `JoinRel`
   `RightSemi` emitting the right row (`row_tuples == right.row_tuples`, width `right.output_width`).

### 6.3 CN component tests (`compute_node_service.rs` tests module, `StubExecutor`, no GPU)

Modelled on `self_exchange_executes_sender_then_receiver_when_receiver_arrives_first` (`:2323-2382`)
and `self_exchange_executes_an_intermediate_receiver_and_reuses_cached_descriptors` (`:2384-2427`),
which today assert `executor.calls == 2` and `== 3`:

1. `fusion_runs_receiver_and_local_leaf_as_one_fragment`: same fixture, `calls == 1`; the single
   `FragmentRun` has `inputs.is_empty()` and `outputs.is_empty()`; `fetch_data` returns rows.
2. `fusion_chains_root_middle_and_leaf_into_one_run` (`calls == 1`, cached descriptor references
   still resolve).
3. `fusion_off_keeps_todays_two_runs` (`set_fusion_mode(Off)` -> `calls == 2`).
4. `leaf_mode_fuses_leaves_but_keeps_the_intermediate_boundary` (root <- middle <- leaf ->
   `calls == 2`: middle runs with the leaf fused and parks; root reads a stream).
5. `fusion_is_skipped_when_the_exchange_expects_two_senders` (`per_exch_num_senders[7] = 2`, two
   local senders -> `calls == 3`, log contains `ReceiverExpectsMany`).
6. `fusion_is_skipped_for_a_remote_single_destination` (reuses the remote-destination fixtures at
   `:2481-2533`; the transport path is unchanged).
7. `fused_fragment_failure_fails_the_fe_polled_result` (`FailingReceiverExecutor`; `fetch_data`
   returns the error; message names the fused exchange).
8. `fusion_precedes_async_sender_dispatch` (`set_async_sender_dispatch(true)`: the leaf is fused,
   never queued; `calls == 1`).
9. `batch_dispatch_fuses_too` (`exec_batch_plan_fragments` with receiver + leaf instances).
10. `sender_arriving_before_its_receiver_is_not_fused_and_still_completes_it` (sender first ->
    `calls == 2`, rows fetched; log contains `NoPendingReceiver`).

### 6.4 SF1000 acceptance (orchestrator-run, one arm at a time, GPU 2 per the plan)

Same harness as the campaign (`capture-cn.sh`, `run-queries.sh`, `cnlog_extract.py`,
`card_compare.py`, oracle compare at rel tol 1e-6):

1. **1 CN, `SIRIUS_CN_FRAGMENT_FUSION=all`, q05 q08 q09 q17 q18 q21, cold + 2 warm:** all pass,
   oracle MATCH; record warm medians against the table in section 4; record `fused` counts per run
   (expect 2/2/2/3/2/2 engine runs), `[gpu_pool]` peak, and `oom_resched == 0`.
2. **1 CN, `all`, the 15 passing queries:** oracle compare set unchanged (MATCH for q02 q04 q06
   q12 q13 q14 q20 q22; the known 1e-3 decimal diffs for q01 q03 q07 q19; q10 as analysed; q11
   EMPTY; q15 flaky as before), warm medians recorded next to the campaign's; any regression
   beyond noise (n=3) is triaged before the default is decided (section 8.1).
3. **1 CN, `leaf`:** the same 21 queries; this is the fallback default's evidence.
4. **1 CN, `off`:** two queries (q03, q05) to prove the knob restores today's behaviour (q05 fails
   as before, in ~3 s once fix 1 is in).
5. **4 CNs, `all`, smoke (q03 q04 q07 q22):** results and timings within noise of the campaign,
   count of `fused sender fragment` lines reported (expect 0 or a handful of dimension scans).
6. Post-run hygiene check: `[gpu_pool] allocated` returns to baseline after each query (fusion must
   not add parks; with fix 2 in, this is also the leak check).

---

## 7. Files to touch

| file | change | size (inference) |
|---|---|---|
| `experimental/starrocks/crates/starrocks-plan-translator/src/lib.rs` | `FusedFragment`, `FusionRefusal`, `translate_fragment_with_inputs(params, exchange_inputs, fused)`, `exchange_fusability(receiver, sender, node_id)`; the two-argument function becomes a wrapper | ~120 lines |
| `.../src/node_translator.rs` | `PlanContext.fused`; `translate_fused_exchange`; run the pre-passes per grafted plan; `merge_fed_exchanges(plan)` helper exposed for `exchange_fusability`; companion `RIGHT_SEMI_JOIN` arm + `JoinOutput::Right` | ~150 lines |
| `.../src/scan_paths.rs` | `extend_from_fragment` (duplicate node id refused) | ~30 lines |
| `.../tests/translate.rs` | tests 6.2 | ~350 lines |
| `experimental/starrocks/src/local_exchange.rs` | `PendingReceiver.fused`, `ExchangeState.absorbed`, `ReadyFragment.fused`, `try_fuse_sender`, redirect in `push_sender`/`push_remote_frame`, `forget_query` (or hook into fix 2's removal) | ~180 lines + ~250 test lines |
| `experimental/starrocks/src/compute_node_service.rs` | `ServiceCore.fusion_mode`, `try_fuse`, calls from `exec_single_attachment` and `translate_batch_attachment`, `execute_ready_fragment` passes `fused`, `run_labeled` logs `fused`, `translate_fragment_logged_with_inputs` signature | ~150 lines + ~300 test lines |
| `experimental/starrocks/src/main.rs` / bring-up log | resolve and log the knob (or add an enum `Knob` to `tunable.rs`) | ~20 lines |
| `experimental/starrocks/docs/TUNABLES.md` | document `SIRIUS_CN_FRAGMENT_FUSION` | ~15 lines |
| `scratchpad/perf/sf1000/cnlog_extract.py` (tooling, not shipped) | parse `fused=` | ~10 lines |

No change under `src/` (engine), `experimental/starrocks/patches/` (FE), or the proto.

PR shape (carve plan: CN and translator -> fork PRs, stacked, merged bottom-up): (1) translator
fused inputs + fusability + tests; (2) `LocalExchange` graft/redirect + tests; (3) CN wiring, knob,
docs, component tests; (4, optional, independent) `RIGHT_SEMI_JOIN` arm. Each is reviewable alone:
(1) changes no behaviour until (3) passes a non-empty `fused` list.

---

## 8. Risks and failure modes

### 8.1 A fused plan is planned worse than today's boundary plan (performance, possibly OOM)

Today a receiver declares the *exact* row count of every parked stream before `build()`
(`engine.rs:551-587`, C2), and DuckDB sizes the join with it. A fused plan has no boundary there;
DuckDB estimates from parquet row counts and default filter selectivities. C2/BJ-4 measured the
hazard: the translator emits the FE's `IS NOT NULL` join-key conjunct as a `Filter`, DuckDB applies
`DEFAULT_SELECTIVITY = 0.2` to it, and in q10 the receiver built on the 25.5 GB customer x nation
side instead of the 2.29 GB stream. Standalone has no such filters and passes, but a fused plan is
"FE shape re-planned by DuckDB", not the standalone plan. Mitigations: the acceptance sweep on the
15 (6.4 item 2); `leaf` keeps the exact-cardinality boundaries at intermediate edges; C2's
proposed translator change (do not emit the join-key `IS NOT NULL` as a `Filter`) removes the
distortion at its source and is worth landing with or right after fusion. Failure mode if it goes
wrong: a slower passing query, or an OOM that fix 1 turns into a fast, named failure.

### 8.2 q21's emulated anti join over lineitem

The translator lowers `RIGHT_ANTI_JOIN` to a right outer join plus `IS NULL` filter
(`node_translator.rs:1750, 1835-1842`) because the Substrait consumer lacks native anti joins
(P5 SAJ-5). In the fused q21 plan, `10: RIGHT ANTI` has the l3 lineitem scan on its build side and
`14: LEFT SEMI` has the l2 scan on its build side. DuckDB can flip both (`HasInverseJoinType` for
RIGHT, the SEMI arm above) but only if its estimates rank lineitem as the larger side against a
join output whose estimate is uncertain. If it does not flip, the engine builds a hash table on
~1-6B rows (12 B/row -> 73 GB for l2): OOM, bounded by fix 1. Standalone avoids the question with
`DELIM` joins from SQL (`standalone-failed/runs/q21.explain.txt`). Confidence medium; the P5
native-anti-join follow-up (item 5 in the report's section 4) removes the outer-join emulation
that inflates the intermediate 30x in q22 (`HASH_JOIN(15) in=6.59GB -> FILTER(16) 6.93GB ->
PROJECTION(17) 0.22GB`) and is the right fix if q21 disappoints.

### 8.3 Translation refusals that only exist in fused shapes

`refuse_carried_join_child` is avoided by confining at the graft (3.4); slot/tuple/node ids are
query-global so no collisions are expected; a `Merge` aggregation above a grafted subtree is
impossible (its exchange is never fused). A residual refusal fails the query loudly with the node
id, exactly as an untranslatable fragment does today, and the FE learns of it via `fetch_data`
rather than the sender's RPC status (same as `SIRIUS_CN_ASYNC_SENDER_DISPATCH` today, `:284-288`).
`off` restores the old path per CN without a rebuild.

### 8.4 Arrival-order dependence

Fusion happens only when the receiver is already pending. If the FE ever deploys a sender before
its receiver (not observed; `Deployer.java:208-215` deploys stage by stage from the root), the edge
runs today's path and the CN logs `NoPendingReceiver`. Performance-only; never a hang or a wrong
result (3.6).

### 8.5 Memory profile of the fused plan

Removing parks lowers peak pool usage for every query in section 5. What does not change: the
cold-start scan reservation (C3: four tasks x 19.5 GB) and any materialising operator the fused
plan contains (hash-join builds, partitions, aggregate states) — the same ones standalone
materialises and that already fit at 100 GiB for all 21 queries (`standalone/runs`,
`standalone-failed/runs`).

### 8.6 Interactions with the other three fixes

- **Fix 1 (fail fast):** independent; it bounds the cost of 8.1/8.2 going wrong. Land first or in
  parallel.
- **Fix 2 (bookkeeping):** the only real coupling (3.8): by-query removal must include `fused` and
  `absorbed`. If fix 2 lands first, fusion extends its removal; otherwise fusion ships
  `forget_query` and fix 2 absorbs it. Fusion also *reduces* fix 2's exposure at 1 CN: absorbed
  senders never park, so a failed query leaves at most the merge-fed partial states behind.
- **Fix 3 (cardinalities):** complementary, not competing. Fix 3 changes the FE cut for multi-CN
  (broadcast the small side); fusion removes the cut's cost at 1 CN whatever the FE decides. Two
  consequences: with real statistics the FE may emit `RIGHT_SEMI_JOIN` (`JoinCommutativityRule`,
  P5) — hence the companion translator arm; and the single-destination property of the six leaves
  may change (if the FE broadcasts lineitem on 1 CN it is still one destination; on 4 CNs
  broadcast = 4 destinations = not fusable, as intended). Re-run 6.4 after fix 3 lands.

### 8.7 Things fusion does not do

It does not stream across a *real* exchange (multi-sender or remote), so B1 remains for 4-CN
shuffles and broadcasts; those need either fix 3 (smaller streams), spillable parked repositories
(plan item 4(b)), or streaming receivers (B1 route 2). It does not touch the engine's
single-flight lifecycle. It does not fuse two-phase aggregations; a later translator change could
collapse update+merge into one aggregation when both live in the fused plan.

---

## 9. Companion change: `RIGHT_SEMI_JOIN` in the translator (small, recommended, independent)

```rust
// node_translator.rs:1743-1761 today: no RIGHT_SEMI arm; `_ =>` refuses "hash join type is unsupported"
        TJoinOp::LEFT_SEMI_JOIN => (join_rel::JoinType::LeftSemi, JoinOutput::Left),
        TJoinOp::LEFT_ANTI_JOIN => (join_rel::JoinType::Left, JoinOutput::LeftAnti),
        TJoinOp::RIGHT_ANTI_JOIN => (join_rel::JoinType::Right, JoinOutput::RightAnti),
```
Add `TJoinOp::RIGHT_SEMI_JOIN => (join_rel::JoinType::RightSemi, JoinOutput::Right)` and a
`JoinOutput::Right` arm at `:1800-1807` (`row_tuples = right.row_tuples`, `output_width =
right.output_width`). The consumer and the engine already support it (`substrait/src/from_substrait.cpp:558-560`
per P5; `src/planner/sirius_plan_comparison_join.cpp:245, 273`). Not required for the six under
the evidence in section 4, but ~15 lines that keep fix 3's stats-aware FE plans translatable.

---

## 10. Why not the engine-side spill (plan item 4(b)), and where it still belongs

| | fusion (this design) | spillable parked repositories |
|---|---|---|
| root cause addressed | removes the exchange at 1 CN (B1) | keeps full materialisation, makes it survivable (B2) |
| which of the six | all six (q21 medium) at ~standalone speed | five slowly; q09 marginal (253 GB vs 279 GB GPU+HOST, B2); throughput unmeasured (open question 10) |
| code | Rust: translator + CN | C++: `streaming_fragment.cpp:103-113` registration, a third downgrade tier at `downgrade_executor.cpp:203-232`, HOST-tier `Fragment::output_row_count` (`sirius_ffi.cpp:1062-1066` throws today) and `export_packed` (`:822-826` throws today), plus the relay's HOST->GPU upgrade path |
| layer / PR target | fork PRs | `dev` PR (engine) |
| 4-CN value | none by construction | real: shuffle/broadcast receivers at larger SF |
| interaction with the fail-fast fix | none | changes what "freed == 0" means; must land together with a re-tuned gate |

Both can coexist; the spill is the right follow-up for multi-CN once fix 3 shrinks the streams.

---

## 11. Effort and sequencing

- Implementation: ~1.5 days translator (incl. tests), ~1 day `LocalExchange` + CN wiring (incl.
  tests), ~0.5 day docs/tooling/knob; ~3 engineer-days total (inference). Companion RIGHT_SEMI: ~1 h.
- Verification: the SF1000 arms in 6.4 are ~1 hour of GPU time on 1 CN (21 queries x 3 runs at
  1-7 s each plus bring-up), plus the 4-CN smoke.
- Order: fix 1 (or in parallel) -> this design -> fix 2 hook -> fix 3 re-sweep.
- Default decision rule: ship `all` if 6.4 items 1-2 hold (six pass, 15 unchanged in the oracle
  set and within noise); otherwise ship `leaf` as default with `all` documented as opt-in, and
  file the offending query against 8.1.

---

## Appendix A. Evidence and code excerpts relied on (file:line)

Evidence (`scratchpad/perf/sf1000/`):
- `cn1-cnlog.txt` — six failures `role=sender`, `oom_resched` counts (quoted in section 1).
- `cn1/dump/fragment-0134..0145.txt` — q05 arrival order and instance ids; `fragment-0137.txt:3003-3030`
  (exchange 9 `input_row_tuples [6]`), `fragment-0141.txt:11-27` (sender root `row_tuples [6]`);
  `fragment-0134.txt:35` (`sort_info: Some` on the merging gather); `output_exprs: Some` absent on
  the six leaf senders.
- `standalone-failed/runs/runs.csv`, `q05.explain.txt` — standalone pass times and plan.
- `results-ratios.md` — 1-CN/standalone parity band used for the predictions.
- `survey/plan-summary.txt` — merge-fed exchanges per query (section 4 and 5 tables).
- Report findings P1 (six-shape, parked GB table), P5 (q04 `RIGHT_SEMI` flip measured; translator
  gap), B1 (receiver-first materialisation; single-flight engine), B2 (parked repositories outside
  the sweep; spill arithmetic), C2/BJ-4 (`IS NOT NULL` 0.2 selectivity hazard).

Code (worktree `/home/prestouser/aocsa/sirius-stacks-wt/perf`):
- `experimental/starrocks/src/compute_node_service.rs`: `ServiceCore` 186-211; `with_transport`
  238-269; `async_sender_dispatch_from_env` 289-294; `try_dispatch_sender` 309-329; `dispatch_worker`
  380-390; `cancel_plan_fragment` 457-479; `exec_single_attachment` 705-717; `run_ready_fragment`
  864-911; `process_fragment` 918-951; `resolve_descriptor_table` 954-988; `dump_fragment` 992-1004;
  `execute_fragment_with_inputs` 1020-1246 (sink checks 1079-1094, destinations 1099-1104, fan-out
  1105-1140, routing 1142-1172, run 1176-1187, local rendezvous 1189-1206); `route_destination`
  1251-1275; `execute_ready_fragment` 1325-1394; `receiver_exchanges` 1397-1431;
  `translate_fragment_logged_with_inputs` 1444-1461; `dump_substrait` 1466-1478; `run_labeled`
  1540-1581; `translate_batch_attachment` 1624-1679; tests 2323-2427 (self-exchange fixtures),
  fixture builders 3793-3973.
- `experimental/starrocks/src/local_exchange.rs`: `ExchangeKey` 19-22; `SenderSource` 26-48;
  `ReadyFragment` 76-79; `PendingReceiver` 82-85; `ExchangeState` 88-95; `register_receiver`
  105-139; `push_sender` 142-155; `push_remote_frame` 159-246; `take_ready` 248-313.
- `experimental/starrocks/src/engine.rs`: one request at a time 278-283; failure wipe 291-316;
  `run_fragment_inner` 486-729 (stream declaration 508-549, cardinality 551-587, relay 616-647,
  park 697-717); `stream_id_of` 733-735.
- `experimental/starrocks/src/fragment_executor.rs`: `SenderSlot` 42-49; `FragmentLabel` 58-84;
  `FragmentRun` 145-166; `FragmentExecutor` 178-241.
- `experimental/starrocks/src/result_store.rs`: `fail_query` 163; `cancel` 205.
- `experimental/starrocks/src/tunable.rs`: knob rules 1-20.
- `experimental/starrocks/crates/starrocks-plan-translator/src/lib.rs`: `TranslatedPlan` 125-139;
  `ExchangeInput` 143-150; `StreamInputSchema` 154-161; `translate_fragment_with_exchange_inputs`
  247-437 (desc 271, scan paths 272, exchange map 273-276, `translate_plan` 278-289, output_exprs
  291-312, `confine_carried` 320, root guard 336-342, partition columns 347-410).
- `.../src/node_translator.rs`: `TranslatedRel` 28-54; `PlanContext` 57-83; `TranslatedFragment`
  114-121; `TranslatePlan for TPlan` 164-175; `PlanNodeCursor` 178-223; `translate_plan_node`
  227-249; `apply_fetch` 258-297; `translate_scan` 341-356; `translate_plan` 390-428;
  `merge_exchange_overrides` 438-483; `common_slots_consumed_above` 499-556; `translate_exchange`
  637-763; `translate_hash_join` 1722-1862 (join_op match 1743-1761, output layout 1800-1807);
  `refuse_carried_join_child` 1880-1895; `confine_carried` 2338-2345; `apply_conjuncts` 2461-2493.
- `.../src/agg_phase.rs`: `classify` 44-80. `.../src/scan_paths.rs`: `from_fragment` 62-93,
  `for_node` 97-102, `add_ranges` 105-147.
- `src/sirius_ffi.cpp`: `lower_substrait` 91-150 (optimizer 134-138); disabled optimizers 233-243;
  `Fragment::build` 638-725; `relay_from` 727-795; `export_packed` GPU-only 822-826;
  `output_row_count` GPU-only 1062-1066.
- `src/exec/streaming_fragment.cpp`: repositories outside the manager 103-113.
- `src/downgrade/downgrade_executor.cpp`: tier 1 203-232, tier 2 293-300, warning 357-363.
- `src/op/sirius_physical_hash_join.cpp`: `RIGHT_SEMI` support 230, 825, 1352.
- `duckdb/src/optimizer/optimizer.cpp`: `JOIN_ORDER` 205, `BUILD_SIDE_PROBE_SIDE` 247,
  `STATISTICS_PROPAGATION` 289. `duckdb/src/optimizer/build_probe_side_optimizer.cpp`:
  `TryFlipJoinChildren` 160-213; SEMI/ANTI and inverse-type flips 225-246.
- Thrift: `PlanNodes.thrift` `TJoinOp` 731-751, `THashJoinNode` 764-803, `TExchangeNode` 1170-1182,
  `TPlanNode` 1423-1432; `InternalService.thrift` `TPlanFragmentExecParams` 412-445
  (`per_node_scan_ranges` 421, `per_exch_num_senders` 425, `destinations` 431, `sender_id` 434,
  `node_to_per_driver_seq_scan_ranges` 445); `DataSinks.thrift` `TDataStreamSink` 123-150.
- FE: `planner/ExchangeNode.java` 180-185 (`computeTupleIds`); `qe/scheduler/Deployer.java`
  208-215; `qe/scheduler/dag/ExecutionDAG.java` 195-218.
