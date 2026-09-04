# A2 refutation attempt: DrainTicket overlap vs arena exhaustion

Verdict: NOT refuted. The finding's thesis (the overlap is neither the cause of nor a remedy for the arena
exhaustion; it raises the pool peak; the carve already has the same overlap on the remote-EOS path and that
is the proximate death of q09 at 64/72 GiB) survives every check. Three sub-claims need correcting, all in
the direction of making DrainTicket *less* relevant than A2 says.

Paths: `$SP` = /tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad;
`C/` = /home/prestouser/aocsa/sirius/experimental/starrocks/src (feat/pin-table-cn);
`W/` = /home/prestouser/aocsa/sirius-stacks-wt/demo-plus/experimental/starrocks/src (carve).
Measured unless marked inference.

## 1. What checks out (source)

- DrainTicket `C/nixl_transport.rs:112-160`, `start_fragment :225-240`: posts one drain to the single FIFO transport
  thread and returns a ticket; "drains ... run one at a time, in the order they were posted". `SenderDrains::join`
  `C/compute_node_service.rs:95-117`; `dispatch_then_join :265-300`: "The RPC still does not return until every drain
  has been joined" and "the parked batches of the not-yet-drained destinations stay resident while it computes -- the
  deliberate memory-for-latency trade". Gain cited `:1108-1112`: "3 x 30 ms per CN on q14 SF500's 948 MB/destination
  stage". The carve-out is recorded in `~/.claude/plans/demo-q1q6-report.md:129-133` (C8 "Not carried").
- Carve: `W/compute_node_service.rs:1364-1385` local push first, then blocking `send_fragment` per remote destination in
  FE order, then `FragmentOutcome::from_ready`; `process_inline :609-622` returns only after that; the exec RPC wraps it
  in `spawn_blocking` (`:281`). `handle_transmit_packed :627-712` dispatches a receiver completed by a remote frame at
  once (`:702-711`); identical on feat/pin-table-cn (`C/` `push_remote_frame ... self.dispatch(ready)`).
- Lease release: `W/engine.rs:547-577` vs `C/engine.rs:615-655` differ only by one log field and one error string
  (`diff` output: 2 lines). Leases are released inside the receiver's Run, after `push_packed`.
- Arena capacity is the binding constraint independent of dispatch order: a receiver is ready only when all N senders
  have EOS'd, so all (N-1) remote shares must already be leased. At 16 GiB those shares are 32-46 GB per CN
  (`arena-4cn.md` section 1, from `dp-cn4-six-stg48/cluster.log` transmit sums) against 17.18 GB; every 16 GiB failure
  (`dp-cn4/runs/q*.r0.err`) and the 48 GiB q09 failure (`dp-cn4-six-stg48/runs/q09.r0.err`, 95 leases / 50.55 GB of
  51.54) happened with no lineitem receiver ready on any CN (no `fragment run started ... inputs=8` before the error in
  either cluster.log). The q08 `export_packed` failure is the sending CN's own arena full of peers' frames
  (`dp-cn4/runs/q08.r0.err`: 43 leases, 16.03 GB held, largest free block 664,006,656 < request 664,009,728) with only
  two stream-4 drains completed cluster-wide (summary: senders=[0], 2 x 12 GB) -- no receiver could have been ready.
