# Why q05 q08 q09 q17 q18 q21 fail with 2 or 4 CNs on demo/q1q6-integration-plus-fixes

Evidence root: `scratchpad/demoplus/arms/` (arms `dp-cn4`, `dp-cn1-six`, `dp-cn4-six-stg48`, `dp-cn4-q09-stg72`), yesterday's bundle `scratchpad/perf/sf1000/`, the report `~/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md`, source in the read-only worktree `/home/prestouser/aocsa/sirius-stacks-wt/demo-plus` (`W/`) and the `feat/pin-table-cn` checkout at `/home/prestouser/aocsa/sirius` (`C/`). Every number is **measured** (file named) unless marked **inference**.

## 1. One sentence

At N > 1 the FE hash-partitions the whole lineitem projection across the CNs (cardinality-1 plans, C1/P1); the CN exchange is receiver-first and park-then-export, so every CN must hold its own parked share **plus** the inbound frames from the other N-1 CNs before the join fragment may start; the inbound frames live in the fixed staging arena until then, and the arena throws instead of waiting (B6). With the default 16 GiB arena all six die of arena exhaustion at 4 CNs; with 48 GiB five pass and q09 still exhausts; with 72 GiB q09 moves to a pool OOM at the receiver. On a 96 GB GPU at 2 CNs the per-CN parked share alone exceeds any pool for four of the six, and the other two exhaust the arena.

## 2. What was measured

### 2.1 Outcomes per arm (SF1000 decimal, `runs/runs.csv` of each arm)

| arm | pool / arena per CN | q05 | q08 | q09 | q17 | q18 | q21 | failure class |
|---|---|---|---|---|---|---|---|---|
| dp-cn1-six (1 CN, fusion `leaf`) | 100 GiB / 16 GiB | pass 5.4 s | pass 3.8 | pass 5.2 | pass 4.9 | pass 3.2 | pass 10.6 | none; `fix4_fused` = 4/2/2/2/3/2 (`dp-cn1-six/cnlog.txt`) |
| dp-cn4 (4 CN) | 100 GiB / 16 GiB | fail 1.4 s | fail 2.4 | fail 2.1 | fail 1.3 | fail 1.3 | fail 2.1 | **arena exhausted** on all six |
| dp-cn4-six-stg48 (4 CN) | 90 GiB / 48 GiB | pass 5.3/3.2 | pass 3.2 | **fail** 4.0 | pass 2.6 | pass 2.0 | pass 5.2 | q09 **arena exhausted** |
| dp-cn4-q09-stg72 (4 CN) | 64 GiB / 72 GiB | | | **fail** 10.5 | | | | q09 **pool OOM** in `push_packed` |

Pool sizes: `capture-arm.sh` default `GPU_MEM=100GiB STAGING=16GiB`; arena sizes from `engine-.cn0.log` line `exchange staging arena: 17179869184 / 51539607552 / 77309411328 bytes (cudaMalloc)`; the 64 GiB pool of stg72 is read off the OOM peak `peak=68698126080 bytes` (= 63.98 GiB) in `dp-cn4-q09-stg72/engine-.cn3.log:201` (the run log does not print GPU_MEM_OVERRIDE). 1 CN and 4 CN results match the oracle where they pass (`compare.txt`; q05/q08 differ at 1e-4..1e-5 rel, the known FP64 lowering).

### 2.2 The six error strings at 4 CNs, 16 GiB arena (`dp-cn4/runs/qNN.r0.err`)

| q | failing call | requested | free | leases outstanding | bytes held |
|---|---|---:|---:|---:|---:|
| q05 | `request_staging_lease` (peer arena, backend 10001) | 655.3 MB | 121.5 MB | 31 | 17.06 GB |
| q08 | `export_packed` (local arena) | 664.0 MB | 1152.9 MB | 43 | 16.03 GB |
| q09 | `request_staging_lease` | 649.0 MB | 344.1 MB | 35 | 16.84 GB |
| q17 | `request_staging_lease` | 654.0 MB | 162.5 MB | 26 | 17.02 GB |
| q18 | `request_staging_lease` | 659.3 MB | 561.6 MB | 31 | 16.62 GB |
| q21 | `export_packed` | 422.5 MB | 243.7 MB | 47 | 16.94 GB |

