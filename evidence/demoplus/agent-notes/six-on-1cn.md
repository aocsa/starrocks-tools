# six-on-1cn: the six (q05 q08 q09 q17 q18 q21) on 1 CN, branch demo/q1q6-integration-plus-fixes

Written 2026-09-04 from the arm `demoplus/arms/dp-cn1-six` (SF1000 decimal TPC-H, 1 CN on GPU 0,
100 GiB GPU pool, 160 GiB host pool, 16 GiB staging arena, Quent on, fusion knob at its default
`leaf`). Paths below are relative to
`/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/`
(abbreviated `S/`). "Measured" = copied from the named file. "Inference" is marked.

Config lines (measured): `S/demoplus/arms/dp-cn1-six/cluster.log` 22:03:45.464 `resolved CN transport
tunables ... fusion_mode=Leaf`; 22:03:48.507 `staging_capacity=17179869184`;
`S/demoplus/arms/dp-cn1-six/engine-.cn0.log` line 10 `exchange staging arena: 17179869184 bytes`;
`[gpu_pool] ... peak=107374182400` (= 100 GiB pool); `S/perf/sf1000/capture-cn.sh:11`
`GPU_MEM=100GiB HOST_MEM=160GiB STAGING=16GiB`.

## 1. Result: 6/6 pass, all three runs each, correct row counts

`S/demoplus/arms/dp-cn1-six/runs/runs.csv` (ms; 18 rows, all `pass`, all `.err` files 0 bytes):

| query | cold r0 | warm r1 | warm r2 | warm median (mean of 2) | standalone warm, 2026-09-03 (n=1) | ratio CN/standalone | pin-table-cn 1 CN ref (pinned) | ratio CN/ref | yesterday 1 CN (perf/profile-sf1000) | spec prediction (fragment-fusion-SPEC.md sec. 8) |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| q05 | 12881 | 5403 | 5721 | 5562 | 2786 | 2.00 | 3.45 † | 1.61 | fail, 101,460 ms | pass, 3.2-4.0 s |
| q08 | 3902 | 3775 | 3765 | 3770 | 3674 | 1.03 | 3.97 † | 0.95 | fail, 168,285 ms | pass, 4-5 s |
| q09 | 5149 | 5206 | 5100 | 5153 | 5427 | 0.95 | 5.29 † | 0.97 | fail, 232,533 ms | pass (medium), 6-7.5 s |
| q17 | 4803 | 4939 | 4973 | 4956 | 3786 | 1.31 | 12.69 | 0.39 | fail, 103,894 ms | pass, 7-10 s |
| q18 | 3265 | 3239 | 3273 | 3256 | 2691 | 1.21 | 8.01 | 0.41 | fail, 54,202 ms | pass, 4.5-6.5 s |
| q21 | 10585 | 10661 | 10580 | 10620 | 6401 | 1.66 | 6.46 † | 1.64 | fail, 114,485 ms | "most likely still OOMs" |
| sum | | | | 33.3 s | 24.8 s | 1.35 | 39.9 s | 0.84 | 775 s to die | |

Sources: standalone `S/perf/sf1000/standalone-failed/runs/runs.csv` (r1 = the only warm run);
reference `S/demoplus/reference-pin-table-cn.md` (header: dagger = "filled, partial 1 CN", so the
q05/q08/q09/q21 reference cells are not clean measurements); yesterday
`S/perf/sf1000/cn1/runs/runs.csv` and `S/perf/sf1000/results.md`; predictions
`S/fix/designs/fragment-fusion-SPEC.md` section 8 "Arm A" row.

Reading: q08 and q09 are at standalone parity; q17/q18 carry a 20-30 % fragment-boundary cost;
q05 is 2.0x and q21 1.66x standalone. Against the pinned reference, q17 and q18 are 2.5x faster,
q08/q09 equal, q05/q21 1.6x slower (but those reference cells are the dagger-filled ones). Only q05
shows a cold penalty (12.9 s vs 5.4-5.7 s warm); the other five are cold = warm within 3 %.

Run-to-run spread (warm pairs): q05 5.9 %, q08 0.3 %, q09 2.1 %, q17 0.7 %, q18 1.0 %, q21 0.8 %.

## 2. Correctness