- "Raises the pool peak" is real, because a drained batch leaves the pool as it is exported: `sirius_ffi.cpp:706-780`
  `export_packed` does `session().pull(stream_id)` (destructive; `:936` "by-id lookups leave the parked queue intact,
  unlike session().pull") and the batch handle dies at scope end. Measured in stg48 q05.r2 on all four CNs: lineitem
  sender QueryEnd allocated 42,002,459,648 / 42,332,658,176 / 41,915,230,720 / 41,769,515,264 B, next window QueryBegin
  10,502,348,032 / 10,587,974,912 / 10,480,531,456 / 10,446,803,712 B (`dp-cn4-six-stg48/engine-.cn0..3.log`,
  22:08:22-23). So drains-first leaves 1 partition; receiver-first holds N and adds N-1.
- stg72 q09 timeline verified line by line in `dp-cn4-q09-stg72/cluster.log` and `engine-.cn3.log:191-264`:
  sender fd0 finished 49.733081 (allocated 61,803,680,512 B); `transmitted stream_id=4 sender_id=3 dest=9132 batches=37
  bytes=15,116,039,936` at 49.740979; receiver fcb `inputs=8` started 49.741163 (engine QueryBegin allocated
  61,388,723,456); `relayed ... stream_id=4 sender_id=1 batches=37` at 49.744753; QueryEnd 49.777 allocated
  68,449,237,760 peak 68,698,126,080 (= 63.98 GiB) outcome=unwind; `fragment run failed` 49.786836; no
  `transmitted ... sender_id=1` for stream 4 anywhere in the run (summary senders=[0,2,3]).

## 2. Corrections

### 2.1 "~0.1-0.6 s per-shuffle-stage latency item" is an upper bound; the measured q05 shape realizes 0 ms

The 0.6 s of drains in stg48 q05.r2 is on the critical path through the sender RPC's return, not through a waiting
receiver. `dp-cn4-six-stg48/cluster.log` exec_plan_fragment span closes: 22:08:22.290714 (idle 1.30 s), 22.733561
(1.74 s), 22.884641 (1.90 s), 23.134307 (2.15 s) -- each 0.2-0.3 ms after that CN's last `transmitted stream_id=9`
line (22.733296, 22.884430, 23.134088). The FE deployed the next wave (stream-1 senders 09a0-09a3, `fragment run
started` on all four CNs at 23.141504-23.141508) 7 ms after the last close. DrainTicket does not move the RPC return
(`C/compute_node_service.rs:269-271`; carried test `the_sender_rpc_reports_ok_only_after_every_drain_has_finished`,
appendix :507), so the 0.6 s is untouched by it.

The only receiver in q05.r2 that waited on its own CN's drains: 9102's join receiver 0990. Its stream-7 remote frames
landed 23.413036 (sender 2), 23.436845 (sender 0), 23.445543 (sender 1); the middle fragment 099b (streams 4/1 ->
stream 7) on 9102 finished 23.448004, so the local push completed the set at ~23.448; 099b's three stream-7 drains
closed 23.451732 / 23.455412 / 23.459128 and 0990 started 23.459269. Wait = 11 ms, and it is on the dispatch-worker
path (099b itself was dispatched by a remote frame: started 23.426135, 0.04 ms after `stream_id=4 sender_id=0 dest=9102`),
where feat/pin-table-cn ALSO joins before running (`join_into_ready` `C/compute_node_service.rs:140-155`, used at
`:777-781`; comment: "The worker is single-threaded and is itself the thread that would run those receivers, so
deferring the join there would buy no overlap"). Realizable DrainTicket saving for this stage: 0 ms. The 3 x 30 ms in
the source is the q14 SF500 shape where a leaf sender's local push completes a set on the RPC path.

### 2.2 "changes neither when leases are released" -- imprecise, peak unchanged

On the RPC path, when this CN's local push is the last EOS, DrainTicket runs the receiver during rather than after the
drains, so that receiver's remote leases are released earlier. The peak is unchanged (all N-1 shares had to be resident
for the set to complete), and no such receiver existed in any 16/48 GiB failure (section 1), so it is moot for the
arena question. Say "changes neither the peak arena occupancy nor the readiness condition".

### 2.3 "is what killed q09 at 64/72" -- proximate mechanism, not a counterfactual pass

Inference from the export-frees-progressively measurement: drains-first would have left cn3 at ~15.5 GB (own quarter)
+ 1.9 GB (earlier parked, w5 QueryBegin 1,929,917,184) + 45.3 + 3.4 + 2.4 GB pushed (streams 4/18/13 per-dest,
`stg72-q09-summary.txt`) = ~68.5 GB before the join, against 68.70 GB. The receiver-first order is why it died at
49.777 in `push_packed`; it is not evidence that the 64 GiB pool would otherwise have sufficed.

### 2.4 Side note: `SIRIUS_CN_ASYNC_SENDER_DISPATCH` is inert

`$SP/fix/capture-arm.sh:14` exports it, but no file in either tree reads it (grep 'ASYNC' over `W/` and `C/`
experimental/starrocks: empty; `git log -S` on feat/pin-table-cn: empty). The carve's sender path is the synchronous
`process_inline`. Where notes say "the equivalent async dispatch was on", read "the carve's sender RPC blocks for scan +
drains, same as feat/pin-table-cn's".
