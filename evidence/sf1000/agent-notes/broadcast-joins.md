# broadcast-joins family: q03 q10 q12 q14 q19 (SF1000, 1 CN / 4 CN / standalone)

Evidence root: scratchpad/perf/sf1000. Source: /home/prestouser/aocsa/sirius-stacks-wt/perf.
Arms available: cn1, cn4, standalone (standalone-failed / q15 arms do not cover this family).

## 1. Timings (results.md, warm medians, ms)

| q | standalone | 1 CN | 4 CN | 1CN/standalone | 1CN/4CN |
|---|---|---|---|---|---|
| q03 | 3050 | 3106 | 1607 | 1.02 | 1.93 |
| q10 | 3080 | 3709 | 1789 | 1.20 | 2.07 |
| q12 | 2232 | 2518 | 1284 | 1.13 | 1.96 |
| q14 | 2728 | 2581 | 1226 | 0.95 | 2.11 |
| q19 | 3186 | 3082 | 1440 | 0.97 | 2.14 |

Correctness (compare-*.txt): q12 q14 MATCH; q03/q19 differ 1.8e-3 / 9.6e-4 (known 1-0.07 truncation); q10 TOP-20 reorder from the same cause. Standalone 5/5 MATCH.
OOM reschedules: 0 in every run of these queries on both CN arms (cn1-cnlog.txt, cn4-cnlog.txt).

## 2. FE plan vs actual rows per exchange

1 CN (survey/plan-summary.txt, card-compare-cn1.txt): every join BROADCAST, every cardinality 1.
- q03: 9:INNER JOIN (BROADCAST) build=exch8<-orders(x customer) 145,769,513 rows; 6: build=exch5<-customer 29,998,152
- q10: 13: build=exch12<-lineitem(x orders) 114,711,320; 10: build=exch9<-orders 57,358,391; 3: build=exch2<-nation 25
- q12: 4: build=exch3<-lineitem 31,162,904 (probe orders 1.5B)
- q14: 4: build=exch3<-part 200,000,000 (probe lineitem-filtered ~75M)
- q19: 4: build=exch3<-lineitem 128,557,109 (probe part; part filter applied inside the fragment: 2.16M rows)

4 CN (survey/plan-summary-4cn.txt, card-compare-cn4.txt): the big joins flip to PARTITIONED (shuffle both sides):
- q03: exch2 SHUFFLE lineitem 3,234,397,656 rows; exch9 SHUFFLE orders 145,769,513; exch6 BROADCAST customer 119,992,608 (=30M x 4)
- q10: exch8 SHUFFLE lineitem 1,480,675,200; exch5 SHUFFLE customer 150,000,000; exch11 orders 57M; exch14 114.7M; nation BROADCAST
- q12: exch1 SHUFFLE orders 1,500,000,000; exch4 lineitem 31,162,904
- q14: exch4 SHUFFLE part 200,000,000; exch2 lineitem 74,818,904
- q19: exch4 SHUFFLE lineitem 128,557,109; exch1 part 2,158,428

Why it flips (FE source): StatisticsCalculator.computeFileScanNode (fe-core .../statistics/StatisticsCalculator.java:664-676) sets outputRowCount=1 and every column UNKNOWN;
Statistics.getOutputSize (Statistics.java:88-108) = sum(type widths) x 1; CostModel.visitPhysicalDistribution (cost/CostModel.java:401-432):
BROADCAST cost = outputSize x aliveBackendNumber, SHUFFLE cost = outputSize (x factor). So with N=1 broadcast beats shuffling both sides; with N=4 shuffle wins unless the build side is narrow
(customer c_custkey 4B vs orders 16B -> customer stays BROADCAST). EnforceAndCostTask.checkBroadcastRowCountLimit (task/EnforceAndCostTask.java:281-322) never trips: rowCount 1 <= broadcastRowCountLimit 15,000,000.
=> The distribution choice is a function of column type widths and alive-node count only.

