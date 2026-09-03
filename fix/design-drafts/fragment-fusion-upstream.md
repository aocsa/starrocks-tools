# Design: same-node fragment fusion at the TPlan level (issue 4, "what upstream would accept" angle)

Written 2026-09-03 from the SF1000 report (P1, P5, B1, B2, section 4), the evidence bundle
`scratchpad/perf/sf1000/`, and the source worktree `/home/prestouser/aocsa/sirius-stacks-wt/perf`
at `45dab3be`. Every line number below was read from that worktree. "Measured" means copied from
a named evidence file; everything else is marked inference.

## 0. One paragraph

On 1 CN the FE still cuts every query into fragments and the CN parks a whole exchange input on the
GPU before the receiver can start (B1). The six failures are a bare `lineitem` scan under a
one-destination `HASH_PARTITIONED` sink whose projection (122-245 GB) cannot be parked in a 100 GiB
pool (P1). The design: when a data-stream sender's only destination is a receiver **on this CN**
whose exchange node expects **exactly one sender**, do not run the sender. Park its *plan* in the
rendezvous instead of its *rows*, and when the receiver becomes ready splice the sender's `TPlan`
node list in place of the receiver's `EXCHANGE_NODE`, merge the scan ranges, and translate the
fused fragment as one plan. The engine then sees the lineitem scan as an ordinary `ReadRel` inside
the join fragment, DuckDB's optimizer (which already runs on every fragment, `src/sirius_ffi.cpp:131-135`)
puts the 6.0e9-row scan on the probe side, and the scan streams through the hash join exactly as it
does in standalone Sirius, which passes all six in 2.7-6.5 s. No engine change, no FE change, no
proto change; the FE never learns the sender did not run because the CN reports nothing per
instance (no `report_exec_status` anywhere under `experimental/starrocks/src/`). Two-phase
aggregation and merging (sorted) exchanges are deliberately left as stream boundaries: they carry
the small aggregate outputs and the translator's partial-state model depends on the boundary
(`node_translator.rs:438-496`). No `RIGHT_SEMI` translator arm is needed: the flip happens inside
the engine after fusion, as it already does today for q04 (`cn1/engine-cn0.log:48480`,
`HASH_JOIN (id=6) type: RIGHT_SEMI`).

## 1. What the code does today (verified mechanism)

### 1.1 Receiver-first rendezvous, one fragment at a time

- `compute_node_service.rs:918-959 process_fragment`: a fragment with `EXCHANGE_NODE`s is
  registered as a receiver and returns (`:934-949`); a fragment with none is translated and run
  (`:958-959`). `receiver_exchanges` (`:1398-1432`) reads `per_exch_num_senders` per exchange node.
- `compute_node_service.rs:1021-1245 execute_fragment_with_inputs`: a `DATA_STREAM_SINK` fragment
  runs on the engine (`run_labeled("sender", ...)`, `:1173-1186`) with one output stream per
  destination, then each **local** destination is pushed into the rendezvous as
  `SenderSource::LocalParked { names, slot }` (`:1189-1206`). One destination is a gather regardless
  of partition label (`:1104-1111`).
- `local_exchange.rs:26-48 SenderSource` has two variants, `LocalParked` (complete by construction,
  `:59-64`) and `Remote`. `register_receiver` (`:105-139`) stores `PendingReceiver { params,
  expected_senders }`; `take_ready` (`:248-313`) releases the receiver once every exchange has
  `expected` complete sources.
- `compute_node_service.rs:1312-1395 execute_ready_fragment`: builds one `ExchangeInput` per
  exchange (sender names + `sirius_stream_<node_id>` view), translates with
  `translate_fragment_logged_with_inputs`, and runs with the parked slots.
- `engine.rs:486-733 run_fragment_inner`: declare stream columns and senders (`:510-549`), declare
  the **exact** parked row count (`:551-587`, `output_row_count`), `build()` (`:610-612`),
  `relay_from` every parked sender (`:616-647`), `run()`, park the output (`:697-716`). The engine
  is single-flight (`Fragment::build()` takes the query lifecycle slot, B1), so the sender must have
  finished and parked everything before the receiver's `relay_from` (`src/sirius_ffi.cpp:735-745`
  refuses a source that has not run).
- `src/exec/streaming_fragment.cpp:103-113`: sink output repositories and receiver input
  repositories are created outside `data_repository_manager`, so the downgrade sweep
  (`src/downgrade/downgrade_executor.cpp:203-291` TIER 1 over `_data_repo_registry.get_all()`,
  `:293-345` TIER 2 pipeline queue) never sees a parked batch (B2). `Fragment::output_row_count`
  (`src/sirius_ffi.cpp:1050-1071`) and `export_packed` (`:803-888`) throw on a non-GPU batch.

### 1.2 What a fused plan needs to be valid

