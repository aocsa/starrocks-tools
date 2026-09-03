# five-way-join family: q07 (SF1000) — working notes

Evidence root: scratchpad/perf/sf1000. Source: /home/prestouser/aocsa/sirius-stacks-wt/perf. All times UTC 2026-09-03.

## Results (results.md, results-ratios.md)
| arm | cold | warm median | FE Time (r1) | fragment span (r1) | FE pre-dispatch |
|---|---|---|---|---|---|
| standalone | 3383 | 3266 | - | 3.220 s (Quent) | - |
| cn1 | 3593 | 3476 | 3494 ms | 34.776 -> 38.057 = 3281 ms | 34.564 -> 34.776 = 212 ms |
| cn4 | 1984 | 2009 | 2000 ms | 56.678 -> 58.471 = 1793 ms | 56.474 -> 56.678 = 204 ms |
T_1/T_4 = 1.73 (eff 0.43); 1-CN parity 1.06. Correctness: cn arms VALUES-DIFFER 9.6e-4 (known 1-0.07 truncation, fact 16).

## FE plans (survey/plan-summary.txt, survey/explain/q07.costs.txt, survey/explain4/q07.costs.txt)
1 CN (10 frags): 4 PARTITIONED(supplier x nation), 9 PARTITIONED build=lineitem(exch 8, 1,822,871,264 rows) probe=exch 6 (799,746),
13 BROADCAST build=orders (exch 12, 1.5B), 17 BROADCAST build=customer (exch 16, 150M), 21 BROADCAST build=nation (2 rows).
Runtime filters (costs.txt lines 196-218, 344-349): filter 1 build=l_suppkey probe=s_suppkey; filter 2 build=o_orderkey probe=l_orderkey;
filter 3 build=c_custkey probe=o_custkey; filter 4 build=n_nationkey probe=c_nationkey. Translator drops them (no 'runtime' in
crates/starrocks-plan-translator/src except an unrelated comment).
4 CN (12 frags): 9 PARTITIONED (lineitem shuffled by l_suppkey), 14 PARTITIONED (orders shuffled by o_orderkey, exch 13 1.5B),
18 BROADCAST customer (exch 17, 150M x4), 23 PARTITIONED (145.7M rows shuffled by c_nationkey to meet 2 nation rows, exch 20/22).
CostModel.java:419-426: BROADCAST cost = outputSize * aliveBackendNumber; with outputSize from cardinality 1 the node count alone decides.
StatisticsCalculator.java:664-676 computeFileScanNode: setOutputRowCount(1), every column UNKNOWN.
EnforceAndCostTask.java:281-330 checkBroadcastRowCountLimit (broadcastRowCountLimit 15,000,000, SessionVariable.java:1833) never fires at card 1.

DuckDB standalone plan (standalone/runs/q07.explain.txt): build sides supplier x nation (~400K), lineitem-filtered x that (~48.9M), customer x nation (~6.1M); orders (1.54B) is the probe.

## Declared stream cardinalities (cn1-cnlog.json / cn4-cnlog.json, q07.r1)
cn1: s1=10,000,000 s3=2 s6=799,746 s8=1,822,871,264 s12=1,500,000,000 s16=150,000,000 s20=2 s24=4 s27=4
cn4 per CN: s8 455.6-455.8M (balanced), s6 ~200K, s13 375.0M, s17 150M each (broadcast), s11 36.4M, s20 = 46.6M / 35.0M / 46.6M / 17.5M (2.7x skew, c_nationkey has 25 values), s22 0/1/1/0, s26 2/0/2/0.

## 1-CN timeline q07.r1 (cn1/cluster.log lines 7126-7357; engine log cn1/engine-.cn0.log gpu_pool lines)
| fragment | start | elapsed | gpu_pool allocated after | note |
|---|---|---|---|---|
| 753ee lineitem F06 | 34.776 | 2453 ms | 64.16 GB (+59.47) | GPU_SCAN 74 tasks 9.639 s summed, in 195.75 GB decoded, out 59.47 GB; Queued 86.3 s |
| 753ed orders F09 | 37.240 | 316 | 82.54 GB (+18.38) | 7 tasks |
| 753ec customer F11 | 37.567 | 33 | 83.77 GB (+1.24) | |
| 753eb nation, 753f1 supplier (0.08 GB), 753f0 nation | 37.602-37.626 | 4-7 | 83.86 GB | |
| 753ef F04 supp x nation | 37.629 | 4 | 83.79 GB | declares s1, s3 |
| 753ea F08 join | 37.643 | 402 | 4.71 GB after | declares s6 s8 s12 s16 s20; req 163.1 GB, alloc-sum 26.3 GB |
| 753e9 agg, 753e8 result | 38.048-38.057 | 4, 3 | | |
Pool before q07: 4.69 GB. Parked before the join: 83.79 - 4.69 = 79.1 GB of 107.4 GB (100 GiB). Fragments strictly sequential (gaps 2-10 ms).

