# multi-fragment-small: q02 q11 q15 (SF1000, cn1 / cn4 / standalone / cn1-canon-q15 / cn1-nocanon-q15)

Evidence root B=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000
Source S=/home/prestouser/aocsa/sirius-stacks-wt/perf

## 1. Timings (results.md / results-ratios.md, warm medians ms)

| q | standalone | 1 CN | 4 CN | 1CN/standalone | 1CN/4CN |
|---|---|---|---|---|---|
| q02 | 571 | 1245 | 1154 | 2.18 | 1.08 |
| q11 | 336 | 970 | 664 | 2.88 | 1.46 |
| q15 | 2627 | 4685 | 2034 | 1.78 | 2.30 |

Engine span (Quent, first fragment start -> last fragment finish) vs client wall, run 1:

| arm/run | client wall | engine span | outside engine |
|---|---|---|---|
| cn1 q02.r1 | 1264 | 1067 (13:03:24.461 -> 25.528) | 197 |
| cn1 q11.r1 | 969 | 733 (34.973 -> 35.706) | 236 |
| cn1 q15.r1 | 4701 | 4505 (03.370 -> 07.876) | 196 |
| cn4 q02.r1 | 1264 | 1081 (40.301 -> 41.382) | 183 |
| cn4 q11.r1 | 663 | 396 | 267 |
| cn4 q15.r1 | 2028 | 1800 | 228 |
| standalone q11.r1 | 336 | 324 | 12 |

## 2. FE front-end decomposition (cn1/cluster.log, CN-side RPC spans; python parse of get_file_schema / exec_plan_fragment "close" lines)

Command: python3 over cn1/cluster.log filtering the client window (runs.csv start_utc .. start+ms).

q02.r1 (client start 13:03:24.267):
- 24.280 first exec_plan_fragment close (13 ms after client start)
- 24.287 .. 24.360  get_file_schema x8 (one per FILES() CTE in the WITH clause, whether used or not); lineitem call busy 28.3 ms (60 footers read serially, file_schema.rs:62-75) -> 73 ms
- 24.360 .. 24.417  FE planning (57 ms, no CN activity)
- 24.417 .. 24.455  deploy wave 1 (7 exec_plan_fragment RPCs, translate idle 0.1-18.6 ms), 24.495/24.499 wave 2, 24.552-24.596 wave 3 (Deployer.java:164-216 stage-by-stage deployAsync + waitForDeploymentCompletion)
- 24.461 first fragment run -> 194 ms before any GPU work; tail after last fragment 4 ms

q11.r1 (start 34.741): first RPC 34.755; schema x8 34.762-34.824 (62 ms); plan 34.824-34.859 (35 ms); deploy 8 RPCs in 3 waves 34.859-34.972 (113 ms, inter-wave gaps 45 and 28 ms); first run 34.973 -> 232 ms.
q15.r1 (start 03.178): first RPC 03.193; schema x8 03.200-03.312 (112 ms; lineitem call busy 30 ms idle 60 ms); plan 26 ms; deploy 03.338-03.356; first run 03.370 -> 192 ms.

All three queries issue exactly 8 get_file_schema RPCs (TableFunctionTable.java:264 -> :688 -> BackendServiceClient.getFileSchema :712); no cache on either side (compute_node_service.rs:683 handler, file_schema.rs).

## 3. Per-fragment timeline, 1 CN (cluster.log "fragment run started/finished")

q02.r1: 13 fragments strictly serial: 56b 47ms | 56a 5 | 569 5 | 56d 204 | 56c 81 | 565 5 | 568 7 | 567 4 | 566 443 | 564 113 | 563 16 | 562 41 | 561 6. Sum 977 ms, span 1067 ms, gaps 90 ms (8.4%).
q11.r1: 9 fragments: a92 9 | a91 8 | a90 350 | a8f 8 | a8e 8 | a8d 289 | a8c 3 | a8b 11 | a8a 2. Sum 688, span 733, gaps 45 (6%). a90 and a8d (the two partsupp scans) are independent leaves but run one after the other.
q15.r1: 567 2253 | 566 2163 | 565 5 | 564 3 | 563 6 | 562 36 | 561 3. Sum 4469, span 4505.
Mechanism: engine.rs:1-14 ("the single process-global context serializes fragment execution"), engine.rs:278-282 (`while let Ok(request) = requests.recv()` -> run_fragment inline, one at a time).

