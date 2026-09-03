# 06 — Data-flow simulation: sketch, graphs, predictions, and a replay that runs

Agent 6 report, 2026-09-03. Read-only on both trees (`$SOT` = `/home/prestouser/aocsa/sirius-stacks-wt/sot`,
`$Q` = `/home/prestouser/aocsa/sirius-stacks-wt/quent`, both `d24f02c4`). No builds, no GPU runs. Every
ndjson field name and every log line below is quoted as it appears on disk; every number is computed
from the files named. Code cites are `file:line` in `$SOT` (identical in `$Q`).

Files written (scratchpad `$SIM` = `/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/sim`):

| File | What |
|---|---|
| `$SIM/extract.py` | Quent session dir(s) → one JSON of fragments / pipelines / tasks / batches / placements (stdlib) |
| `$SIM/replay.py` | the discrete-event replay (stdlib); prints predicted vs measured per fragment and per query |
| `$SIM/sf1000-4cn-warm.json` | extracted SF1000 4-CN WARM generation: cn0 `01a065d8-3978-72d1-8656-c51b2c532ed4`, cn1 `01a065d8-397a-72c0-bed2-1f91bdac03c1`, cn2 `01a065d8-39c3-71f2-bc2d-a954f8985016`, cn3 `01a065d8-39a8-7013-93c2-95c2f2f662ba` (engine Init 05:57:44 UTC; 4×q01 + 4×q06; FE-measured 7488/7430/7088/6893 and 1332/1219/1331/1245 ms per `perf/decimal-chain.log`) |
| `$SIM/sf1000-1cn-warm.json` | SF1000 1-CN session cn0 `01a065d1-453c-7780-9dfb-e283d5238d75` (05:50:08 UTC; 4×q01, 1×q06 complete) |
| `$SIM/sf1000-4cn-cold-q06.json` | the q06 cold-restart generation (05:59:29 UTC) used only to line Quent up with the CN log |
| `$SIM/replay-output.txt`, `$SIM/hop-calibration.txt` | the outputs pasted in sections 4 and 1.3 |

Run: `python3 $SIM/extract.py --cn-from-path <session dirs> -o x.json` then
`python3 $SIM/replay.py x.json --onecn $SIM/sf1000-1cn-warm.json --fe-ms q01:7488,7430,7088,6893 --fe-ms q06:1332,1219,1331,1245`.

## 0. Two facts that shape everything below (found while extracting, verified in code)

1. **A sender fragment's Quent `query` window is `build() … fragment drop`, not `run()`.** `Init` is emitted in
   `sirius_engine`'s constructor (`src/sirius_engine.cpp:180-190`), which `streaming_fragment::build` creates
   together with the `sirius_interface` (`src/exec/streaming_fragment.cpp:182-184`); `Exit` is emitted by
   `sirius_engine::~sirius_engine() { query_handle_->exit(); }` (`sirius_engine.cpp:192`), i.e. when the
   parked fragment is dropped by `release_slot` after its last destination is drained (remote) or relayed
   (local) (`experimental/starrocks/src/engine.rs:609-618`, `:683`). Consequence, measured on the 4-CN
   generation: `scan last task Exit → query Exit` is 8–502 ms for q01 senders (the park wait for the
   straggler CN) and 1–4 ms for q06 senders with one remote destination (`$SIM/hop-calibration.txt`,
   column B). So *compute end = last `task` `Exit` of the fragment*, and the sender's "exec" duration must
   never be read as compute time. The trace-schema doc's row "Query (fragment) start / end = Executing →
   Exit" is right for result fragments and wrong for senders.
2. **The FE deploys in two phases and a sender's RPC blocks for its whole run.** `exec_plan_fragment` runs
   `exec_single_attachment` on a `spawn_blocking` worker and only replies when it returns
   (`compute_node_service.rs:322-345`); for a sender that is translate + `executor.run` + park +
   remote drains joined (`:1081-1090`, `:1119-1141`, `dispatch_then_join` `:276-300`), for a receiver it is
   registration only (`take_ready`, `local_exchange.rs:248-312`, runs later on the single `fragment-dispatch`
   thread `:247`, `:305-316`). On the q06 cold-restart generation the CN log shows three scans `translated
   StarRocks plan fragment` at 05:59:52.499, their RPCs `close … time.idle=853ms/1.33s/1.36s` at 53.351/53.831/53.861
   each coinciding with its `transmitted batches via nixl … bytes=64`, and the fourth (merge-hosting cn0) scan
   translated at 53.863 — 2 ms after the last phase-1 reply — with `time.idle=1.02s`. The lead's report-notes
   record the same mechanism ("StarRocks deploys the first fragment instance per node in one wave"). q01 has
   three fragment levels, so phase 1 is the result + the three intermediate receivers (all return at once) and
   every scan is in phase 2 — no stagger, confirmed: the four q01 scan `Init`s are within 0.8 ms on every run.