Bytes actually moved at 4 CN (cn4-transmits.txt; per-stream from cluster.log 'transmitted batches via nixl'):
| q | nixl total GB | dominant stream | alternative (1-CN plan generalised: broadcast small side, big table local) |
|---|---|---|---|
| q03 | 60.34 | lineitem 4x14.5 = 58 GB (3x4.85 GB frames per CN) | broadcast orders x customer 2.33 GB parked -> 3x2.33 = 7 GB |
| q10 | 48.04 | lineitem 26.6 GB + customer 19.2 GB | broadcast orders-filtered 0.70 GB (2.1 GB) + lineitem x orders 2.29 GB (6.9 GB) = 9 GB |
| q12 | 23.47 | orders 4x5.8 = 23.2 GB | broadcast lineitem-filtered 0.51 GB -> 1.5 GB |
| q14 | 5.43 | part 4.3 GB + lineitem 1.1 GB | broadcast lineitem-filtered 1.52 GB -> 4.6 GB (wash) |
| q19 | 2.75 | lineitem-filtered 2.75 GB | broadcast part-filtered 0.02 GB -> 0.06 GB |
(parked sizes at 1 CN: cn1-ops.txt STREAMING_SOURCE/SINK input_GB: q03 a208 2.33, q10 315e sink 0.70 / 315d sink 2.29, q12 69b sink 0.51, q14 c4 sink 5.77 / c3 PARTITION(4) 1.52, q19 96d sink 3.66, 96c CONCAT(3) 0.02)

## 3. What DuckDB standalone chose (standalone/runs/qNN.explain.txt)

- q03: HASH_JOIN build = (orders x customer) est 61.6M, probe lineitem est 1.22B -- same shape as the FE 1-CN plan.
- q10: build = (lineitem x orders) est 244M, probe customer x nation est 151.5M -- same as FE 1-CN.
- q12: build = lineitem-filtered est 244M, probe orders est 1.54B -- same.
- q14: build = part est 203M, probe lineitem est 1.22B (actual 75M!) -- same as FE; both wrong vs actual (lineitem-filtered 75M < part 200M).
- q19: build = part-filtered est 8.1M (derived filters pushed), probe lineitem est 1.22B -- OPPOSITE to FE (FE build = lineitem exchange 128.5M).
DuckDB's parquet estimates: lineitem 1.22B (actual 6B), orders 308M (actual 1.5B), customer 151.5M, part 203M.

## 4. Engine-side re-planning inside the receiver fragment (fact 11 mechanism)

engine.rs:540-585 declares the exact parked row count per stream before build(); stream_bind_catalog.hpp:44-46 feeds it to DuckDB's TableFunction cardinality callback.
DuckDB join-order (query_graph_manager.cpp:264-266): right child = build = smaller estimated cardinality. DuckDB JoinFilterPushdown pushes build-side filters into PROBE-side scans
(join_filter_pushdown_optimizer.cpp:61-134); Sirius wraps the probe leaf (GPU_SCAN or STREAMING_SOURCE) in DYNAMIC_FILTER (sirius_physical_plan_generator.cpp:296-324).
=> DYNAMIC_FILTER on X means X is a probe side.