4 CN q02.r1, cn0 (127.0.0.1:9102): 46a 58 | 469 5 | 476 52 | 472 348 | 464 96 | 460 7 | 46e 28 | 45d 45 | 45a 42 | 452 7 | 44e 8 | 44b 44 | 449 8 = 748 ms; span 40.301->41.382 = 1081 ms; gaps 333 ms (31%). Largest gaps sit before receiver fragments (45a->452 71 ms, 452->44e 50 ms): cross-CN rendezvous (all 4 senders must finish + nixl transmit before the receiver can declare its stream cardinality).

## 4. q15 determinism

Rows returned (1 = correct):
- cn1: r0 1, r1 0, r2 1 (cn1/runs/runs.csv); cn1-cnlog.txt:37-39 -> r1 streams 18 and 22 declared 0 rows, every other stream identical.
- cn4: 1,0,0. standalone: 1,1,1 (standalone plan materializes the CTE once: standalone/runs/q15.explain.txt shows CTE + 2x CTE_SCAN; standalone-quent.txt:205 one GPU_SCAN(3) n=56 in=122.25GB).
- cn1-canon-q15 (SIRIUS_CANONICAL_FLOAT_SUMS=1, 6 runs): 0,1,0,0,1,0. cn1-nocanon-q15 (control): 0,0,1,0,0,0.
- Gate was active: agent-notes/mfs-canon-quent.txt HASH_GROUP_BY(2) n=56 sum 0.50-0.62 s per scan fragment vs 0.075-0.105 s in mfs-nocanon-quent.txt (6-7x, the gather sort), MERGE_GROUP_BY(4) 0.091 vs 0.171 s.
  (quent_bp.py cn1-canon-q15/quent/.cn0/*/ ; quent_bp.py cn1-nocanon-q15/quent/.cn0/*/)