- **Row layout.** The FE's `ExchangeNode` copies its child's tuple ids:
  `ExchangeNode.java:181-184 computeTupleIds() { tupleIds.addAll(getChild(0).getTupleIds()); ... }`
  and ships them as `input_row_tuples` (`ExchangeNode.java:360`). Confirmed in the q05 dumps: the
  lineitem sender root has `row_tuples: [6]` (`cn1/dump/fragment-0141.txt:11-19`, node 8) and the
  receiver's exchange 9 has `input_row_tuples: [6]` (`cn1/dump/fragment-0137.txt:3020-3022`).
  The translator resolves slot refs through `row_tuples` (`DescriptorTable::slot_global_index`,
  `lib.rs` module doc "Adding a node" step 3), so the parent's expressions resolve identically
  whether the child is the exchange or the sender's root.
- **Flat preorder.** `TPlan.nodes` is a preorder list with `num_children` per node
  (`lib.rs:12-22`). An `EXCHANGE_NODE` has `num_children: 0` (`fragment-0137.txt:2541, 3003`), so
  replacing that single node by the sender's whole node list (itself a valid preorder) keeps the
  parent's `num_children` and `PlanNodeCursor::ensure_consumed` (`node_translator.rs:187-225`)
  satisfied.
- **Scan ranges** are keyed by scan node id: `scan_paths.rs:62-90` reads
  `per_node_scan_ranges` and `node_to_per_driver_seq_scan_ranges`; `translate_scan`
  (`node_translator.rs:341-356`) looks up `ctx.scan_paths.for_node(node.node_id)`. Node ids are
  query-unique (the FE's `PlanNodeId` generator), so the maps union without collision.
- **Descriptor table** is per query: `resolve_descriptor_table` (`compute_node_service.rs:961-996`)
  caches the full table sent once and restores it for `is_cached` references, so the receiver's
  resolved `desc_tbl` already holds the sender's tuples and slots.
- **Exchange-only decorations** that the fused subtree would lose: `TExchangeNode.sort_info` and
  `offset` (`PlanNodes.thrift TExchangeNode`, translated at `node_translator.rs:746-775` and
  `apply_fetch :258-270`), node `limit` and `conjuncts` (`apply_conjuncts :2461-2494`). In the six
  failing plans the join-side exchanges have `limit: -1, conjuncts: None, sort_info: None,
  offset: Some(0)` (`fragment-0137.txt:2537-2565` node 7; `:2999-3026` node 9).
- **Two-phase aggregation** requires the boundary: `merge_exchange_overrides`
  (`node_translator.rs:438-496`) refuses a merge aggregation whose next preorder node is not an
  exchange, and a partial aggregation over `avg` must be the fragment root (`:404-414`).
- **Common-expr projections**: `common_slots_consumed_above` (`:498-560`) walks ancestors in the
  flat list; `refuse_carried_join_child` (`:1880-1895`) refuses carried columns into a join. All
  sampled q05 fragments have `common_slot_map: None` (`cn1/dump/fragment-0137/0140/0141/0142/0143/0145/0136`),
  so the guard in section 2.2 costs nothing for TPC-H.
- **Dispatch order.** The FE deploys fragments root-first, stage by stage:
  `ExecutionDAG.java:170-200 getFragmentsInTopologicalOrderFromRoot` ("All the upstream fragments
  of the fragments in a group must belong to the previous groups. Each group should be delivered
  sequentially") and `Deployer.java:164-215 deployFragments`. Inside one batch RPC the CN processes
  instances in FE order (`translate_batch_attachment :1622-1680`). So a receiver is registered
  before its sender is processed on both the inline and the `SIRIUS_CN_ASYNC_SENDER_DISPATCH`
  (`:271-329`) paths.
- **The engine already re-plans inside a fragment.** `lower_substrait` runs DuckDB's optimizer
  (`src/sirius_ffi.cpp:131-135`) with only `IN_CLAUSE, COMPRESSED_MATERIALIZATION,
  STATISTICS_PROPAGATION, COLUMN_LIFETIME, LATE_MATERIALIZATION` disabled (`:233-240`);
  `JOIN_ORDER` and `BUILD_SIDE_PROBE_SIDE` run. `BuildProbeSideOptimizer::TryFlipJoinChildren`
  (`duckdb/src/optimizer/build_probe_side_optimizer.cpp:160-213`) swaps children when
  `row_width x estimated_cardinality` of the right (build) side exceeds the left, and turns
  `SEMI/ANTI` into `RIGHT_SEMI/RIGHT_ANTI` (`:228-236`). Parquet scans estimate from footers;
  non-equality filters take `DEFAULT_SELECTIVITY = 0.2`
  (`duckdb/src/include/duckdb/optimizer/join_order/relation_statistics_helper.hpp:57`,
  `relation_statistics_helper.cpp:143-150`, `relation_manager.cpp:199,258,276,293,374`). Measured:
  q04's FE plan builds on lineitem, the engine ran `HASH_JOIN (id=6) type: RIGHT_SEMI` with the
  orders scan as build (`cn1/engine-cn0.log:48470-48482`, P5). The engine implements
  `RIGHT_SEMI/RIGHT_ANTI` (`src/op/sirius_physical_hash_join.cpp:228-231, 2148-2163`;
  `src/planner/sirius_plan_comparison_join.cpp:241-246, 272-274`).

## 2. Mechanism

### 2.1 Layering

| layer | change | PR target (carve plan) |
|---|---|---|
| translator crate `experimental/starrocks/crates/starrocks-plan-translator` | new `pub mod fusion` with two pure functions over thrift structs; unit tests | fork PR (CI `cargo test --workspace --no-default-features`, `.github/workflows/experimental.yml:76-79`) |
| CN `experimental/starrocks/src` | `SenderSource::LocalPlan` variant + a peek in `local_exchange.rs`; a deferral hook before translation and a fold at ready time in `compute_node_service.rs`; one off switch read at bring-up | fork PR, stacked on the translator PR (or one PR of ~600 lines if the reviewer prefers) |
| engine `src/` | none | - |
| FE patch `experimental/starrocks/patches/` | none | - |
| docs | `experimental/starrocks/docs/TUNABLES.md` row; `experimental/starrocks/DEMO.md` "Sequential fragments" paragraph | same PR |

### 2.2 Translator: `fusion.rs` (pure, no engine, no I/O)

```rust
/// Why a receiver exchange keeps its stream boundary instead of absorbing its sender.
pub enum FusionRefusal { MergeAggregationParent, SortedExchange, ExchangeLimit, ExchangeConjuncts,
    ExchangeOffset, SenderOutputExprs, SenderSinkMismatch, SenderNonIdentityColumns, SenderSinkLimit,
    RowTuplesDiffer { exchange: Vec<i32>, sender_root: Vec<i32> }, CommonSlotProjection { node_id: i32 },
    ScanRangeCollision { node_id: i32 }, NestedExpansionRoot }

/// Checks the receiver side only (the sender is not known yet when the receiver registers).
pub fn fusable_exchange(receiver_plan: &TPlan, exchange_node_id: i32) -> Result<(), FusionRefusal>;

/// Splices `sender.fragment.plan.nodes` over the receiver's EXCHANGE_NODE, unions the exec params
/// the translator reads (`per_node_scan_ranges`, `node_to_per_driver_seq_scan_ranges`,
/// `per_exch_num_senders` for exchanges the sender itself still reads), and returns the fused
/// params with the RECEIVER's identity (query_id, fragment_instance_id, sink, destinations).
pub fn fuse_sender(receiver: &TExecPlanFragmentParams, exchange_node_id: i32,
                   sender: &TExecPlanFragmentParams) -> Result<TExecPlanFragmentParams, FusionRefusal>;
```

`fusable_exchange` walks the flat preorder list with the same span arithmetic as
`common_slots_consumed_above` to find the exchange's parent and refuses when:

1. the parent is an `AGGREGATION_NODE` classified `AggPhase::Merge` by `agg_phase::classify`
   (`agg_phase.rs:45-79`) -- keeps `merge_exchange_overrides` (`node_translator.rs:438-496`) and the
   avg root rule (`:404-414`) untouched;
2. `exchange_node.sort_info.is_some()` (a MERGING-EXCHANGE, `node_translator.rs:746-761`);
3. `node.limit >= 0` or `exchange_node.offset` is `Some(n > 0)` (`apply_fetch :258-270`);
4. `node.conjuncts` non-empty (`apply_conjuncts :2461-2494`; fusing would have to re-attach them
   above the sender root -- possible, but not needed for TPC-H and not worth the extra path).

`fuse_sender` additionally refuses when the sender has `fragment.output_exprs` (non-empty), its
sink is not a `DATA_STREAM_SINK` with `dest_node_id == exchange_node_id`, `output_columns` is a
non-identity permutation or `limit >= 0` (the same three checks `execute_fragment_with_inputs`
already makes at `:1073-1093`), the sender root's `row_tuples != exchange.input_row_tuples`, any
`PROJECT_NODE` in the sender carries a non-empty `common_slot_map` (`:498-560, 1880-1895`), or a
scan-range key collides. A refusal at this stage is a bug (the receiver-side check passed), so the
CN turns it into a loud query failure rather than a silent fallback.

Everything the translator does downstream is unchanged: the fused `TPlan` is an ordinary fragment
whose remaining `EXCHANGE_NODE`s (the ones that refused) still lower to stream reads
(`translate_exchange :637-777`).

### 2.3 CN: defer at the sender, fold at the receiver

**`local_exchange.rs`**

```rust
pub(crate) enum SenderSource {
    LocalParked { names, slot },
    Remote { .. },
    /// A same-node sender whose PLAN was deferred into its receiver instead of running. Holds
    /// no GPU memory. `inputs` are the exchange inputs the deferred sender itself consumed
    /// (parked slots, staged remote batches, or nested deferred plans) so the fused fragment
    /// relays them under their own stream ids.
    LocalPlan { params: TExecPlanFragmentParams, inputs: Vec<ReadyExchangeInput> },
}
// is_complete(): LocalPlan => true (nothing to wait for).
// names(): only meaningful for LocalParked/Remote; the fold in 2.3 consumes LocalPlan first.

/// Peek used by the sender path: is `receiver` pending, how many senders does `node_id` expect,
/// and does the receiver's plan accept a fusion at that node? Computed under the lock; nothing
/// is cloned but the verdict.
pub(crate) fn fusion_verdict(&self, receiver: FragmentInstanceId, node_id: i32) -> Option<FusionVerdict>;
```

`take_ready` (`:248-313`) is unchanged: a `LocalPlan` counts as a complete sender, so a receiver
whose only sender was deferred becomes ready the moment the sender is pushed, and the ready set
flows to the dispatch worker exactly as today (`FragmentOutcome`, `dispatch_then_join :351-376`,
`dispatch_worker :380-390`).

**`compute_node_service.rs`**

1. `ServiceCore::try_defer_into_receiver(&self, params, inputs: Vec<ReadyExchangeInput>)
   -> Result<Option<Vec<ReadyFragment>>, String>` runs **before translation** in both places a
   sender is about to be translated:
   - `process_fragment` (`:958`) for a sender with no exchange inputs (leaf), and
   - `execute_ready_fragment` (`:1339-1341`) for a receiver that is itself a sender (middle
     fragment), passing its own `ready.inputs` along.
   It returns `Some(ready)` when all of: the switch is on; the sink is a `DATA_STREAM_SINK` with
   exactly one destination and `route_destination` (`:1254-1268`) says `Local`; the sink passes the
   existing limit/output_columns checks; `exchanges.fusion_verdict(dest_instance, dest_node_id)`
   is `Fusable { expected: 1 }`. Then `exchanges.push_sender(key, sender_id,
   SenderSource::LocalPlan { params, inputs })` and the caller treats the result like today's
   `push_sender` (`:1189-1206`). Otherwise `None` and the existing path runs unchanged. A receiver
   that is not registered yet (not the FE's order, section 1.2) simply yields `None`: correct,
   just unfused.
2. `execute_ready_fragment` starts with a fold: for every `ReadyExchangeInput` whose single source
   is `LocalPlan`, call `fusion::fuse_sender(&params, node_id, &sender_params)`, replace `params`,
   and append the deferred sender's carried `inputs` to the working input list (recursively, since
   a carried input can itself be a `LocalPlan`). Then the existing code builds `ExchangeInput`s for
   what is left, translates, and runs. One `info!` line per fused exchange
   (`receiver_fragment_instance_id, exchange node ids, sender fragment instance ids`) so the
   cluster log still shows every FE instance once. `dump_fragment` at `:1339` already re-dumps the
   ready params, so `SIRIUS_CN_DUMP_FRAGMENTS` captures the fused plan for review.
3. Failure attribution is unchanged: a deferred sender never ran, so there is no parked output to
   wipe (`engine.rs:291-316`); the fused fragment fails through `run_ready_fragment ->
   results.fail_query` (`:864-905`) under the receiver's ids as any receiver does today.
   `cancel_plan_fragment` (`:450-476`) is result-store only and unaffected.
4. Switch: `SIRIUS_CN_FRAGMENT_FUSION` read once at bring-up next to
   `async_sender_dispatch_from_env` (`:271-296`); `off`/`0`/`false` disables, unset means on. A
   `#[cfg(test)] set_fragment_fusion(bool)` mirrors `set_async_sender_dispatch` (`:299-303`).
   Doc row in `experimental/starrocks/docs/TUNABLES.md` under "Engine-side".

### 2.4 Behaviour on the six SF1000 failures (1 CN)

Fragment ids are the FE's `PLAN FRAGMENT k(Fxx)` from `survey/explain/qNN.costs.txt`; sizes are
measured where a file is named and inference otherwise.

| query | exchanges fused (parent) | exchanges kept as streams | fragments run (was) | biggest build after DuckDB's flip (inference) |
|---|---|---|---|---|
| q05 | 9 lineitem, 13 supplier, 7 (F08 <- join-5 fragment, itself absorbing 1 customer, 4 orders), 16 (F14 <- F08), 18 nation, 23 region: all join parents | 27 (merge agg 28), 30 (MERGING-EXCHANGE) | 3 (11) | customer x orders-1994, 227,571,151 rows measured (`card-compare-cn1.txt` q05 exch 4) ~2.7 GB + hash table; lineitem 171.00 GB streams as probe (`agent-notes/standalone-failed-quent.txt` q05 `GPU_SCAN(27) in=171.00GB`) |
| q08 | 2 part, 4 lineitem, 8 supplier, 12 orders, 16 customer, 20/24 nation, 29 region (joins 5..30) | 33 (merge agg 34), 37 (merging) | 3 (11) | orders 1995-1996 ~457M rows x 20 B ~9 GB + table; part filtered 1.33M |
| q09 | 2 part, 4 lineitem, 8 supplier, 12 partsupp, 16 orders, 20 nation | 24 (merge agg 25), 27 | 3 (9) | orders unfiltered 1.5e9 x 12 B = 18 GB + table, partsupp 8e8 x 16 B = 12.8 GB + table; heaviest of the six, standalone passes the same relation set (`standalone-failed/runs/q09.explain.txt:286-312`, `alloc=387.1GB` cumulative) |
| q17 | 1 lineitem, 4 part (join 5) | 9 (merge agg 10 <- F05 partial avg over lineitem), 14 (merge agg 15) | 3 (5) | part filtered; merge-agg output 200M groups declared exactly; F05 still runs first and parks ~5.6 GB (200M x (4+16+8) B, inference; today 4.0 GB measured leftover, P1 table) |
| q18 | 10 lineitem, 1 orders (join 11 / semi 7 probe), 13 (F09 <- F03), 15 customer | 4 (merge agg 5 <- F02 partial sum over lineitem, 24.0 GB parked measured, P1 table), 20 (merging) | 3 (7) | merge-agg table ~1.5e9 groups x 24 B ~36 GB while stream 4 (24 GB) drains: ~60 GB early peak, then scans stream; tightest of the six |
| q21 | 13 l2 lineitem (semi 14), 2 l3 lineitem (anti 10 probe), 9 (F07 <- F03 join 7, absorbing 6 orders-F), 16 (F00 <- F07), 21 nation | 25 (merge agg 26), 28 (merging) | 3 (9) | anti join: right = l1 x orders-F, 730,806,711 orders rows measured (exch 6) -> ~1.85e9 rows x 12 B ~22 GB + table ~15-30 GB; l3 (3.8e9 rows) must stay the probe -- see risk 1 |

Predicted wall (inference from standalone warm medians in `results-failed.md` plus the ~120-270 ms
fixed FE head (P4) and two small stream hops): q05 3.0-5.5 s, q08 3.9-4.5 s, q09 5.5-7.5 s,
q17 8-11 s (F05's partial avg over 6e9 rows ran 6,343 ms today, `cn1-cnlog.txt` q17 slowest[1],
then the fused join fragment ~2.5-3 s), q18 4-6 s, q21 7-10 s. All six drop from 54-232 s failures
(775 s of the 910 s sweep) to passes.

### 2.5 Behaviour on the 15 passing queries

- q01, q06: only merge-agg and merging exchanges -> nothing fuses, plans byte-identical to today.
- q04: exch 4 (3,793,363,900 lineitem rows, 30.82 GB parked today, P5) fuses into the semi join;
  DuckDB flips `SEMI -> RIGHT_SEMI` on `6.0e9` vs `1.5e9 x 0.2` and builds on orders as it already
  does with the stream (`engine-cn0.log:48480`). Parked bytes drop 30.82 GB -> 0.
- q07: exchanges 1, 3, 6, 8 (1.82e9 lineitem rows), 12 (1.5e9 orders), 16, 20 fuse; 24/27 stay.
  Today 79.1 GB (74 % of the pool) is parked before the join fragment starts (B1); after fusion
  nothing but the aggregate output is parked. Fragments 10 -> 3.
- q02 (13 fragments), q11 (9), q22 (6), q15 (7): the fragment-heavy small queries at 1.8-2.9x
  parity (fact 17) collapse to 3-5 fragments each; expected gain is the per-fragment engine window
  plus hops (MFS-4: 8 % of q02's span is inter-fragment gaps at 1 CN). q22's `LEFT ANTI` on the
  1.5e9-row orders exchange becomes a fused `LEFT` outer + `IS NULL` whose DuckDB flip builds on the
  customer side (`build_probe_side_optimizer.cpp:160-213`), removing today's 1.5e9-row build
  (P5 `HASH_JOIN(15)` 0.5-0.6 s).
- q03, q10, q12, q13, q14, q19, q20: join-side leaves fuse; two-phase agg and gather stay.
- 4 CNs: `per_exch_num_senders` is 4 for every SHUFFLE/BROADCAST receiver and for the GATHER
  receivers (`survey/plan-summary-4cn.txt`), so nothing fuses and the 4-CN path is untouched.

What changes for these queries is **which cardinalities DuckDB sees**: today a receiver sees the
exact parked row count of every stream (`engine.rs:551-587`, C2); after fusion a scan carries the
parquet row count and filters take 0.2. This is the same information standalone Sirius has minus
statistics propagation, and the acceptance arm (section 3) measures the effect rather than
assuming it.

## 3. Tests

### 3.1 Translator (`crates/starrocks-plan-translator/tests/translate.rs`, pure Rust, runs in CI)

Fixtures already exist: `scan_node`, `base_plan_node`, `params`, `desc_table`, `slot`,
`broker_scan_range` (`translate.rs:62-360, 440-540`).

1. `fuse_sender_splices_a_leaf_scan_over_its_exchange`: receiver `[HASH_JOIN(2), EXCHANGE(7,
   tuples [1]), FILE_SCAN(tuple 0)]` with `per_exch_num_senders {7: 1}`, sender
   `[FILE_SCAN(node 3, tuple 1)]` with `per_node_scan_ranges {3: ...}`. Assert the fused plan
   translates to the same Substrait `Plan` as a hand-built `[HASH_JOIN, FILE_SCAN(3), FILE_SCAN]`
   fragment and `stream_inputs.is_empty()`.
2. `fuse_sender_keeps_unfused_exchanges_as_streams`: receiver with two exchanges, one deferred and
   one bound as `ExchangeInput`; the fused translation has exactly one `StreamInputSchema`.
3. `fuse_sender_unions_scan_ranges_by_node_id` and `_refuses_a_scan_range_collision`.
4. `fusable_exchange_refuses_*`: merge-aggregation parent (`need_finalize = true`, all
   `is_merge_agg`), `sort_info`, `limit >= 0`, `offset > 0`, conjuncts.
5. `fuse_sender_refuses_*`: `output_exprs`, wrong `dest_node_id`, non-identity `output_columns`,
   sink `limit`, `row_tuples` mismatch, `common_slot_map` project.

### 3.2 CN (`src/compute_node_service.rs` tests, `StubExecutor`/`CountingExecutor`, no GPU)

Reuse `fragment_params`, `exec_params`, `scan_plan`, `exchange_plan`, `data_stream_sink`,
`result_sink`, `local_destination`, `wait_until`, `fetch_rows_eventually` (`:1810-2120, 3730-4060`)
and add a `RecordingExecutor` that keeps each `FragmentRun`'s `(inputs.len(), outputs.len(),
plan.stream_inputs.len())`.

1. `single_local_sender_is_fused_into_its_receiver`: the existing receiver+sender shape
   (`:2330-2382`) now yields **one** executor call with no inputs and no stream inputs, and
   `fetch_data` returns rows.
2. `two_expected_senders_keep_the_parked_path`: `per_exch_num_senders {7: 2}` -> three calls as
   today.
3. `remote_destination_is_never_fused` and `merge_aggregation_receiver_keeps_the_stream`
   (receiver plan `[AGGREGATION(merge), EXCHANGE]` -> two calls, receiver run has one stream input).
4. `intermediate_receiver_fuses_upward_with_its_own_inputs`: the three-level chain of
   `self_exchange_executes_an_intermediate_receiver_and_reuses_cached_descriptors` (`:2384-2437`)
   collapses to one call; a variant where the middle's second exchange expects two senders keeps
   that one stream and carries its parked slots into the fused run.
5. `fusion_switch_off_restores_todays_runs`; `async_sender_dispatch_fuses_too`
   (`set_async_sender_dispatch(true)`, `:299-303`).
6. `local_exchange.rs`: `local_plan_source_completes_the_sender_set`, `fusion_verdict_reports_
   expected_senders_and_refusals`.

### 3.3 Engine-backed (feature `sirius-engine`, `pixi run cn-test`, GPU 2 per the plan)

Extend `engine_executes_local_files_and_sequential_exchange` (`engine.rs:1284`) with a fused
counterpart: the same two `local_files` parquet fixtures joined in one fragment return the same
rows as the sender+receiver pair. This pins the claim "fused plan == standalone plan" on a real
engine without touching engine code.

### 3.4 SF1000 acceptance (orchestrator, 1 CN, GPU 2, 100 GiB pool, same `bench.sh` arm)

1. q05 q08 q09 q17 q18 q21 pass; `compare.py` vs `../../oracle/tpch_sf1000/qNN.tsv` at rel tol
   1e-6, reading q05/q08/q09 with the known ~1e-3 FP64 revenue-sum deviation (fact 16) and
   expecting exact MATCH for q17/q18/q21.
2. Per query in `cluster.log`: `fragment run started` count equals the "fragments run" column of
   section 2.4; one `fused same-node sender fragments` line per fused exchange; zero
   `oom_resched`.
3. Engine log: peak `[gpu_pool] allocated` per query recorded next to the table; the fused
   fragment's pipeline print shows the lineitem scan feeding a `HASH_JOIN` probe port (as
   `engine-cn0.log:11572-11574` shows today for a passing query) and, for q21, the anti join's
   build side.
4. The 15 passing queries: warm medians within noise of `results.md`, `compare-cn1.txt` set
   unchanged, parked bytes at the join fragment start (q07: was 79.1 GB) near zero.
5. `SIRIUS_CN_FRAGMENT_FUSION=off` re-run of q05 reproduces today's failure shape (proves the
   switch), then the 4-CN sweep of the 15 confirms byte-identical fragment counts (nothing fuses).

## 4. Predicted effect (numbers)

- Six queries: fail (54-232 s each, 775 s of a 910 s sweep, measured) -> pass in roughly
  3-11 s each (inference from standalone 2.7-6.5 s warm plus fragment overhead); sweep time drops
  by ~700 s.
- Parked GPU bytes for join inputs on 1 CN: q05 104.30, q08 99.54, q09 96.42, q17 66.26,
  q18 35.87 (+68.69 own senders), q21 25.37 (+77.76) GB at death (P1 table) -> only aggregate
  outputs remain parked (q18 24.0 GB, q17 ~5.6 GB, others < 1 GB).
- Passing queries: fragments per query 3-13 -> 2-5; q07's 79.1 GB pre-join parking disappears;
  q04's 30.82 GB likewise. Wall effect is second-order at 1 CN (fragment gaps are 8 % of q02's
  span) and is measured, not promised.
- 4 CNs: no change by construction.

## 5. Risks

1. **q21's anti join could flip the wrong way (medium).** The translator lowers `RIGHT ANTI` as
   `RIGHT` outer + `IS NULL` (`node_translator.rs:1750, 1837-1844`). After fusion DuckDB compares
   `est(l3 filtered) = 6.0e9 x 0.2 = 1.2e9` against its estimate of `l1 x orders-F`. If the join
   estimate exceeds 1.2e9 the optimizer builds on l3 (3.8e9 actual rows x 12 B = 46 GB plus a
   3.8e9-key table, ~100 GB) and q21 OOMs again; if not (the expected outcome: DuckDB's
   join-cardinality estimate without statistics is bounded by the smaller input, ~3e8), the build
   is ~40-52 GB and q21 passes. Verify from the fused plan's engine print before the SF1000 arm;
   the fallback is the native `LEFT_ANTI/RIGHT_ANTI` lowering (section 4 item 5 of the report),
   which does not remove the estimate problem but makes the join an `ANTI` DuckDB can also flip
   back. Fix 1 (fail fast) makes the wrong outcome cost ~3 s instead of 114 s.
2. **Join order in a fused plan comes from DuckDB without statistics propagation (medium).**
   Today a receiver plans with exact stream counts (C2); after fusion a filtered scan is guessed at
   0.2. A wrong pick on a big unfiltered relation (q09's orders 1.5e9 / partsupp 8e8) costs memory
   but stays under the pool by the arithmetic above; q09 remains the closest to the limit.
   Mitigation in-PR: none (this is the standalone planner's information set minus statistics);
   follow-up: enable `STATISTICS_PROPAGATION` on the FFI path once GPU_VALUES coverage exists
   (`src/sirius_ffi.cpp:235-237`).
3. **Two-phase aggregation stays a boundary (low, by design).** q18 keeps 24 GB parked and builds
   a ~36 GB merge table; passes on arithmetic but is the tightest. Follow-up options: collapse
   `update serialize -> exchange -> merge finalize` into one `OneShot` aggregation at fusion time
   (needs the intermediate/output tuple remap and the `avg` state model), or the FE session
   variable `new_planner_agg_stage = 1` the translator already names in its errors
   (`node_translator.rs:462, 1131`).
4. **Blast radius on passing queries (low-medium).** Every 1-CN plan with a join-side exchange
   changes shape. The acceptance arm compares the 15 warm medians and oracle set; the switch gives
   an immediate revert path in the field. 4-CN plans are unchanged.
5. **Telemetry and log consumers (low).** A fused sender has no `fragment run started` line and
   no Quent query of its own (labels come from the receiver, `engine.rs:499-507`);
   `cnlog_extract.py`/`quent_bp.py` counts per query drop. The new `fused` log line carries the
   sender instance ids so the stitch stays possible.
6. **Deferred sender never runs if the receiver is lost (low).** If the FE cancels the query
   before the receiver is ready, the `LocalPlan` sits in `ExchangeState.sources` like a parked
   slot does today; it holds no GPU memory. Fix 2's by-query removal in `LocalExchange` should
   drop `LocalPlan` sources too (one match arm).

## 6. Effort and dependencies

- Translator module + tests: ~250 + ~250 lines, half a day.
- CN variant, peek, hook, fold, switch + tests: ~200 + ~250 lines, one day.
- Docs (TUNABLES row, DEMO.md paragraph, PR description per the CONTRIBUTING reviewability
  checklist): an hour. Total ~2-3 engineer-days plus the SF1000 arm (~15 minutes of cluster time
  for the six, ~20 for the 15).
- Dependencies: none to land. Interactions: fix 2 touches `local_exchange.rs` (rebase order,
  one extra match arm for `LocalPlan`); fix 1 is independent and still wanted for risk 1; fix 3
  is complementary (real cardinalities change the FE cut, fusion makes the 1-CN join inputs
  independent of that cut; P1 notes fix 3 alone may not undo the six because of
  `BROADCAST_JOIN_MEM_EXCEED_PENALTY` and zero single-node shuffle cost). The P5 `RIGHT_SEMI`
  translator arm is **not** required here (the engine flips after fusion); it becomes necessary
  only when the FE itself emits `RIGHT_SEMI_JOIN`, i.e. after fix 3.

## 7. Why this shape, for a reviewer

- It is the plan the report itself asks for (P1 item 2, B1 route 1, section 4 row 4a) and it
  reproduces the plan the standalone engine already runs, so the engine is not asked to do
  anything new; the alternative (spillable parked repositories, B2) touches
  `streaming_fragment.cpp`, the downgrade tiers and two FFI functions, and still leaves q09
  marginal at ~253 GB vs 279 GB GPU+HOST.
- The rewrite is a pure function over thrift structs in the crate that already owns
  `TPlan -> Substrait`, testable without an engine; the CN change is one enum variant and one
  hook before translation, both in files the rendezvous already lives in.
- It refuses rather than guesses: every exchange decoration the fused subtree would lose is a
  named `FusionRefusal`, the fallback is today's behaviour, and the receiver-side checks run before
  anything is deferred so a late refusal is a plan bug surfaced loudly.
- One switch, documented, default on, with a test that proves off restores today's path.

## Appendix: code excerpts relied on

`experimental/starrocks/src/compute_node_service.rs:934-959` (receiver registration vs sender run):
```rust
        let expected_senders = Self::receiver_exchanges(&params)?;
        if !expected_senders.is_empty() {
            ...
            return Ok(FragmentOutcome::from_ready(
                self.exchanges
                    .register_receiver(fragment_instance_id, expected_senders, params)?
                    .into_iter()
                    .collect(),
            ));
        }

        let translated = self.translate_fragment_logged(&params, dump_seq)?;
        self.execute_fragment(&params, translated)
```

`experimental/starrocks/src/compute_node_service.rs:1189-1206` (the local push the deferral replaces):
```rust
        for (slot, route) in slots.iter().zip(&routes) {
            if matches!(route, DestinationRoute::Local) {
                let ready = self.exchanges.push_sender(
                    ExchangeKey { fragment_instance_id: slot.fragment_instance_id, node_id: slot.node_id },
                    sender_id,
                    SenderSource::LocalParked { names: translated.output_names.clone(), slot: *slot },
                )?;
                ready_receivers.extend(ready);
            }
        }
```

`experimental/starrocks/src/local_exchange.rs:26-48, 59-64` (the variant set and completeness):
```rust
pub(crate) enum SenderSource {
    LocalParked { names: Vec<String>, slot: SenderSlot },
    Remote { names: Vec<String>, sender_id: i32, batches: Vec<StagedBatch>, closed: bool },
}
    fn is_complete(&self) -> bool {
        match self {
            Self::LocalParked { .. } => true,
            Self::Remote { closed, .. } => *closed,
        }
    }
```

`experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs:438-466` (why merge-agg exchanges stay):
```rust
fn merge_exchange_overrides(plan: &TPlan) -> Result<HashMap<i32, Vec<StateColumns>>> {
    for (index, node) in plan.nodes.iter().enumerate() {
        ...
        if agg_phase::classify(node.node_id, node.node_type, agg)? != AggPhase::Merge { continue; }
        let child = plan.nodes.get(index + 1)
            .filter(|child| child.node_type == TPlanNodeType::EXCHANGE_NODE);
        if child.is_none() {
            return Err(TranslateError::UnsupportedPlanNode { ..
                reason: "a merge aggregation must read its partial states directly from an \
                         exchange (SET new_planner_agg_stage = 1)", });
        }
```

`experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs:341-356` (scan ranges by node id):
```rust
fn translate_scan(node: &TPlanNode, children: Vec<TranslatedRel>, tuple_id: i32, ctx: &mut PlanContext<'_>) -> Result<TranslatedRel> {
    expect_children(node, &children, 0)?;
    let file_paths = ctx.scan_paths.for_node(node.node_id);
```

`experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks/planner/ExchangeNode.java:181-184, 360`:
```java
    public final void computeTupleIds() {
        clearTupleIds();
        tupleIds.addAll(getChild(0).getTupleIds());
        nullableTupleIds.addAll(getChild(0).getNullableTupleIds());
...
        exchangeNode.setInput_row_tuples(normalizer.remapTupleIds(tupleIds));
```

`src/sirius_ffi.cpp:131-135, 233-240` (the optimizer runs on every fragment; what is disabled):
```cpp
    auto logical_plan = std::move(planner.plan);
    if (client.config.enable_optimizer) {
      duckdb::Optimizer optimizer(*planner.binder, client);
      logical_plan = optimizer.Optimize(std::move(logical_plan));
    }
...
    disabled.insert(duckdb::OptimizerType::IN_CLAUSE);
    disabled.insert(duckdb::OptimizerType::COMPRESSED_MATERIALIZATION);
    disabled.insert(duckdb::OptimizerType::STATISTICS_PROPAGATION);
    disabled.insert(duckdb::OptimizerType::COLUMN_LIFETIME);
    disabled.insert(duckdb::OptimizerType::LATE_MATERIALIZATION);
```

`duckdb/src/optimizer/build_probe_side_optimizer.cpp:184-190, 228-236` (the flip rule and SEMI/ANTI):
```cpp
	// RHS is build side.
	// if right_side metric is larger than left_side metric, then right_side is more costly to build on
	// than the lhs. So we swap
	if (right_side_build_cost > left_side_build_cost) {
		swap = true;
	}
...
		case JoinType::SEMI:
		case JoinType::ANTI: {
			// if the conditions have no equality, do not flip the children.
```

`src/exec/streaming_fragment.cpp:103-113` (why parked batches are invisible to the sweep, the alternative this design avoids):
```cpp
  // Repositories escape data_repository_manager_ cleanup so sender output outlives this fragment.
  for (const auto& [id, _] : _spec.inputs) {
    _input_repos[id] = std::make_shared<cucascade::shared_data_repository>();
  }
  for (auto id : _spec.outputs) {
    ...
    _output_repos[id] = std::make_shared<cucascade::shared_data_repository>();
  }
```

Evidence lines: `cn1/dump/fragment-0141.txt:11-19` (node 8 `row_tuples: [6]`),
`cn1/dump/fragment-0137.txt:2537-2565, 2999-3026, 7703-7707` (exchanges 7/9 fields;
`per_exch_num_senders: {7: 1, 9: 1, 13: 1}`), `cn1/engine-cn0.log:48470-48482` (q04
`HASH_JOIN (id=6) type: RIGHT_SEMI`), `agent-notes/standalone-failed-quent.txt` (lineitem
`GPU_SCAN in=` 171.00 / 195.75 / 244.50 / 122.25 / 97.50 x 2 / 123.00 x 2 + 73.50 GB),
`card-compare-cn1.txt` (q05 exch 4 = 227,571,151; q21 exch 6 = 730,806,711),
`results-failed.md` (standalone 2786 / 3674 / 5427 / 3786 / 2691 / 6401 ms warm).