`S/demoplus/arms/dp-cn1-six/compare.txt`: q17 q18 q21 `MATCH` (maxreldiff 0); q05 `VALUES-DIFFER`
maxreldiff 9.575e-04 (5/5 cells), q08 8.667e-06 (2/2), q09 1.486e-03 (175/175). Row counts match the
oracle (5 / 2 / 175). Standalone on the same data matched all six exactly
(`S/perf/sf1000/compare-standalone-failed.txt`). The 1e-3-class deviation on revenue sums is the
pre-existing CN-path decimal lowering already seen on q01/q03/q07/q19 yesterday
(`S/perf/sf1000/compare-cn1.txt`: 9.57e-04 .. 1.77e-03) and the spec's acceptance row allows it
(`maxreldiff <= 2e-3` confined to sum columns). The identical q05 (9.575e-04) and q08 (8.667e-06)
values appear in the 4-CN/48 GiB arm (`S/demoplus/arms/dp-cn4-six-stg48/compare.txt`), so the
deviation is not fusion-specific. Not a fusion bug by the spec's own criterion; still not oracle-exact.

## 3. Fix 4 (fragment fusion) evidence

### 3.1 CN side (measured, `S/demoplus/arms/dp-cn1-six/cluster.log`)

- 45 lines `fused sender fragment into its local receiver ... mode=Leaf` = 3 runs x 15; 0 lines
  `fragment fusion skipped`. Per query (exchange ids, every run identical):
  q05 `ex18 ex9 ex1 ex4` (4), q08 `ex2 ex4` (2), q09 `ex2 ex4` (2), q17 `ex1 ex4` (2),
  q18 `ex15 ex1 ex10` (3), q21 `ex2 ex13` (2). These are exactly the golden counts and exchange ids
  in `fragment-fusion-SPEC.md` section 7/8 (q05 1,4,9,18; q08 2,4; q09 2,4; q17 1,4; q18 1,10,15;
  q21 2,13).
- `S/demoplus/arms/dp-cn1-six/cnlog.txt`: `fix={'fix4_fused': 4|2|2|2|3|2}` per run; `frags=`
  7/9/7/3/4/7 (the FE still ships every fragment; `dump/` holds 354 `TExecPlanFragmentParams`
  for the 18 runs). The fused senders show up in `fragment_ms` as 2-9 ms entries (cnlog.json
  q05.r1: `[4432, 514, 25, 9, 6, 3, 2]`), i.e. they are deferred, not run.

### 3.2 Engine side: the fused plan shape (measured, `S/demoplus/arms/dp-cn1-six/engine-.cn0.log`)

q05.r1 (window 22:04:19.765-22:04:25.168), engine "Query Plan" prints in order:
- 22:04:20.025, 20.088: `GPU_SCAN (id=0) -> STREAMING_SINK (id=1)` x2 (the declined broadcasts:
  supplier, region; 0.08 GB parked each).
- 22:04:20.173: `GPU_SCAN(0) | GPU_SCAN(3) -> DYNAMIC_FILTER(4) | HASH_JOIN(7) -> PROJECTION(8)
  -> STREAMING_SINK(9)` (customer x orders fused: exchanges 1 and 4 into join 5; parks 4.997 GB).
- 22:04:20.715 -> 25.119 (4.40 s): `STREAMING_SOURCE(0) | STREAMING_SOURCE(3) | GPU_SCAN(6) |
  HASH_JOIN(9) -> PROJECTION(10) | HASH_JOIN(13) -> PROJECTION(14) -> STREAMING_SINK(15)` -- the
  lineitem scan is `GPU_SCAN (id=6)` inside the join fragment (exchange 9 fused into join 10).
  Quent: this fragment is 353 tasks, `GPU_SCAN(6) n=65 sum=9.37 s in=171.0 GB`,
  `HASH_JOIN(9) n=65 2.25 s 170.9 GB`, Queued 236.7 s, Computing 12.24 s
  (`S/demoplus/arms/dp-cn1-six/quent.txt` lines 26-30).
- 22:04:25.132: the receiver with `STREAMING_SOURCE(0) ... HASH_GROUP_BY(16) ... MERGE_GROUP_BY(18)`
  and the two sort/result fragments (25.155, 25.160).

Yesterday's q05 on the same box (`S/perf/sf1000/cn1/engine-.cn0.log` 13:03:43-13:05:41): three bare
`GPU_SCAN (id=0) -> STREAMING_SINK (id=1)` fragments, the lineitem one dying after 101 s of
`OOM at operator GPU_SCAN` reschedules (2500 warnings, `S/perf/sf1000/cn1-cnlog.txt` q05 row).
Standalone plans the whole query as one DuckDB plan (`S/perf/sf1000/standalone-failed/runs/q05.explain.txt`).

The engine log has zero `OOM at operator`, zero `is futile`, zero `retry`, zero `bytes freed` lines
(grep counts on `engine-.cn0.log`).

### 3.3 Fragments run staged, not pipelined (inference from timestamps)