- Batch counts 352 vs 353 per run track rows=0/1 exactly (the result fragment's batch), so they are NOT evidence of batching nondeterminism.

FE plan (survey/explain/q15.costs.txt): the `revenue` CTE is inlined twice: FileScanNode 1 (frag F01, line 241) and FileScanNode 6 (frag F03, line 207) both scan lineitem; F02 join 16 `total_revenue = max` compares F01's finalized sum with F03's max. CTEContext.needInline (CTEContext.java:157-190) inlines unless cost*ratio says otherwise; with every FILES() scan at outputRowCount=1 (StatisticsCalculator.java:664-674) the reuse never wins (cboCTERuseRatio=1.15, SessionVariable.java:1511).
Translator lowers the DECIMAL128(38,4) sum to FP64 (expr_translator.rs:808-836 cast_to_fp64; partial_state.rs:1-30), so the join compares two independently computed FP64 sums for bit equality.
Engine: local aggregate canonical sort is gated (gpu_aggregate_impl.cpp:149-173), merge side always canonical (gpu_merge_impl.cpp:200-221). Both make the sum a function of each batch's row multiset. Remaining source (inference): batch composition is not fixed -- scan chunks are claimed through an atomic cursor (sirius_scan_manager.cpp:235-240 `_index.fetch_add(1)`) and the row-group coalescer packs row groups "in any order" as they finish parsing (duckdb_native_batch_coalescer.hpp:31-40), so the per-batch multisets, hence the FP64 partials, differ between the two evaluations.

Cost of the double scan: cn1-quent.txt:990-1026 q15.r1: two scan fragments each GPU_SCAN n=56 in=122.25GB Comp 8.0/7.6 s, Q 53.9/51.5 s, req 292.7 GB each; total Comp 16.4 s / 4 executor slots = 4.1 s ~ span 4.49 s. Standalone: one scan, Comp 10.1 s, wall 2.6 s. 4 CN: per CN 2 x 30.5 GB, scan fragments wall 0.93 + 0.85 s of the 1.80 s span.

## 5. q02 plan

1 CN (card-compare-cn1.txt:4-15, survey/explain/q02.costs.txt): exch 3 BROADCAST = 800,000,000 rows (partsupp is the BUILD side of join 4 in F00, line 519-535); engine re-plans inside the fragment: cn1-quent.txt fragment 56c has DYNAMIC_FILTER(4) n=5 in=13.10GB and HASH_JOIN(7) (part filtered = build). Subquery: F11 (566) partsupp x supplier x nation = GPU_SCAN 0.779 + HASH_JOIN(14) 0.399 + HASH_JOIN(10) 0.289 s, wall 443 ms = 41% of span; exch 28 SHUFFLE 800M rows by n_regionkey to join a 1-row region (join 31 PARTITIONED, lines 305-327); exch 34/36 = 118,065,576-row min table (SHUFFLE then BROADCAST). Join 37 declares runtime filters remote=true (line 76-79) -- the translator/CN has no runtime-filter handling (grep runtime_filter over crates/starrocks-plan-translator/src and src: only a test fixture field in wire_type_parity.rs:607).
Standalone (standalone/runs/q02.explain.txt): LEFT_DELIM_JOIN restricts the min-subquery to the outer p_partkeys (part filtered first, p_size=15 AND suffix BRASS ~0.4%); standalone-quent.txt:25-30 Comp 1.826 s of which GPU_SCAN 1.77 s (two partsupp scans of 13.1 GB, joins+aggs ~0.06 s) vs cn1 Comp 2.9 s with ~1.0 s in joins/aggs (reservation-ratio.txt).
4 CN (card-compare-cn4.txt:4-18): exch 4, 21, 26, 31 = 800M rows each (partsupp shuffled four times: ps_partkey, ps_suppkey, s_nationkey, n_regionkey), exch 39 BROADCAST 472,262,304 (= 4 x 118M min table), exch 43 GATHER 400. cn4-transmits.txt q02: 135 nixl transmits, 43.6-45.7 GB per run; cn4-quent.txt:316 leases=402 lease_GB=108 lease_held=41.4 s inside a 1.08 s query. HashJoinCostModel.java:140-155 with rowCount=1 on both children.

4-CN scan anomaly (cn4-quent.json q02.r1): fragment ...c472 GPU_SCAN n=2 0.663 s for 3.22 GB (4.9 GB/s) vs ...c464 GPU_SCAN n=2 0.164 s for 3.29 GB (20 GB/s), same table (partsupp), same task count, same run; c472 = 348 ms on every CN (32% of the 1081 ms span). 1-CN partsupp scan 56d: 13.1 GB in 0.788 s Comp (16.6 GB/s). scan_reads bytes=0 in the digest -> the scan_split_read probe is not populated on this path.

## 6. q11

cn1 (card-compare-cn1.txt:66-73): both partsupp scans (a90 16.4 GB 7 tasks Comp 1.18 s; a8d 13.1 GB 5 tasks Comp 0.82 s) serial; standalone Comp 1.05 s total with GPU_SCAN(13) 16.4 GB 0.906 s + two DYNAMIC_FILTERs: DuckDB scans partsupp once for both the main query and the HAVING subquery? (standalone q11.explain.txt shows CTE partsupp with two CTE_SCANs ~805,928,952 rows each, but the Quent shows one GPU_SCAN of 16.4 GB -> the second scan is served from the pinned/cached table; not pursued).
cn4 (card-compare-cn4.txt:45-54, cn4-cnlog.txt q11): exch 4 SHUFFLE 10,000,000 supplier rows on s_nationkey into join 5 PARTITIONED with the GERMANY nation row (exch 2 declared 0/0/1/0 per CN; supplier per CN 2,398,857 / 1,200,355 / 3,201,347 / 3,199,441 = 25-key skew), then exch 7 BROADCAST 1,599,792 = 4 x 399,948; repeated for the subquery (exch 15/17/20). nixl 0.40 GB; fragments ~20 ms -> low cost.
Reservation (reservation-ratio.txt): cn1 q11 req 236.2 GB vs 4.3 GB peak (54x), cn4 67.7x, standalone 3.9x; Rsv wait 0.117 + 0.142 s summed (cn1-quent.txt:699-741) -- not limiting at the 100 GiB pool.

## 7. Commands
- sed -n ranges over cn1-quent.txt / cn4-quent.txt / standalone-quent.txt for q02/q11/q15 r1
- grep -a <query id> cn1/cluster.log | grep "fragment run" (timelines); python re parse for the RPC windows
- python3 quent_bp.py cn1-canon-q15/quent/.cn0/*/ > agent-notes/mfs-canon-quent.txt ; same for nocanon
- json.load(cn4-quent.json)['q02.r1']['fragments'] filtered on c472/c464
- grep -rn CANONICAL_FLOAT_SUMS / canonicalize_row_order / runtime_filter over the source tree
