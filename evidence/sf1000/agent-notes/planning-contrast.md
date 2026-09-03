# planning-contrast: FE plans vs DuckDB/Sirius standalone plans (SF1000)

Evidence root: /tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000 (B below).
Source: /home/prestouser/aocsa/sirius-stacks-wt/perf (S below); FE = S/experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks.

## Helper scripts written here
- agent-notes/condense_duck.py  – strips DuckDB box-drawing EXPLAIN to one cell list per line (used on standalone/runs/*.explain.txt and standalone-failed/runs/*.explain.txt)
- agent-notes/dump_summary.py   – one line per FE fragment dump: decoded node types, tables, sink kind/partition, #dests -> agent-notes/cn1-dump-summary.txt (540 fragments)
- agent-notes/standalone-failed-quent.txt – `python3 quent_bp.py standalone-failed/quent/*/` (the arm had no digest yet)
- failing-fragment extraction: python over cn1/cluster.log: for every `fragment run failed`, find its `fragment run started` (inputs/outputs) — all six have inputs=0 outputs=1 (leaf scan fragments)

## Table 1 — the six 1-CN failures are one plan shape (unfiltered lineitem leaf -> HASH_PARTITIONED exchange)
| q | FE leaf fragment (survey/explain/qNN.costs.txt) | lineitem cols projected | bytes/row (SR DECIMAL64 / parquet decimal128) | est. parked GB (6.0e9 rows) | cn1 death (cn1-cnlog.txt) | failing fragment quent (cn1-quent.txt) | standalone (standalone-failed/runs/runs.csv, agent-notes/standalone-failed-quent.txt) |
|---|---|---|---|---|---|---|---|
| q05 | F06: FileScanNode 8 -> OutPut HASH_PARTITIONED l_orderkey -> exch 9 (build of 10:INNER JOIN PARTITIONED) | l_orderkey,l_suppkey,l_extendedprice,l_discount | 28 / 44 | 168–264 | 101 s, 2500 GPU_SCAN reschedules | line 353: tasks=2565 Q=2228s alloc=226.9GB GPU_SCAN in=6782GB | pass 2786 ms warm; alloc 266.8 GB summed peak |
| q08 | F02: FileScanNode -> HASH_PARTITIONED l_partkey -> exch 4 (build of 5:INNER JOIN PARTITIONED vs part) | +l_partkey (5 cols) | 32 / 60 | 192–360 | 168 s, 3600 | line 533: tasks=3674 Q=5559s alloc=214.3GB in=9728GB | pass 3674 ms; alloc 318.9 GB |
| q09 | F02: -> HASH_PARTITIONED l_partkey -> exch 4 | 6 cols (+l_quantity) | 40 / 76 | 240–456 | 232 s, 5600 | line 561: tasks=5693 Q=12243s alloc=204.7GB in=14913GB | pass 5427 ms; alloc 386 GB |
| q17 | F05: -> HASH_PARTITIONED l_partkey -> exch 1 (probe of 5:INNER JOIN PARTITIONED, build=part) (+2nd lineitem leaf) | l_partkey,l_quantity | 12 / 20 | 72–120 (x2 leaves) | 104 s, 2100 | line 1070: tasks=2146 alloc=148.8GB in=5673GB | pass 3786 ms; alloc 441 GB |
| q18 | F04+F02: two lineitem leaves -> HASH_PARTITIONED l_orderkey | l_orderkey,l_quantity | 16 / 24 | 96–144 each | 45 s, 2300 | line 1082-1100: fragment ad9c tasks=2337 alloc=82.8GB in=6204GB, sibling ad9d alloc 291GB | pass 2691 ms; alloc 381 GB |
| q21 | F08 + F03 (+3rd): lineitem leaves -> HASH_PARTITIONED l_orderkey | l_orderkey,l_suppkey(,l_commitdate,l_receiptdate) | 12–20 / 12–20 | 72–120 each | 112 s, 1800 | line 1258: fragment aca tasks=1828 Q=1710s alloc=61.1GB in=4846GB; sibling acd alloc 231GB | pass 6401 ms; alloc 470 GB |

Row counts from parquet footers (pixi python, /scratch/sirius/datasets/tpch_sf1000): lineitem 5,999,989,709 rows / 177.2 GB, orders 1.5e9 / 52.4 GB, partsupp 8e8 / 37.9 GB, customer 1.5e8, part 2e8, supplier 1e7.
Dump confirmation: cn1/dump/fragment-0141.txt (q05 leaf, nodes=[8:FILE_SCAN_NODE] tables=lineitem) lines 711-720: TDataStreamSink dest_node_id 9, output_partition TPartitionType(2)=HASH_PARTITIONED.
Standalone plans (condensed): q05/q08/q09 stream lineitem as the *probe* of a hash join whose build is the small filtered dimension chain (region->nation->customer->orders / part filtered); q17/q18/q21 use DELIM/RIGHT_SEMI/RIGHT_ANTI shapes; none materializes lineitem.
Mechanism: nothing streams across a fragment boundary (WORKFLOW-PLAN fact 2; engine.rs run_fragment_inner relays parked batches after build()); the reschedule loop (gpu_pipeline_executor.cpp:348-372, MAX_RETRIES=100, 50 ms backoff) re-runs the same scan task while the memory is held by the same fragment's parked output — the GPU_SCAN `in=` totals (4.8–14.9 TB) are the same splits read up to 100x.

## Table 2 — FE distribution decisions vs actual rows (card-compare-cn1.txt / card-compare-cn4.txt)
Cost model: FE CostModel.visitPhysicalDistribution — BROADCAST cost = outputSize * aliveBackendNumber, SHUFFLE = outputSize (cost/CostModel.java:419-441). With every node at cardinality 1 (StatisticsCalculator.computeFileScanNode:664-677) the choice is decided only by CN count -> 1 CN: BROADCAST, 4 CN: PARTITIONED (plan-summary.txt vs plan-summary-4cn.txt).
| q | exch (arm) | FE type | actual rows | what a size-aware planner would do |
|---|---|---|---|---|
| q03 | 2 (cn4) | SHUFFLE | 3,234,397,656 lineitem | broadcast the 146M-row orders⋈customer side (4x146M=583M rows moved vs 3.38B) |
| q12 | 1 (cn4) | SHUFFLE | 1,500,000,000 orders | broadcast the 31M filtered lineitem (125M vs 1.53B) |
| q10 | 8 (cn4) | SHUFFLE | 1,480,675,200 lineitem | broadcast the 57M filtered orders (229M vs 1.54B) |
| q19 | 4 (cn4) | SHUFFLE | 128,557,109 lineitem | broadcast the 2.16M filtered part |
| q22 | 10 (cn1/cn4) | BROADCAST | 1.5B orders (6.0B summed on 4 CN) | build on the 6M filtered customers (RIGHT ANTI) |
| q07 | 17 (cn4) | BROADCAST | 600M (150M customer x4) | shuffle or pre-filter by nation |
| q02 | 39 (cn4) | BROADCAST | 472M (118M x4) | shuffle |
| q04 | 4/5 | BROADCAST(cn1)/SHUFFLE(cn4) | 3,793,363,900 lineitem as SEMI build | RIGHT SEMI with the 57M filtered orders as build |
| q14 | 3 (cn1) | BROADCAST | 200M part as build | build on the 75M filtered lineitem |
4-CN data movement (cn4-transmits.txt / cn4-quent.txt): q03 60.3 GB nixl, 474 leases, 122.7 GB lease_GB, 105 s lease_held in a 1.42 s query; q07 69 GB / 747 leases / 141.5 GB / 92.9 s; q10 48 GB; q12 23.5 GB; q22 18.6 GB (orders o_custkey broadcast 3x). Efficiency (results-ratios.md) 0.43–0.59 for scan-heavy, 0.27–0.36 for q02/q11/q22.

## Table 3 — semi/anti join sides
- FE JoinCommutativityRule has LEFT_ANTI<->RIGHT_ANTI, LEFT_SEMI<->RIGHT_SEMI (rule/transformation/JoinCommutativityRule.java:34-44) but with cardinality 1 it never pays to commute; commuteRightSemiAntiJoin is applied only for nest-loop (NestLoopJoinImplementationRule.java:77).
- Translator supports RIGHT_ANTI_JOIN but not RIGHT_SEMI_JOIN (crates/starrocks-plan-translator/src/node_translator.rs:1744-1760, `_ => unsupported`); engine has JoinType::RIGHT_SEMI (src/planner/sirius_plan_comparison_join.cpp:245,273).
- DuckDB inside the fragment cannot fix it: join-order optimizer swaps children only for inner joins and swaps semi/anti back (duckdb/src/optimizer/join_order/query_graph_manager.cpp:264-288); Sirius builds on children[1] (sirius_plan_comparison_join.cpp:455-464).
- q22 4-CN: HASH_JOIN(15) 0.516 s per CN with 6.17 GB build input (cn4-quent.txt q22.r1 block, fragment ...21f6); 1-CN 943 ms vs standalone 495 ms; 4-CN 831 ms (1.13x).
- q04: 1-CN 1850 ms vs standalone 1896 ms — standalone also materializes a lineitem-derived build (LEFT_DELIM_JOIN), so no measured loss here; the 3.79B-row broadcast build is a memory risk (cn1 alloc 252 GB summed vs standalone 194 GB), not a time loss.

## Table 4 — CTE inlining (WITH queries: q02 q11 q15 q22 all start with WITH)
| q | FE | CN 1 evidence | standalone |
|---|---|---|---|
| q15 | two lineitem FileScanNodes (q15.costs.txt lines 150,172), no multicast; 7 frags | cn1-quent.txt lines 990-1000: two scan fragments wall 4.434 s + 2.149 s, GPU_SCAN 8.0 s + 7.6 s, in=126.7 GB each (253 GB) | q15.explain.txt `CTE Name: revenue`, one READ_PARQUET lineitem; in=133.6 GB; 2627 ms vs 4685 ms |
| q11 | 6 scans (partsupp, supplier, nation x2) | cn1-quent lines 699-720: GPU_SCAN(3) 16.4 GB + 13.1 GB in two fragments | CTE Name: partsupp/supplier/nation reused; 336 ms vs 970 ms |
| q02 | partsupp scanned twice | 13.1 GB x2 | standalone also scans partsupp twice (GPU_SCAN(37),(51)); not a CTE issue |
FE decision: cbo_cte_reuse=true (SessionVariable.java:1505-1511, ratio 1.15); CTEContext.needInline (CTEContext.java:157-190) inlines unless forceCTEList / inlineCTERatio==0; the CBO's cost comparison with 1-row stats picks inline. `SET cbo_cte_reuse_rate=0` would force reuse (isForceCTE: ratio==0), BUT the CN accepts only DATA_STREAM_SINK (compute_node_service.rs:1066-1072) and MULTI_CAST_DATA_STREAM_SINK is rejected by name -> not usable today.

## Session variables / hints checked (FE qe/SessionVariable.java)
- broadcast_row_limit (15,000,000; :1832) — gate in EnforceAndCostTask.checkBroadcastRowCountLimit (:281-329) requires rightChildStats.getOutputRowCount() > limit: never true at cardinality 1. Setting it <=0 returns false for every non-forced broadcast (line 312-314) => forces shuffle everywhere except onlyBroadcast joins (cross / null-aware anti / hint) — would help q22/q07/q02 broadcasts on 4 CN but hurt q03/q12 (which should broadcast), and on 1 CN is irrelevant.
- broadcast_right_table_scale_factor (10.0, invisible) — same gate.
- disable_join_reorder (:1781) — keeps SQL order; untested.
- cbo_cte_reuse / cbo_cte_reuse_rate / cbo_cte_force_reuse_node_count (2000) — see Table 4.
- enable_local_shuffle_agg + single node: PruneShuffleDistributionNodeRule.java:30-37 prunes only agg shuffles with no join in the tree; every join exchange stays on 1 CN.
- Join hints: `JOIN [BROADCAST|SHUFFLE|BUCKET]` parsed in sql/parser/AstBuilder.java:6567; HINT_JOIN_BROADCAST zeroes the child cost (EnforceAndCostTask:291-296). Per-query only; cannot flip semi/anti sides.

## Where row counts already exist
- FE TableFunctionTable.fileStatuses: TBrokerFileStatus.size (gensrc/thrift/FileBrokerService.thrift:48-58) for every file — bytes/avg-row-width estimate needs no RPC.
- CN file_schema.rs:14-40 already opens each parquet footer (ParquetRecordBatchStreamBuilder metadata().file_metadata()) for PGetFileSchemaResult; num_rows is one field away (proto internal_service.proto:647-650 has only status+schema).
- Engine: stream_bind_catalog.hpp:44-46 exact declared stream rows; engine.rs:562-595 declares them before build(); sirius_ffi.cpp:233-240 disables STATISTICS_PROPAGATION (parquet scans inside a fragment keep DuckDB's raw file estimate, no filter selectivity).
