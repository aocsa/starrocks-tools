# Refutation of F2 "q09 is doubly infeasible at 4 CNs"

Verdict: refuted. The two load-bearing statements of the claim are each contradicted by a measurement
or by the source it cites. What survives: q09 did fail in every real 4-CN arm, the arena need
(~51 GB per CN) is real, and the 61.4 GB pool transient at receiver start is real under the current
dispatch order. What does not survive: that the transient is structural, and that the resulting split
"exceeds what the card can hold".

Paths: A = scratchpad/demoplus/arms, W = /home/prestouser/aocsa/sirius-stacks-wt/demo-plus,
C = /home/prestouser/aocsa/sirius. All numbers measured unless marked inference.

## 1. "107 GiB pool + 52 GiB arena exceeds what the card can hold" -- false

* Card: `nvidia-smi --query-gpu=memory.total` = 189,471 MiB = 185.03 GiB per GB200. The runbook the
  claim cites budgets 184.00 GiB usable, occupancy = GPU_MEM + STAGING + 0.76 GiB, and its own SF1000
  preset is GPU_MEM=128 GiB + STAGING=32 GiB = 160.8/184 = 87 % (`C/bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:72-98`).
* Claim's own worst case, taken literally: 107 + 52 + 0.76 = 159.76 GiB = 86.8 % of 184 GiB -- below
  the runbook's SF1000 preset.
* Units: 61.4 + 45.8 = 107.2 GB decimal = 99.8 GiB, not 107 GiB. Measured inbound lineitem to 9132 is
  14,998,330,112 + 14,913,006,336 + 15,116,039,936 = 45,027,376,384 B = 45.03 GB
  (`A/dp-cn4-q09-stg72/cluster.log:1697,1880,2047`), so 61.39 + 45.03 = 106.42 GB = 99.1 GiB.
* Measured occupancy above the claimed ceiling: in `A/dp-cn4-q09-stg72-pool100` each CN held a 72 GiB
  arena (`engine-.cn0.log:10` 77,309,411,328 B cudaMalloc) and its pool ran to the 100 GiB cap
  (`engine-.cn0.log:230` peak=107,374,182,400 B; `engine-.cn1.log:206` same) = 172.76 GiB in use on
  one GPU, and the stop was the pool cap, not the device.

## 2. "held until the LAST destination releases" -- not what the code or the pool trace does

Source (`W/`):
* `src/sirius_ffi.cpp:706-725` `Fragment::export_packed` does `session().pull(stream_id)` ->
  `src/exec/stream_session.cpp:120-124` -> `src/op/sirius_physical_streaming_sink.cpp:232-239`
  `_outputs[index]->try_pull()`: the batch is dequeued from the parked output stream; once packed
  into the arena lease the `shared_ptr` is the last owner and the GPU batch is freed. Per batch.
* `experimental/starrocks/src/parked_registry.rs:158-178` `release()` decrements `outstanding`; the
  fragment "and the GPU memory its *remaining* batches hold" drops at the last release -- the batches
  already exported are not "remaining".
* `experimental/starrocks/src/engine.rs:517-545`: the local destination is a move (`relay_from`,
  "Nothing is converted, written, or copied") followed by an immediate `registry.release(slot)`.

Measured on cn3 of the same run (`A/dp-cn4-q09-stg72/engine-.cn3.log`):
* partsupp sender (stream 18): QueryEnd allocated=4,531,580,672 (line 97) -> next QueryBegin
  allocated=1,132,924,672 (line 103): 3/4 freed at 22:10:41.209 after cn3's three outbound drains
  finished 41.135/41.156/41.178 (`cluster.log` "stream_id=18 sender_id=1 dest=9122/9102/9112").
  No receiver of stream 18 ever ran on cn3 before the failure, so the local (4th) claim was never
  released; the memory went anyway.
* orders sender (stream 13): 4,290,215,680 (line 145) -> 1,927,195,904 (line 151) after drains at
  41.442/41.457/41.473. Same pattern.

## 3. Why the receiver saw 61.39 GB: dispatch order, not a bound

