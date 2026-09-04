# arena-4cn: why the six exhaust a 16 GiB staging arena at 4 CNs, what 48 GiB buys, and what the numbers argue for

Branch demo/q1q6-integration-plus-fixes @ 281b13bc (worktree /home/prestouser/aocsa/sirius-stacks-wt/demo-plus), SF1000 decimal TPC-H, 4 GB200 CNs,
SIRIUS_CN_ASYNC_SENDER_DISPATCH=1, fusion default (leaf). All paths below are under
`/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/` (written `$SP`). Working files for this note:
`$SP/demoplus/agent-notes/arena-4cn-work/` (`arena_summary.py` parses a cluster.log per run window: transmits by stream, per-destination bytes,
fragment timeline, failure and retire lines; outputs `dp-cn4-six-summary.txt`, `stg48-six-summary.txt`, `stg72-q09-summary.txt`).

Arms: `dp-cn4` = 100 GiB pool + 16 GiB arena (all 22); `dp-cn4-six-stg48` = 90 GiB + 48 GiB (the six); `dp-cn4-q09-stg72` = 64 GiB + 72 GiB (q09);
`dp-cn4-q09-stg72-pool100` = 100 GiB + 72 GiB (q09; see 4.3, confounded). Arena sizes confirmed by `engine-.cn0.log` "exchange staging arena: N bytes"
(17179869184 / 51539607552 / 77309411328); pool sizes from `capture-arm.sh:12` (GPU_MEM default 100GiB) and the engine `[gpu_pool]` peaks
(64 GiB arm: peak 68,698,126,080 B = 63.98 GiB; pool100 arm: peak 107,374,182,400 B = 100 GiB exactly).

Every number is measured from the named file unless marked *inference*.

## 1. What is in the arena when it fails (16 GiB, dp-cn4)

Failure text per query (`dp-cn4/runs/qNN.r0.err`; capacity 17,179,869,184 B = 17.18 GB):

| q | request (MB) | leases outstanding | held (GB) | free (MB) / blocks / largest (MB) | failure site |
|---|---:|---:|---:|---|---|
| q05 | 655 | 31 | 17.06 | 122 / 7 / 75 | peer `request_staging_lease` |
| q08 | 664 | 43 | 16.03 | 1153 / 9 / 664.0 | local `export_packed` (largest block 664,006,656 B, request 664,009,728 B: 3 KB short) |
| q09 | 649 | 35 | 16.84 | 344 / 6 / 158 | peer `request_staging_lease` |
| q17 | 654 | 26 | 17.02 | 163 / 3 / 154 | peer `request_staging_lease` |
| q18 | 659 | 31 | 16.62 | 562 / 7 / 237 | peer `request_staging_lease` |
| q21 | 422 | 47 | 16.94 | 244 / 12 / 58 | local `export_packed` |

