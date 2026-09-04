# Refutation check: finding `fix2-verified-fix1-untested` (task six-on-1cn)

Verdict: NOT refuted on its two headline claims (fix 2 baseline is a clean zero on all 18 one-CN
runs; fix 1's futility predicate never fired in any demo-plus arm). Two sub-claims are contradicted
by the measurements and need correcting; four smaller wording nits.

S = /tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT = /home/prestouser/aocsa/sirius-stacks-wt/demo-plus (read only)

## Confirmed (measured)

- `S/demoplus/agent-notes/.pool_windows.py S/demoplus/arms/dp-cn1-six`: 18 rows, last_end_alloc_GB
  = 0.000 for all 18, next_begin_alloc_GB = 0.000 for the 17 that have a successor (q21.r2 has none,
  prints -1). Exact bytes of the first `[gpu_pool] GPU:0 QueryBegin` of every run = 0 (re-parsed from
  `engine-.cn0.log`); the fix-2 spec's criterion is that QueryBegin value <= 1 MiB
  (`S/fix/designs/parked-bookkeeping-SPEC.md:52-53, 786, 796`).
- `grep -c 'OOM at operator'` and `'is futile'` = 0 in every engine log of all five arms under
  `S/demoplus/arms/` (dp-cn1-six, dp-cn4, dp-cn4-six-stg48, dp-cn4-q09-stg72, dp-cn4-q09-stg72-pool100).
  The only `executor metrics` lines in the campaign (fix 1's task_scheduler.cpp:280, emitted on the
  drain-after-error path) read `oom_reschedules=0 futile_aborts=0` (pool100 arm, cn0 and cn1).
- All seven failures the finding looked at are arena refusals: `dp-cn4/runs/{q05,q08,q09,q17,q18,q21}.r0.err`
  and `dp-cn4-six-stg48/runs/q09.r0.err` all contain `exchange staging arena exhausted`.
  `dp-cn4/cnlog.txt`: `fix2_retired: 4` on each of the six; `dp-cn4-six-stg48/cnlog.txt` q09.r0
  `{'fix2_skipped': 1, 'fix2_retired': 4}`; 24 WARN `retired a query's parked sender outputs` lines in
  `dp-cn4/cluster.log`, every one `still_parked=0`.
- Yesterday's 1-CN deaths: `S/perf/sf1000/cn1/runs/runs.csv` q05 101460, q08 168285, q09 232533,
  q17 103894, q18 54202, q21 114485 ms, all fail.

## Contradicted 1: "a point sample 4 ms after a query returns can still show 1.17-1.19 GB (q05, q21),
## which is the last receiver tearing down"

Measured from `dp-cn1-six/engine-.cn0.log` against `runs/runs.csv` (return = start_utc + ms):
every `[gpu_pool]` line in every q05 and q21 window is BEFORE the client return. The 1.165 GB (q05)
and 1.190 GB (q21) values are the QueryEnd of the fused-join fragment and the QueryBegin of the next
fragment of the same query (the group-by receiver, which consumes it):

    q05.r0 return 22:04:19.761: QueryEnd q10 1.165 GB at -77 ms, QueryBegin q14 1.165 GB at -69 ms,
           QueryEnd q14 0 at -33 ms, q17 0 at -21 ms, QueryEnd q20 0 at -17 ms (last sample)
    q05.r1/r2: same shape, 1.165 GB at -49 ms, last sample 0 at -7 ms
    q21.r0 return 22:05:32.927: QueryEnd q265 1.190 GB at -100 ms, QueryBegin q269 1.190 GB at -91 ms,
           QueryEnd q269 0.032 GB at -21 ms, q272 0 at -9 ms, QueryEnd q275 0 at -4 ms (last sample)
    q21.r1/r2: 1.190 GB at -98/-99 ms, last sample 0 at -4/-5 ms

So: no sample exists after a return; the sample nearest the return (4-17 ms before it) reads 0; the
1.17-1.19 GB is the query's own parked join output, not a teardown lag. The "4 ms" is the q21
`QueryEnd ... allocated=0` at -4 ms, with the sign and the value swapped.

## Contradicted 2: "Nothing OOMed anywhere in the demo-plus arms; the 4-CN failures are CN
## staging-arena refusals, not engine OOMs"

True for the three arms the finding grepped; false for the two other 4-CN demo-plus arms in the same
directory (`S/demoplus/agent-notes/EXTRA-ARM.md` items 23-24), both q09 with a 72 GiB arena:

- `dp-cn4-q09-stg72` (pool 64 GiB): fail 10522 ms, `runs/q09.r0.err` = "failed to push a staged remote
  batch from sender 0 into stream 4: std::bad_alloc: out_of_memory: not enough capacity to allocate
  memory" (`WT/experimental/starrocks/src/engine.rs:565`, the CN pushing a staged frame into the
  engine's GPU pool). Engine logs carry no OOM line; `cnlog.txt` `fix2_retired: 4`.
- `dp-cn4-q09-stg72-pool100` (pool 100 GiB): fail 8340 ms with an in-engine OOM on cn0 and cn1
  (`engine-.cn0.log:216-231`): the fragment "Pipeline #0: STREAMING_SINK(1) <- GPU_SCAN(0)" began with
  `[gpu_pool] QueryBegin query=12 allocated=7722405888`; tasks 46/47/48 each got
  "after downgrade (0 bytes freed), reservation still partial (8799623168/8881458562 bytes) ...
  proceeding with partial reservation" (gpu_pipeline_executor.cpp:309, pre-existing at 9c002f97^:265);
  then 3x "Exception during task execution: std::bad_alloc: out_of_memory: not enough capacity to
  allocate memory" (gpu_pipeline_executor.cpp:523) at 22:21:33.904-34.021, "Error executing query"
  (sirius_engine.cpp:278), `[gpu_pool] QueryEnd query=12 allocated=98574559232 bytes` of
  107374182400, `[window] end ... outcome=unwind`; CN side `retired a query's parked sender outputs
  trigger=engine_err ... cause="failed to execute fragment: std::bad_alloc: out_of_memory ..."`.

Why fix 1 did not engage there (source): the OOM was not raised inside the operator loop (no
`OOM at operator`, `WT/src/pipeline/gpu_pipeline_task.cpp:459/489`, whose catch is
`rmm::out_of_memory` at :433 and `cucascade_out_of_memory` derives from it,
`WT/cucascade/include/cucascade/memory/error.hpp:43`), nor in prepare (:638/:659), nor in the
sink-input restore (:721). It came out of the sink, where by design "the reschedule window ...
closes before sink() is entered; an OOM there propagates" (gpu_pipeline_task.cpp ~714-717). The raw
exception reached the generic `catch (const std::exception&)` at gpu_pipeline_executor.cpp:522-527,
which reports the error at once; fix 1's predicate (:426-461) is consulted only for
`task_reschedule_exception` / `oom_reschedule_exception`. Query dead 118 ms after the first OOM
(22:21:33.904 -> QueryEnd 22:21:34.022).

Consequences for the finding's text: (a) an engine OOM did happen in a demo-plus arm; (b) "fix 1
never exercised" stays true in the strict sense but for a stronger reason: the one engine OOM the
campaign captured is a class fix 1 structurally does not see; (c) q09 still OOMs at 4 CNs once the
arena stops refusing (the 1-CN sentence "the six no longer OOM" is fine as a 1-CN statement);
(d) the impact line's "deliberate OOM reproduction" must produce an operator-loop OOM (an
`OOM at operator` reschedule), not a sink OOM, or it will again say nothing about fix 1.