Per-run `fragment_ms` from `S/demoplus/arms/dp-cn1-six/cnlog.json` sum to 90-96 % of the
client-measured wall: q05.r1 4991/5403, q08.r1 3388/3775, q09.r1 4858/5206, q17.r1 4675/4939,
q18.r1 2900/3239, q21.r1 10188/10661. In the engine log each receiver's plan is printed only after
the feeding fragment's `QueryEnd` (q05.r1: fused join fragment ends 25.119, the group-by receiver
starts 25.132). So a query's time is the sum of its fragments plus ~0.3-0.5 s of CN/FE overhead;
the extra over standalone is (a) the un-fused stages that still park and re-read (q17: the
partial-avg-over-lineitem fragment, 2055 ms, `GPU_SCAN(0) 73.5 GB -> HASH_GROUP_BY -> MERGE_GROUP_BY`,
runs before the 2616 ms fused join; q18: 1216 ms group-by sender parking 24.0 GB before the 1584 ms
fused fragment) and (b) the fused fragment itself being slower than standalone's single plan
(q05: 353 tasks / Queued 237 s vs standalone 194 tasks / Queued 130 s,
`S/perf/sf1000/standalone-failed-quent.txt` line 8).

### 3.4 q21: passed where the spec predicted an OOM

Spec (`fragment-fusion-SPEC.md` sec. 1 and 8): "q21 most likely still OOMs (medium; ... DuckDB build
on the 3.8e9-row side)"; asked to record `HASH_JOIN (id=...)` build ports. Measured
(`engine-.cn0.log`, q21.r1 window 22:05:32.931-22:05:43.592):
- 22:05:33.568 -> 36.683 (3.1 s): `STREAMING_SOURCE(0)` on `HASH_JOIN (id=8) port: build`,
  `GPU_SCAN(3) -> FILTER(4) -> PROJECTION(5)` on the probe (`port: default`); parks 36.637 GB
  (`[gpu_pool] QueryEnd query=282 allocated=36636669952`). Preceded by
  `Not wiring dynamic filter(s): ... (build est 730806711 rows)` at 33.567.
- 22:05:36.762 -> 43.494 (6.7 s, the `6783 ms` sender in cnlog): `GPU_SCAN(0) -> FILTER(1) ->
  PROJECTION(2)` is on `HASH_JOIN (id=8) port: build`; the 36.6 GB parked `STREAMING_SOURCE(5)` is
  the probe; join(8)'s output is then the build of `HASH_JOIN (id=16)` against `GPU_SCAN(13)`.
  This is the feared shape (inline filtered lineitem as build), and it completed with no OOM, no
  reschedule, leaving 1.19 GB parked.
q21 is the slowest of the six relative to standalone (1.66x, 10.6 s vs 6.4 s); the two fused
fragments account for 9.9 of 10.7 s.

## 4. Memory (what can and cannot be measured here)

Quent raw memory telemetry is empty on this arm (`quent/.cn0/<session>/memory`, `memory_tier`,
`gpu_device`, `engine`, `channel` all 0 bytes; `task` 16 MB, `data_batch` 16 MB, `plan` 90 KB), the
known CN-exit loss. So there is no true concurrent peak. Three proxies:

