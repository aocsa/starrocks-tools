# Semi/anti-join family: q04 q13 q20 q22 (SF1000, cn1 / cn4 / standalone)

Evidence root: scratchpad/perf/sf1000. Source: /home/prestouser/aocsa/sirius-stacks-wt/perf.
All numbers below are copied from the evidence files named; "inferred" is marked as such.

## 1. Timings (results.md warm medians, ms) and ratios (results-ratios.md)

| q   | standalone | 1 CN | 4 CN | T_1cn/T_sa | speed-up 1->4 | eff |
|-----|-----------:|-----:|-----:|-----------:|--------------:|----:|
| q04 | 1896 | 1850 | 1000 | 0.98 | 1.85 | 0.46 |
| q13 | 1820 | 2198 | 1030 | 1.21 | 2.13 | 0.53 |
| q20 | 3023 | 4171 | 2000 | 1.38 | 2.09 | 0.52 |
| q22 |  496 |  943 |  832 | 1.90 | 1.13 | 0.28 |

Correctness: all four MATCH the DuckDB oracle on cn1, cn4 and standalone (compare-*.txt). No OOM reschedules
(oom_resched=0 on every run, cn1-cnlog.txt / cn4-cnlog.txt), clamped=0.

## 2. FE plan vs runtime cardinality (survey/explain/qNN.costs.txt, card-compare-cn1.txt, card-compare-cn4.txt)

Every node has `cardinality: 1` (StatisticsCalculator.computeFileScanNode, fe-core/.../statistics/StatisticsCalculator.java:664-676:
`builder.setOutputRowCount(1)`, every column `ColumnStatistic.unknown()`).

| q | join (FE node) | FE distribution 1CN / 4CN | build child | actual build rows 1CN | 4CN total rows moved |
|---|---|---|---|---:|---:|
| q04 | 5: LEFT SEMI JOIN | BROADCAST / PARTITIONED (6:) | 4:EXCHANGE <- lineitem (l_commitdate<l_receiptdate) | 3,793,363,900 | exch5 3.79B shuffled + exch2 57.35M orders shuffled |
| q13 | 5: RIGHT OUTER JOIN | PARTITIONED / PARTITIONED | 4:EXCHANGE <- customer | 150,000,000 (probe exch2 = 1,483,831,048 orders) | same |
| q20 | 22: LEFT SEMI (BROADCAST); 19: INNER (BUCKET_SHUFFLE(S)); 16: LEFT SEMI (PARTITIONED) | unchanged | 21: 3.29M (13.16M on 4CN = 4x); 18: 8.69M; 15: 2.17M part | exch9 = 304,124,949 lineitem partial-agg rows | exch9 = 639,885,819 (2.1x) |
| q22 | 11: LEFT ANTI JOIN | BROADCAST / BROADCAST | 10:EXCHANGE <- orders (o_custkey only) | 1,500,000,000 | exch10 = 6,000,000,000 (1.5B to each of 4 CNs) |

Standalone DuckDB plans of the same SQL (standalone/runs/qNN.explain.txt):
- q04: LEFT_DELIM_JOIN SEMI; the lineitem scan is probe of `HASH_JOIN INNER l_orderkey = o_orderkey` against DELIM_SCAN (distinct filtered o_orderkey), i.e. build = filtered orders keys.
- q13: HASH_JOIN RIGHT, build = customer, probe = orders (same shape as FE) -- but Sirius fuses it into DENSE_COUNT_JOIN(4).
- q20: HASH_JOIN INNER (l_partkey=ps_partkey AND l_suppkey=ps_suppkey) against DELIM_SCAN (8.69M pairs) BELOW the HASH_GROUP_BY; top join is RIGHT_SEMI with supplier x nation as build.
- q22: RIGHT_DELIM_JOIN RIGHT_ANTI: build = filtered customer keys (~27M est), probe = orders (1.54B est).

## 3. Engine-side plans actually run on the CN (cn1/engine-cn0.log "Query Plan:" blocks)

Extracted with a python walker over the log (see section 8). Join types and build ports:
- q04 F00: `HASH_JOIN (id=6) type: RIGHT_SEMI`, build port <- Pipeline #2 (CONCAT <- PARTITION <- GPU_SCAN id=0 = orders), probe <- Pipeline #5 (STREAMING_SOURCE id=3 = lineitem stream).
  => the engine's DuckDB optimizer (sirius_ffi.cpp:135, declared stream cardinality via engine.rs:559-586) already flips the semi join to build on orders. The FE's broadcast only costs parking + exchange, not join work.