## Nits

- "the log's final line (22:05:54.170) is allocated=0": the literal last line is
  `[window] end ... query=315 outcome=ok`; the `[gpu_pool] QueryEnd query=315 allocated=0` is the line
  before it. The `[host_pool]` line at the same timestamp reads 72351744 bytes, constant since the end
  of q05.r0 (67108864 at the very first QueryBegin): steady, not growing.
- "Parked bytes rise ... to 36.6 GB": inference, not a parked counter. `[gpu_pool] QueryEnd
  query=262/282/302 allocated=36636669184/952`; zero lines containing `parked` in
  `dp-cn1-six/engine-.cn0.log` or `cluster.log`.
- "the retire path fired only in the 4-CN arm": the WARN/skip counters are 0 at 1 CN, correct, but fix
  2's cancel-teardown line `cancel_plan_fragment retired the query on this CN` (commit 37360a1b;
  parked-bookkeeping-SPEC.md sec. 4.8) fired 156 times in `dp-cn1-six/cluster.log`
  (108 `reason="QUERY_FINISHED"`, 48 `reason="LIMIT_REACH"`, all `released_leases=0`), counted under
  `cancels` by `S/perf/sf1000/cnlog_extract.py:52`. The teardown path was exercised at 1 CN, benignly.
- "next run's first QueryBegin reads allocated=0 for all 18": 17 have a successor.