## 1. Discrete-event sketch

### 1.1 Nodes, queues, resources

```
FE ──deploy phase 1/2──► CN_i: [translate+build] ─► SCAN FRAGMENT ─► PARK ─┬─ local ─► RELAY ─┐
                                                                            └─ remote ► NIXL ──┤
                                                                                               ▼
                                          RECEIVER FRAGMENT (q01) / MERGE (result fragment) ─► FE result
```

| Node | What moves | Service resource | Queue in front of it |
|---|---|---|---|
| **scan split** | one `parquet_split_info` → one `GPU_SCAN` task; input `Reserving.input_basis` ≈ 2.41 GB, output `data_batch` 2.38 GB on GPU | disk/page cache (uring reactor), HBM (reservation), 1 of 4 executor threads | split connector (unbounded, `split_connector.cpp:31-46`); `task-scheduler-gpu-queue`, `gpu_pipeline-task-queue` |
| **compute operator** | the same task walks its pipeline chain: `GPU_SCAN(0) -> PROJECTION(1) -> PROJECTION(2) -> HASH_GROUP_BY(3)` (q01) / `GPU_SCAN(0) -> PROJECTION(1) -> UNGROUPED_AGGREGATE(2)` (q06) | executor thread + GPU | none (chain runs inline in the task) |
| **pipeline barrier** | P0 outputs (464 B per task for q01, 8 B-ish partials for q06) wait in the consumer port repository until P0 finishes; then `PARTITION(4)` (27 tasks, ~1 ms total) and `MERGE_GROUP_BY(5) -> PROJECTION(6) -> STREAMING_SINK(7)` (1 task, ~10 ms) | executor threads | port repository (`batch_placement` `BatchQueued`) |
| **park** | the sink's per-destination output streams stay on the GPU inside the parked fragment (`engine.rs:683`) until each destination claims them | HBM | `parked` map, one `ParkedOutput` per fragment |
| **relay** (same CN) | pointer hand-off `relay_from` (`sirius_ffi.cpp:694`, `engine.rs:609`); zero bytes copied | engine thread | — |
| **nixl hop** (other CN) | `export_packed` (chunked_pack into the staging arena) → peer lease RPC → nixl WRITE → transmit RPC → eos; serial per destination on the one transport thread (`:1119-1141`) | staging arena (17179869184 B on this run: `exchange_staging_arena.cpp:79` log), NVLink (`nixl bandwidth canary … gbps="302-404"` to 9112/9122/9132, `"77-107"` to 9102) | transport FIFO |
| **merge / receiver** | `STREAMING_SOURCE(0) -> …` fragment; starts when `take_ready` sees every sender complete; one fragment at a time per CN (`engine.rs:275` "One request at a time") | engine thread, executor threads | `fragment-dispatch` inbox |
| **FE result** | `RESULT_COLLECTOR` D2H (8–700 B) → MySQL encode → `fetch_data` long-poll | host | FE result buffer |

Resources modelled as capacities: executor threads per CN = 4 (`executor_thread` entities
`gpu_pipeline-gpu0-exec-0..3`), engine thread per CN = 1, transport thread per CN = 1. HBM and the staging
arena are tracked but never bind at SF1000 (`Reserving.requested_bytes` 19.5 GB per task × 4 threads ≪
100 GiB; no `Downgrading`, no `InTransit` anywhere on disk). Disk is not modelled as a separate server: the
scan task's `Preparing → Finalizing` interval already contains its read.

### 1.2 Where each node's start / end / bytes come from