1. `[gpu_pool] QueryEnd allocated=` between engine sub-queries = bytes parked at a fragment boundary
   (measured, `engine-.cn0.log`; max per StarRocks-query window via
   `S/demoplus/agent-notes/.pool_windows.py`): q05 4.997 GB, q08 8.782 GB, q09 31.558 GB
   (13.18 + 18.4 GB broadcasts of partsupp/orders, matching the spec's "~13 GB, ~18 GB stay parked"),
   q17 4.000 GB, q18 24.000 GB (the group-by sender the spec listed as "24.0 GB parked, measured P1"),
   q21 36.637 GB. Identical in all three runs of each query.
2. GPU pool high-water (`peak=`, session-level): 0.61 GiB at 22:04:07.911, 63.58 GiB at 22:04:08.739
   (0.9 s into q05 cold), 99.01 GiB at 22:04:19.684 (end of q05.r0), 100.00 GiB = the pool cap at
   22:04:25.119 (end of q05.r1's fused fragment). q05 fills the 100 GiB pool; the 237-631 s of Quent
   Queued time in its fused fragment is consistent with reservation throttling at the cap (inference).
3. Host pool high-water (`[host_pool] peak=`): 71 MB until 22:04:08.760, then 106,962,092,032 bytes
   at 22:04:19.684 (set during q05.r0's fused lineitem fragment, engine query 10) and
   108,326,289,408 at 22:04:30.843 (q05.r2); `allocated` is back to 72 MB at every boundary.
   Yesterday's whole-day host high-water on the same box was 33,991,688,192
   (`S/perf/sf1000/cn1/engine-.cn0.log`). There are no downgrade / spill / `bytes freed` lines at
   info level in today's log, so whether the ~107 GB is HOST-tier spill of the fused fragment's
   port repositories (the mechanism `fragment-fusion-SPEC.md` sec. 8 warned about) or host-side
   read buffers cannot be told from these logs (inference either way). Either reading says q05 at
   1 CN uses essentially the whole 100 GiB GPU pool plus ~107 GB of the 160 GiB host pool.

Quent `alloc=` (sum over tasks of per-task peak, not concurrent) per run for scale only
(`S/demoplus/agent-notes/.quent_windows.py` over `quent.json`): q05 357-413 GB over 795-802 GB of
input, q08 486-489 / 251-257, q09 702 / 621, q17 710-740 / 361-366, q18 675-1053 / 368-590,
q21 935-1282 / 982-1204. Quent captured 101 fragments (85 streaming + 16 ffi) of the 111 shipped;
q21.r2 has no Quent fragments (lost at CN exit).

## 5. Fix 1 (engine OOM fail-fast) evidence: none exercised

Nothing OOMed anywhere in the demo-plus arms: `OOM at operator` = 0 and `is futile` = 0 in
`dp-cn1-six/engine-.cn0.log`, in all four `dp-cn4/engine-.cn*.log`, and in all four
`dp-cn4-six-stg48/engine-.cn*.log`; `cnlog.txt` `oom_resched=0 futile=0 partial=0` on every run.
The 4-CN failures of the six are staging-arena refusals in the CN, not engine OOMs
(`S/demoplus/arms/dp-cn4/runs/q05.r0.err`: `exchange staging arena exhausted: requested 655281088
... 31 leases outstanding holding 17058347776 bytes`; `dp-cn4-six-stg48/runs/q09.r0.err`: same
text with 95 leases holding 50.55 GB of the 48 GiB arena). So the fail-fast path and its
"die in ~3 s instead of 54-232 s" claim (`S/fix/designs/oom-failfast-SPEC.md`, yesterday's report
line 353) are untested by these runs; the 54-232 s deaths of yesterday are simply gone because the
six no longer OOM at 1 CN.

## 6. Fix 2 (per-query parked-output bookkeeping) evidence

Measured, `engine-.cn0.log` via `.pool_windows.py`: for all 18 runs the last `[gpu_pool] QueryEnd`
inside the run's window reads `allocated=0 bytes`, and the first `QueryBegin` of the next run reads
`allocated=0 bytes`; the final line of the log (22:05:54.170, query 315) is `allocated=0`. Parked
bytes rise inside a query (up to 36.6 GB, sec. 4) and are consumed by the receivers before the
result fragment ends. Since nothing failed at 1 CN, `fix2_retired` / `fix2_skipped` are 0 here; the
retire path fired in the 4-CN arm instead (`dp-cn4/cnlog.txt`: `fix={'fix2_retired': 4}` on each of
the six failing runs, `dp-cn4-six-stg48` q09 `{'fix2_skipped': 1, 'fix2_retired': 4}`). Note the
point-sampled `QueryBegin allocated=` 4 ms after a query returns can still show 1.17 GB (q05) or
1.19 GB (q21): that is the previous query's last receiver still tearing down, and it reaches 0
within the same window.

## 7. What this says about "why do the six fail with more than one CN"

Not measured here, but the 1-CN mechanism bounds the answer: fusion in `leaf` mode fires only when a
sender has exactly one local destination and the receiver expects exactly one sender. At 4 CNs the
CN logged `fusion_mode=Leaf` on all four nodes and 0 `fused sender fragment` lines in both
`dp-cn4/cluster.log` and `dp-cn4-six-stg48/cluster.log` (0 `skipped` lines too), as the spec states
("4 CNs: ... nothing fuses by construction", sec. 7). So on 2 or 4 CNs the six run the old shape --
lineitem scanned in its own fragment and shipped whole through the staging arena / parked -- and
the fix that made them pass on 1 CN is inert. The RTX PRO 6000 2-CN failures
(`S/demoplus/reference-2cn-rtxpro6000.md`) are therefore the unfused path on 96 GB GPUs, and the
GB200 4-CN run needed a 48 GiB arena to pass five of six. The 1-CN memory profile in sec. 4 (q05 at
the 100 GiB GPU cap plus ~107 GB host) also says the fused shape itself would not fit a 96 GB GPU
without spilling.

## Helper scripts written for this note (under agent-notes, read-only on the arms)

- `S/demoplus/agent-notes/.pool_windows.py <arm>`: per-run max / last `[gpu_pool]` allocated and
  the pool `peak=` progression.
- `S/demoplus/agent-notes/.quent_windows.py <arm>`: Quent fragments grouped into run windows by `t0`.
