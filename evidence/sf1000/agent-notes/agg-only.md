# agg-only family (q01, q06): working notes

Evidence root: scratchpad/perf/sf1000 (SP). Source: /home/prestouser/aocsa/sirius-stacks-wt/perf (WT).
Scripts written here: agent-notes/agg_tasks.py (per-task GPU_SCAN durations, Queued, reservation, Computing concurrency),
agent-notes/scan_reads.py (scan_split_read Statistics joined to runs; one task FSM; lease timing), agent-notes/req_hist.py
(reservation multiplier histogram; materialize time vs files-per-split).

## 1. Timings (results.md, results-ratios.md)

| q | standalone warm | 1 CN warm | 4 CN warm | T_cn1/T_alone | T_1/T_4 | eff |
|---|---|---|---|---|---|---|
| q01 | 4882 | 5073 | 2160 | 1.04 | 2.35 | 0.59 |
| q06 | 1974 | 1916 | 852 | 0.97 | 2.25 | 0.56 |

Correctness: q06 MATCH everywhere; q01 CN arms VALUES-DIFFER 9.57e-4 (8 cells) = the known `1 - 0.07` decimal(16,2) lowering
(compare-cn1.txt:2, compare-cn4.txt:2); standalone MATCH 1.4e-16.

## 2. Plans (survey/explain/q01.costs.txt, q06.costs.txt, standalone/runs/*.explain.txt)

- q01 FE: 3 fragments: F00 scan+Project+AGGREGATE(update serialize, STREAMING) -> exch 3 SHUFFLE(l_returnflag,l_linestatus) ->
  F01 AGGREGATE(merge finalize)+SORT -> exch 6 GATHER (MERGING) -> F02 result. Cardinality 1 everywhere. No joins, so the
  cardinality-1 defect has no planning consequence here. card-compare: exch3 actual 4 (1 CN) / 16 (4 CN), exch6 4.
- q06 FE: 2 fragments: F00 scan+Project+AGGREGATE(update serialize) -> exch 3 GATHER -> F01 merge finalize. exch3 actual 1 / 4 rows.
- Standalone DuckDB plans are structurally identical (READ_PARQUET with pushed filters -> PROJECTION -> HASH_GROUP_BY/UNGROUPED_AGGREGATE).
- Translator output for q01 scan fragment (cn1/dump/fragment-000x "=== Plan"): 6 nested Project rels with decimal->fp64 casts and a
  `1 - l_discount` computed in fp64 then cast to decimal<16,2> and back to fp64 (the truncation source), Filter(lte l_shipdate) over Read.
- With 4 CNs the FE plans are unchanged for q01/q06 (plan-summary-4cn.txt lines 1, 11).

## 3. Where the time goes (Quent, cluster.log, FE audit)

Per-statement FE-side timeline, from the audit Timestamp (statement received) and the CN log RPC spans
(agent-notes: computed by the inline script in this session; cluster.log get_file_schema/exec_plan_fragment/fragment run lines):

| arm run | audit Time | 8 get_file_schema RPCs | first exec_plan_fragment | first fragment start | last fragment finish | tail |
|---|---|---|---|---|---|---|
| cn1 q01.r1 | 5171 | +6 .. +128 ms | +151 | +192 | +5168 | 3 |
| cn1 q01.r2 | 4933 | +7 .. +132 | +154 | +196 | +4930 | 3 |
| cn1 q06.r1 | 1888 | +6 .. +131 | +147 | +166 | +1886 | 2 |
| cn4 q01.r1 | 1999 | +9 .. +142 | +171 | +188 | +1996 | 3 |
| cn4 q06.r1 |  836 | +5 .. +127 | +137 | +141 | +835 | 1 |

The lineitem get_file_schema RPC alone is 91-95 ms (cluster.log 13:03:12.521 "busy=28.7ms idle=62.1ms"; 4 CN 13:19:33.767
"busy=30.9ms idle=64.6ms"): file_schema.rs::parquet_files_schema awaits parquet_file_schema(path) for each of the 60 files
sequentially (file_schema.rs:68). 8 RPCs per statement = one per FILES() CTE in the bench SQL (7 of them unused by q01/q06).
So ~130 ms of a fixed ~140-190 ms pre-scan latency is schema inference; standalone pays 13 ms total (4858 wall vs 4.845 s Quent span).
Share of wall: q01 1 CN 3.7%, q01 4 CN 9.4%, q06 1 CN 8.7%, q06 4 CN 17%.

Scan fragment (Quent task FSM; cn1-quent.txt lines 17-32, 381-391; cn4-quent.txt 39-76, 1176-1201; standalone-quent.txt 7-12, 79-84):

