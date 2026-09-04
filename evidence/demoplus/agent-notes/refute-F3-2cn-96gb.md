# Refutation check: F3 "2-CN 96 GB box is pool-bound for four, arena-bound for two"

Verdict: the headline classification is contradicted on q18 (and on the mechanism it rests on); the bottom line
"no STAGING/GPU_MEM split makes any of the six pass at 2 CNs on a 96 GB card" survives with corrected arithmetic.
Paths: `$SP` = scratchpad, `W/` = /home/prestouser/aocsa/sirius-stacks-wt/demo-plus, `C/` = /home/prestouser/aocsa/sirius.

## What checks out (measured / source)
- Card 97,887 MiB: `C/bench/rtxpro6000-2gpu/TPCH-STATUS.md:3`. Budget `GPU_MEM = CARD - STAGING - 2 GiB`, arena is bare cudaMalloc outside the
  pool: `C/bench/rtxpro6000-2gpu/SIRIUS-TUNING-RUNBOOK.md:67-73, 83`. 16 GiB arena -> pool <= 77.6 GiB = 83.3 GB. Same doc (:249-253) already says
  2 CN "has no working split" at SF500 on 80 GiB cards.
- P (standalone GPU_SCAN in=): q05 171.00 (`$SP/perf/sf1000/agent-notes/standalone-failed-quent.txt:3`); q08 195.75, q09 244.50, q17 122.25, q18 97.50,
  q21 73.50 (+77.76) (report P1 table, `~/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md:117-124`).
  4-CN sinks are P/4 within 1% (stg48 engine logs below), so S = P/2 at N=2 is a sound extrapolation.
- q05/q08/q09: S = 85.5 / 97.9 / 122.3 GB > 83.3 GB pool -> the lineitem scan alone overflows the pool. Pool-bound, correct.
- Source lines cited exist: `W/experimental/starrocks/src/engine.rs:332` (LAST destination), `:548` (deep copy into pool, release lease);
  `C/experimental/starrocks/src/engine.rs:403, 624`.

## What is contradicted
1. "The sender's parked output is held until the last destination releases it" (used to sum S + S(N-1)/N on every CN and to carry
   the full outputs of earlier senders into the q18 sum). Measured, `$SP/demoplus/arms/dp-cn4-six-stg48/engine-.cn{0,1,2,3}.log` `[gpu_pool] allocated=`:
   - q17 lineitem sender ends at 30.23 / 29.83 / 29.94 / 30.00 GB; the next fragment begins 0.4 s later at 7.56 / 7.46 / 7.48 / 7.50 GB = exactly 1/4.
     The three remote drains freed 3/4 of the parked output before any receiver ran.
   - q18 chain on cn0 (w89-w93): customer 0 -> 0.94 (next begin 0.235), agg -> 6.24 (1.735), orders -> 10.77 (3.99), lineitem 3.99 -> 27.99 (receiver begins 9.99).
     Identical on cn1/cn2 (4.03 / 3.98 GB before the lineitem scan). So when the lineitem scan started, 3.99 GB was parked, not the 15.97 GB the
     "held in full" reading gives.
   - Source agrees: `engine.rs:331-332` / `parked_registry.rs:160-161` say the memory of the *remaining* batches drops at the last release;
     `export_packed` pulls each batch out of the parked session (`W/src/sirius_ffi.cpp:719 impl_->session().pull(stream_id)`), so packed
     batches leave the pool as the drain proceeds.
   - The full-S-at-receiver case (stg72 cn3 61.4 GB, `dp-cn4-q09-stg72/engine-.cn3.log:193-201`; stg48 q18 cn3 22.5 of 27.8 GB at w88 begin)
     is the CN that finishes the scan last and whose receiver is dispatched by the last inbound frame before its own drains run
     (`$SP/demoplus/agent-notes/arena-4cn.md` section 2). It applies to one CN and only when lineitem is the receiver's last-arriving stream;
     for q17 all four receivers began at 8.46-8.56 GB (local quarters only; w78/w81/w77/w75) because the agg wave ran between the drains and the join.
2. "q18 parks ~81 GB of concurrent inputs ... at or above the ~83 GB pool ... OOM while the sender is still scanning". The 81 GB halves the 1-CN
   P1 sum (customer 3.94 + agg 24.0 + orders 36.75 + lineitem 97.5), which is a full-hold sum because at 1 CN the single destination is local and
   nothing is drained (`$SP/perf/sf1000/cn1-quent.txt:1081-1092`). At N=2 the earlier senders keep only their local halves after their drains:
   (3.94 + 24.0 + 36.75)/4 = 16.2 GB, plus the lineitem share 48.75 GB = 65 GB during the scan (+ ~6 GB scan overshoot, the q09 analogue
   68.06 peak vs 61.80 end in stg72 cn3) = ~71 GB < 83.3 GB. The q18 scan fits the pool. 81 was also below 83 by the finding's own numbers.
3. Therefore, under the finding's own 16 GiB assumption, q18 is arena-bound, not pool-bound: when the lineitem drain starts, the arena already
   holds the customer/agg/orders inbound frames (0.98 + 6.0 + 9.2 = 16.2 GB; they wait for the same join receiver, which needs lineitem;
   in `dp-cn4/runs/q18.r0.err` the three earlier waves had completed and no receiver ever started, `arena-4cn-work/dp-cn4-six-summary.txt:230-255`)
   and the lineitem inbound is 24.4 GB, against 17.18 GB capacity. The model predicts 3 x "fail (OOM)" (q05 q08 q09) + 3 x plain "fail"
   (q17 q18 q21); the screenshot shows 4 + 2. The claimed match to the screenshot's split does not hold, and the arena-vs-OOM class of q18
   cannot be read off this model. Only the box's .err text and GPU_MEM/STAGING can settle it (a STAGING of ~28-30 GiB would flip q18 to a
   scan-phase OOM while keeping q17 on the arena; neither documented arm, 40/16 or 60/32 GiB, does).
4. q21's 4-CN plan ships three lineitem-sized streams per CN (18.86 + 18.86 + 17.90 GB, cn0 w109/w111/w112), i.e. ~222 GB total, not the
   1-CN 77.76 + 73.50; the finding's 75.6 GB per CN at N=2 understates the per-CN traffic (55.6 GB at N=4 -> ~111 GB of shuffle per CN at N=2).
   Class unchanged (arena at 16 GiB; scan of the third stream 37.7 + 35.8 + overshoot ~79 GB fits 83.3 barely).

## Bottom line, corrected
Per CN at N=2 the last-finishing CN's receiver needs pool >= (local halves of earlier streams) + (its own last stream, full) + (all inbound copies),
and the arena must hold all inbound frames first: q17 30.5 + 8 + 34.5 = 73 GB vs pool <= 100.5 - 34.5 = 66 GB; q18 16.2 + 48.75 + 40.6 = 105.6
vs pool <= 60; q21 37.7 + 35.8 + 55.7 = 129 vs pool <= 45; q05/q08/q09 fail in the scan. No split passes any of the six -- the conclusion the user
needs is intact, but the four/two classification and the S + S/2 "receiver-start peak" (92-183 GB) are not: the peak is ~73 GB for q17,
and q18's failure class at 16 GiB is the arena.