## 1-CN join fragment (engine Query Plan DAG, cn1-ops.txt fragment 772c72f753ea, raw Quent task timestamps rel. to 13:05:37.640)
Pipelines: #12 HASH_JOIN(16) build=#8 (stream 6 supp x nation) probe=#11 (lineitem after DYNAMIC_FILTER(13)); #18 HASH_JOIN(24) build=#14 CONCAT(19) (lineitem x supp, 5.61 GB) probe=#17 CONCAT(23) (orders 18.0 GB); #21 HASH_JOIN(28) build=#5 (customer after DF(5)) ; #24 HASH_JOIN(32) build=#2 (nation).
DuckDB inside the fragment fixed build/probe (the FE had lineitem/orders/customer as build sides).
| op | first | last | tasks | input GB |
|---|---|---|---|---|
| DYNAMIC_FILTER(5) customer | +0.009 | | 1 | 1.24 -> CONCAT(7) 0.10 |
| DYNAMIC_FILTER(13) lineitem | +0.011 | +0.040 | 74 | 59.47 -> PARTITION(14) 4.75 |
| HASH_JOIN(16) | +0.051 | | 2 | 4.68 (0.060 s) |
| CONCAT(19) build of HJ(24) | +0.094 | +0.095 | 3 | 5.61 |
| DYNAMIC_FILTER(21) orders | +0.097 | +0.097 | 7 | 18.38 -> PARTITION(22) 18.38 (no-op, 0.000 s) |
| HASH_JOIN(24) | +0.125 | +0.317 | 9 | 34.83 (0.753 s) |
| HASH_JOIN(28) | +0.375 | +0.386 | 2 | 5.12 (0.031 s) |
Publisher log (engine-.cn0.log 13:05:37.646-650): Wired 1 DF (build est 159949), Wired 1 DF (build est 364,573,796), Not wiring (build est 150M, customer), Wired 1 DF (build est 1);
"Pushed 1 dynamic filter(s)... (2 build rows)" and "(799746 build rows)" only — no push for the 364.6M-est build (its probe DF(21) had already run at +0.097; the DAG shows no dependency between Pipeline #15 and #14).
Standalone (standalone-ops.txt q07.r1): GPU_SCAN(30) orders in 18.37 GB but PARTITION(32) input 1.49 GB (scan-bound DF applied); HASH_JOIN(34) 3 tasks 0.072 s in 7.66 GB; HASH_JOIN(27) 0.050 s. CN HASH_JOIN(24) 0.753 s + CONCAT(23) 0.215 + PARTITION(22) 0.098 vs standalone 0.072 + 0.005 + 0.010.
4 CN F12 (cn4/engine-.cn1.log 13:19:58.309): "Not wiring dynamic filter(s): build side carries neither filter nor opaque-build evidence (build est 36435995 rows)" -> orders 4.50 GB per CN unfiltered, HASH_JOIN(9) 2 x 60 ms per CN (cn4-ops.txt 23a5).

## 4-CN timeline q07.r1 (cn4/cluster.log, rel. to first fragment start 13:19:56.678)
FE deploy RPCs (exec_plan_fragment close events): 56.670-56.678 (15), 56.723-56.724 (12), 56.772-56.774 (8), 56.818 (5) = all 39 instances by +0.140 s; busy <= 30 us, idle <= 1 ms each
(groups from ExecutionDAG.getFragmentsInTopologicalOrderFromRoot, Deployer.deployFragments waits per group; async sender dispatch returned immediately).
Per-CN serial chain (cn1 = 9112): nation F16 +0.000 (7 ms) -> orders F10 +0.046 (143 ms) -> drains -> customer F13 +0.252 (23) -> drains -> lineitem F06 +0.293 (912; other CNs 947-951) -> 3 nixl drains +1.281/+1.355/+1.428 (each 72-76 ms) -> supplier F00 +1.429 (9) -> F04 +1.497 (4) -> F08 +1.508 (92) -> drain -> F12 +1.629 (101) -> drain -> F18 +1.758 (20) -> F19 +1.780 (2) -> [F20 on cn0 +1.788 (4)] -> end +1.793.
Every "gap" between waves is the previous sender's remote drain: next queued fragment starts within 1 ms of the CN's last transmit (verified for all 4 CNs: 23c1 +1.429 vs last 23b8 transmit +1.428; 23be +1.461 vs +1.461; 23bf +1.479 vs +1.478; 23c0 +1.483 vs +1.483).
Critical-path accounting (ms): FE 204 + scans (7+150+29+951 = 1137) + drains (~39+52+5+222 = ~318) + supplier/nation/F04 (~70) + joins (95+104+20 = 219) + agg/result (~15) + misc gaps ~= 2000.

## nixl transmits (cn4-cnlog.json q07.r1; cn4-transmits.txt)
| stream | n | GB | elapsed sum ms | lease sum | write sum | write GB/s med |
|---|---|---|---|---|---|---|
| 8 lineitem | 12 | 43.75 | 885 | 13 | 64 | 657 |
| 13 orders | 12 | 13.50 | 243 | 0 | 12 | 722 |
| 11 lineitem x supp | 12 | 4.21 | 109 | 0 | 0 | 518 |
| 20 by c_nationkey | 12 | 3.77 | 69 | 0 | 2 | 702 |
| 17 customer bcast | 12 | 3.71 | 66 | 0 | 0 | 705 |
| 1, 6, 26, 29, 22, 3 | | 0.07 | | | | |
Total 69.0 GB. A 3.65 GB lineitem drain = 19 frames, elapsed 72-76 ms, write 5-7 ms, lease 1-2 ms: ~3.9 ms per frame = request_staging_lease RPC + WRITE + transmit_packed RPC (cluster.log shows the RPC pairs every ~4 ms per connection). nixl_transport.rs send_fragment: per-batch lease+write+transmit loop, destinations drained sequentially, drains joined on the dispatch worker (compute_node_service.rs run_ready_fragment -> join_into_ready; dispatch_worker :380 is a single FIFO thread; engine.rs: one engine thread, SiriusContext is !Send).

## Memory / staging (measured)
1 CN: 79.1 GB parked before the join (74% of pool); join fragment reservation request 163.1 GB vs 26.3 GB summed peak (6.2x); whole query 670.8 vs 414.5 GB (reservation-ratio.txt); dwell max 2.76 s (lineitem batches wait for the scan to end).
4 CN: pool per CN after lineitem ~5.07 GB; remote inputs resident in the 16 GiB (17.18 GB) staging arena before the joins: lineitem 3 x 3.64 + orders 3 x 1.13 + customer 3 x 0.30 = ~15.2 GB (88%); max concurrent leases 63-64 per CN; quent lease_GB 141.5 = 2 x 69 GB because the sender stages its packed copy in its own arena too.
exchange_staging_arena.cpp:247 throws "exchange staging arena exhausted"; handle_staging_lease (compute_node_service.rs:1302) and rpc_request_lease (nixl_transport.rs) have no wait/retry -> exhaustion fails the query.

## Scan scaling
GPU_SCAN per task: 1 CN 9.639 s/74 = 130 ms; standalone 10.495/74 = 142 ms; 4 CN 3.404-3.541/19 = 179-186 ms (+40%). q06: 100 -> 138-146 ms (+42%).
Aggregate decoded throughput lineitem: 80 GB/s (1 CN) vs 206 GB/s (4 CN). lineitem dir 166 GB / 60 files on GPFS; host 1.69 TB RAM, 329 GB buff/cache. scan_split_read Quent stats: bytes=0 ns=0 on all arms (probe not attributing).

## FE overhead
listFileMeta for all 8 CTE tables every query (cluster.log 13:19:56.477-56.627; lineitem alone 56.483->56.585 = 100 ms; part/partsupp/region listed though unused); CN file_schema.rs parquet_file_schema opens the first file's footer for the schema RPC (no num_rows returned; FE TableFunctionTable.getFileSchema:688).

## Commands
python3 tools as in WORKFLOW-PLAN; timelines from cn*/cluster.log with grep '<qid>' + regex; engine pool lines: awk '$2>="13:05:34.5" && $2<="13:05:38.2"' cn1/engine-.cn0.log | grep gpu_pool; join DAG: grep -n 'QueryBegin: ...753ea' cn1/engine-.cn0.log then sed the next 140 lines; raw Quent task Computing states filtered by timestamp window for per-op first/last.
