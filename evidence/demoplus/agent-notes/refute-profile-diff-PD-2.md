# Refutation check of PD-2 ("every other profile difference is tree lineage, not the fixes")

Written 2026-09-04. Read-only check of `demoplus/arms/dp-cn4` vs `perf/sf1000/cn4` and the demo-plus source
(`/home/prestouser/aocsa/sirius-stacks-wt/demo-plus` @ 281b13bc). Verdict: NOT refuted on substance; the
"left no trace" wording and two evidence items need correcting.

## What verifies (measured)

- Plan chains: multiset of engine `Pipeline #N` chains identical for 44 of the 45 common runs
  (`plan-shapes-perf-cn4.txt` vs `plan-shapes-dp-cn4.txt`, re-parsed pairwise). All 15 queries identical at r1
  as claimed. The one mismatch, q22.r2 (107 vs 95 pipelines), is a capture artifact: `dp-cn4/engine-.cn2.log`
  ends mid-plan (`Pipeline #3: SORT_SAMPLE (id=6) -> SORT_PARTITION `) and `engine-.cn3.log` ends in
  `Pipeline #5: STREAMING_SI`; q22.r0/r1 are 107 in both arms.
- Fragment counts per query identical at r1 (`dp-cn4/cnlog.txt` vs `perf/sf1000/cn4-cnlog.txt`: 9/49/17/17/5/39/22/32/17/17/13/22/13/26/18).
- nixl frames: `tx` identical for every common run (`prof-perf-cn4.json` vs `prof-dp-cn4.json`); `tx_GB`
  identical to >= 5 digits except q02 (45.14/43.60/45.66 vs 42.59/44.63/43.61, both arms vary) and q11
  (0.3989-0.4003 vs 0.3998-0.4037, 0.2-1%).
- Pacing: `percn-q03-r1.txt` baseline sender ec5f TX at 903.5 / 997.7 / 1091.4 ms; dp sender_id=3 at 838.8 /
  932.3 / 1026.0 ms, frame sets 4.82-4.89 GB in both. Numbers in the finding are exact.
- Reservation/peak numbers quoted (q01 943.64/743.12 both; q11 380.18/5.59 vs 379.32/5.84; q03 1042.36/345.32
  vs 1039.43/345.32) are what `compare-warm-perf-vs-dp.txt` says.
- Fix counters as extracted: `fix2_skipped=fix2_retired=fix4_fused=fix4_skipped=0` and all engine counters 0 on
  every passing run in `dp-cn4/cnlog.json`; `fix2_retired=4` on each of q05/q08/q09/q17/q18/q21 (24 WARN
  `retired a query's parked sender outputs` lines total, all `trigger=cn_err`).
- Fix 1: 0 lines matching `futile|executor metrics|dropping task|already failed|reschedule (retry` in any
  `dp-cn4/engine-.cn*.log`. Baseline `perf/sf1000/cn4/engine-.cn0.log` has 17,900 `reschedule (retry` lines,
  all timestamped 13:03-13:18 on 2026-09-03 (the earlier 1-CN campaign appended to the same file); the cn4
  windows are 13:19:30-13:20:37, so "oom_reschedules=0 on all 45 baseline runs" holds.

## Corrections