- q13 F04: `HASH_JOIN (id=6) type: RIGHT` -> PROJECTION(7) -> HASH_GROUP_BY(8) -> PARTITION(9) -> MERGE_GROUP_BY(10) -> PROJECTION(11) -> HASH_GROUP_BY(12) ... No DENSE_COUNT_JOIN. `grep -c "Fusing COUNT-join" cn1/engine-cn0.log` = 0 while the same log has 176 `[sirius_plan_comparison_join]` INFO lines, so planner INFO logging is on.
- q22 F00: `HASH_JOIN (id=15) type: RIGHT` -> FILTER(16) -> PROJECTION(17) -> HASH_GROUP_BY(18); build <- Pipeline #8 (customer x avg), probe <- Pipeline #11 (STREAMING_SOURCE id=12 = orders stream). The translator lowered LEFT ANTI to LEFT outer + IS NULL filter (node_translator.rs:1749, 1824-1832); DuckDB flipped it to RIGHT with customer as build.
- q20 F03 (lineitem): GPU_SCAN(0) -> PROJECTION(1) -> HASH_GROUP_BY(2) -> PARTITION(3) -> MERGE_GROUP_BY(4) -> STREAMING_SINK(5): a full blocking local aggregate.

Substrait of q13 F04 (cn1/dump/plan-0339.substrait, decoded with a hand-written wire walker): `aggregate -> project -> aggregate -> project -> join(read, read)`.
A ProjectRel sits between the count aggregate and the join.

## 4. Per-fragment Quent (cn1-quent.txt / cn4-quent.txt / standalone-quent.txt, run r1 unless noted)

### q04
| arm | fragment | wall | notable ops (summed) |
|---|---|---:|---|
| cn1 | lineitem scan F01 (…8481) | 1.163s | GPU_SCAN 4.168s in=98.25GB; FILTER 0.274s in=98.25GB; STREAMING_SINK in=30.82GB (3.79B x 8B parked) ; 37 tasks Q=18.97s |
| cn1 | join F00 (…8480) | 0.457s | GPU_SCAN(orders) 0.822s in=30.97GB; PARTITION(4) 0.214s in=30.82GB; HASH_JOIN(6) 0.382s in=31.52GB; req=253.1GB alloc=61.3GB |
| cn4 | lineitem scan x4 | 0.50-0.53s each | GPU_SCAN ~1.45s in=24.5GB; sink 7.7GB per CN |
| cn4 | orders scan x4 | 0.63-0.67s each | GPU_SCAN 0.31-0.34s; sink 0.30GB; leases 90/12.2GB held 8.4-10.8s maxconc 30-31 per CN (lease attribution is by time) |
| cn4 | join x4 | 0.08-0.12s | HASH_JOIN 0.075s; PARTITION(4) in=7.59GB |
| standalone | single | 2.093s | GPU_SCAN(7) 4.809s in=98.25GB; DYNAMIC_FILTER(8) in=98.25GB; FILTER(9) in=3.88GB (96% of lineitem bytes dropped post-decode by the IN-list/Bloom built from 57M order keys) |
cn4 header: leases=360 lease_GB=48.80 lease_held=36.165s; cn4-transmits q04: 39 nixl transmits, 23.64GB, lease_ms med/max 0/1, write_ms max 3.

### q13
| arm | fragment | wall | notable ops |
|---|---|---:|---|
| cn1 | orders scan F00 | 1.551s | GPU_SCAN 5.916s in=18.37GB; sink 18.18GB (1.48B rows) |
| cn1 | join F04 (…3429) | 0.558s | HASH_JOIN(6) n=7 1.406s in=18.41GB; HASH_GROUP_BY(8) 0.261s in=18.60GB; PARTITION(4) 0.111s |
| cn4 | orders scan x4 | 0.73s | GPU_SCAN 2.1-2.4s in=4.6GB; leases 73-75/7.2GB per CN |
| cn4 | join x4 | 0.15s | HASH_JOIN 0.19s; HASH_GROUP_BY 0.038s |
| standalone | single | 1.814s | GPU_SCAN(2) 6.557s in=18.37GB; PARTITION(3) 0.106s; DENSE_COUNT_JOIN(4) n=2 0.066s in=18.41GB |