All six die at 93-99% occupancy with 26-47 leases of one hash partition of one scan-task batch each (580-664 MB for the lineitem
projections of q05/q08/q09/q17/q18, 395-422 MB for q21's narrower one). Across the 24 `retired a query's parked sender outputs` lines
(`dp-cn4/cluster.log`, 4 CNs x 6 queries) 19 are a peer refusing a lease to an incoming drain and 5 are the sending CN unable to lease
local space to pack its own outgoing batch ("failed to export a packed batch for SenderSlot"): one arena per CN serves both incoming frames
(held) and outgoing packing (one batch in flight), so a CN full of peers' frames cannot even start its own drain.

What the arena is holding is the remote share of the unfiltered lineitem hash shuffle (P1/P3 of yesterday's report). Per-destination bytes of
the completed transmits in the passing 48 GiB runs (`arena-4cn-work/stg48-six-summary.txt`, from `dp-cn4-six-stg48/cluster.log`
"transmitted batches via nixl"), i.e. what each CN's arena has to hold for one receiver, since frames are only consumed when the receiver runs (section 2):

| q | lineitem stream (declared rows per CN) | per-dest GB | other streams held at the same time (per-dest GB) | arena needed per CN (GB) | total nixl GB per run |
|---|---|---:|---|---:|---:|
| q05 | s9 (1,500,124,755) | 31.4-31.6 | s12 orders 4.10, s7 0.51, s4 0.51, s1 0.22, s17 <=0.18 | ~37 | 148.0 |
| q08 | s4 (1,500,157,085) | 35.9-36.1 | s14 1.37, s19 0.23, s12 0.21, s7 0.21, s27/s22/s17 <=0.1 | ~38.3 | 152.9 |
| q09 | s4 (1,499,902,600) | 45.0 (3 x 15.0-15.1) | s18 3.38, s13 2.41, s7 0.82 | ~51.6 | ~174 (would be) |
| q17 | s1 (1,500,157,085) | 22.4-22.5 | s9 3.00 | ~25.5 | 102.0 |
| q18 | s10 (1,500,124,755) | 17.96-18.03 | s1 6.77, s4 4.51, s15 0.75 | ~30 | 120.0 |
| q21 | s13 (1,500,124,755) | 13.5 | s4 14.25, s2 14.25 (948,435,784 rows each), s7 1.10, s16 0.30 | ~43.3 | 173.4 |

The "held at the same time" column is measured, not assumed: in `dp-cn4` q18.r0 streams 1, 4 and 15 had completed all 12 transmits with
`rx_batches=0` when stream 10's lease failed (`dp-cn4-six-summary.txt`); in stg48 q21.r1 stream 4 finished at +1.16..1.37 s, stream 2 at
+2.22..2.45 s, stream 13 at +2.89..4.11 s and the four `inputs=16` receivers started at +2.95/3.03/4.00/4.11 s, one per CN as its last
stream-13 frame landed, so all three lineitem-sized streams sat in each arena together.

Arithmetic (inference, matches the table): a hash shuffle of a stream of S bytes over N CNs puts S/N on each CN, of which (N-1)/N arrives
remotely, so arena per CN = S(N-1)/N^2 per held stream; at N=4 that is 3/16 of S. Lineitem per-CN outputs (local + remote) are 42 GB (q05),
48 (q08), 60 (q09), 30 (q17), 24 (q18), 18+19+19 (q21). 16 GiB covers none of the six; 48 GiB (51.5 GB) covers five; q09 needs ~52 GB
plus the outgoing pack batch.

Note on the 16 GiB numbers: `transmitted batches via nixl` is logged only when a whole (sender, destination) drain completes, so the per-stream
totals in `dp-cn4-six-summary.txt` understate what was in flight at the failure (e.g. q09.r0 at 16 GiB shows only streams 18+13 = 5.8 GB per
CN completed while the error reports 35 leases holding 16.84 GB: ~23 stream-4 frames were leased mid-drain). The `.err` "leases outstanding
holding N bytes" is the authoritative occupancy at failure.

## 2. Mechanism: the arena is a receiver-first materialisation buffer, and the drain order does not change that

Source (demo-plus):
- `experimental/starrocks/src/engine.rs:547-577` — remote batches are pushed into pool memory (`fragment.push_packed`) and their leases
  released (`context.staging_release`) inside the receiver fragment's `Run`, one stream at a time, before the plan runs. The comment's
  "copy-out-on-arrival" means arrival at the engine, not at the wire. Identical in feat/pin-table-cn `engine.rs:623-650`.
- `compute_node_service.rs:627-712 handle_transmit_packed` — a frame lands in the arena (lease granted by `handle_staging_lease`), is recorded
  in the rendezvous, and only a frame that completes the receiver's whole sender set yields `ready` -> `dispatch` (:234-237) to the single
  `dispatch_worker` (:254-265). `take_ready only releases complete sender sets` (:1509).
- `src/exec/exchange_staging_arena.cpp:213-251` — `lease()` is a non-blocking address-ordered first fit that throws on exhaustion; there is
  no wait and no retry (`nixl_transport.rs:688 rpc_request_lease(...)?` propagates it out of the drain).

So every remote frame of every stream a receiver consumes stays leased from the moment it lands until that receiver has received EOS from all
N senders and been scheduled. The per-CN arena requirement is the (N-1)/N share of the receiver's whole input (section 1), independent of
how the drains are ordered.

The blocking drain in the carve vs the DrainTicket overlap on feat/pin-table-cn:
- Carve: `compute_node_service.rs:1365-1385` — remote destinations are drained "one at a time in the FE's destination order", each
  `send_fragment` blocking the fragment-processing thread (the RPC blocking thread under async dispatch, `process_inline` :609-622),
  and only then are the receivers this fragment readied by its *local* push returned and dispatched.
- feat/pin-table-cn: `nixl_transport.rs:112-160` `DrainTicket`, `:225-240` `start_fragment` (posts the drain, returns a ticket);
  `compute_node_service.rs:96-130` `SenderDrains::join`, `:265-300` `dispatch_then_join` (dispatch the readied local receivers, then join
  the tickets), `:1110-1145`. Its own comment: "ORDER IS UNCHANGED ... the drains still run one at a time, in the FE's destination order"
  (`:1115-1120`); the measured gain it cites is "3 x 30 ms per CN on q14 SF500's 948 MB/destination stage" (`:1112-1113`); and the cost:
  "the parked batches of the not-yet-drained destinations stay resident while it computes -- the deliberate memory-for-latency trade" (`:273-275`).
- Consequence: the overlap the carve left out changes neither when leases are released (still at receiver `Run`) nor how many frames a CN
  holds (still all N senders' EOS). It would trim ~0.1-0.6 s of critical path per shuffle stage (the drains measured in 4.2 below) and
  *raise* the pool peak. It is not the cause of, nor a remedy for, the arena exhaustion.

The same overlap already happens in the carve on the remote-EOS path, and it is what killed q09 at 64 GiB/72 GiB (`dp-cn4-q09-stg72`):
cn3 (9132) finished its lineitem scan at 22:10:49.733 with 61.80 GB parked (`engine-.cn3.log` window 5 QueryEnd `allocated=61803680512`);
the last remote partition for it (sender 3 -> 9132, 37 batches, 15.12 GB) completed at 49.741 (`cluster.log`); its receiver (`inputs=8`)
started at 49.741 via `handle_transmit_packed -> dispatch`, relayed its own partition (`relayed native batches ... stream_id=4 sender_id=1
batches=37` at 49.7448) and died pushing the remote frames at `allocated=68449237760 peak=68698126080` (= 64 GiB) with `outcome=unwind`
at 49.777 — before cn3 had completed a single drain of its own three remote partitions (sender 1 never appears in a `transmitted` line;
`stg72-q09-summary.txt` senders=[0,2,3]). Pool need on the receiving CN at that moment = its own N parked partitions + (N-1) incoming
= 60 + 45 GB for q09 (+ ~7 GB of streams 18/13), i.e. ~110 GB, against a 64 GiB pool.

## 3. Fix 2 evidence (per-query parked-output bookkeeping, cancel teardown)

- Every `retired a query's parked sender outputs` line reports `still_parked=0`: 24/24 at 16 GiB (`dp-cn4/cluster.log`, triggers all
  `cn_err`), 4/4 at 48 GiB (1 `cn_err` + 3 `cancel:INTERNAL_ERROR`), 4/4 at 64/72 (1 `engine_err` + 3 cancel), 2/2 in pool100.
  cnlog: `fix2_retired=4` for each failed run (`dp-cn4/cnlog.txt`), `fix2_skipped=1` once (stg48 q09: a queued fragment of the dead query refused).