| arm | run | scan tasks | GPU_SCAN sum | HASH_GROUP_BY/UNGROUPED | PROJECTION | Comp sum | Queued sum | wall | busy=4 slots |
|---|---|---|---|---|---|---|---|---|---|
| cn1 | q01.r1 | 107 | 12.15 | 4.57 | 1.87+0.95 | 19.5 | 251.7 | 4.94 | 4.87 s of 4.94 |
| alone | q01.r1 | 107 | 12.32 | 5.91 | 0.43+0.33 | 19.0 | 243.3 | 4.85 | 4.71 of 4.80 |
| cn4 | q01.r1 | 4x27 | 4.19/4.73/4.88/4.71 | ~1.1 each | ~0.6 each | 6.0-6.7/CN | 18.5-23.2/CN | 1.74 | 1.49-1.65 of 1.53-1.77 |
| cn1 | q06.r1 | 65 | 6.52 | 0.07 | 0.08 | 6.67 | 51.0 | 1.70 | 1.66 of 1.68 |
| alone | q06.r1 | 65 | 7.47 | 0.12 | 0.12 | 7.7 | 59.7 | 2.01 | 1.89 of 1.95 |
| cn4 | q06.r1 | 16-17/CN | 2.29-2.40/CN | | | 2.35-2.44/CN | 4.1-4.6/CN | 0.65-0.67 | 0.54-0.57 of 0.62-0.65 |

All scan tasks are created within 9-50 ms of fragment start (created_window) and wait FIFO for one of 4 pipeline slots
(pipeline.num_threads default 4, configuration.md:92). Queued median = half the fragment wall (2.34 s of 4.94; 0.80 of 1.70).
Reserving time 0.001-0.004 s per fragment: the memory gate never binds (4 x 7 GB in flight of 100 GiB).
Two-phase aggregation cost: merge fragment 6-24 ms, result fragment 3-13 ms, fragment hops 1-7 ms; cn1 q01.r1 post-scan tail
17.565 -> 17.584 = 20 ms; cn4 q01.r1 35.610 -> 35.650 = 40 ms (15 nixl transmits of 960 B, 0-1 ms each). Not a cost centre.

## 4. Per-split scan statistics (agent-notes/agg_tasks.py, scan_reads.py)

scan_split_read probe (scan_telemetry.cpp:41-57 keys: compressed_bytes, output_bytes, materialize_ns): q01 reads 40.4 GB
compressed (0.38 GB/split, 55.6M rows, 2.36 GB decoded) -> 252.9 GB output; q06 34.9 GB (0.55 GB/split) -> 1.9 GB after filter.
pyarrow footer of part.0.parquet: q01's 7 columns = 0.673 GB/file x 60 = 40.4 GB; q06's 4 columns 0.582 x 60 = 34.9 GB (match).
Codecs: SNAPPY/UNCOMPRESSED, decimal columns are INT64 physical; l_comment (unused) is 1.17 GB/file.

| arm CN run | splits | GPU_SCAN med | p90 | max | read GB/s (fragment) |
|---|---|---|---|---|---|
| cn1 q01.r1 | 107 | 0.107 | 0.144 | 0.167 | 40.4/4.94 = 8.2 |
| alone q01.r1 | 107 | 0.117 | 0.146 | 0.165 | 8.3 |
| cn4 q01.r1 cn0/cn1/cn2/cn3 | 27 each | 0.146/0.124/0.134/0.128 | 0.237/0.505/0.379/0.321 | 0.244/0.541/0.384/0.548 | 40.4/1.768 = 22.9 aggregate |
| cn4 q01.r2 cn0/cn1/cn2/cn3 | 27 each | 0.132/0.099/0.125/0.135 | 0.797/0.155/0.604/0.793 | 0.806/0.172/0.627/0.870 | 40.4/2.06 = 19.6 |
| cn1 q06.r1 | 65 | 0.109 | 0.131 | 0.141 | 34.9/1.70 = 20.5 |
| cn4 q06.r1 per CN | 16-17 | 0.150-0.162 | 0.208-0.222 | 0.220-0.228 | 34.9/0.687 = 50.8 aggregate |

- Bytes per CN on 4 CN are balanced (63.8/64.0/64.1/64.6 GB decoded, same four sets rotate across CNs run to run), so the
  per-CN spread (GPU_SCAN 3.11 s vs 6.27 s in q01.r2) is not data skew.
- q01 tails do not correlate with files-per-split (req_hist.py: 1-file splits also stall, e.g. cn2 r1 1f max 0.382).
- q06 on 4 CN shows a uniform +40-60% per split without the heavy tail; it pulled 50.8 GB/s aggregate, so q01's 22.9 GB/s tails
  are not a raw GPFS bandwidth cap. Cause of the q01 0.5-0.87 s stalls (10% of splits on 2-3 of 4 CNs, varying per run) not identified.
- Storage: /scratch is GPFS (df -T). The uring datasource opens files O_RDONLY|O_DIRECT (src/io/uring/uring_reactor.cpp:528,
  io/uring/config.hpp:27-28 use_odirect default true), so warm runs re-read everything from GPFS; no page cache.