| Node event | Quent record (as on disk) | Gap (nothing carries it) |
|---|---|---|
| FE deploy of a fragment (RPC start) | — | **nowhere in Quent.** CN log `translated StarRocks plan fragment output_names=[…]` (no ids) and `exec_plan_fragment … close time.busy=… time.idle=…` (no ids). The sim infers phase 1/2 from the ordering rule in §0.2. |
| translate + DuckDB `build()` | `query` `Init` marks the end of it | start is nowhere; measured 19–20 ms (CN log 52.499 → Quent 52.518; 53.863 → 53.883) |
| Sirius planning | `query` `Planning` → `Executing` (0.1–0.8 ms) | — |
| split discovery | `Init` → first `task` `Preparing` (`hop-calibration.txt` col. A: 4–32 ms warm, 81–109 ms on the first run of a generation) | which splits/byte ranges: only `SIRIUS_CN_DUMP_FRAGMENTS`; compressed bytes read: nowhere |
| task queueing | `Queued.queue.resource_id` → `task_queue` (`task-scheduler-gpu-queue`, `gpu_pipeline-task-queue`); `Routing.preferred_device_id`; `Reserving.{requested_bytes,input_basis,peak_estimate,bytes_to_materialize}` | — |
| task service (executor-thread occupancy) | `Preparing.timestamp` → `Finalizing.timestamp`; `Preparing.{origin_tier:"SOURCE",target_tier:"GPU",input_bytes}`, `executor_thread.resource_id` | — |
| per-operator time inside a task | consecutive `Computing.{instance_name,current_operator_id,input_bytes,peak_allocated_bytes}` timestamps; next `Computing`/`Finalizing` closes the interval | rows: nowhere |
| batch produced | `data_batch` `Constructed.{data_batch_id,producer_pipeline_uuid}` + `Stationary.memory.{resource_id,capacity.capacity_bytes}` (e.g. 2376328412 for a scan output, 464 for a `HASH_GROUP_BY(3)` partial, 8 for a `MERGE_AGGREGATE(3)` partial) | — |
| batch waiting at a consumer port / claimed / processed | `batch_placement` `BatchRegistered.{batch_id,pipeline_uuid,port_uuid,origin:"operator_output",tier.capacity.capacity_bytes}` → `BatchQueued` → `BatchPackaged.task_uuid` → `BatchProcessing` → `BatchConsumed.reason:"processed"` | — |
| pipeline barrier | `P1` first `Created` − `P0` last `Exit` = 0.1 ms (`+6855.6 → +6855.7`) | — |
| compute end of a fragment | last `task` `Exit` of its pipelines | — |
| park start / end | start = compute end (+ teardown); end = the sender `query` `Exit` (§0.1) | the park is only implicit; bytes = the sink pipeline's `data_batch` sizes (q01: 6 batches, 1080 B per CN; q06: 8 B) |
| relay | receiver-side `batch_placement` `BatchRegistered … "origin":"reschedule_intermediate"` at claim (lazy, `batch_telemetry.cpp:349`) | the relay instant itself (CN log `relayed native batches across a fragment boundary stream_id=3 sender_id=1 batches=1`) |
| nixl hop | receiver-side placement with `"origin":"reschedule_intermediate"` and `tier.capacity.capacity_bytes` = payload (302 / 106 B for q01 scan→receiver, 422 / 274 B receiver→result, 8 B for q06) | **duration nowhere** (`write_and_wait`'s `Duration` is discarded, `nixl_transport.rs:713-719`); packed bytes only in the CN log `transmitted batches via nixl stream_id=3 sender_id=… dest=127.0.0.1:91x2 batches=1 bytes=64` (q06) — every cluster log on disk holds only the last (q06) generation, so q01's packed size is known only from the earlier SF1 sweep (`bytes=1088`, `step0/sot-cluster-sf1.log`); **remote batches have no `data_batch` entity** (3 of 4 receiver inputs per run have placements only) |
| receiver start | receiver `query` `Init` (build) → `Executing`; `take_ready` instant is nowhere; measured `Init − straggler last task Exit` = +6 … +29 ms (col. D) | — |
| result end | result fragment `query` `Exit` (0.3–0.4 ms after its `RESULT_COLLECTOR` task `Exit`) | FE fetch / MySQL / parse-plan: nowhere. Measured residual `FE ms − (result Exit − first scan Init)` = 180–213 ms on all 8 runs (col. G) |
| GPU / CN identity | `gpu_device.Declaration.{instance_name:"gpu-0",ordinal:0}` on every CN; only the session path `.cn<i>/` and `worker.Init.instance_name:"worker-<pid>"` differ | physical GPU: nowhere in Quent |

### 1.3 Calibration facts taken from the trace (ms) — `$SIM/hop-calibration.txt`

```
run    | A per cn                | B per cn                        | C            | D                                  | E     | F    | G
q01 r0 | cn0:109 cn1:83 cn2:81 cn3:84 | cn0:403 cn1:215 cn2:502 cn3:11  | cn3 @7255 | cn0:+14.5 cn1:+13.5 cn2:+13.4 cn3:+9.1 | +3.8  | 7488 | 185
q01 r1 | cn0:4 cn1:4 cn2:9 cn3:18 | cn0:501 cn1:26 cn2:227 cn3:169  | cn1 @7173 | cn0:+29.1 cn1:+5.4 cn2:+29.1 cn3:+29.1 | +3.3  | 7430 | 206
q01 r2 | cn0:8 cn1:8 cn2:15 cn3:8 | cn0:236 cn1:354 cn2:8 cn3:383   | cn2 @6847 | cn0:+10.2 cn1:+11.1 cn2:+6.1 cn3:+10.3 | +3.4  | 7088 | 203
q01 r3 | cn0:6 cn1:7 cn2:12 cn3:17 | cn0:185 cn1:160 cn2:165 cn3:18  | cn3 @6643 | cn0:+21.1 cn1:+21.1 cn2:+21.2 cn3:+6.0 | +3.1  | 6893 | 213
q06 r0 | cn0:16 cn1:18 cn2:30 cn3:32 | cn0:4 cn1:2 cn2:2 cn3:2         | cn0 @1142 | result(cn0):+3.7                   | -     | 1332 | 185
q06 r1 | cn0:28 cn1:18 cn2:29 cn3:27 | cn0:2 cn1:1 cn2:4 cn3:2         | cn2 @1032 | result(cn2):+4.3                   | -     | 1219 | 180
q06 r2 | cn0:19 cn1:27 cn2:24 cn3:26 | cn0:3 cn1:2 cn2:2 cn3:2         | cn0 @1138 | result(cn0):+3.7                   | -     | 1331 | 188
q06 r3 | cn0:26 cn1:23 cn2:23 cn3:26 | cn0:1 cn1:2 cn2:4 cn3:3         | cn2 @1050 | result(cn2):+4.0                   | -     | 1245 | 189
```
A = scan `Init` → first `Preparing`; B = scan last task `Exit` → `query` `Exit`; C = straggler CN and its
last-task-`Exit` offset from the first scan `Init`; D = receiver `Init` − C; E = result `Init` − max receiver
last task `Exit`; F = FE ms; G = F − (result `Exit` − first scan `Init`).

Constants the replay uses (all named in its output): `build_ms=20` (scan), `build_recv_ms=2`,
`teardown_ms=3`, `drain_ms=3` per remote destination, `dispatch_ms=1`, `barrier_ms=0.1`,
`fe_phase_gap_ms=2`, `fe_overhead_ms=175` (= G − build), `nixl_gbps=350`.

## 2. The two shapes as graphs

Notation: `[…]` a fragment on one CN, `t=` measured offsets (ms) from the first scan `Init` of q01 r0 /
q06 r0 of the 4-CN generation, `→` local relay (pointer), `⇒` nixl hop (packed bytes in the CN log),
`27×` = `GPU_SCAN` tasks. Bytes are `Stationary.capacity_bytes` / `BatchRegistered.tier.capacity_bytes`.

### 2.1 q06 gather: `AGGREGATE (update serialize)` → `EXCHANGE UNPARTITIONED` → `AGGREGATE (merge finalize)`

1 CN (SF1000 1-CN trace, 65 splits, 4 threads):
```
FE ─phase1─► [cn0 RESULT: STREAMING_SOURCE(0)->UNGROUPED_AGGREGATE(1) | MERGE_AGGREGATE(2) | RESULT_COLLECTOR(3)]  (registers, returns)
FE ─phase2─► [cn0 SCAN: 65× GPU_SCAN(0)->PROJECTION(1)->UNGROUPED_AGGREGATE(2) | MERGE_AGGREGATE(3) | STREAMING_SINK(4)]
                 ▲ 4 executor threads, 65 tasks × ~107 ms = 6.9 s busy → 1785 ms compute      8 B ──park──► relay → result ── 1.1 ms ──► FE
measured: scan Init→last task Exit 1785 ms; FE 1826-1958 ms
```

N = 4 CNs (measured q06 r0; the merge-hosting CN is cn0):
```
t=0      FE phase 1: [cn0 RESULT] registers (returns at once)      [cn1 SCAN 17×] [cn2 SCAN 16×] [cn3 SCAN 16×]  (RPCs block)
t=493    cn1 compute end  ──8 B, packed 64 B ⇒ cn0 staging (transmitted 2 ms later)   RPC returns t=495
t=702    cn2, cn3 compute end ⇒ cn0                                                     RPCs return t=704
t=706    FE phase 2 (2 ms after the last phase-1 reply): [cn0 SCAN 17×] deployed; Init t=726 (20 ms translate+build)
t=1142   cn0 compute end → park → push_sender → take_ready(4/4) → dispatch → [cn0 RESULT] Init t=1145.4, Exit t=1147.1
t=1332   FE reports (185 ms outside every Quent window)
```
The critical path is `max(remote scans) + 22 ms + coordinator scan + 4 ms + 185 ms`: two scans in series,
which is why 4 CNs give 1245 ms against 1826 ms on 1 CN (the lead's "scale-out q06 root cause"). The straggler
is always the merge-hosting CN — by deploy order, not by data.

### 2.2 q01 hash shuffle: `AGGREGATE (update serialize)` → `EXCHANGE HASH_PARTITIONED(l_returnflag,l_linestatus)` → `AGGREGATE (merge finalize)` → `SORT` → `MERGING-EXCHANGE`

1 CN (SF1000 1-CN trace, 107 splits):
```
[cn0 SCAN: 107× GPU_SCAN(0)->PROJECTION(1)->PROJECTION(2)->HASH_GROUP_BY(3) | 107× PARTITION(4) | MERGE_GROUP_BY(5)->PROJECTION(6)->STREAMING_SINK(7)]
   4 threads: Σ(Preparing→Finalizing) 107.2 s / 4 = 26.8 s → compute 26 994 ms (HASH_GROUP_BY 56.0 s, GPU_SCAN 46.2 s of the 107.2 s)
   ──1080 B (4 groups)──► relay → [cn0 RECV: STREAMING_SOURCE(0)->HASH_GROUP_BY(1) | PARTITION(2) | MERGE_GROUP_BY(3)->PROJECTION(4)->ORDER_BY(5) | SORT_SAMPLE(6)->SORT_PARTITION(7) | MERGE_SORT(8) | STREAMING_SINK(9)]  17 ms
   ──696 B──► relay → [cn0 RESULT: STREAMING_SOURCE(0)->ORDER_BY(1) | SORT_SAMPLE(2)->SORT_PARTITION(3) | MERGE_SORT(4) | RESULT_COLLECTOR(5)]  1.6 ms → FE
measured: 27 000 ms Quent, FE 27 194 ms
```

N = 4 CNs (measured q01 r0; result fragment on cn0; receivers on all four CNs but only two of them get rows —
the 4 groups hash to cn0 (3 groups, 302 B per sender) and cn2 (1 group, 106 B per sender)):
```
t=0      FE phase 1: [cn0 RESULT] + [cn1 RECV] [cn2 RECV] [cn3 RECV] register   phase 2 at t≈2: [cn0 RECV] + 4× [cn_i SCAN 27×]
         each CN: 27 tasks on 4 threads, Σ(Preparing→Finalizing) 25.6 s (cn2) … 26.3 s (cn0) → compute ends cn2 6786, cn0 6886, cn1 7073, cn3 7255  (straggler cn3, +7 % over cn2)
         each finished CN parks (B = 11-502 ms) while its 3 remote drains go out: 302 B/106 B/0 B per destination, packed ~1 KiB
t=7255   cn3 (last) compute end → 3 drains (~3 ms each) → every receiver's take_ready(4/4) fires: receivers Init t=7264-7269 (D = +9..+15)
         [cn0 RECV] 4×302 B in → 23 ms → 422 B ⇒/→ cn0 ; [cn2 RECV] 4×106 B → 22 ms → 274 B ⇒ cn0 ; [cn1],[cn3] RECV: 0 rows, 0.7-2 ms
t=7293   [cn0 RESULT] Init (E = +3.8 after the last receiver's last task), Exit t=7303
t=7488   FE reports (185 ms outside)
```
Bytes on every hop are hundreds of bytes; at 350 Gbps the wire time is < 10 µs. Every hop is latency
(~3 ms per remote destination, serial), and the whole exchange tail (7255 → 7303) is 48 ms of a 7488 ms query.

## 3. What the sim predicts that the box can check

Numbers from `$SIM/replay-output.txt` (section 4). "Measured" = FE ms from `perf/decimal-chain.log`.

1. **Per-run wall and straggler, 4 CNs.** q01: 7504/7413/7090/6871 predicted vs 7488/7430/7088/6893 measured
   (−0.3 … +0.2 %); q06: 1347/1237/1344/1257 vs 1332/1219/1331/1245 (+0.9 … +1.5 %). The straggler CN
   (cn3, cn1, cn2, cn3 for q01; the merge-hosting cn0/cn2/cn0/cn2 for q06) matches on all 8 runs. Per-fragment
   compute end from FIFO list scheduling of the measured `Preparing→Finalizing` service times on 4 threads lands
   within −1.2 … +4.4 ms of the measured last task `Exit` on all 32 scan fragments — the executor pool, not the
   I/O path or the scheduler, is what sets a fragment's length at SF1000.
2. **q01's straggler is task-time variance, not split imbalance.** Every CN runs exactly 27 tasks, yet compute
   ends spread 6786–7255 ms (7 %) with a different straggler each run. Check on the box: per-CN
   Σ `Computing` by operator (`HASH_GROUP_BY(3)` 14.9–15.8 s, `GPU_SCAN(0)` 8.7–9.5 s per CN in r0). Static FE
   byte ranges cannot fix this; only work stealing across CNs could, and the sim says the prize is ≤ 7 % of q01.
3. **Async sender dispatch (the lead's uncommitted `SIRIUS_CN_ASYNC_SENDER_DISPATCH`) on q06.** With a sender's
   RPC returning at once, phase 2 starts at t≈2 ms and all four scans overlap: predicted 907/777/869/813 ms for
   the four runs (today 1332/1219/1331/1245), i.e. −33 … −36 %; 2-CN q06 predicted 1212 ms from the 1-CN trace
   (today 1903–1976). q01 is predicted unchanged (7504/7413/7090/6871) because its scans are already in phase 2.
   Check: bench q06 4-CN with the fix; the Quent trace must show all four scan `Init`s within ~5 ms.
4. **N-CN scaling from the 1-CN trace alone** (107 q01 tasks / 65 q06 tasks split into N contiguous byte-range
   chunks, each list-scheduled on 4 threads, plus the hop/FE constants): q01 N=2 → 13 225–14 178 ms (measured
   13 525–13 976), N=4 → 7 066–7 635 (measured 6 893–7 488), N=8 → ≈ 4 100–4 450 (projection; needs the two-host
   kit, `tpch-2host`); q06 today's deploy N=2 → 2 111 (measured 1 903–1 976), N=4 → 1 267 (measured 1 219–1 332),
   N=8 → 810; q06 with async dispatch N=4 → 741, N=8 → 498. The q06 N=2 over-prediction (+7 %) is the per-task
   variance of an I/O-bound 107-ms scan task between generations, not the structure.
5. **A 175–213 ms floor per query that no CN-side change removes** (col. G): FE parse/plan, two deploy phases,
   `fetch_data`, MySQL; plus 20 ms translate+`build()` per scan fragment (`sirius_ffi.cpp:607-630`). At SF10 this
   floor *is* the SR-vs-standalone gap for q06 (86 vs 45 ms) and half of q01's. Check: FE-side timing of
   `deliverExecFragments` vs the first CN `translated` line; the CN log's `time.idle` on the receivers' RPCs.
6. **The 1-GPU q01 tax is inside the tasks, not in the data flow.** On the 1-CN trace Σ(`Preparing→Finalizing`)
   = 107.2 s over 4 threads = 26.8 s against a 26 994 ms compute window (99 % thread saturation); park + relay +
   receiver + result add 25 ms. The sim reproduces 27 217 ms vs 27 194 measured but cannot see *inside* a task
   (`HASH_GROUP_BY(3)` 523 ms avg here vs 54 ms standalone — the lead's `canonicalize_row_order` finding). The
   sim's job for that fix is the regression check: with `SIRIUS_CANONICAL_FLOAT_SUMS` off, re-extract and the
   4-CN q01 prediction should drop to ≈ (Σ new service)/4 + 225 ms.
7. **Cold first run**: A = 81–109 ms on r0 vs 4–32 ms afterwards (footer reads through a cold page cache after
   the cluster restart); with cold `SIRIUS_LOG_LEVEL=debug` the `[parquet_gpu_ingestible] Byte range` lines
   should bracket that window.

Not predicted (no input on disk): anything bandwidth-shaped. All hops here are ≤ 1 KiB; q14-class stages
(948 MB/destination) would exercise B0/B1/B3/B5 of `04-memcpy-throttle.md`, and the replay's `hop_ms(bytes)`
term is ready for them but uncalibrated (`nixl_gbps` from the canary, no measured `write_and_wait` durations).

## 4. DECISION: coded the replay

Both conditions in the brief held: Quent has per-task durations (`task` `Preparing`/`Computing`/`Finalizing`
timestamps, 340 tasks per CN on this generation) and the hop bytes are on disk (CN log `bytes=64` for q06;
receiver-side `BatchRegistered.tier.capacity_bytes` for q01's 302/106/422/274 B payloads). The replay is
`$SIM/replay.py` (stdlib, ~330 lines), driven by `$SIM/extract.py` over the four CN sessions of the SF1000
4-CN WARM generation; the 1-CN session drives the out-of-sample N-CN section. Output, verbatim
(`$SIM/replay-output.txt`, q01 r0 and q06 r0 detail plus the summary and the N-CN block; the other six runs are
in the file):

```
trace sf1000-4cn-warm.json: 4 CN sessions, executor threads {0: 4, 1: 4, 2: 4, 3: 4}, 8 runs
constants (ms): build_ms=20.0, build_recv_ms=2.0, teardown_ms=3.0, drain_ms=3.0, dispatch_ms=1.0, barrier_ms=0.1, fe_phase_gap_ms=2.0, fe_overhead_ms=175.0, nixl_gbps=350.0

== q01 run0  (FE measured 7488.0 ms)
    cn0 scan  tasks= 27 start pred=    2.0 meas=    0.0  compute_end pred=  6886.9 meas=  6885.8 (  +1.1)  rpc_ret pred=  6898.9 meas(Exit)=  7289.3
    cn1 scan  tasks= 27 start pred=    2.0 meas=    0.1  compute_end pred=  7074.1 meas=  7073.3 (  +0.8)  rpc_ret pred=  7086.1 meas(Exit)=  7288.2
    cn2 scan  tasks= 27 start pred=    2.0 meas=    0.0  compute_end pred=  6785.9 meas=  6785.9 (  -0.0)  rpc_ret pred=  6797.9 meas(Exit)=  7288.2
    cn3 scan  tasks= 27 start pred=    2.0 meas=    0.0  compute_end pred=  7274.6 meas=  7274.7 (  -0.0)  rpc_ret pred=  7286.6 meas(Exit)=  7286.0
    cn0 recv  start pred=  7283.6 meas(Init)=  7289.2  end pred=  7303.5 meas=  7308.9
    cn1 recv  start pred=  7286.6 meas(Init)=  7288.1  end pred=  7286.6 meas=  7288.1
    cn2 recv  start pred=  7289.6 meas(Init)=  7288.1  end pred=  7310.7 meas=  7309.6
    cn3 recv  start pred=  7280.6 meas(Init)=  7283.8  end pred=  7280.6 meas=  7283.8
    cn0 result end pred=  7328.9 meas(Exit)=  7322.8   straggler pred=cn3 meas=cn3
    query wall: predicted    7504 ms   measured FE 7488.0 ms   (trace span + fe_overhead = 7498)   counterfactual async sender dispatch:    7504 ms

== q06 run0  (FE measured 1332.0 ms)
    cn0 scan  tasks= 17 start pred=  729.7 meas=  726.3  compute_end pred=  1164.6 meas=  1161.7 (  +3.0)  rpc_ret pred=  1167.6 meas(Exit)=  1165.2
    cn1 scan  tasks= 17 start pred=    0.0 meas=    0.0  compute_end pred=   512.8 meas=   513.3 (  -0.5)  rpc_ret pred=   518.8 meas(Exit)=   515.4
    cn2 scan  tasks= 16 start pred=    0.0 meas=    0.0  compute_end pred=   721.5 meas=   722.1 (  -0.6)  rpc_ret pred=   727.5 meas(Exit)=   724.4
    cn3 scan  tasks= 16 start pred=    0.0 meas=    0.0  compute_end pred=   721.7 meas=   722.4 (  -0.7)  rpc_ret pred=   727.7 meas(Exit)=   724.4
    cn0 result end pred=  1172.0 meas(Exit)=  1167.1   straggler pred=cn0 meas=cn0
    query wall: predicted    1347 ms   measured FE 1332.0 ms   (trace span + fe_overhead = 1342)   counterfactual async sender dispatch:     907 ms

== summary (ms)
q    run  FE_meas  pred   err%   pred_async_sender
q01  r0    7488.0   7504    0.2     7504
q01  r1    7430.0   7413   -0.2     7413
q01  r2    7088.0   7090    0.0     7090
q01  r3    6893.0   6871   -0.3     6871
q06  r0    1332.0   1347    1.1      907
q06  r1    1219.0   1237    1.5      777
q06  r2    1331.0   1344    1.0      869
q06  r3    1245.0   1257    0.9      813

== N-CN prediction from the 1-CN trace sf1000-1cn-warm.json (4 executor threads)
  q01: 107 scan tasks, service mean 1002 ms, median 958, max 1562, sum 107.2 s; measured scan fragment compute 26994 ms
    N=1: per-CN tasks [107], predicted scan compute per CN [27010], predicted FE wall 27217 ms
    N=2: per-CN tasks [53, 54], predicted scan compute per CN [13968, 13495], predicted FE wall 14178 ms
    N=4: per-CN tasks [26, 27, 27, 27], predicted scan compute per CN [7161, 7148, 7419, 6752], predicted FE wall 7635 ms
    N=8: per-CN tasks [13, 13, 14, 13, 13, 14, 13, 14], predicted scan compute per CN [3564, 3880, 4226, 3881, 3785, 3956, 3722, 3829], predicted FE wall 4454 ms
  q01: 107 scan tasks, service mean 947 ms, median 956, max 1083, sum 101.3 s; measured scan fragment compute 25562 ms
    N=1: per-CN tasks [107], predicted scan compute per CN [25578], predicted FE wall 25785 ms
    N=2: per-CN tasks [53, 54], predicted scan compute per CN [12886, 13015], predicted FE wall 13225 ms
    N=4: per-CN tasks [26, 27, 27, 27], predicted scan compute per CN [6856, 6743, 6730, 6718], predicted FE wall 7072 ms
    N=8: per-CN tasks [13, 13, 14, 13, 13, 14, 13, 14], predicted scan compute per CN [3317, 3859, 3863, 3873, 3860, 3861, 3856, 3455], predicted FE wall 4101 ms
  q06: 65 scan tasks, service mean 107 ms, median 119, max 142, sum 6.9 s; measured scan fragment compute 1785 ms
    N=1: per-CN tasks [65], predicted scan compute per CN [1802], predicted FE wall 1985 ms  (async sender dispatch: 1988)
    N=2: per-CN tasks [32, 33], predicted scan compute per CN [1026, 894], predicted FE wall 2111 ms  (async sender dispatch: 1212)
    N=4: per-CN tasks [16, 16, 16, 17], predicted scan compute per CN [555, 522, 495, 474], predicted FE wall 1267 ms  (async sender dispatch: 741)
    N=8: per-CN tasks [8, 8, 8, 8, 8, 8, 8, 9], predicted scan compute per CN [312, 295, 283, 292, 283, 283, 307, 277], predicted FE wall 810 ms  (async sender dispatch: 498)
```
(The three other q01 1-CN runs, 25785/13225/7072/4101, 25775/13227/7066/4094 and 25775/13223/7074/4092 ms for N=1/2/4/8, are in the file.)

What the fit does and does not mean. Per-run wall error ≤ 1.5 % is partly calibration: `fe_overhead_ms` and the
hop constants are residuals of this same generation. The non-trivial content is (a) the per-fragment compute
end from list scheduling (no free parameter, ≤ 4.4 ms error on 32 fragments), (b) the FE two-phase rule
reproducing q06's coordinator stagger and q01's absence of one from the same code, and (c) the N-CN section,
which uses only the 1-CN trace and lands within 5 % (q01) / 7 % (q06) of arms it never saw.

## 5. Open issues for the lead

1. `01-trace-schema.md` §2 row "Query (fragment) start / end": for sender fragments `Executing → Exit` includes
   the park (up to 502 ms here); the compute end is the last `task` `Exit`. Suggest the sim-facing definition be
   written there, or (instrumentation chain) a `query` attribute / `stationary => stationary` self-transition on
   the sink batches at park time so the interval is explicit rather than inferred from `~sirius_engine`.
2. Quent session `01a065d1-453c…` (the 1-CN run, killed by the cold restart at 05:52:34) has `task`,
   `data_batch`, `batch_placement`, `query`, `plan`, `operator`, `port` files but **zero-byte** `executor_thread`,
   `engine`, `worker`, `gpu_device`, `memory`, `channel`, `task_queue`, `thread_group`, `query_group`: the small
   per-type buffers are flushed only at engine exit. `extract.py` falls back to counting executor threads from
   `task.*.executor_thread.resource_id`. A SIGTERM flush in the exporter would make killed sessions whole.
3. The FE two-phase deploy is inferred from timing (§0.2) and from the lead's notes; I could not read the FE
   source here. The `async sender dispatch` prediction (P3) is the direct test.
4. All cluster logs on disk hold only the final (q06 cold-restart) generation, so q01's packed frame size at
   SF1000 is not on disk (1088 B at SF1 from `step0/sot-cluster-sf1.log`); the sim uses the receiver-side
   `capacity_bytes` payload instead. Irrelevant at these sizes; relevant if the replay is pointed at q14-class stages.
5. The replay has no bandwidth-bound regime calibrated (no `InTransit`, no `write_and_wait` durations). Extending
   it to q14/SF500 needs proposal 5 of `01-trace-schema.md` (DataBatch `in_transit` over a `staging(cn_a)->staging(cn_b)`
   channel) or at least the `bytes` + `Duration` in the `transmitted batches via nixl` log line.
6. Standalone comparison: `sf1000-standalone.json` was extracted (8 `unnamed_query` entities: q01 5257–7484 ms,
   q06 2114–2723 ms) but not replayed — it has no fragments to move; its only use is the per-task service-time
   contrast quoted in P6.