* cn3's lineitem sender finished 22:10:49.733 (`cluster.log:2038`); the last remote eos into 9132
  landed 49.741 (`:2047`, sender 3); the receiver fcb was dispatched 49.741 (`:2063`) and its
  QueryBegin read allocated=61,388,723,456 (`engine-.cn3.log:201`).
* Readiness is "eos completes the sender set" (`experimental/starrocks/src/local_exchange.rs:319`
  `push_remote_frame`; `compute_node_service.rs:704-712` `dispatch(ready)`). Nothing waits for the
  local sender's own outbound drains.
* cn3's outbound lineitem drains (sender_id=1 -> 9102/9112/9122) never completed before the failure:
  there is no `transmitted batches via nixl stream_id=4 sender_id=1` line in the log. Peer drains are
  sequential at ~0.28 s each (sender 0: finished 49.264 -> 49.543/49.819/50.092; sender 3: 49.455 ->
  49.741/50.022/50.304; `cluster.log:1710,1806,1880,2138,2286,2047,2246,2346`), so cn3's would have
  released ~15 GB at ~50.01, ~50.29, ~50.57. The receiver died at 49.777-49.787 after ~7 GB of
  `push_packed` (`engine-.cn3.log:263` allocated=68,449,237,760, peak=68,698,126,080 = 63.98 GiB cap).
* So 61.39 GB = ~15 GB local quarter (moved, still pool) + ~45 GB of remote-destined batches still
  queued for export + ~1.4 GB of other parked output. It is the outcome of copy-in (45 GB) racing
  drain-out (45 GB) on the same pool; with a 64 GiB pool copy-in lost. It is not "S + S(N-1)/N" by
  construction. Inference: a dispatch that waits for the local drains (<= 0.9 s here) or copies in
  after draining out needs ~17 + 45 = 62 GB at receiver start, inside the default 100 GiB pool.
* `SIRIUS_CN_ASYNC_SENDER_DISPATCH`, exported by `capture-arm.sh`, is not read by this crate (the
  crate's knob set is SIRIUS_CN_DUMP_FRAGMENTS, _NIXL_*, _CPU_AFFINITY, _USE_SIRIUS_DATASOURCE,
  _RPC_TIMEOUT_SECS, _FRAGMENT_FUSION, _TRANSLATE_ONLY); it changed nothing here.

## 4. "Measured three ways" did not include the split the claim rules out

* The three real 4-CN arms used pools of 100 GiB (16 GiB arena), 90 GiB (48 GiB arena) and 64 GiB
  (72 GiB arena). No 4-CN arm ran pool >= 100 GiB with arena >= 50 GiB.
* `A/dp-cn4-q09-stg72-pool100` (100 GiB + 72 GiB) is not a 4-CN measurement: q09 was submitted at
  22:21:26.074 (`runs/runs.csv`); BRPC on 9122/9132 came up at 26.247/26.325 (`cluster.log:970,1016`);
  the FE got "Connection refused" and blacklisted nodes 10002 and 10004 at 26.106/26.127
  (`cluster.log:669,876`); all 11 fragments ran on 9102/9112 with `outputs=2` (`cluster.log:1127-1428`),
  and the two lineitem senders hit the pool cap exactly (peak=107,374,182,400) while scanning a 2-CN
  share (~122 GB). EXTRA-ARM.md item 24 records the error string without this.

## 5. What a correct statement looks like

* Arena per CN for q09 at 4 CNs: 50.55 GB held + 585 MB requested with the largest free block 463 MB
  (`A/dp-cn4-six-stg48/runs/q09.r0.err`) -> ~51.2 GB live; 50-52 GiB is enough (measured need, GB).
* Pool per CN under the current dispatch order: 106.4 GB transient at receiver start plus the join's
  working set (unmeasured) -> ~100-105 GiB (inference). 104 GiB + 52 GiB + 0.76 = 156.8 GiB = 85 %
  of 184 GiB, the runbook's SF100/SF500 occupancy. Untested; the claim's proof does not exclude it.
* With the dispatch-order fix in section 3, pool need falls to ~62 GB + join, i.e. the default
  100 GiB pool with a 50 GiB arena (150.8 GiB occupancy). Untested; inference from the per-batch
  release measured on streams 18 and 13.