- The FE's `cancel_plan_fragment ... reason="INTERNAL_ERROR"` teardown released the arena on all CNs: `released_leases` summed per failure =
  77 (q05), 151 (q08), 107 (q09), 49 (q17), 104 (q18), 175 (q21) at 16 GiB; 224 (stg48 q09); 314 (stg72 q09) (`cluster.log`, e.g. dp-cn4
  22:02:35.742 `released_leases=25/17/17`).
- No leftover parked GB: engine `[gpu_pool] ... QueryBegin allocated=0 bytes` on every CN at the first fragment after each failure —
  cn2 w69 `allocated=42002451968` at the failed q05 fragment's QueryEnd -> w70 `0`; cn0 w119 `61571590400` -> w120 `0` after q09; cn0 w226
  `29936842240` -> w227 `0` after q17; cn0 w230 `27850472960` -> w231 `0` after q18; cn0 w264 `24021805312` -> w265 `0` after q21
  (`dp-cn4/engine-.cn*.log`). Yesterday's B4 on perf/profile-sf1000 measured 0-38 GB left parked per failure.
- The queries that ran right after each failure passed with warm medians within noise of yesterday's perf-cn4 (`results-cn4.md`: q10 1786
  vs 1789 ms, q19 1459 vs 1440, q22 852 vs 832; q06 1224 vs 852 is slower on all three runs, so not a leftover effect — out of scope here).
- Tooling note: the Quent lease digest is empty for this branch (`quent.txt` `_session_*: leases_total 0` in every arm) — the staging-lease
  probes of 45dab3be are not in the carve — so all occupancy numbers above come from `cluster.log` and the `.err` texts, not Quent.