### q20
| arm | fragment | wall | notable ops |
|---|---|---:|---|
| cn1 | lineitem F03 (…31fb) | 3.303s (of 3.800s span) | GPU_SCAN 7.857s in=98.25GB; HASH_GROUP_BY(2) 0.254s in=14.91GB; PARTITION(3) 0.107s in=14.36GB; MERGE_GROUP_BY(4) n=6 2.903s in=14.14GB; sink 4.87GB (304M rows) |
| cn1 | F04 receiver (…31f7) | 0.384s | MERGE_GROUP_BY(6) 0.503s in=4.87GB; HASH_GROUP_BY(4) 0.175s in=4.87GB; HASH_JOIN(9) 0.102s |
| cn1 | nation scan F01 (…31fc) | 3.761s | Comp 0.002s; dwell_max 3.783s (parked, waiting for the receiver) |
| cn4 | lineitem x4 | 1.41s | GPU_SCAN 3.3-3.4s in=24.5GB; MERGE_GROUP_BY 0.37s; sink ~3.5GB per CN (160M rows each -> 640M total) |
| standalone | single | 2.958s | GPU_SCAN(24) 9.821s in=98.25GB; DYNAMIC_FILTER(25) in=14.91GB |
Pre-aggregation effect on 1 CN: HASH_GROUP_BY(2) input 14.91GB -> PARTITION(3) input 14.36GB (3.7% reduction per batch); the fragment-level merge reduces 910M -> 304M; the receiver's second phase re-aggregates 304M -> 304M (no reduction on 1 CN).

### q22
| arm | fragment | wall | notable ops |
|---|---|---:|---|
| cn1 r0 | orders scan F04 (…4fc9) | 0.392s | GPU_SCAN 0.593s in=6.19GB; sink 6.19GB |
| cn1 r0 | join F00 (…4fc8) | 0.349s | HASH_JOIN(15) n=3 0.588s in=6.59GB; FILTER(16) in=6.93GB; PROJECTION(17) in=0.22GB; PARTITION(13) in=6.19GB; GPU_SCAN(4) customer 0.221s in=4.71GB |
| cn4 r1 | orders scan x4 | 0.36s | GPU_SCAN 0.13-0.15s in=1.55GB per CN |
| cn4 r1 | join (cn2 shown) | 0.315s | HASH_JOIN(15) 0.516s in=6.17GB; PARTITION(13) in=6.19GB; GPU_SCAN(4) 0.103s in=1.31GB |
| standalone | single | 0.481s | GPU_SCAN(23) orders 0.797s in=6.19GB; GPU_SCAN(0) customer 0.280s; RIGHT_DELIM_JOIN; HASH_JOIN(27) in=0.85GB, HASH_JOIN(31) in=1.51GB |
cn4-transmits q22: 33 transmits, 18.56GB (= 1.5B x 4B x 3 remote CNs), lease_ms 0. Cold cn1 r0 4110ms vs warm 943ms while engine span was 0.747s (FE-side cold cost, not engine).

Anti-join emulation inflation (inferred rows from bytes): join output 6.93GB reaches FILTER(16), 0.22GB survives to PROJECTION(17); at ~35 B/row that is ~200M outer-join rows kept for ~6.4M anti rows (~97% discarded).

## 5. Reservation vs peak (reservation-ratio.txt)
cn1: q04 509/252GB (2.02x), q13 315/251 (1.25x), q20 562/315 (1.78x), q22 62/25 (2.53x). cn4: q04 2.86x, q13 1.28x, q20 2.32x, q22 2.21x. standalone: q13 549.8GB requested for 0.9GB peak (610x, DENSE_COUNT_JOIN path), q04 1.73x, q20 1.55x, q22 3.39x.