Translated plans (cn1/cluster.log 'translated StarRocks plan fragment' blocks, lines ~3184 q03, 12533 q10, 14162 q12, 15297 q14, 18958 q19):
- q19 receiver 996c: Join(Filter[p_size<=15 ...](part Read), sirius_stream_3). cn1-ops.txt 6493-6546: STREAMING_SOURCE(4)->DYNAMIC_FILTER(5) 120 tasks 3.66GB -> 0.01GB; HASH_JOIN(9) n=1 in=0.02GB. => build = part (est 203M x 0.2 x 0.2 ~ 8.1M; actual 2.16M), probe = lineitem stream. Engine flipped the FE's build side. Fragment 150 ms.
- q10 receiver 315c: Join(Project(Join(Filter[is_not_null($0)](customer Read), stream_2 nation)), stream_12). cn1-ops.txt 3437-3573: Pipeline 9 [STREAMING_SOURCE(10)->DYNAMIC_FILTER(11)] 17 tasks 2.29GB => the 114.7M-row stream is PROBE; build = customer x nation (150M rows, PARTITION(8) 25.55GB 0.479s, CONCAT(9) 0.114s, HASH_JOIN(14) 0.301s).
  Cause: DuckDB RelationStatisticsHelper::DEFAULT_SELECTIVITY = 0.2 (duckdb/src/include/duckdb/optimizer/join_order/relation_statistics_helper.hpp:57, applied .cpp:149) on the FE's null-rejecting scan conjunct (translated by expr_translator.rs:630/929 'is_not_null_pred'): customer 150M x 0.2 = 30M < 114.7M declared. Standalone (no is_not_null) estimates customer x nation 151.5M and has no DYNAMIC_FILTER on the lineitem x orders side.
- q12 receiver 69a: Filter[is_not_null](orders Read) est 1.54B x 0.2 = 308M vs stream 31.2M -> build = stream (sensible). q14 c3: lineitem Read (date filter) 1.22B x 0.2 = 244M vs stream part 200M -> build = part (same as standalone; actual lineitem 75M would be better). q03 a208: lineitem 1.22B x 0.2 = 244M vs stream 145.8M -> build = stream.

## 5. 1-CN timelines (cn1/cluster.log 'fragment run started/finished'), strictly serial per CN

q03.r1: customer 44 ms -> orders x customer 509 ms -> lineitem x stream 2193 ms -> agg 13 -> result 2 (gaps 15/19/3/1 ms). Sum 2761 of 3031 ms reported.
q10.r1: nation 4 -> orders 386 -> lineitem x orders 1753 -> customer x nation x stream 1067 -> agg 179 -> result 5 (sum 3394; gaps ~90 ms).
q12.r1: lineitem 1899 -> orders x stream 405 -> agg 4 -> result 2. q14.r1: part 46 -> lineitem x stream 2330 -> result 4. q19.r1: lineitem 2641 -> part x stream 165 -> result 3.
Engine: one engine thread (engine.rs:242-280 `while let Ok(request) = requests.recv()`), dispatch worker "Executes ready receiver fragments sequentially" (compute_node_service.rs:376-392).
Parity with standalone within +-13% except q10 (1.2x): the hash-join dependency serialises standalone too; the fragment boundary costs only relay + PARTITION/CONCAT (q03: CONCAT(2) 13 ms on 2.33 GB).

## 6. 4-CN timeline q03.r1 (cluster.log lines 10260-11250), span 1.418 s (Quent), 1639 ms reported

All 16 fragment RPCs arrived 13:19:44.708-44.719 (+ result fragment 44.765). Per CN (times relative to 44.7187):
- lineitem scan+shuffle senders: run 609-683 ms (16-17 tasks, GPU_SCAN 2.15-2.44 s Computing = 134-145 ms/task over 4 slots)
- drain of 3 remote destinations, SEQUENTIAL, after 'finished': transmits done at +95/+190/+283 ms (each elapsed_ms 93-96, lease_ms 1, write_ms 6-8 = 600-717 GB/s, 4.82-4.89 GB) => ~87 ms of each 95 ms is export_packed_next (engine.rs:813 engine_call -> pack on the engine thread)
- next fragment on the same CN (customer scan, 35-43 ms) starts exactly when the last transmit completes (+0.893..0.970 s)
- orders x customer fragments +1.008 s, run 185-277 ms; their drains 10-15 ms
- partitioned join fragments +1.292 s, run 127-130 ms (relay 16-17 local + push 3x16-17 remote frames; HASH_JOIN(6) 8 tasks 20 GB 0.34 s)
- result +1.431 s
Critical path: 0.68 scan + 0.28 drain + 0.04 customer + 0.28 orders x customer + 0.13 join + gaps = 1.42 s. The lineitem scan of the OTHER tables' fragments could overlap but does not (single engine thread; drains joined before the next queued sender).