## 4. 48 GiB: five pass, q09 does not, and how fast

### 4.1 Results (`dp-cn4-six-stg48/runs/runs.csv`, `compare.txt`; reference `reference-pin-table-cn.md`; merged `compare-ref.md`)

| q | 16 GiB | 48 GiB cold | 48 GiB warm (r1, r2) -> median | pinned 4 CN | ratio | demo-plus 1 CN warm | 4-CN speedup | oracle |
|---|---|---:|---|---:|---:|---:|---:|---|
| q05 | arena | 6621 | 5329, 3241 -> 4285 | 1.78 | 2.41x | 5562 | 1.30x | VALUES-DIFFER 9.6e-4 (decimal lowering, accepted) |
| q08 | arena | 3323 | 3247, 3942 -> 3594 | 2.05† | 1.75x | 3770 | 1.05x | VALUES-DIFFER 8.7e-6 |
| q09 | arena | fail (arena: 95 leases holding 50.55 GB of 51.54 GB, request 585 MB) | - | 2.73† | - | 5153 | - | EMPTY |
| q17 | arena | 2810 | 2648, 2652 -> 2650 | 3.89 | 0.68x | 4956 | 1.87x | MATCH |
| q18 | arena | 2071 | 2029, 2023 -> 2026 | 2.89 | 0.70x | 3256 | 1.61x | MATCH |
| q21 | arena | 5220 | 5186, 5302 -> 5244 | 3.33† | 1.57x | 10620 | 2.03x | MATCH |

q17/q18 beat the pinned reference (which reads the tables from GPU-resident pins; these read parquet). q05/q08/q21 are 1.6-2.4x slower.

### 4.2 Where q05's 3.24 s go at 4 CNs (stg48 q05.r2 timeline, `stg48-six-summary.txt`)

