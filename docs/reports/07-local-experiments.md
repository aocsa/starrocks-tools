# 07 — Local experiments that test the data-flow sim (design only; nothing run)

Agent 7 report, 2026-09-03. Read-only on `$SOT` = `/home/prestouser/aocsa/sirius-stacks-wt/sot` and
`$Q` = `/home/prestouser/aocsa/sirius-stacks-wt/quent` (both `d24f02c4`); no builds, no GPU runs, no
benchmark runs. Inputs: `06-dataflow-sim.md` (the replay in `$SIM` =
`/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/sim`),
`01-trace-schema.md` §3 gaps, `03-io-throttle.md`, `04-memcpy-throttle.md`, the lead's arms in
`scratchpad/perf/decimal-chain.log` + `scratchpad/report-notes.md`, and the Quent sessions under
`$SOT/experimental/starrocks/.cn{0..3}/telemetry/`. The only computations done here are read-only
passes over ndjson/JSON already on disk (§0); every number below is either quoted from those files or
derived from them by the stated arithmetic.

Shorthands: `$SP` = the scratchpad root above; `$SR` = `$SOT/experimental/starrocks`;
`$PERF` = `/home/prestouser/aocsa/sirius-stacks-wt/perf` (the lead's patched worktree: uncommitted
`SIRIUS_CANONICAL_FLOAT_SUMS` gate and `SIRIUS_CN_ASYNC_SENDER_DISPATCH`, both verified present by grep
at `$PERF/src/op/aggregate/aggregate_op_util.cpp:229` and
`$PERF/experimental/starrocks/src/compute_node_service.rs:263-301`).

## 0. Two facts found while designing (from sessions already on disk) that change the experiments

The complete 4-CN sessions are the **cold-restart generations** (the newest session per CN, engine
`Init` 05:43:46 / 05:44:21 / 05:49:02 / 05:49:39 / 05:58:40 / 05:59:28 UTC); the WARM generations are
killed by `stop-cluster.sh` (SIGTERM, 3 s, SIGKILL) and only some CNs flush their `query` file
(e.g. cn0 `01a065cb-0f73…` has 0 query entities, cn1 `01a065cb-0f72…` has 15). Per run: scan
fragments' `GPU_SCAN` task count and last `task` `Exit` offset per CN, the scan `query` `Init` offsets,
the span `first scan Init → result query Exit`, the FE ms from `decimal-chain.log`, and
`G = FE − span`:

```
generation (4 CN)     run  scan tasks@compute-end per CN (ms)              scan Init offsets   span   FE    G
SF10  q01  05:43:46   r0   cn0 1@165  cn1 1@165  cn2 1@166  cn3 1@173      0 0 0 0            239    971   732
                      r1   cn0 1@95   cn1 1@90   cn2 1@100  cn3 1@97       0 0 0 0            157    249    92
SF10  q06  05:44:21   r0   cn0 1@512  cn1 1@373  cn2 1@374  cn3 1@378      396 0 0 0          521   1444   923
                      r1   cn0 1@25   cn1 1@25   cn2 1@63   cn3 1@27       0 0 41 0            71    163    92
SF10  q06  2 CN 05:42 r1   cn0 1@62   cn1 1@26                             40 0                66    137    71
SF100 q01  05:49:02   r0   cn0 3@1433 cn1 3@1210 cn2 3@1213 cn3 3@1369     0 0 0 0           1501   2314   813
                      r1   cn0 3@697  cn1 3@739  cn2 3@714  cn3 3@727      0 0 0 0            772    889   117
SF100 q06  05:49:39   r0   cn0 2@655  cn1 2@428  cn2 2@437  cn3 2@425      462 0 0 0          660   1596   936
                      r1   cn0 2@98   cn1 2@91   cn2 2@198  cn3 2@100      0 0 123 0          204    310   106
SF1000 q01 05:58:40   r0   cn0 27@7191 cn1 27@6809 cn2 27@6775 cn3 27@7346 0 0 0 0           7396   8223   827
                      r1   cn0 27@6417 cn1 27@6660 cn2 27@6492 cn3 27@6514 0 0 0 0           6702   6912   210
SF1000 q06 05:59:28   r0   cn0 16@2361 cn1 16@1309 cn2 17@1339 cn3 17@829  1365 0 0 0        2367   3448  1081
                      r1   cn0 17@1592 cn1 16@1534 cn2 17@2229 cn3 16@1213 0 0 1618 0        2236   2422   186
```