1. **Fix 2 did leave a trace on every passing run; its measured effect is nil.** The dp CN's
   `cancel_plan_fragment` handler (`compute_node_service.rs:340-400`) now runs `results.cancel`,
   `retire_receiver`/`release_sources` and `executor.retire_query(RetireTrigger::Cancel(..))` on every FE cancel and
   logs `cancel_plan_fragment retired the query on this CN ... reason=<..> released_leases=N` (INFO; spec
   `parked-bookkeeping-SPEC.md` 4.8 "cancel" row). `dp-cn4/cluster.log` has 1,160 such lines: 735 QUERY_FINISHED +
   264 LIMIT_REACH on the 48 passing runs (one per fragment, e.g. q02.r1: 49 fragments, 49 cancels) + 161
   INTERNAL_ERROR on the six failing runs. The baseline logs `acknowledging cancel_plan_fragment (best-effort: no
   engine-side abort yet)` for the same RPCs (948 on its 45 runs). `cnlog_extract.py` counts these under `cancels`
   because its `'cancel_plan_fragment' in l` branch precedes the `fix2_*` branches, so `fix2_*=0` does not show
   fix 2 was inert, only that nothing was parked.
   Measured effect on the 48 passing runs: `released_leases=0` on all 999 lines; 0 `skipping fragment of a retired
   query`; 0 retire WARNs (i.e. `retire found nothing parked`, DEBUG, not captured); 0 fragments finished after
   the first cancel of their run; first cancel minus last `fragment run finished` = 0 / 1 / 60 ms (min/median/max;
   baseline 0 / 2 / 75). Cancel RPC `time.busy` median 12.4 us (p90 31.9, max 225) vs baseline 7.2 us (p90 17.9,
   max 72.1), 22.0 ms vs 9.1 ms summed over the whole arm. Query tail (`timeline-*.txt`, 30 warm runs each):
   res_fin -> client_end 8.3 ms (dp) vs 8.7 ms (base); cancel_last -> client_end 3.5 vs 3.1 ms. This is exactly
   spec 5.3's prediction ("one non-blocking channel send, one RetiredQueries::mark, one empty retire_receiver
   and one empty engine retire (DEBUG line). No plan, memory or ordering change").
2. **`fix4_skipped=0` is not evidence that fusion was inert.** `try_defer_sender`
   (`compute_node_service.rs:975-1105`) logs every policy decline at `debug!`; the arm captured INFO/WARN only
   (33,715 INFO, 105 WARN, 0 DEBUG lines in `dp-cn4/cluster.log`). The reason nothing fuses at 4 CNs is in the
   source: `fusion::sender_shape` refuses any sink without exactly one destination
   (`crates/starrocks-plan-translator/src/fusion.rs:208-212`, `NotSingleDestination`; the unit test at `:1498`
   uses `destinations: 4`), which every HASH_PARTITIONED leaf has at 4 CNs; the single-destination UNPARTITIONED
   leaves (q06's four lineitem senders, `outputs=1`) are declined by "leaf mode fuses HASH_PARTITIONED sinks only"
   (`:1039-1046`). `fix4_fused=0` (INFO line, would have been captured) is the measured fact; "fuses nothing at
   4 CNs" is correct, the cited counter is not what shows it.
3. **"Identical" reservation vs peak is loose**: `compare-warm-perf-vs-dp.txt` req_GB q02 543.21 -> 546.96,
   q10 1208.19 -> 1216.13, q22 258.51 -> 254.64 (about 1%); alloc_GB q11 5.59 -> 5.84 (+4%), q20 317.45 -> 320.92,
   q15 480.73 -> 482.52. Small and not fix-shaped, but not identical.
4. **48 passing dp runs, not 45.** q16 passes three times in dp (`runs.csv` rows=27840; `demoplus/oracle-q16.log`
   MATCH; `dp-cn4/compare.txt` says NO-ORACLE) and has no cn4 baseline (`perf/sf1000/results.md` cn4 column '-';
   cn1 failed in 356 ms with `"multi_distinct_count" has a partial state this translator does not model`).
   Both trees still carry the refusal test (`partial_state.rs` `unmodeled_functions_are_refused`), so q16's pass
   is not attributed here; it is a pass/fail difference between arms the finding does not mention.

## Bottom line

The claim's mechanism (differences come from the dev-based engine + carved CN, not fixes 1/2/4) survives: no
fix changed a plan, a byte moved, a frame count, a reservation, or a peak on the passing runs, and fix 2's
per-cancel retire ran after the last fragment finished with nothing to release. Reword "left no trace" to
"ran only its empty end-of-query cancel path (999 retires, released_leases=0)" and replace the `fix4_skipped=0`
evidence with the single-destination rule.