Capacity 17,179,869,184 in every message. At 48 GiB, q09: `95 leases outstanding holding 50,552,338,944 bytes` of 51,539,607,552 (`dp-cn4-six-stg48/runs/q09.r0.err`); the arena exit summaries of the four CNs read `peak live 50.55 / 43.65 / 50.88 / 51.28 GB` (`dp-cn4-six-stg48/engine-.cn*.log`, line `exchange_staging_arena.cpp:169`), i.e. q09 alone drove three arenas to the cap. At 72 GiB, q09: `failed to push a staged remote batch from sender 0 into stream 4: std::bad_alloc: out_of_memory` (`dp-cn4-q09-stg72/runs/q09.r0.err`).

None of the 4-CN failures reached the pipeline executor's OOM path: `oom_resched=0 futile=0` on every row of the three 4-CN `cnlog.txt`, so fix 1 never fired; the arena throws first. Fix 2 did its job on every failure: `fix2_retired=4` per failing query (one retire per CN) and every `retired a query's parked sender outputs` line says `still_parked=0` (`dp-cn4/cluster.log` 22:02:35.52-35.74, `dp-cn4-q09-stg72/cluster.log:2108`), so unlike yesterday's B4 the failures leave 0 bytes behind. Fix 4 is inert at 4 CNs by construction (`fragment-fusion-SPEC.md` "Anything on 4 CNs ... nothing fuses"): no `fix4_*` counter appears in any 4-CN row.

### 2.3 Timeline of one failure (dp-cn4 q05, `dp-cn4/cluster.log`, ANSI stripped)

```
22:02:34.598  four lineitem senders start (role=sender inputs=0 outputs=4)
22:02:35.356 .. 35.434  the four senders finish (757-835 ms; each parked 42.5-43.1 GB, see 2.4)
22:02:35.522 / 35.531 / 35.535  three CNs: request_staging_lease against backend 10001 fails, 31 leases / 17.06 GB
22:02:35.742  one CN: export_packed fails against its own arena, 30 leases
```
The receiver of streams 7/9 never started (`dp-cn4/cnlog.txt q05.r0: frags=10 {'sender': 10}`, no `result`). Only one sender got its drain going before the wall: `stream 9 from sender 2 -> 9112: 17 batches 10.58 GB; -> 9132: 18 batches 10.58 GB` (transmit sums from the same log). 10.6 GB per destination is exactly one quarter of a 42.75 GB share; three senders' quarters = 32 GB into one 17.18 GB arena.

Contrast the passing stg48 q05.r0: lineitem stream 9 moved **126.0 GB** in 216 batches across all destinations (= 31.5 GB inbound per CN) between 22:08:12.92 and 13.58, the receivers of streams 7/9 started at 14.82 with `declared input stream cardinality stream_id=9 rows=1,499,866,858..1,500,124,755` per CN (`dp-cn4-six-stg48/cluster.log`). Frames therefore sat in the arena ~1.2 s waiting for the orders/customer senders and the stream-1/4 receiver to run first (receiver-first, one fragment at a time, B1).

### 2.4 Per-CN bytes: what each CN parks and what it must receive

Measured per-CN lineitem projections at 4 CNs = the `STREAMING_SINK in=` of the sender fragments (`dp-cn4-six-stg48/quent.txt`, `dp-cn4/quent.txt`; Quent lost the query labels in these arms, so fragments are matched to queries by size against the standalone totals `GPU_SCAN in=` in `perf/sf1000/agent-notes/standalone-failed-quent.txt`):

| q | total lineitem projection P (standalone) | per-CN share S at 4 CN (measured sink) | rows per CN (declared) | B/row | other big parked streams per CN |
|---|---:|---:|---:|---:|---|
| q05 | 171.00 GB | 42.51-43.08 GB | 1.500e9 | 28.5 | orders 18.37/4 = 4.6 GB |
| q08 | 195.75 | 48.66-49.32 | (1.5e9) | 32.6 | orders 24.56/4 = 6.1 GB, part 0.2 |
| q09 | 244.50 | 60.78-61.60 | 1.4999e9 (`stg72/cluster.log`) | 40.7 | orders 4.6, partsupp 3.3 (stream 18 = 13.50 GB, stream 13 = 9.60 GB total transmitted, stg48) |
| q17 | 122.25 | 30.39-30.80 | | 20.4 | part small |
| q18 | 97.50 | 24.24-24.56 | | 16.3 | orders 9.12-9.25 GB, l_quantity agg ~6 GB (24.0/4, inference from the 1-CN 24.0 in P1) |
| q21 | 77.76 (filtered l1/l3) + 73.50 (l2 leaf) | 19.33-19.59 + 18.27-18.52 | | | orders 12.19/4 = 3 GB |

Two bounds follow from the code (all in `W/experimental/starrocks/src`):

* **Arena bound (receiver side).** The sender's local lease is released per batch right after the nixl WRITE (`nixl_transport.rs:9-12`, `:719-733`), but the **peer's** lease is released only when the receiver fragment runs and `push_packed`-copies the frame into pool memory (`engine.rs:546-576`), and the receiver runs only after every sender has finished (receiver-first; `engine.rs:275-280` one request at a time). So each CN's arena must hold, for every stream whose receiver has not yet run, `S_i x (N-1)/N` of inbound frames. At N=4: q05 32.1 GB, q08 36.7, q09 45.8 + 3.4 + 2.4 = 51.6, q17 22.9, q18 18.3 (+6.9 if the orders frames are still there), q21 14.5 + 13.8 = 28.3. Against 17.18 GB: all six fail (measured). Against 51.54 GB: five fit, q09 (51.6) does not (measured: 50.55 GB held, 95 leases, 987 MB free in 11 blocks).
* **Pool bound (receiver start).** The sender's parked output is held until **the last destination releases it** (`engine.rs:331-337`, "the fragment (and the GPU memory its remaining batches hold) drops when the LAST destination releases"; `drop_parked` is called after the eos of a drain, `nixl_transport.rs:757`), and the receiver deep-copies each inbound frame into the pool. So the CN that is last in the FE's destination order holds `S + S(N-1)/N` before `run()`, plus the query's other parked outputs, plus the hash table once it runs. Measured in stg72 on cn3 (`dp-cn4-q09-stg72/engine-.cn3.log:193-201`): the lineitem sender ended with `allocated=61,803,680,512` (peak 68.06 GB during the scan, 0.6 GiB under the 64 GiB cap); the receiver started 10 ms later at `allocated=61,388,723,456` with all 45 GB of inbound frames already in the 72 GiB arena (transmits to 9132 completed 22:10:49.21-49.74, `stg72/cluster.log`), and died at `allocated=68,449,237,760` after ~7 GB of `push_packed` copies. Needed: 61.4 + 45.8 = 107 GB before the join, i.e. more than the 100 GiB pool of the default arm as well.

q09 at 4 CNs on this box is therefore infeasible under the current exchange for any pool/arena split: arena >= 52 GB and pool >= 107 GB + hash table exceed the ~160 GiB the runbook allows on a 184 GiB GB200 (`C/bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:82-87`). Measured three ways: 16 GiB arena -> arena; 48 GiB arena -> arena; 72 GiB arena with 64 GiB pool -> pool.

### 2.5 The 2-CN 96 GB box (user's `reference-2cn-rtxpro6000.md`): inference from the same arithmetic

Card = 97,887 MiB (`C/bench/rtxpro6000-2gpu/TPCH-STATUS.md:3`) = 102.6 GB; the runbook's budget is `GPU_MEM = CARD - STAGING - 2 GiB` (`.../SIRIUS-TUNING-RUNBOOK.md:83`), so a 16 GiB arena leaves at most ~77.6 GiB = 83 GB of pool, a 32 GiB arena ~66 GB. At N=2, S = P/2 and inbound = S/2:

| q | S per CN | + other parked | sender phase vs 83 GB pool | inbound vs 17.2 GB arena | receiver-start peak S + S/2 | user's screenshot |
|---|---:|---|---|---:|---:|---|
| q05 | 85.5 GB | orders 9.2 | **exceeds pool while scanning** | 42.8 | 128 | fail (OOM) |
| q08 | 97.9 | orders 12.3 | exceeds pool | 48.9 | 147 | fail (OOM) |
| q09 | 122.3 | orders 9.2, partsupp 6.6 | exceeds pool | 61.1 | 183 | fail (OOM) |
| q18 | 48.8 | orders 18.4 + agg ~12 + customer 2 = ~81 concurrently | at the cap; the first receiver's copies push it over | 24.4 | >= 105 | fail (OOM) |
| q17 | 61.1 | part small | fits | **30.5 > 17.2** | 92 | fail (no OOM shown) |
| q21 | 38.9 + 36.8 = 75.6 | orders 6 | fits (barely) | **37.8 > 17.2** | 113 | fail (no OOM shown) |

The split matches the screenshot: the four "fail (OOM)" queries are exactly those whose parked share alone is at or above any pool that fits a 96 GB card; the two plain "fail" are the ones that fit the pool and then hit the arena during the drain. Even with the arena sized to the inbound bytes, the receiver-start peak (92-183 GB) exceeds the card for all six, so **no STAGING/GPU_MEM split makes any of the six pass at 2 CNs on this GPU under the current exchange** (inference, medium-high; the actual `.err` text and the box's GPU_MEM/STAGING were not in the bundle and would confirm arena-vs-OOM for q17/q21).

### 2.6 Why 1 CN and standalone pass

1 CN: fix 4 splices the lineitem leaf into the join fragment (`fix4_fused` on every row of `dp-cn1-six/cnlog.txt`), so there is no exchange for lineitem and DuckDB's optimizer makes it the probe (the standalone shape: `perf/sf1000/standalone-failed/runs/q05.explain.txt:50-61`, P1). Standalone never had the exchange. At 4 CNs every shuffle receiver expects 4 senders and nothing fuses, so the lineitem projection is materialised whole across the cluster, which is the B1 mechanism at N > 1.

### 2.7 What feat/pin-table-cn changes, and what it does not

The exchange design is identical on `feat/pin-table-cn` (`C/experimental/starrocks/src`): one engine request at a time (`engine.rs:275`), parked output held until the last destination releases (`engine.rs:403`), inbound frames pushed and copied at receiver time (`engine.rs:624-639`). `DrainTicket`/`SenderDrains`/`dispatch_then_join` (`compute_node_service.rs:96-148, 276`) is the C8 "drain overlap": the plan appendix describes it as "dispatch a local receiver before joining the remote drains ... pure optimization (3x30 ms per CN on q14 SF500); wire order unchanged" (`~/.claude/plans/i-want-to-create-golden-crane-appendix.md:507, 1823, 2069-2071`). It changes when the RPC returns, not what is held in memory; the demo arms already ran with the equivalent `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` (`capture-arm.sh`). Pinning replaces the FILES() read (the scan source), not the `DATA_STREAM_SINK`; the FE plan and the shuffle bytes are the same.

What differs in the pinned reference runs is configuration: the pinned kit's SF1000 row is `GPU_MEM=128GiB STAGING=32GiB` (`C/bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:98`), twice the demo arena and 1.28x the pool. Checking the user's dagger pattern (`reference-pin-table-cn.md`; legend "dagger = filled, partial 1 CN", meaning not fully measured) against the arena bound at 34.4 GB: q05 needs 32.1 (+~1) at 4 CN -> fits, reference shows a clean 1.78 s; q08 needs 36.7 -> does not fit, reference daggers q08 at 4 CN; q09 needs 51.6 -> daggered at 4 CN; at 8 CN q08 needs 21.4 -> clean 1.81 s. The pattern is consistent for q05/q08/q09 at N <= 4 (inference). Not explained by this arithmetic: q21 at 4 CN (28.3 GB < 34.4, yet daggered), q09 at 8 CN (30 GB < 34.4, daggered), and the clean 1-CN q18 (8.01 s) whose concurrently parked inputs measured 166 GB yesterday against a 137 GB pool. Pinned columns share the RMM pool with parked output (inference), and the pinned runs' logs are not in the bundle; treat the reference as "same failure family, bigger arena, different I/O", not as evidence that pinning avoids the shuffle.

## 3. Arena exhaustion vs pool OOM, by case

| case | class | proof |
|---|---|---|
| 4 CN, 16 GiB arena, all six | receiver-side **arena exhaustion** during the lineitem drain, receiver never started | `dp-cn4/runs/*.err`, `cnlog.txt` frags without `result`, timeline 2.3 |
| 4 CN, 48 GiB arena, q09 | arena exhaustion, 51.6 GB needed vs 51.54 | `stg48/runs/q09.r0.err`, arena peaks 50.6-51.3 GB |
| 4 CN, 72 GiB arena / 64 GiB pool, q09 | **pool OOM** at receiver start in `push_packed` (own 61.4 GB parked + inbound copies) | `stg72/engine-.cn3.log:193-201`, `runs/q09.r0.err` |
| 2 CN, 96 GB GPU: q05 q08 q09 q18 | pool OOM in the sender phase (share >= pool) | inference, 2.5 |
| 2 CN, 96 GB GPU: q17 q21 | arena exhaustion at the drain (share fits, inbound 30-38 GB) | inference, 2.5 |
| 1 CN, any of the six | pass (fused), or with fusion off the P1 pool OOM of yesterday | `dp-cn1-six`, `perf/sf1000/cn1` |

## 4. Remedies, ranked for "make the six pass at N > 1"

Sizing rule that every option must beat, per CN, with today's plans: arena >= sum over live shuffled streams of `S_i (N-1)/N`; pool >= `S + S(N-1)/N` + other parked outputs + hash table, where `S = P/N` and P is 97-245 GB for these six.

1. **Fix 3, real FILES() cardinalities, plus a GPU-sized `broadcast_row_limit`** (FE; `files-cardinality-SPEC.md`). Removes the lineitem shuffle itself for q08 (filtered part ~1.3M rows), q09 (filtered part 10.9M rows = 4 x the declared 2,718,201 of stream 2, `stg72/cluster.log`), q17 (~200K rows) under the stock 15M-row guard, and for q05 only if the guard is raised to cover the ~46-62M-row orders x customer side (P1/P3 arithmetic). q18's join side becomes a local probe; its l_quantity aggregate still shuffles ~24 GB total, which fits everywhere. q21's lineitem self-joins stay partitioned on both sides, so q21 still ships 74-123 GB of lineitem and still fails at 2 CNs on 96 GB (S = 37-61 GB, peak 1.5 S). The spec's own non-goal says fix 3 does not fix 1 CN; at N > 1 it is the only option that removes the bytes rather than finding room for them. Confidence medium: FE cost code never executed with statistics; gate with `EXPLAIN COSTS`.
2. **Streaming receivers** (B1 route 2): start the receiver once build-side streams are complete and consume probe-side frames as they arrive, releasing each lease on consumption. Since the receiver already re-plans with exact declared cardinalities (C2: DuckDB builds on the small side and probes the stream even when the FE made lineitem the build), this removes both bounds for all six, q21 included, independent of the FE plan. Cost: the single-flight `query_lifecycle_mutex_` and shared task creator in the engine (B1), a build/probe decision before the streamed side is complete. Highest value, highest effort.
3. **Cheap engine/CN pair that fits q09 at 4 CNs on GB200** (inference): (a) release exported batches as they go instead of at the last destination (`engine.rs:331-337`) by draining batch-major across destinations, which cuts the receiver-start pool need from `S + S(N-1)/N` (107 GB) to `S` (61 GB); (b) copy inbound frames into the pool on arrival and release the arena lease immediately (yesterday's B6 proposal; `push_packed` is already a deep copy), which moves the 45.8 GB from the 16 GiB arena into the 100 GiB pool. Together: q09 peak ~61 + hash table in a 100 GiB pool with the default arena; five of six would pass on GB200 at 4 CNs with no STAGING change. Does nothing for 96 GB GPUs (pool-bound there).
4. **Spillable parked repositories** (B2): register sink outputs and receiver inputs with the downgrade sweep, HOST-tier `output_row_count`/`export_packed`/`push_packed`. Makes the six runnable wherever GPU + HOST covers the peak (2 CN 96 GB box: 92-183 GB per CN against 96 GB HBM + hundreds of GB host), at host-bandwidth speed. Medium effort, unknown throughput.
5. **Bigger arena**. Measured: 48 GiB passes five of six at 4 CNs on GB200; q09 needs >= 52 GB of arena and a >= 107 GB pool at once, which the card cannot hold. On a 96 GB GPU the arena is zero-sum with the pool and cannot help any of the six (2.5). Document `STAGING >= P (N-1)/N^2` per live stream for the current plans.
6. **Blocking lease / backpressure alone** (B6 item 8): with receiver-first, a full arena would stall the senders while the receiver waits for those same senders to finish, i.e. a deadlock unless paired with 2 or 3(b). Not a standalone remedy.
7. **DrainTicket overlap (C8)**: latency-only (RPC path), memory footprint unchanged; the equivalent async dispatch was on in every arm here. Irrelevant to these failures.
8. **Fix 4 fusion**: 1-CN only by construction; measured 0 fusions at 4 CNs.

## 5. Open items

* The 2-CN box's `.err` strings and GPU_MEM/STAGING are needed to confirm the arena-vs-OOM split of 2.5.
* The pinned reference's 1-CN q18 and 8-CN q09 outcomes are not explained by the arithmetic here; its cluster/engine logs would settle whether the pinned pool budget or a different deploy order is responsible.
* Quent lost the query labels in the demo arms (`quent.txt` headers read `sirius_streaming_fragment [ing_fragment]`, `leases=0`), so per-query lease digests were taken from `cluster.log` and the `.err` strings instead.