1. **`fe_overhead_ms=175` is not a constant.** Warm `G` is 71–92 ms at SF10 (1 lineitem file), 106–117
   at SF100 (6 files), 186–213 at SF1000 (60 files; agent 6's col. G). The replay therefore
   over-predicts every SF10 arm by ~80 ms on a 118–216 ms query (SF10 4-CN q06 warm median 118: the
   sim would say ≈ 71 + 175 + hops ≈ 250) and its "SR-vs-standalone floor" claim (P5) needs the
   SF-dependent value. Cold first runs carry a further 640–900 ms of `G` (732–1081) that is FE-side
   and outside every CN window. → E1.
2. **Scan work is balanced per CN at every SF** (1 / 3 or 2 / 27 or 16–17 tasks per CN); the
   `cn-distribution.py` `task` column (e.g. SF100 4-CN `12 6 12 6`) counts receiver/result-fragment
   tasks too and must not be read as scan imbalance. The q06 merge-hosting CN's scan `Init` lag is
   41 / 123 / 1365–1618 ms at SF10 / SF100 / SF1000, i.e. the remote scan's duration, at every SF and
   also at 2 CN (40 ms at SF10). → E2's prediction applies at all three SFs.

## 1. Protocol shared by every experiment

- **Box etiquette.** Before any GPU work: `while pgrep -f 'run-decimal-chain.sh|run-perf-retime' >/dev/null; do sleep 60; done`;
  never inside 02:00–03:50 UTC (nightly CI owns all 4 GPUs); `nvidia-smi --query-gpu=index,memory.used --format=csv,noheader`
  must show < 1000 MiB on every GPU an arm will use (CN *i* → GPU *i*; standalone → GPU 0). One arm at
  a time; nothing else on the box.
- **Arm runner.** `NUM_CNS=<n> bash $SP/run-step3.sh <worktree> <dataset> <tag>` with the lead's env
  (`GPU_MEM=100GiB STAGING=16GiB HOST_MEM=160GiB QUERY_TIMEOUT=600 COLD_TIMEOUT=900 SIRIUS_EXCHANGE_STAGING_BYTES=16GiB`;
  SF10: `64GiB/8GiB/128GiB`). It prints `== WARM bench:` (bench.sh, 3 timed runs after run 0) as
  `qNN rN warm pass NNNms rows=R` and `== COLD-RESTART bench:` (1 run, cluster relaunched before each
  query). **Non-profiled time** = median of the three `warm pass` values.
- **Oracle.** `compare.py <out_dir> $SP/oracle/<dataset>` (`$SOT/bench/rtxpro6000-2gpu/tools/compare.py`,
  rel tol 1e-6) prints `qNN MATCH rows=R maxreldiff=…` or `qNN VALUES-DIFFER rows=R maxreldiff=… badcells=N`.
  On decimal data q01 is `VALUES-DIFFER rows=4 maxreldiff=9.57e-04 badcells=8` on every arm (the
  `1-0.07` DECIMAL cast truncation, pre-existing) and q14 `VALUES-DIFFER rows=1 maxreldiff=5.5e-06 badcells=1`;
  **pass = the same verdict, rows and badcells as the baseline arm**, not `MATCH`. New datasets need
  `oracle.py <queries_dir> <tpch_data> <out_dir> qNN…` first (CPU-only DuckDB; fine inside the CI window).
- **cn-distribution.** `cn-distribution.py --dir <wt>/experimental/starrocks --prefix .cn --metric rows`
  → `WORK DISTRIBUTION` table, columns `CN run-uuid operator task data_batch query channel share%`
  (`share%` on `task`), analysing the **newest** session per CN (= the last cold-restart generation);
  heed its `CONTAMINATION WARNING` and use `--all-runs` only to see older generations.
- **Canary.** From `cluster.log`: `nixl bandwidth canary peer=127.0.0.1:91x2 gbps="…" bytes=16777216 floor_gbps=2.0`
  (SF1000 4-CN: 9102 `77.4/92.2/357.6`, 9112 `107.2/388.6/397.8`, 9122 `213.6/372.1/391.0`,
  9132 `341.8/348.8/380.5`) and `transmitted batches via nixl stream_id=S sender_id=I dest=127.0.0.1:91x2 batches=B bytes=N`;
  plus the `negative markers:` line (all five counters must stay 0).
- **Quent.** Session = `<engine dir>/telemetry/<uuid>/`. Durations: `task` `Preparing.timestamp → Finalizing.timestamp`
  (service), consecutive `Computing.{instance_name,current_operator_id,input_bytes}` (per operator),
  `query` `Init`/`Executing`/`Exit`; bytes: `data_batch.Stationary.memory.capacity.capacity_bytes`,
  `batch_placement.BatchRegistered.tier.capacity.capacity_bytes` (+ `"origin":"reschedule_intermediate"`
  for relayed/remote inputs); threads: `executor_thread` entities `gpu_pipeline-gpu0-exec-<i>`.
  Extract/replay: `python3 $SIM/extract.py --cn-from-path <session dirs> -o x.json` then
  `python3 $SIM/replay.py x.json [--onecn …] --fe-ms qNN:a,b,c,d`. **Capture hygiene:** a session is
  usable only if `engine/*.ndjson` holds both `Init` and `Exit`; for sim captures give the CN more than
  `stop-cluster.sh`'s 3 s before SIGKILL (scratchpad script change), and until the CN sets labels every
  fragment is `sirius_streaming_fragment`/`sirius_ffi`, so runs are aligned by time order as
  `replay.py` does (a result fragment closes a run). Quent is on in every timing run today
  (`enable_quent` defaults `true`; the derived YAML sets only `output_directory`), so "non-profiled"
  currently means "with the exporter"; E3's control arm measures that tax.
- **Two predictions per experiment.** *A-priori*: made before the run from the existing traces and the
  sim's structural rules (this is the real test). *Post-hoc*: replay of the new session with its own
  measured service times (tests only the structure: list scheduling, barriers, deploy rule, hops).
  Default pass rule unless the experiment says otherwise: a-priori FE wall within **20 %** of the warm
  median; post-hoc within **2 %** and the **same straggler CN** (scan fragment with the latest last-task
  `Exit`) on every run. An a-priori miss with a post-hoc hit means the *service-time input* moved
  (contention, page cache, process shape), not the model; both missing means the structure is wrong.

## 2. Experiments

### E1 — SF sweep at fixed 4 CN: make the FE floor a function, then re-predict SF10/SF100

- **Change:** SF 10 / 100 / 1000 (lineitem 1 / 6 / 60 files) at NUM_CNS=4, q01 and q06; nothing else.
- **Sim must predict:** with `fe_overhead_ms` fitted on SF10 and SF1000 only (two candidate forms:
  `a + b·files` → a≈90, b≈1.9 ms/file; or `a + b·log(SF)`), the SF100 warm `G` (measured 106–117 ms),
  then the SF10 and SF100 4-CN warm medians (measured q01 216 / 903, q06 118 / 309) from their own
  sessions: SF10 q06 = one 25–63 ms task per CN + 41 ms deploy lag + hops + `G`; SF100 q06 = two-task
  scans (91–198 ms) + 123 ms lag + `G`.
- **Measure:** nothing new is required — the six cold-restart generations in §0 and `decimal-chain.log`
  are the data; one optional confirm run per SF with a graceful stop so the WARM generation is complete
  (SF10 ≈ 1.5 min, SF100 ≈ 2 min, SF1000 ≈ 2.5 min of box time).
- **This week:** yes — the fit is zero-GPU and can be done today; the confirm runs any evening slot.
- **Pass:** fitted `G` predicts SF100 within ±20 ms; replay of the SF10 and SF100 4-CN sessions with the
  fitted `G` lands within 20 % of the warm medians for both queries (today's constant fails SF10 by
  > 50 %); the q06 straggler is the merge-hosting CN on every run at every SF.

### E2 — Async sender dispatch A/B (the lead's `SIRIUS_CN_ASYNC_SENDER_DISPATCH`)

- **Change:** `$PERF` CN with the sender RPC returning at dispatch vs `SIRIUS_CN_ASYNC_SENDER_DISPATCH=0`;
  SF1000 and SF100 at 2 and 4 CN (queued in `$SP/perf/run-perf-retime2.sh`), plus SF10 4 CN for the
  floor. Same data, same YAML, same build otherwise (`SIRIUS_CANONICAL_FLOAT_SUMS` unset in both arms).
- **Sim must predict (a-priori, already printed by `replay.py` as `pred_async_sender`):** SF1000 4-CN q06
  777–907 ms per run (N-CN projection from the 1-CN trace: 741) against 1219–1332 today; SF1000 2-CN
  q06 ≈ 1212 (today 1879–1976; measured lag 830–850 ms); SF100 4-CN q06 ≈ 309 − 123 + 5 ≈ 185–200;
  SF10 4-CN q06 ≈ 118 − 41 ≈ 75–85; **q01 unchanged** (7504/7413/7090/6871; SF100 903) because its
  scans are already in deploy phase 2. Straggler: no longer the merge-hosting CN by rule; it becomes
  task-time variance like q01.
- **Measure:** warm medians and cold-restart run; oracle verdicts identical to baseline; Quent: all
  scan `query` `Init`s of a q06 run within 5 ms (today 41 / 123 / 1618 ms apart), `G` unchanged
  (a change in `G` would mean the FE, not the CN, moved); CN log: sender `exec_plan_fragment … close
  time.idle=` drops from the scan duration to milliseconds; `transmitted batches via nixl … bytes=64`
  count unchanged (6 pairs at 4 CN); `cn-distribution` `task` shares unchanged; canary unchanged.
- **This week:** yes — the lead's script covers SF1000/SF100 × 2/4 CN and the off-control (~25 min);
  add SF10 4 CN (1.5 min).
- **Pass:** q06 within 20 % of the a-priori number at each SF/CN arm **and** the SF1000 4-CN warm median
  falls below 950 ms; q01 within 5 % of its baseline (a q01 change means the dispatch path did more
  than the sim thinks); `Init` spread ≤ 5 ms; post-hoc replay of the new sessions within 2 %.

### E3 — Executor-thread sweep (2 / 4 / 8) at SF1000 4 CN, with Quent-tax and launcher controls

The proxy for the not-yet-implemented I/O inflight cap: `03-io-throttle.md` §0 shows the number of
scan reads in flight per CN is `pipeline.num_threads` (4) today.

- **Change:** `sirius.executor.pipeline.num_threads: 2 | 4 | 8` (key documented at
  `docs/super-sirius/configuration.md:84,785`; the derived YAML deliberately omits this pool, so use a
  full YAML through `$SR/benchmarks/pinned/up.sh` with `CONFIG_DIR` pointing at hand-written
  `cn<i>.yaml`: the lead's `$SP/perf/sirius-1gpu-100.yaml` shape plus `memory.gpu.usage_limit_bytes:
  "100GiB"`, `memory.host.capacity_bytes: "160GiB"`, the `cpu_affinity` lists copied from
  `$SR/.cn<i>/derived-sirius-config.yaml`, `telemetry.output_directory: ".cn<i>/telemetry"`;
  `STAGING=16GiB`). Control arms: (a) full YAML, 4 threads, Quent on — must reproduce the derived-flag
  baseline; (b) same with `telemetry: {enable_quent: false}` — the exporter tax.
- **Sim must predict (a-priori; computed here by list-scheduling the SF1000 4-CN WARM per-task service
  times on n threads, service assumed independent of n):** q01 straggler scan compute 12.9–14.0 s at
  n=2 (→ FE ≈ 13.2–14.3 s), 6.6–7.3 s at n=4 (today), 3.56–3.86 s at n=8 (→ FE ≈ 3.8–4.1 s); q06 with
  today's serialized deploy: n=2 → remote max 1.09–1.36 s + coordinator 0.79–1.18 s + 0.21 s ≈ 2.1–2.7 s,
  n=8 → 0.33–0.38 + 0.25–0.33 + 0.21 ≈ 0.8–0.9 s. `replay.py` needs a `--threads N` override (one
  line) to print the exact per-run values. Prior in-process data says the service assumption is
  optimistic: `configuration.md:785` records q01 −16 % at 8 threads, not −45 %.
- **Measure:** warm medians per n; oracle verdicts; Quent: `executor_thread` count = n, per-task
  `Preparing→Finalizing` distribution and Σ `Computing` per operator (`GPU_SCAN(0)`, `HASH_GROUP_BY(3)`)
  per n — the service inflation is the number the sim is missing; `cn-distribution` shares unchanged;
  canary unchanged.
- **This week:** yes — 3 sweep arms + 2 controls ≈ 20 min of box time plus writing five YAMLs and a
  scratchpad `start-cluster` variant that launches `up.sh` instead of `cluster8.sh`.
- **Pass:** control (a) within 3 % of 7088 / 1245 (else the launcher confounds and the sweep must be
  read relative to (a)); control (b) within 3 % of (a) (else every sim number inherits an exporter tax
  that must be subtracted); a-priori within 20 % at n=2 and n=8; post-hoc within 2 % with the same
  straggler. If n=8 misses a-priori but hits post-hoc, the fix is a measured `service(n)` factor
  (Σ service at n / Σ service at 4), not a structural change.

### E4 — One file vs many files at fixed SF (the FE split-assignment rule)

- **Change:** f64 SF10 `tpch_sf10_f64_1file` (1 lineitem file) vs `tpch_sf10_f64_multi` (16 files),
  and f64 SF100 `tpch_sf100_f64_1file` vs `tpch_sf100_f64_multi` (both under
  `/scratch/prestouser/aocsa/demo-q1q6/`; the SF100 pair has no oracle yet), 4 CN, q01 and q06.
- **Sim must predict:** per-CN `GPU_SCAN` task counts from the FE rule (contiguous byte ranges of the
  table across instances, cut into tasks at `scan_task_batch_size`; confirmed balanced at 1/3/27 per
  CN in §0) — SF10 1 file → 1 task per CN; 16 files → 4 files (~104 MB each) per CN, which the
  coalescer either fuses into **1 task per CN** (`batch_within_file_boundaries=false` on the query
  path, `03-io-throttle.md` §1, `parquet_gpu_ingestible.hpp:98`) or leaves as **4 short tasks** on 4
  threads — either way **no change in compute end**, and the exact-count pass rule below decides which
  rule the sim must encode; the only predicted wall delta is `G`: +15 files × b ≈ +25–30 ms if E1's
  per-file form holds, ≈ 0 if `G` is per-SF. SF100: 1 file → 3 tasks per CN as today; multi → between
  ceil(files per CN × file bytes / `scan_task_batch_size`) (fused) and files per CN (unfused), compute
  end = max over a CN's 4 threads.
- **Measure:** SF10 already ran (`$SP/step3/sot-sf10-1file` 04:02, `sot-sf10-multi` 04:05: warm q01
  284/284/323 vs 349/356/305, q06 288/285/280 vs 306/290/284; oracles `MATCH`); their sessions are in
  `.cn*/telemetry` (04:02–04:05) if flushed. New: SF100 pair (oracle.py first, then 2 arms), and one
  run with `SIRIUS_CN_DUMP_FRAGMENTS=<dir>` to read the byte ranges the FE actually assigned
  (`compute_node_service.rs:899-905`). Quent: task count per scan fragment per CN, `Computing
  GPU_SCAN(0)` `input_bytes` per task (the planner's split estimate), compute end per CN; `G` per run.
- **This week:** yes — SF10 part is offline today; SF100 pair ≈ 10 min CPU oracle + 2 × 2 min arms.
- **Pass:** predicted task count equals the Quent count on **every** CN (exact); predicted
  Δ(1file → multi) of the warm median within ±15 ms of the measured Δ for each query (SF10 today: q01
  +65, q06 +5 — one of the two candidate `G` forms must fit both once the sessions are replayed); per-CN
  compute end within 20 %.

### E5 — Standalone vs SR 1 CN on the same GPU at SF1000 (process shape as a service-time input)

- **Change:** the process around the same engine: DuckDB client + extension on GPU 0
  (`$SP/perf/standalone_run.py`, `sirius-1gpu-100.yaml`) vs one CN behind the FE (`NUM_CNS=1`), both
  on the `$PERF` build so `SIRIUS_CANONICAL_FLOAT_SUMS` is off in both (the CN's FP64-lowered sums
  otherwise sort every batch; standalone sums DECIMAL and never did).
- **Sim must predict:** standalone wall = list-schedule of its own `GPU_SCAN` tasks on 4 threads +
  a client floor bounded by the SF10 standalone q06 (45 ms total); SR 1-CN = the same service model
  + `build_ms` 20 + relay + result + `G(SF1000)` ≈ 200 → a-priori **1 CN slower than standalone by
  ≈ 230 ms**. Measured today says the opposite (patched 1-CN q01 4954 vs standalone 5271; q06 1877 vs
  2302; standalone q06 spread 2128–2738), and `report-notes.md` already shows the per-task cause
  (q06 `GPU_SCAN` avg 103.9 ms on the CN vs 123–158 ms standalone). The experiment's job is to make the
  sim's service-time input process-aware and to check that `G` is the *only* structural difference.
- **Measure:** `runtimes.csv` (standalone, 3 warm iterations) and warm medians (SR); oracles; Quent:
  standalone session (label the iterations with `CALL sirius_set_query_label('q01_r1')` from the
  scratchpad runner so they are not all `unnamed_query`) vs the CN session — per-task service
  distribution and Σ `Computing` per operator, `input_bytes` per task (same `scan_task_batch_size`?
  the default is derived from the GPU's memory in both), `executor_thread` count; standalone `query`
  `Executing→Exit` vs client wall (the client floor).
- **This week:** yes — ≈ 10 min box time (standalone SF1000 q01 ≈ 5 s × 4, q06 ≈ 2 s × 4; 1-CN arm 4 min).
  Needs a ~30-line replay extension: treat an `unnamed_query`/labelled standalone fragment as
  scan+result in one process with no deploy and no hop.
- **Pass:** post-hoc replay of each process within 20 % of its own wall (standalone has never been
  replayed); the standalone−1CN difference explained within ±100 ms by (Σ service difference)/4 + `G`;
  the a-priori sign is expected to be wrong — record the per-operator service ratio as the sim's
  process factor.

### E6 — A bandwidth-shaped exchange: q14 at SF100 and SF1000, 1 / 2 / 4 CN

The only multi-MB hop measured so far: SF100 4-CN q14 `transmitted batches via nixl stream_id=2
sender_id=0 dest=127.0.0.1:9112 batches=2 bytes=9279872` … 12 sender/dest pairs of 9.27–9.71 MB
(`$SP/step3/patched-sf100-4cn-extra/summary.txt`), against 64 B for q06 and ≤ 1 KiB for q01.

- **Change:** query shape (q14: part ⋈ lineitem, hash-shuffled) instead of q01/q06; SF100 (measured:
  standalone 248, 1 CN 331, 4 CN 283 ms warm) and SF1000 (≈ 10× the frame, ~95 MB per destination —
  new); CN count 1/2/4.
- **Sim must predict:** with `hop_ms(bytes) = drain_ms 3 + bytes·8/(nixl_gbps 350·1e6)` the 9.7 MB frame
  costs 3.2 ms and the 95 MB frame 5.2 ms per destination, serial on the transport thread → the sim
  says the exchange is invisible at SF100 and ≤ 15 ms at SF1000; per-CN scan compute from the 1-CN
  session's tasks split N ways (the N-CN projection, two scan pipelines per fragment). The slow peer
  (canary 77–107 gbps to `:9102` vs 300–400 elsewhere) predicts +6 ms on its 95 MB hop only.
- **Measure:** warm medians, oracles (`q14 VALUES-DIFFER … maxreldiff=5.5e-06 badcells=1` is the known
  baseline verdict), `bytes=` per pair, canary per peer, `exchange_staging_arena` `peak live … bytes`
  line at teardown; Quent: receiver-side `BatchRegistered` with `"origin":"reschedule_intermediate"`
  and `tier.capacity.capacity_bytes` (payload), the hop `D` = receiver `query` `Init` − straggler
  sender last `task` `Exit` per destination (agent 6 col. D: 6–29 ms at 4 CN for ≤ 1 KiB), sender
  park `B` = last task `Exit` → `query` `Exit`.
- **This week:** yes — SF100 arms exist for 1 and 4 CN (add 2 CN, 2 min); SF1000 needs a q14 oracle
  (DuckDB over 166 GB of lineitem — CPU-heavy, run it in the CI window) and 3 arms ≈ 15 min. Full
  attribution of the hop (pack / WRITE / unpack) is **blocked on the instrumentation chain**
  (`01-trace-schema.md` §5.5 `in_transit` over a `staging(cn_a)->staging(cn_b)` channel;
  `write_and_wait`'s `Duration` is discarded at `nixl_transport.rs:713-719`); the CN log `bytes=` and
  Quent `D` suffice for the pass rule.
- **Pass:** wall within 20 % at every arm; `D` within ±3 ms per destination at SF100 and ±10 ms at
  SF1000 of `teardown_ms + k·hop_ms`; if `D` grows with bytes faster than the nixl term, fit
  `drain_ms(bytes)` (lease RTT + pack + unpack copies, `04-memcpy-throttle.md` §1.2 B1/B3/B5) and
  re-run the replay — a-priori miss, post-hoc hit expected on the first SF1000 pass.

### E7 — BLOCKED: I/O inflight cap sweep (`03-io-throttle.md`, not implemented)

- **Change:** `SIRIUS_CN_SCAN_IO_INFLIGHT` = 1 / 2 / 4 (→ `scan_io_inflight_splits` in the derived YAML),
  SF1000 4 CN, q06 (one scan stream) and q03 (two scans competing), default threads.
- **Sim must predict:** an admission resource of capacity `cap` in front of the *read* part of each
  `GPU_SCAN` task and 4 executor threads behind it; needs the task split into I/O and GPU portions,
  which today's `Computing GPU_SCAN(0)` interval does not give (`01-trace-schema.md` §5.3
  `Computing.io_bytes`, or the design's `[scan-io-admit] … waited_us=` / `[scan-io-release] … held_us=`
  log lines). Structural predictions without it: `cap=4` equals the baseline within noise (the cap
  equals `pipeline.num_threads`); `cap=1` serializes the 16–17 q06 reads per CN → compute end between
  Σ(read part) and Σ(service) = 2.1–2.7 s per CN if the read is the whole task.
- **Measure:** warm medians, oracle unchanged (rows, not just ms — §6.5 of the design), Quent
  `GPU_SCAN(0)` overlap count per CN (≤ cap), waited/held from the admit/release lines, `G`.
- **Pass:** `cap=4` within 3 % of baseline; `cap=1,2` within 20 % once the I/O portion is on disk;
  no `[scan-io-*]` watchdog line (a lost wake-up is the design's risk 1).
- **Blocked on:** the feature (`scan_io_admission` in `sirius_scan_manager`, knobs in `tunable.rs` /
  `engine_settings.rs`) and one of the two I/O carriers above.

### E8 — BLOCKED: memcpy rate cap on the exchange (`04-memcpy-throttle.md`, not implemented)

- **Change:** `SIRIUS_CN_MEMCPY_RATE_BYTES_PER_SEC` / `SIRIUS_CN_MEMCPY_BURST_BYTES` (debit-after token
  bucket before `export_packed_next`), `SIRIUS_CN_MEMCPY_INFLIGHT=1` (= today). Two settings: the
  design's demo `RATE=64 BURST=64` on q06 4 CN (each 64 B frame waits ≈ 1 s after the previous), and
  `RATE=64 MiB/s` on q14 SF100 4 CN (each 9.7 MB frame waits ≈ 0.15 s).
- **Sim must predict:** `hop_ms += max(0, len/rate − credit)` per destination, serial per sender;
  q06 4 CN: the three remote senders wait in parallel → **+≈ 1.0 s** on the warm median (1245 →
  ≈ 2.25 s), coordinator scan unaffected; q14 SF100: 3 destinations × 0.15 s serial per sender →
  receivers start ≈ 0.45 s later → wall +0.45 s; `INFLIGHT=1, RATE=0` reproduces the baseline exactly
  (design §5 R6).
- **Measure:** warm medians; oracle identical; CN log `memcpy budget admit … waited_ms=` and the delay
  between `translated StarRocks plan fragment` and `transmitted batches via nixl` (`bytes=` unchanged);
  Quent: sender park `B` grows by the wait, receiver `Init` shifts by it, `data_batch` sizes unchanged;
  FE `time.idle` of the sender RPC grows by the wait (must stay under the FE query timeout).
- **Pass:** predicted shift within 20 % of measured shift (not of the wall — the shift is the signal);
  baseline setting within 2 % of the unthrottled arm; no `MAX_WAIT` failure.
- **Blocked on:** the `CopyBudget` in `ServiceCore`/`TransportState` and the `tunable.rs` knobs.

## 3. Sim changes the runnable experiments need (all small, all in `$SIM/replay.py`)

| For | Change |
|---|---|
| E1, E4 | `fe_overhead_ms` → `fe_overhead(sf_or_files)`; fit on the §0 table, hold SF100 out |
| E3 | `--threads N` override of `threads = {cn: n_threads_of(s)}` |
| E4 | FE split-assignment rule → predicted tasks per CN (today the N-CN section uses `n // N` contiguous chunks, which is right for byte ranges but must be stated and checked against `SIRIUS_CN_DUMP_FRAGMENTS`) |
| E5 | treat a standalone `query` (label `unnamed_query` / `qNN_rK`) as scan+result in one process; `group_runs` currently waits for a `sirius_ffi` result fragment and yields zero runs on `sf1000-standalone.json` |
| E6 | `drain_ms(bytes)` = constant + bytes/rate with the per-peer canary `gbps` instead of one `nixl_gbps` |
| E2 | none (`pred_async_sender` already printed) |

## 4. Instrumentation-chain items these experiments would use (none is a prerequisite for E1–E6)

From `01-trace-schema.md` §5: fragment labels + one `query_group` per FE query (§5.1) turn the
time-order run alignment into a join for every experiment; a SIGTERM flush (agent 6 open issue 2)
makes WARM generations whole; `enable_quent` in the derived YAML (§5.9) lets E3's control (b) run
without the full-YAML launcher; nixl `in_transit` (§5.5) gives E6 its per-hop durations; `Computing.io_bytes`
(§5.3) is what E7 needs beyond the feature itself. Experiments E1–E6 run today with the CN-log
fallbacks named in each "Measure" line.

## 5. This week on the box (all outside 02:00–03:50 UTC; one arm at a time)

| Day | Experiment | Box time | Notes |
|---|---|---|---|
| Thu (today) | E2 (lead's `run-perf-retime2.sh`) | ~25 min | already queued; add SF10 4 CN |
| Thu | E1 fit, E4 SF10 replay | 0 | offline over sessions on disk |
| Fri | E3 (3 arms + 2 controls) | ~20 min | five YAMLs + `up.sh` start-script variant first |
| Fri | E4 SF100 pair | ~15 min | oracle.py for both f64 SF100 datasets first (CPU) |
| Sat | E6 SF100 2 CN, SF1000 1/2/4 CN | ~20 min | SF1000 q14 oracle in the CI window (CPU) |
| Sat | E5 | ~10 min | after the replay extension |
| — | E7, E8 | — | blocked on the throttle implementations |

## 6. Open issues for the lead

1. The sim's `fe_overhead_ms=175` is an SF1000 value; at SF10 the warm residual is 71–92 ms and at SF100
   106–117 (§0). Any SF10/SF100 prediction quoted so far from the replay is ~80 / ~65 ms too high.
2. WARM-generation sessions are killed by `stop-cluster.sh` 3 s after SIGTERM and flush
   nondeterministically (cn0 `01a065cb-0f73…` empty, cn1 `01a065cb-0f72…` complete). Sim captures
   should stop the cluster gracefully or use the cold-restart generation's `r1` as the warm sample
   (which is itself page-cache-cold at SF1000: q06 r1 2422 vs the WARM median 1245).
3. E3 needs the full-YAML launcher (`up.sh`), which also changes CPU affinity handling; its control
   arm (a) decides whether the sweep is comparable to the derived-flag baselines.
4. E5's a-priori sign is known to be wrong (CN faster than standalone per task); the experiment
   quantifies a process factor the sim has no input for today.
5. Every timing arm today runs with Quent on; E3 control (b) is the only measurement of the exporter
   tax proposed here — if it is > 3 % the brief's "enable_quent off in performance YAMLs" changes every
   baseline number in `decimal-chain.log`, and the sim must be calibrated against the off arm.
