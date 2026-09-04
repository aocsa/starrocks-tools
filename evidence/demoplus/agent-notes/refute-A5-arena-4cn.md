# Refutation check of finding A5 (arena-4cn): not refuted; three evidence lines corrected

Source tree read: /home/prestouser/aocsa/sirius-stacks-wt/demo-plus @ 281b13bc (read-only). Measurements under
/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/demoplus/arms/ (written `$A`).
"Measured" = copied from the named file; "inference" marked.

## 1. What A5 gets right (verified)

| claim | verification |
|---|---|
| push_packed is a deep copy into pool memory, lease released right after | `src/sirius_ffi.cpp:786-850`: `cudf::unpack` aliases the lease ("Allocates no device memory"), then `std::make_unique<cudf::table>(unpacked, stream, gpu_space->get_default_allocator())` + `stream.synchronize()`; `experimental/starrocks/src/engine.rs:563-577` calls `push_packed` then `context.staging_release(batch.offset)`. |
| leases of remote frames are released only when the receiver runs | `engine.rs:546-583` is inside `run_fragment_inner`, before `fragment.run()`; the only other releases are error/cancel paths (`engine.rs:355-370`, `compute_node_service.rs:669-683` retired receiver, `StagedLeases` guard). |
| the receiver runs only after every sender's EOS | `local_exchange.rs:408-440 take_ready`: "A remote sender counts only once its eos arrived"; `complete != expected -> Ok(None)`. Comment cited at `compute_node_service.rs:1509` is at exactly line 1509. |
| a sender blocked in the lease request never sends EOS | `nixl_transport.rs:680-745`: `while let Some(batch) = export_packed_next { rpc_request_lease(...)?; write_and_wait; rpc_transmit(eos=false) }` then `rpc_transmit(eos=true)` at 727; drains are sequential per destination (`compute_node_service.rs:1366-1385`). |
| the arena never waits | `src/exec/exchange_staging_arena.cpp:213-251 lease()`: first fit, throws "exchange staging arena exhausted" with no retry. |
| 16 GiB fails all six at 4 CNs, 48 GiB passes five | `$A/dp-cn4/runs/runs.csv` (six `fail`), `$A/dp-cn4/runs/q*.r0.err` (26-47 leases, 16.03-17.06 GB of 17.18 GB); `$A/dp-cn4-six-stg48/runs/runs.csv` (q09 `fail`, others pass); `$A/dp-cn4-six-stg48/runs/q09.r0.err` 95 leases / 50.55 GB of 51.54; arena exit peaks 51.28/43.65/50.88/50.55 GB (`engine-.cn{0,1,2,3}.log` line `exchange_staging_arena.cpp:169`). |
| q09 at 64/72 dies in the pool at the receiver | `$A/dp-cn4-q09-stg72/engine-.cn3.log:193` QueryEnd `allocated=61803680512` (scan), `:201` QueryBegin `61388723456` 10 ms later, `:263` QueryEnd `68449237760 peak=68698126080 outcome=unwind`; `runs/q09.r0.err` "failed to push a staged remote batch from sender 0 into stream 4: std::bad_alloc". |
| RTX kit citations | `bench/rtxpro6000-2gpu/SF500-CONFIG-AND-ARCHITECTURE.md:13` `GPU_MEM=60GiB STAGING=32GiB`; `bench/rtxpro6000-2gpu/STATUS.md:38-41` "Arena occupancy is a pressure gauge for the pool ... push_packed deep-copies arena->pool before releasing the lease"; `.claude/skills/cn-tuning/SKILL.md:36-37` same + "q09 needs copy-out-on-arrival (PLAN-01)"; `bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:100` 128/32 GiB, `:303` 116/40 GiB; `docs/README.md:80` "Queries don't change". All verbatim. |
| FE shuffles lineitem | `perf/sf1000/survey/plan-summary-4cn.txt:9-10,17-18`: q05/q09 `card_all_one=True`; lineitem is the BUILD side of a PARTITIONED join (q05 `10:INNER JOIN (PARTITIONED) build=9:EXCHANGE<-lineitem`, q09 `5:... build=4:EXCHANGE<-lineitem`). |
| 2-CN arithmetic | S from stg48 transmits (`arena-4cn-work/stg48-six-summary.txt`: lineitem per-dest 31.5/36.0/45.0/22.5/18.0/13.5+14.25+14.25 GB at 4 CN -> S = 168/192/240/120/160/224 GB). At N=2: arena/CN S/4 = 42/48/60/30/40/56, parked/CN S/2 = 84/96/120/60/80/112 GB (A5 says 39/78 for q18; it omitted stream 15). Card budget 94.97 GiB = 102 GB (`SF500-CONFIG-AND-ARCHITECTURE.md:6-7`). For q05/q08/q09/q18/q21 parked alone is 80-120 GB, plus >= 40 GB arena: no split. Inference, as A5 says. |

So the core of A5 stands: copy-out on arrival makes the arena O(frames in flight); a bounded-wait lease alone converts a fast failure into a timeout (single-stream regime: every arena's frames belong to one receiver that needs every sender's EOS); 2 CNs on 96 GB cards cannot run q05/q08/q09/q18/q21 at SF1000 with receiver-first materialisation; a plan change or a streaming/spilling consumer is required there.

## 2. Corrections (concrete contradictions in the evidence/impact, none overturning the core)