Drain cost per sender (finished -> last transmit; next fragment start on that CN):
q03 stream 2: 0.283-0.287 s (3 x 14.5 GB), next fragment +0.283-0.287 s
q10 stream 8 (lineitem): 0.145-0.159 s; stream 5 (customer): 0.083-0.102 s; next fragment starts exactly after
q12 stream 1 (orders): 0.115-0.125 s; q14: 0.02-0.03 s; q19: 0.055-0.078 s
compute_node_service.rs:1207-1219 documents the design: "one FIFO request channel into ONE transport thread ... the drains still run one at a time, in the FE's destination order".

Receive-arena occupancy (q03 4-CN, receiver fid ..53 on 9102): stream 2 frames 4.8885+4.8423+4.8506 GB + stream 9 3x0.146 + stream 6 3x0.03 = 15.1 GB = 88% of the 16 GiB arena
(SIRIUS_EXCHANGE_STAGING_BYTES=16GiB, capture-cn.sh). Frames arrive 45.42-45.62 and are consumed ('received remote batches') 46.014-46.03 => 0.4-0.6 s dwell. cn4-quent q03.r1: leases=474, 122.67 GB, lease_held 105 s, maxconc 55-56 per CN.
Exhaustion path: exchange_staging_arena.cpp:245-258 throws invalid_input_exception "exchange staging arena exhausted ... (raise SIRIUS_EXCHANGE_STAGING_BYTES)"; nixl_transport.rs:720 `rpc_request_lease(...)?` -> drain Err -> fragment fails. No wait/retry.

## 7. Per-task GPU_SCAN Computing (ms per ~2.2-2.3 GB task) 1 CN vs 4 CN vs standalone (from *-quent.json top_ops)

| q | cn1 | cn4 (4 CNs) | standalone |
|---|---|---|---|
| q03 lineitem | 102 | 134-145 | 111 |
| q10 lineitem | 90 | 100-119 | 96 |
| q12 orders | 107 | 131-146 | 117 |
| q14 lineitem | 151 | 240-249 | 176 |
| q19 lineitem | 85 | 113-126 | 98 |
scan_split_read probe: only counts recorded (bytes=0, ns=0 in cn*-quent.json), so I/O vs decode cannot be split.

## 8. Memory backpressure (reservation-ratio.txt, quent state sums)

cn1: q03 req 698.8 GB / peak 49.3 GB (14.2x), q10 6.3x, q12 1.8x, q14 15.2x, q19 1.2x; Reserving state sum <= 0.180 s per query (q10 0.180, q14 0.138, q12 0.110, q03 0.003, q19 0.004).
Queued sums 58-156 s (4 pipeline slots vs 56-120 scan tasks) -- slot queueing, not memory. cn4: Reserving <= 0.007 s per fragment. No OOM reschedules; engine-log warnings in the 13:19-13:29 window are unrelated (slot_for_device fallback, no-scan queries).
=> No memory backpressure in this family at SF1000; the over-reservation is harmless here because 4 slots x per-task request << 100 GiB pool.

## Commands run (all read-only; python one-liners over cn4/cluster.log, cn*-quent.json, cn4-cnlog.json — see transcript)
- results.md, results-ratios.md, reservation-ratio.txt, cn4-transmits.txt, card-compare-cn{1,4}.txt, plan-summary{,-4cn}.txt, survey/explain/q{03,10,12,14,19}.costs.txt, survey/explain4/q{03,10}.costs.txt
- cn1-quent.txt / cn4-quent.txt / standalone-quent.txt sections for r1; cn1-ops.txt pipelines (q03 1541-1677, q10 3398-3573, q12 4809-4929, q14 5480-5560, q19 6492-6566); standalone-ops.txt (1148-1231, 2420-2533, 3460-3524, 3810-3855, 4341-4386)
- cluster.log greps for fragment run started/finished, declared input stream cardinality, transmitted batches via nixl, received remote batches, translated StarRocks plan fragment
