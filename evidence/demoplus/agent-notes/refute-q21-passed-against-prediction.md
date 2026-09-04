# Refutation check: "q21-passed-against-prediction" (task six-on-1cn)

Verdict: headline confirmed, three supporting statements contradicted by the measurements.
All paths below are under `S = scratchpad/demoplus/arms/dp-cn1-six` unless stated.

## Confirmed (measured)

- q21 passed in all three runs: `S/runs/runs.csv` 10585 / 10661 / 10580 ms, 100 rows;
  `S/compare.txt` q21 MATCH maxreldiff 0.
- Spec did predict an OOM: `fix/designs/fragment-fusion-SPEC.md` sec. 1 "q21 most likely still
  OOMs (medium; the leaf rule keeps the F03 stream declared exactly while l3 becomes an estimate
  ...)"; sec. 8 Arm A "q21 recorded either way (predicted: OOM at GPU_SCAN in the fused F07 with
  the build on l3)".
- The fused fragment (engine query 285, window 101, 22:05:36.712-43.494, `S/engine-.cn0.log`
  lines 8546-8661) has `GPU_SCAN (id=0) -> FILTER (id=1) -> PROJECTION (id=2)` on
  `HASH_JOIN (id=8), port: build` (type LEFT) and `STREAMING_SOURCE (id=5)` on `port: default`;
  join(8) -> FILTER(9) -> PROJECTION(10) is the build of `HASH_JOIN (id=16)` (RIGHT_SEMI) whose
  probe is `GPU_SCAN (id=13)`. The translated plan (`S/cluster.log:6899-6921`) shows the leaf on
  the build port is the filtered lineitem l3 (`Filter[gt(l_receiptdate, l_commitdate)]
  Read[lineitem ...]`) and GPU_SCAN(13) is the unfiltered lineitem l2. The translator emitted
  `Join[&Right](l3, sirius_stream_9)`, i.e. the stream as DuckDB's build; the engine printed
  `type: LEFT` with l3 on build, so DuckDB flipped RIGHT->LEFT and chose the lineitem side —
  exactly the spec's "mixed information set" mechanism (stream 9 declared exactly at
  1,831,833,139 rows, `S/cluster.log:6929`; l3 an inline estimate).
- No OOM / reschedule / downgrade in the q21.r1 window: 0 lines matching
  oom|resched|downgrade|freed|spill|retry between 22:05:33 and 22:05:43 in `S/engine-.cn0.log`;
  `S/cnlog.json` q21.r1 engine counters all 0.
- 36.637 GB parked between the two big fragments: `[gpu_pool] QueryEnd query=282
  allocated=36636669952` (`S/engine-.cn0.log:8542`); query 285 begins at the same figure and ends
  at 1,189,635,840.

## Contradicted (measured)

1. **Engine query 282 is not a fused fragment.** It is fragment instance ...9347 = FE fragment
   `S/dump/fragment-0220.txt` (nodes 8 HASH_JOIN, 7, 6 EXCHANGE, 3 lineitem FILE_SCAN; sink
   HASH_PARTITIONED -> dest 9). Its lineitem scan is native to the FE fragment (StarRocks put the
   l1 scan beside the join; orders is broadcast to it from ...9348, `fragment-0222.txt`, sink
   `TPartitionType(0)` = UNPARTITIONED). It ran with `inputs=1 outputs=1` (`S/cluster.log:6836`)
   and has no fusion line. Both fused senders went into ONE receiver, ...9345 = engine query 285:
   `S/cluster.log:6794` (sender ...9349, exchange 2, `fragment-0219.txt` node 1), `:6797` (sender
   ...9346, exchange 13, `fragment-0221.txt` node 12), `:6898` "fused deferred sender plans into
   receiver ... 9345 fused=2 senders=[9346, 9349]". `fix4_fused: 2` in cnlog counts fused
   senders, not fused receivers. So "its two fused fragments are 9.9 of the 10.7 s" is wrong: the
   one fused fragment is 6783 ms = 64 % of 10661 ms; the 3136 ms fragment is F03, the un-fused
   middle fragment whose 36.6 GB output is what `all` mode would remove.
2. **"FE estimate 730,806,711 rows" is not an FE number.** It is the exact parked row count the CN
   declared for stream 6 (`S/cluster.log:6837 declared input stream cardinality stream_id=6
   rows=730806711`, from `experimental/starrocks/src/engine.rs:464-485`, sum of parked sender
   output_row_count). Fix 3 is not on this branch, so the FE ships FILES() scans at cardinality 1.
   730,806,711 x 8 B = 5.85 GB matches the 5,937,807,104 bytes parked by query 279.
3. **q21 is not the slowest of the six relative to standalone.** Warm medians vs
   `perf/sf1000/results-failed.md` (standalone warm, n=1): q05 5562/2786 = 2.00x, q21 10620/6401 =
   1.66x, q17 1.31x, q18 1.21x, q08 1.03x, q09 0.95x. q21 has the largest absolute gap (+4.2 s)
   and the longest absolute time; q05 has the worst ratio.
4. Wording in the impact: "the spec's build-side fear did not materialise". The build-side
   placement did materialise (item above); only the predicted OOM did not.

## Inference

The l3 build fit because the pool is 100 GiB: 3.8e9 rows x (i64 + i32) ~ 45 GB of build columns
plus the 36.6 GB parked probe. Quent (`S/quent.txt:322-326`, the 6.641 s fragment) shows
GPU_SCAN(0) in=123 GB, HASH_JOIN(8) in=67.5 GB, GPU_SCAN(13) in=73.5 GB, HASH_JOIN(16) in=74 GB;
raw memory telemetry for the arm is empty, so there is no measured concurrent peak.