### 2.1 Impact item (3) is foreclosed by impact item (1)

A5 orders: (1) copy-out on arrival, then (3) "dispatch a remote-EOS-readied receiver after this CN's own drains to cut the pool peak from N+(N-1) to 1+(N-1) partitions (q09: ~105 -> ~60 GB per CN)".

Source facts: exported batches leave the parked sink's queue as they are packed (`src/op/sirius_physical_streaming_sink.cpp:232-239 pull -> try_pull()`, `stream_session.cpp pull`), and `export_packed`'s `auto batch` is the last owner (`sirius_ffi.cpp:716-720`), so a CN's own drains free its remote-destined partitions progressively. That is why item (3) works *today*: with receiver-time push (`engine.rs:546-583`), a receiver dispatched after this CN's drains holds 1 own + (N-1) pushed partitions.

Under item (1) the incoming (N-1) partitions are copied into the pool the moment each frame lands, independent of when the receiver is dispatched. Dispatch order then changes nothing about the pool; the pool at CN A = (own partitions not yet drained) + (frames peers have already pushed). At the slowest-scanning CN (the stg72 cn3 case: scan QueryEnd 61.80 GB at 22:10:49.733, all 45 GB of inbound already landed 49.21-49.74, `$A/dp-cn4-q09-stg72/cluster.log`, `engine-.cn3.log:193-201`) the peak is N + (N-1) partitions plus the scan's working set, and no ordering can lower it. So after (1), the "~105 -> ~60 GB" cut of (3) is not available; the pool must be sized for ~112-120 GB per CN on q09 (61 + 45 + 7 of streams 18/13 + hash table). A5's "the freed ~40 GiB moves to the pool" is therefore a requirement, not an option: (1) alone with today's 100 GiB pool would move q09 from an arena failure to a pool OOM on the slowest CN (inference from the stg72 numbers; not run).

Also a scope note, not a contradiction: `push_packed` requires a built Fragment (`sirius_ffi.cpp:792-794`), and the receiver is translated only when its sender set completes (`compute_node_service.rs:1524-1530` "A receiver translates when its sender set completes, not at arrival"), so copy-out at `handle_transmit_packed` needs a pool-backed holding area independent of the receiver fragment, not a call into the existing `push_packed`.

### 2.2 Evidence line 4 (pinned daggers ~ arena needs) is contradicted by its own arithmetic

`demoplus/reference-pin-table-cn.md` legend: "dagger = filled, partial 1 CN". Daggers: q05 at 1,2 CN; q08 at 1,2,4; q09 at 1,2,4,8; q21 at 1,2,4.
- 1-CN daggers on q05/q08/q09/q21: at 1 CN there is no remote exchange and no arena use, so the dagger is not an arena marker.
- 4 CN: A5's own needs are q05 37, q08 38.3, q09 51.6, q21 43.3 GB. Against the 32 GiB preset (34.4 GB) q05 would exhaust too (it is clean, 1.78 s); against 40 GiB (42.9 GB) q08 would not (it is daggered). No preset in the 32-40 GiB band yields {q08,q09,q21} without q05.
- 8 CN: q09 arena need = 240 x 7/64 = 26.2 GB < 34.4 GB, yet q09 is daggered at 8 CN. `arena-4cn.md` item 5 calls this "consistent"; it is the opposite.
`why-six-fail-multi-cn.md` 2.7 reached the same non-explanation independently. Drop this line from A5; it was already flagged low confidence.

### 2.3 "three CNs within 13 ms" overstates what the log shows

`$A/dp-cn4/cluster.log` (ANSI stripped) 22:02:35.522 / .530 / .535: three `retired a query's parked sender outputs ... trigger=cn_err` lines whose causes are three sender-side `request_staging_lease` refusals; the second and third quote an identical peer state ("120951552 free ... 31 leases"), i.e. plausibly one refusing peer. The fourth CN failed at 35.741 exporting into its own arena (30 leases). Provably at capacity: at least two arenas (the refusing peer(s) at 17.06 GB and the exporter). "All four full" is inference from symmetry (four senders finished 35.356-35.434, each CN's inbound need 32 GB vs 17.2 GB, `dp-cn4-six-summary.txt` q05.r0). The deadlock conclusion survives with one full arena: that sender's sequential drain blocks, so none of its later EOS frames are sent either.

### 2.4 Minor

- "deadlocks until the watchdog": a *bounded* wait fails at its own bound, or earlier at the PRPC timeout the lease RPC runs under (`engine.rs:18-21` records the q02 case where a stalled lease request hit the PRPC timeout). Same outcome (delayed failure), different clock.
- "every exchange SHUFFLE": q05 has one BROADCAST (region, exchange 24) and q09 one BUCKET_SHUFFLE (partsupp, 13) (`plan-summary-4cn.txt:10,18`). The lineitem exchanges are PARTITIONED, which is what matters.
- q18 2-CN figures: 40/80 GB when stream 15 is included (A5: 39/78).
- The RTX kit docs describe SF500 f64 and SF100 (`SF500-CONFIG-AND-ARCHITECTURE.md:8`, `TPCH-STATUS.md:4`); the user's 2-CN run was SF1000 (`reference-2cn-rtxpro6000.md:1`). The 60/32 split is the kit's validated row, not the recorded split of that run (A5 says so).