Lineitem leaf senders start at +0.338 s and finish at +1.06 / 1.50 / 1.65 / 1.91 s (elapsed 719 / 1161 / 1315 / 1567 ms: a 2.2x skew
between CNs on identical byte-range splits); their twelve 10.44-10.58 GB stream-9 drains complete between +1.25 and +2.48 s (three sequential
drains per CN take ~0.6 s, ~52 GB/s, pack-bound as in yesterday's B5); the `inputs=8` join receivers run +2.80..3.04 s (233 ms); the orders
shuffle (stream 12, 12 x 1.37 GB) completes +3.07..3.15 s; result at +3.235 s. So ~1.9 s skewed scan + ~0.6 s drain + ~0.7 s joins. The
pinned reference has no scan and no skew to wait on; the DrainTicket overlap would shave part of the 0.6 s drain, not the 1.9 s.

### 4.3 q09 at 4 CNs: the three splits tried, and why the fourth is not a 4-CN result

| pool / arena (GiB) | outcome | evidence |
|---|---|---|
| 100 / 16 | arena at +1.93 s, 35 leases / 16.84 GB | `dp-cn4/runs/q09.r0.err` |
| 90 / 48 | arena at +2.66 s, 95 leases / 50.55 GB of 51.54 | `dp-cn4-six-stg48/runs/q09.r0.err` |
| 64 / 72 | pool: receiver push `std::bad_alloc` at 68.70 GB peak = 64 GiB, after 10.5 s | `dp-cn4-q09-stg72/runs/q09.r0.err`, `engine-.cn3.log` w6 |
| 100 / 72 | pool: the lineitem scan itself OOMs at 98.6 GB allocated / 107.37 GB peak — but the FE placed all 11 fragments on 9102 and 9112 only (`cluster.log` `fragment run started` cn= counts 6+5; `engine-.cn2.log`/`cn3` contain only bring-up; transmits list senders [0,1], 4.5 GB per dest = a 2-CN share). Each of the two CNs had to park ~120 GB. A 2-CN placement, not a 4-CN measurement. | `dp-cn4-q09-stg72-pool100/` |

Inference for a real 4-CN q09: arena >= ~52 GB (56 GiB = 60.1 GB leaves 8 GB for outgoing packs) AND pool >= ~110 GB for the receiving
CN when its receiver is readied by the last remote EOS before its own drains (60 parked + 45 pushed + ~7 of streams 18/13). 56 + 104 GiB
+ 0.76 GiB context = ~161 GiB of 185.03 GiB HBM (`bench/gb200-4gpu/HARDWARE.md:14`) fits on paper; it was not run. Neither of the
runbook's SF1000 presets (128/32, 116/40; `bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:100,303`) does.

## 5. What the result argues for

1. Sizing rule for this exchange (measured at N=4): STAGING per CN >= sum over the streams one receiver holds of (N-1)/N x per-CN stream
   output, plus one pack batch; for SF1000 that is 37 / 38 / 52 / 25.5 / 30 / 43 GB for q05 / q08 / q09 / q17 / q18 / q21. 48 GiB covers
   five; 56 GiB covers six on the arena side. The retired `96 GiB x SF/500 / N` (cn-tuning SKILL.md:37) gives 48 GiB here — right for
   q05/q08/q21 by coincidence, wrong for q09. Pool per CN must additionally hold N parked + (N-1) pushed partitions of the largest shuffle
   (q09: ~105 GB) whenever the receiver is readied by a remote EOS before this CN's drains.
2. Copy-out on wire arrival (yesterday's B6 second proposal, PLAN-01 on the RTX notes): push the frame into pool memory in
   `handle_transmit_packed` and release the lease immediately. The arena becomes O(frames in flight) — a few x 660 MB per peer — instead of
   O(stream share), and 16 GiB is enough for all six; the pool requirement is unchanged (the bytes must be resident either way), so on this
   box the freed ~40 GiB of arena moves into the pool where q09 needs it. This is the change the arithmetic argues for first.
3. A blocking lease with bounded wait (B6 first proposal) must not ship alone: leases are released only when the receiver runs, the receiver
   runs only after every sender's EOS, and a sender waiting for arena space never sends EOS — with all four CNs full, every drain waits on
   space only the receivers can free. It converts a 1.4-2.4 s failure into a watchdog timeout (inference from section 2's source facts).
4. Ordering: dispatching a remote-EOS-readied receiver after this CN's own drains have released its remote partitions would cut the pool peak
   from N+(N-1) to 1+(N-1) partitions (q09: ~105 -> ~60 GB per CN) at the cost of the latency the DrainTicket change was buying. Restoring the
   DrainTicket overlap in the carve is a latency item (~0.1-0.6 s per shuffle stage) with a pool cost, not an arena item.
5. The 2 x RTX PRO 6000 result (`reference-2cn-rtxpro6000.md`: q05 q08 q09 q18 "fail (OOM)", q17 q21 "fail") is the same wall at N=2
   (inference from the 4-CN stream sizes; the box's split is not recorded, the RTX kit's validated split is 60/32,
   `bench/rtxpro6000-2gpu/SF500-CONFIG-AND-ARCHITECTURE.md:13`): per-CN arena share S/4 = 42 / 48 / 60 / 30 / 39 / 56 GB and per-CN
   receiver input S/2 = 84 / 96 / 120 / 60 / 78 / 112 GB (q18 and q21 summing their co-held streams) for q05 / q08 / q09 / q17 / q18 / q21.
   No split of a 96 GB card holds any of them at SF1000 with receiver-first materialisation; copy-out (item 2) removes the arena term but
   the pool term alone exceeds the card for all but q17. Passing these at 2 CNs needs the FE to stop shuffling lineitem (P3: broadcast the
   small side; the FE shuffles because FILES() estimates are 1 row — fix 3, not on this branch) or a streaming/spilling exchange consumer.
   The pinned kit does not change the FE plan (`docs/README.md:80` "Queries don't change") and its daggers sit on exactly q08/q09/q21 at
   4 CNs (arena needs 38/52/43 GB vs 32-40 GiB presets) and only q09 at 8 CNs ((N-1)/N^2 = 7/64: q09 needs ~26 GB) — consistent with the
   same arithmetic, low confidence since the reference's split is not recorded.

## 6. Not measured / caveats

- Quent has no lease or per-fragment lease-concurrency data on this branch (probes absent); occupancy comes from the arena's own error text.
- The pinned reference's GPU_MEM/STAGING and the RTX 2-CN split are not in the inputs; items 5 and the dagger consistency are inference.
- The 100/72 q09 arm is confounded by a 2-CN placement (4.3) and says nothing about 4-CN sizing; a 4-CN q09 with 104/56 was not run.
- Batch sizes (580-664 MB) follow the scan-task batch and N; at 8 CNs both the per-CN share and the batch shrink, which the reference's
  8-CN column reflects but which was not measured here.