## 6. Source facts used
- node_translator.rs:1743-1760 join op map: INNER/LEFT/RIGHT/FULL/LEFT_SEMI/LEFT_ANTI(->Left+LeftAnti filter)/RIGHT_ANTI(->Right+RightAnti filter)/NULL_AWARE_LEFT_ANTI; `_ => UnsupportedPlanNode "hash join type is unsupported"` (RIGHT_SEMI_JOIN refused). 1824-1832 `filter_is_null(joined, right_key)` emulation.
- substrait/src/from_substrait.cpp:540-570: consumer maps INNER/LEFT/RIGHT/LEFT_SINGLE/LEFT_SEMI/RIGHT_SEMI/LEFT_MARK/OUTER; LEFT_ANTI/RIGHT_ANTI -> NotImplementedException.
- Engine supports RIGHT_SEMI/ANTI/RIGHT_ANTI: sirius_plan_comparison_join.cpp:242-246, 272-274, 488-490; sirius_physical_hash_join.hpp:342-343.
- Runtime filters: the only reference in CN/translator Rust is wire_type_parity.rs:607 `probe_runtime_filters: None` (grep -rn -i runtime_filter over experimental/starrocks/src and crates/starrocks-plan-translator/src = 1 hit).
- detect_dense_count_join (src/planner/sirius_plan_aggregate.cpp): `if (op.children[0]->type != LOGICAL_COMPARISON_JOIN) return nullopt;` plus single group/count, LEFT/RIGHT, 1 equality on INTEGER/BIGINT, can_feed_dense_count_join (no delim/CTE). Fusion logs "Fusing COUNT-join into DENSE_COUNT_JOIN" at INFO.
- sirius_ffi.cpp:133-136 optimizer runs per fragment; :233-243 disables IN_CLAUSE, COMPRESSED_MATERIALIZATION, STATISTICS_PROPAGATION, COLUMN_LIFETIME, LATE_MATERIALIZATION.
- engine.rs:559-586 declares exact stream cardinality only when every contributor's row count is known; local_exchange.rs:1-6, 97-125, 260-275 receiver-first rendezvous: receiver registered first, runs once every sender is complete.
- FE: JoinCommutativityRule.java:34-51 maps LEFT_ANTI<->RIGHT_ANTI, LEFT_SEMI<->RIGHT_SEMI (stats-driven CBO can flip). HashJoinCostModel.java:94-151: broadcast buildCost = rightOutput/parallelFactor, memCost = rightOutput*beNum (=1*N with cardinality 1). SplitTwoPhaseAggRule.check (:55-69) + Utils.couldGenerateMultiStageAggregate: 2-phase agg is unconditional for plain GROUP BY (session hint only, no statistics). Translator ignores the STREAMING pre-agg mode (no `streaming_preaggregation` handling; agg_phase.rs classifies only by need_finalize/is_merge_agg).
- docs/super-sirius/dynamic-filters.md:8, 58, 89-94: exact IN-list / hash IN-list / Bloom membership filters published from the hash-join build to a reachable GPU scan or a join-edge endpoint; consumers never wait for publication.

## 7. Notes on the 1->4 CN behaviour of this family (cn4-quent.txt, cn4-transmits.txt)
- No lease waits: lease_ms median 0, max 1-3 ms; write_ms max <= 3 ms; oom_resched 0. Arena (16 GiB) never limited these four.
- q22: per-CN anti-join work is unchanged (HASH_JOIN(15) 0.516s vs 0.588s on 1 CN) because every CN receives all 1.5B o_custkey; only the customer probe side shrinks (GPU_SCAN(4) 0.103s vs 0.221s). Speed-up 1.13x.
- q04: the 4-CN wall (978ms) is set by the orders-scan fragments (0.63-0.67s wall for 0.31-0.34s of compute; the remainder is output staging/transmit of the shuffled batches, attributed leases 90 per CN) plus ~0.2s outside the engine (span 0.764s vs wall 0.978s).
- q20: exch9 grows from 304M to 640M rows because each CN pre-aggregates a quarter of lineitem-1994 with fewer duplicates per group; the per-CN lineitem fragment (1.41s) is 70% of the 1.98s wall.
- q13: cleanest scaling in the family (2.13x); the orders scan is 0.73s of the 0.89s span on 4 CN.

## 8. Commands run (from scratchpad/perf/sf1000)
- `awk '/^q(04|13|20|22)/...' survey/plan-summary.txt survey/plan-summary-4cn.txt card-compare-cn1.txt card-compare-cn4.txt`
- `grep -E "^q(04|13|20|22)\." cn1-cnlog.txt cn4-cnlog.txt`
- `sed -n '304,324p;868,889p;1179,1213p;1269,1307p' cn1-quent.txt`; `awk '/^## q(04|13|20|22)\.r1 /...' cn4-quent.txt standalone-quent.txt`
- cn1-ops.txt sections for fragments 08c6c9d58480 (q04 join), 832d7f224fc8 (q22 join), 286810da3429 (q13 join), a6d8eef131fb (q20 lineitem)
- `grep -nE "join_op|distribution_mode|runtime_filter" cn1/dump/fragment-0112.txt` (q04 join fragment: TJoinOp(2)=LEFT_SEMI_JOIN, TJoinDistributionMode(1)=BROADCAST, build_runtime_filters Some, probe_runtime_filters at the orders scan)
- python wire-format walker over cn1/dump/plan-0339.substrait (q13 join fragment) printing the Rel nesting
- python extraction of "Query Plan:" blocks from cn1/engine-cn0.log by run window (13:03:37-43 q04, 13:12:44-51 q13, 13:16:00-13 q20, 13:18:07-13 q22), printing HASH_JOIN `type:` and build/probe ports
- `grep -c "Fusing COUNT-join" cn1/engine-cn0.log` (0); `grep -oE "\[sirius_plan_[a-z_]+\] ..." | sort | uniq -c` (152 "Not wiring dynamic filter", 24 "Wired hash join with")
