# 08 — Sim vs experiment, after the two implemented changes

Agent 8 report, 2026-09-03 07:30 UTC. No repository was modified; `/home/prestouser/aocsa/sirius` untouched; no gh, no push.
Shorthands: `$SP` = `/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad`,
`$SIM` = `$SP/sim` (agent 6's replay, unchanged), `$S8` = `$SP/sim8` (this report's wrapper, traces, arm runner),
`$PERF` = `/home/prestouser/aocsa/sirius-stacks-wt/perf` (lead's build: `85658f09` gate + `9a5a4da6` async dispatch).

## 1. Gate

Both conditions held. (a) A runnable simulation: `$SIM/replay.py` + `$SIM/extract.py`, traces `sf1000-{1cn,4cn}-warm.json`,
`sf1000-4cn-cold-q06.json`, `sf1000-standalone.json`, output `replay-output.txt` (06-dataflow-sim.md). (b) Two changes implemented
and re-timed: `SIRIUS_CANONICAL_FLOAT_SUMS` (default off; the per-batch canonical sort in the local aggregate is gone unless set)
and `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` (opt-in; sender-only fragments queued on the dispatch worker, the FE's first deploy
wave is not held open). Not implemented: the I/O inflight cap (03) and the memcpy rate cap (04) — E7/E8 are blocked, not run.

## 2. What was run and what was reused

- Reused: `$SP/perf/decimal-chain.log` + `$SP/step3/dec-*` (sot baseline), `retime-1.log` (`step3/patched-*`, gate off),
  `retime-2.log` (`step3/async-*`, `asyncoff-sf1000-4cn`), the 5-run A/B `perf/ab-4cn.log` (finished 07:03:25; dirs
  `perf/ab/{off-070104,on-070142,off-070214,on-070251}`), `perf/standalone/dec-sf1000/runtimes.csv`, and the f64 SF10
  `step3/sot-sf10-{1file,multi}` arms (04:02/04:05). Quent sessions from `$PERF/experimental/starrocks/.cn{0..3}/telemetry/`
  and the sot tree; 27 traces extracted into `$S8/*.json` (session uuids in each file's `session` field).
- New (6 cluster launches, all on `$PERF`, box idle-checked, outside the CI window): `$S8/run-agent8.sh` = the lead's harness
  pieces (`cluster-env.sh`, `start-cluster.sh`, `bench.sh`, `compare.py`) with 5 warm runs; arms 1–4 = SF1000/SF100/SF10 4-CN
  async ON and SF10 4-CN async OFF (`step3/agent8-sf{1000,100,10}-4cn-asyncon`, `agent8-sf10-4cn-asyncoff`, 07:11–07:13);
  arms 5–6 = q06 x15 at SF1000 4-CN and 2-CN async ON (`step3/agent8-sf1000-{4,2}cn-asyncon-q06`, 07:22 / 07:24). Env per
  07 §1 (`GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB`; SF10 `64/8/128GiB`; `QUERY_TIMEOUT=600`). Negative markers 0 on all
  six; the 4–16 `ERROR` lines are FE `ChannelPool.getChannel(): Unable to validate object` at cluster start, not query errors.
- Sim: `$S8/sim8.py` wraps `replay.py` (imported, not edited) and adds what 07 §3 asked for: `--async` (post-hoc rule for
  async-ON sessions), `--fe-overhead`, `--threads`, `standalone` grouping, and per-run Init spread / `G` / merge-host columns.
  Smoke test reproduces 06 exactly (q01 7504/7413/7090/6871, q06 1347/1237/1344/1257, counterfactuals 907/777/869/813).
- Capture caveat (cost me the "graceful stop" idea): Quent's per-type buffers flush independently and the final flush is lost
  when the CN exits on SIGTERM — even with one signal and 3–9 s to exit (`main.rs:673-681` force-exits on a second signal or
  the 15 s grace; arms 5/6 got neither and still wrote `engine_bytes=0`). Killed WARM sessions keep a prefix of 14–15 `query`
  entities and all `task` records, but the non-merge-host CNs (45 `operator` records) lose every pipeline declaration.
  `$S8/repair.py` rebuilds pipelines from `task.Created.pipeline_uuid` + `Computing.instance_name` and assigns them to
  fragments by their Init/Exit window (84 pipelines rebuilt on arm 5, 78 on arm 6); `*.fixed.json` are the repaired traces.
  Cold-restart generations (2–5 fragments) always flush completely and were used as-is.

## 3. Results

Measured = non-profiled warm median (runs in brackets, bench.sh `warm pass`); predicted = sim ms; a-priori = from a trace that
did not see the arm; post-hoc = replay of the arm's own session. Oracle: q01 `VALUES-DIFFER rows=4 maxreldiff=9.57e-04 badcells=8`
on every SR arm = known-fail (decimal cast), unchanged; q06 `MATCH` on every arm (maxreldiff 0 or ≤1.24e-16).

| Exp | knobs | measured | predicted | err % | oracle | straggler pred/meas |
|---|---|---|---|---|---|---|
| E1 | SF10 4CN, off, `fe_overhead` 175 (as shipped) | sot cold-gen r1: q01 249, q06 163 (`dec-sf10-4cn`) | 347 / 283 | +39 / +73 | known-fail / MATCH | q06 cn2/cn2 = merge host |
| E1 | SF10 4CN, off, fitted `G(1 file)=92` → `fe_overhead 72` | same runs; new arm 4 warm q01 158 [215,158,162,162,154], q06 120 [127,121,119,120,116] | 244 / 180; arm 4 q01 r1–r4 217/171/173/171 vs 215/158/162/162 | −2.0 / +10.1; +1…+8 | same | q01 variance (cn2,cn0,cn2,cn1) |
| E1 | SF100 4CN, off, 175 | sot cold-gen r1: q01 889, q06 310 | 979 / 405 | +10 / +31 | same | q06 cn2/cn2 = merge host |
| E1 | SF100 4CN, off, per-file fit held out `G(6)=101` → 81 | same | 885 / 311 | −0.5 / +0.3 | same | same |
| E2 | SF1000 4CN q06, off→on | off 1224 [1269,1224,1237,1200,1203] / 1271 / 1234 / 1243 (AB-1/AB-2/asyncoff/patched); on 849 [858,849,810,855,839] / 828 / 904 / 844 [848,836,844,849,819] / 860 (arm 5 r3–r14) | a-priori 06: 777–907 (baseline trace), 741 (N-CN); same-build off-gens counterfactual 770–841, median 797 | on 849 vs 797: +6.5; vs 741: +15 | MATCH | off: merge host = straggler 14/14 runs; on: 4/6 exact, 2 ties ≤1 ms, host ≠ straggler by rule |
| E2 | post-hoc, arm 5 warm r3–r8 (`a8-sf1000-4cn-asyncon-q06x15.fixed.json`) | 896, 862, 851, 896, 845, 890 | 898, 866, 852, 892, 843, 882 | −0.9…+0.4 | MATCH | Init spread 2.5–3.0 ms; G 190–207 |
| E2 | SF1000 2CN q06, off→on | off 1863 [1863,1921,1836]; on 1419 [1397,1419,1444] (retime-2), 1347 (arm 6, 14 runs 1275–1585) | a-priori 1212 (N-CN from 1-CN trace); 1143 (2-CN serialized r1 counterfactual) | −10 / −15 vs 1347 | MATCH | post-hoc r1–r13 +1.1…+2.5 (1362 vs 1333); spread 0.4–1.5 ms |
| E2 | SF100 4CN q06, off→on | off 275 [268,277,275]; on 215 [215,212,215], arm 2 212 [197,201,218,215,212] | 07 a-priori 185–200; sot r1 counterfactual with fitted G: 211 | −0.5 (211 vs 212) | MATCH | post-hoc cold r1 222 vs 210 (+5.9; +1.4 with the run's own G=92); spread 1.7–2.6 ms |
| E2 | SF10 4CN q06, off→on | off 120 (arm 4); on 99 [108,99,102,98,95] (arm 3) | 07 a-priori 75–85; sim relative on sot r1: 180→133 = −26 % vs measured −17.5 % | 75–85 vs 99: −16…−24 | MATCH | no warm q06 trace flushed (prefix ends in q01) |
| E2 | q01 all SFs (must be unchanged) | SF1000 on 1657/1712/1675 vs off 1811/1669/1715; SF100 332 vs 332; SF10 161 vs 162 | unchanged (scans already in wave 2) | ≤ 5.5 | known-fail unchanged | post-hoc arm 1 r1–r4 −1.7…0.0; Init spread 0.4–2.1 ms both modes |
| E2 | CN-log checks | sender RPC `close … time.idle` 1.17–1.56 s (off) → 693–791 µs (on); the 4 `translated` lines 1.28 s apart (off) → 1.3 ms (on); `transmitted … bytes=64` 3 per run at 4 CN, 1 at 2 CN, unchanged; canary 9102 still the slow peer (79.8–385.8 gbps) | — | — | — | — |
| P6 (06) | gate off, 4CN q01 regression check | AB-1 r1–r5 1862/1811/2008/1717/1721 | post-hoc 1828/1785/1987/1706/1717; Σ service per CN 25.6–26.3 s → 5.2–6.7 s, `HASH_GROUP_BY(3)` 14.9–15.8 s → 1.10–1.17 s | −1.8…−0.2 | known-fail | task-time variance, as before |
| E3 | threads 2/4/8 | not run: no `executor.pipeline.num_threads` in the derived YAML and no env knob (`tunable.rs`, `engine_settings.rs`, `compute_node_service.rs` carry only `SIRIUS_CN_{NIXL_*,RPC_TIMEOUT_SECS,DUMP_FRAGMENTS,TRANSLATE_ONLY,USE_SIRIUS_DATASOURCE,ASYNC_SENDER_DISPATCH}`) | a-priori n=2: q01 13.2–14.3 s (gate on) / 3.1–3.6 s (off), q06 2.1–2.4 s; n=8: q01 3.8–4.1 / 1.03–1.18 s, q06 0.80–0.86 s (serialized) / 0.53–0.59 s (async) | — | — | — |
| E4 | SF10 f64, 1 file vs 16 files, 4CN (reused 04:02/04:05 arms) | tasks per CN 1 vs 1 on every CN, both queries (`input_bytes` 195–297 MB per task either way); warm Δ q01 +65 (284→349), q06 +5 (285→290); cold-gen r1 Δ +56 / +2; G 85→102 (q01), 70→81 (q06) | count: 1 (fused) exact; a-priori Δ from per-file G: +26 / +26; post-hoc Δ (G shift + measured compute-end shift +26 / −10): +52 / +16 | count ✓; Δ a-priori −39 / +21 ms ✗; post-hoc −4 / +14 ms | MATCH both | — |
| E5 | standalone vs SR 1 CN, SF1000 | standalone q01 5271 [5271,5565,5269], q06 2302 [2738,2302,2128]; 1 CN q01 4954 / q06 1877 (patched), 4993 / 1790 (async on) | standalone replay −0.1…−0.3 % of its Quent window; client floor 13–18 ms; 1-CN replay +0.4 % (q01), +1.7 % (q06); Δ(standalone − 1 CN) = ΔΣservice/4 − (G + build − floor): +338 / +341 vs measured +361 / +361 | miss 20–23 ms | MATCH / known-fail | process factor: `GPU_SCAN` Σ ×1.19 (q01), ×1.18–1.52 (q06); `HASH_GROUP_BY` ×1.23 |
| E6 | q14 SF100 | standalone 248; 1 CN 331; 4 CN 283 (patched) / 279 (async); frames 9.27–9.71 MB × 12 pairs (`patched-sf100-4cn-extra`) | not replayed: `replay.py` classifies runs as q01/q06 only | — | q14 `VALUES-DIFFER 5.5e-06 badcells=1` unchanged | 2 CN and SF1000: not run |
| E7 / E8 | I/O inflight cap / memcpy rate cap | blocked, not run (03/04 are designs only) | — | — | — | — |

Per-task q06 `GPU_SCAN` service (mean ms per task, Quent `Preparing→Finalizing`), the number behind the E2 a-priori misses:
1 CN 107 (both builds); 2 CN one-at-a-time (serialized r1) 110/110; 2 CN concurrent (arm 6 warm) 125–144; 4 CN with 3
concurrent + 1 alone (AB-off r1–r5) 90–132; 4 CN all concurrent (arm 5 warm) 126–156. Compute ends follow: 4-CN remote scans
427–592 ms serialized vs 584–713 ms with all four overlapping.

## 4. Verdict against 07's pass rule

- **E2 (async sender dispatch): working.** q06 is within 20 % of the a-priori number on every arm that has one (SF1000 4 CN
  +6.5 % vs the same-build counterfactual, +15 % vs the 1-CN projection; 2 CN −10 %; SF100 −0.5 %); the SF1000 4-CN warm median
  is 828–904 < 950; q01 unchanged within 5.5 %; scan `Init` spread 0.4–3.4 ms (rule ≤ 5; was 587–962 ms); G unchanged
  (166–207 vs 169–213); post-hoc within 2 % at SF1000 4 CN (−0.9…+0.4 %) and cold r1 (+0.3 %). Misses: SF1000 2-CN post-hoc is
  +1.1…+2.5 % (systematic: the constant `fe_overhead 175` is 20–30 ms above these runs' G 166–182 minus build); SF100 post-hoc
  +5.9 % with the fitted G (+1.4 % with the run's own G — G at SF100 varies 92–122 across generations); SF10's absolute
  a-priori (07's 75–85) misses 99 by 16–24 %, while the sim's relative prediction (−26 %) is close to the measured −17.5 %.
  The straggler is no longer the merge-hosting CN by rule (host = straggler in 14/14 serialized runs, 2/6 async warm runs, both
  ties); predicted = measured straggler in 4/6 async runs, the two misses are 1-ms ties.
- **E1 (FE floor as a function of files): working.** With `G = 90.3 + 1.75·files` fitted on SF10 (92) and SF1000 (mean of q06
  ~180 / q01 ~210), the held-out SF100 prediction 101 is within −5/−16 ms of the measured 106/117 (rule ±20); the per-log10(SF)
  form gives 143.5 and fails (+26/+37). Replays with the fitted floor land −2.0/+10.1 % (SF10 q01/q06) and −0.5/+0.3 % (SF100);
  the shipped constant fails SF10 by +39/+73 % and SF100 by +10/+31 %, confirming 07 §6.1. Merge host = straggler on every
  serialized q06 run at all three SFs.
- **E5 (process shape): working (post-hoc).** The standalone process replays within 0.3 % of its own Quent window (client floor
  13–18 ms); the standalone-minus-1-CN difference is explained within 20–23 ms (rule ±100) by ΔΣservice/4 + G. The a-priori
  sign is wrong as 07 expected; the process factor to record is `GPU_SCAN` ×1.19–1.52 and `HASH_GROUP_BY` ×1.23 slower per task
  in the DuckDB-client process than in the CN.
- **E4 (split assignment): working for the count, not for Δ a-priori.** 1 fused `GPU_SCAN` task per CN in both layouts on
  every CN (exact); the warm Δ (q01 +65, q06 +5) is not predicted by the per-file G alone (+26 for both — the measured G shift
  was +17/+11, so the slope is 0.7–1.1 ms/file on this f64 data, not 1.75); post-hoc the Δ is within 4/14 ms.
- **P6 regression check (gate off): working.** 4-CN q01 post-hoc −1.8…−0.2 % with the new service times.
- **E3: not run** (no thread knob reachable without the full-YAML `up.sh` launcher; a-priori numbers stand as predictions).
  **E6: measured only, not replayed.** **E7/E8: blocked.**
- Overall: the sim's structure (list scheduling, two-wave deploy, hops, barriers) is right for q01/q06 at every SF and CN
  count tested — post-hoc ≤ 2.5 % on 40 replayed runs; its two inputs that move under the experiments are the FE floor
  (a function of files/levels, not a constant) and the per-task service under concurrent CN readers.

## 5. What the sim got wrong, and the smallest fix

1. `fe_overhead_ms=175` is an SF1000 value. Measured `G` (FE − Quent span): SF10 92/92, SF100 106/117, SF1000 q06 166–207,
   q01 201–263 (one more fragment level ≈ +30 ms). Fix: `fe_overhead(files, levels) = 70 + 1.75·files + 30·(levels−2)`
   (i.e. `G = 90 + 1.75·files`), one line in `K` plus a `--files` argument; refit the slope per dataset (0.7–1.1 on f64 SF10).
2. The N-CN projection assumes per-task service independent of N. Concurrent CN scans of the same dataset inflate `GPU_SCAN`
   service ×1.2–1.35 (107 → 125–144 at 2 concurrent, 126–156 at 4). That is the whole 2-CN a-priori miss (1212 vs 1347) and
   most of the 4-CN one (741 vs 849). Smallest fix: multiply the 1-CN service by a measured factor 1.0/1.25/1.33 for 1/2/4
   concurrent readers in the N-CN section (one line). The right fix is a shared disk server in front of `GPU_SCAN` — which is
   exactly the resource `03-io-throttle.md` would make explicit; E7 would calibrate it.
3. Cold first runs (r0) miss by −20…−57 %: the sim replays measured service (compute ends match) but `G` is 730–1044 ms on a
   cold-restart r0 (FE `FILES()` planning), not modelled. Out of 07's scope; note only.
4. `replay.py` groups runs by a `sirius_ffi` result fragment and classifies q01/q06 by `HASH_GROUP_BY`; q14/q03 (E6) and
   standalone sessions need the grouping in `$S8/sim8.py` (standalone done; join shapes not).
5. Not a sim error but the largest practical obstacle: the exporter's final flush is lost on SIGTERM (§2). The instrumentation
   fix is a periodic flush or a flush on `shutdown signal received` before `tearing down Sirius engine` (agent 6 open issue 2,
   01 §5.9); until then use `$S8/repair.py` and cold-restart generations.

## 6. Files

`$S8/sim8.py` (wrapper; `replay|standalone|service`), `$S8/repair.py`, `$S8/run-agent8.sh`, `$S8/run-agent8b.sh`
(single-SIGTERM stop), `$S8/agent8-arms.sh`, `$S8/agent8-arms2.sh`, logs `$SP/perf/agent8-arms.log`, `agent8-arms2.log`,
`agent8-arms2b.log`; traces `$S8/*.json` (`a8-*` = new arms, `*-cold-*` = cold-restart generations, `sf1000-4cn-perf-off{A,B}`
= the A/B off generations, `*.fixed.json` = repaired); arm outputs `$SP/step3/agent8-*/{summary.txt,cluster.log,out.csv,*.out}`.
Reproduce a row: `python3 $S8/sim8.py replay --quiet --async $S8/a8-sf1000-4cn-asyncon-q06x15.fixed.json --fe-ms q06:2566,1958,1387,896,862,851,896,845,890,812,878,825,843,876,858`.

## 7. Open issues for the lead

1. Every WARM Quent capture on this box is a prefix (14–15 `query` entities) unless the CN happens to tear down inside the
   SIGKILL window; the two complete WARM generations on disk are the A/B off runs (07:01:10, 07:02:20). `repair.py` recovers
   pipelines from tasks but not `data_batch` sizes for the missing `operator` declarations (hop bytes fall back to `drain_ms`).
2. `G` at SF100 ranges 92–122 across generations; with a ~200 ms query the sim's ±20 % rule is dominated by that spread.
3. The async-ON q06 gain shrinks with SF as the sim predicts (SF1000 −31 %, SF100 −22 %, SF10 −17.5 %); at SF10 the remaining
   99 ms is 92 of `G` plus one 45–65 ms scan — the floor, not the CN, is the next target there.
4. `cn-distribution.py` task shares were not re-checked on the new arms (not in `run-agent8.sh`); scan task counts per CN are
   in the table (27/27/27/27, 16–17, 3, 2, 1) and unchanged between modes.