Scaling decomposition (ideal = 1-CN scan fragment / 4):
- q01.r1 4 CN 2022 ms = 188 FE front + 1768 slowest scan fragment (ideal 1239, excess 529 = 26% of wall) + 66 post-scan/tail.
- q06.r1 4 CN 836 ms = 141 FE front + 687 slowest scan fragment (ideal 429, excess 258 = 31%) + 8.

## 5. Reservation estimate vs peak (req_hist.py; raw Reserving/Computing records)

- After execution history, each q01 scan task requests exactly the measured peak: 6.98 GB for a 2.41 GB input (2.9x);
  q06: 3.74 GB for 1.52 GB (2.5x). Hence req/peak 1.07 / 1.16 in reservation-ratio.txt.
- The first 4 tasks of every fragment (one per pipeline slot, no history yet) request 8x: q01 19.52 GB x 4 = 78 GB,
  q06 13.32 GB x 4 = 53 GB (histogram {8.1: 4, 2.9: 103} / {8.8: 4, 2.5: 61}). sirius_gpu_scan_operator.cpp:66
  kMaxNumericCarrierExpansion = 8, :664-668 no_history_peak_memory_estimate for fresh reads. On 4 CN every CN pays this wave,
  which is the whole of the higher req/peak (1.27, 1.64): 23 x 6.98 + 4 x 19.5 = 238.5 GB vs 235-237 reported per CN.
- The executor clamps a request to the space max (gpu_pipeline_executor.cpp:163-176) and a partial reservation triggers the
  downgrade predicate path (:200-260). With 8 pipeline threads the first wave would ask 156 GB of a 107 GB pool.

## 6. Tool caveats found

- quent_bp.py attributes staging leases by a +-0.5 s time window (quent_bp.py "leases carry no producer pipeline"); the bench
  issues the next statement ~6 ms after a run returns (runs.csv: q06.r2 13:19:53.609 + 852 ms, q07.r0 13:19:54.467), so
  the last run of each query inherits the next cold run's first leases: cn4 q06.r2 55 leases/25.6 GB, q01.r2 50/2.5 GB while
  q01/q06 streams carry 64-960 B (cn4-cnlog.json transmits). Verified: leases inside the q06.r2 span 0-1 per CN, 12-13 within
  +0.5 s after it.
- quent_bp.py scan_reads reads keys `bytes`/`elapsed_ns`; the probe emits compressed_bytes/output_bytes/materialize_ns
  (scan_telemetry.cpp:52-56), so the digest shows bytes=0 ns=0.

## 7. Source read

engine.rs:486-640 run_fragment_inner (declare columns/senders, exact declared cardinality, build, relay); local_exchange.rs
header (receiver-first rendezvous); gpu_pipeline_executor.cpp:135-262 (reservation, clamp, downgrade predicate), :340-400
(OOM reschedule MAX_RETRIES=100); sirius_gpu_scan_operator.cpp:640-673; file_schema.rs:1-80; TableFunctionTable.java:688-730
(FE picks a random alive node and sends one RPC with all files); engine_settings.rs:60-165 (derived YAML: memory, datasource
toggle, cpu_affinity, telemetry; no pipeline/prefetch knobs); io/uring/config.hpp; docs configuration.md:91-94, 337-345,
775-806 (GB300 host-pinned tuning: pipeline.num_threads 8 => q1 -16%, cliff at 12).

## 8. FE scan-range granularity (cn1/dump/fragment-0004.txt, a q01 scan fragment)

per_node_scan_ranges carries 60 paths / 91 TBrokerRangeDesc byte ranges (0.74-2.58 GB each, i.e. files are cut into byte ranges,
not handed out whole). With 4 CNs the FE spreads these ranges across CNs (scan_paths.rs:70 add_ranges), which is why the CN-side
coalescer forms 27 splits per CN spanning 1-8 files (scan_reads.py files/split histogram) versus 1-2 files per split on 1 CN.
No correlation of stall time with files-per-split was found (section 4).

## 9. Findings ranked (see structured output)

1. 4-CN scan-phase scaling loss (26-31% of wall) from per-split slowdowns/stalls on O_DIRECT GPFS reads; cause of q01 stalls open.
2. 4 pipeline slots bound the scan fragment at 1 CN (busy 4/4 for 98%+, memory gate idle); CN launcher cannot raise them.
3. 8x no-history scan reservation (first 4 tasks per fragment per CN) vs 2.5-2.9x measured; blocks more slots, inflates first wave.
4. Fixed ~140-190 ms FE front per statement, ~130 ms of it FILES() schema inference (8 RPCs, lineitem 60 footers sequential).
5. Two-phase aggregation and fragment hops are cheap (20-40 ms); parity 1.04/0.97; decimal lowering shifts cost, causes 9.6e-4.
6. quent_bp.py: lease time-window misattribution and scan_split_read key mismatch.
