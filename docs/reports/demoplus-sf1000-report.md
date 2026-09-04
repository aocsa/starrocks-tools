# demo/q1q6-integration-plus-fixes at TPC-H SF1000: what the fixes did, what regressed, and why the six still fail at more than one CN

Written 2026-09-04 for the Sirius engineers. Every number is measured from a named file unless it is marked **inference**.

Path abbreviations used below:

| short | full |
|---|---|
| `S/` | `/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/` |
| `A/` | `S/demoplus/arms/` (the five arms of this campaign) |
| `N/` | `S/demoplus/agent-notes/` (analysis notes, refutation notes, helper scripts) |
| `P/` | `S/perf/sf1000/` (yesterday's baseline bundle) |
| `W/` | `/home/prestouser/aocsa/sirius-stacks-wt/demo-plus` (the branch under test, commit 281b13bc, read-only) |
| `C/` | `/home/prestouser/aocsa/sirius` (feat/pin-table-cn checkout, source of the pinned reference's notes and runbooks) |

Deeper notes behind each section: `N/scaleout-4cn.md`, `N/arena-4cn.md`, `N/why-six-fail-multi-cn.md`, `N/six-on-1cn.md`, `N/profile-diff.md`, and the `N/refute-*.md` files that checked them. Where a refutation corrected a number, this report carries the corrected value.

## 0. Summary

- At 4 CNs with the default 16 GiB staging arena the branch passes 16/22. The 15 queries it shares with yesterday's unpinned baseline sum to 21.70 s vs 21.08 s warm (+2.9%). Only q06 regressed by more than 10% (+44%). q16 passes and matches the oracle; the baseline never ran it at 4 CNs.
- The six (q05 q08 q09 q17 q18 q21) all fail at 4 CNs in 1.3-2.4 s with `exchange staging arena exhausted`. With a 48 GiB arena (90 GiB pool) five pass and match or nearly match the oracle. q09 exhausts 48 GiB too, and with 72 GiB it dies in the GPU pool instead.
- At 1 CN all six pass in every run (fix 4, fragment fusion). Yesterday they died after 54-232 s of OOM retries. Sum of warm times 33.3 s vs 24.8 s for standalone Sirius.
- Fix 2 (per-query parked-output bookkeeping) is clean on every failure: 24/24 retire lines report `still_parked=0`, the GPU pool reads 0 bytes at the next query on all four CNs after each of the six failures, and the queries that ran right after a failure match yesterday's times.
- Fix 1 (OOM fail-fast) never fired. No arm produced an operator-loop OOM. The one engine OOM captured (a degenerate 2-CN q09 run) was a sink-side `bad_alloc`, which bypasses the reschedule path by design. Its "3 s instead of 54-232 s" claim is untested.
- Fix 4 is inert at 4 CNs by construction (0 fusions; every shuffle sink has 4 destinations). It is not the answer to the multi-CN question.
- The q06 regression is a carve gap, not a fix: this branch has no async sender dispatch, so a sender's `exec_plan_fragment` RPC stays open for the whole scan and the FE's staged deploy serialises the fourth scan behind the other three. `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1` was exported by the harness but no code on this branch reads it.
- The q20 speedup (-9%) is the dev base lacking feat/pin-table-cn's merge-side canonical float-sum sort. The carve is faster there and has one fewer defence against arrival-order-dependent FP64 merged sums.
- Why the six fail with more than one CN: the FE hash-partitions the whole lineitem projection across the CNs (FILES() cardinality is 1; fix 3 is not on this branch), and the CN exchange is receiver-first, so each CN's arena must hold (N-1)/N of every co-held shuffled stream until the join receiver runs. That is 25-52 GB per CN at 4 CNs for these six, against a 17.2 GB arena. The pinned feat/pin-table-cn reference has the same plan and the same exchange; it ran with a 32 GiB arena and its own notes say it refused q08/q09/q21 at 4 CNs (those cells in the reference table are filled, not measured). On a 96 GB 2-CN box the per-CN lineitem share alone (85-122 GB for q05/q08/q09) exceeds any pool that fits beside an arena, so no sizing passes them there.

## 1. Setup

### 1.1 Branch and commits

Branch `demo/q1q6-integration-plus-fixes` at `281b13bc`, built in `W/` (`S/demoplus/build.log`: engine 21:56-22:00, CN 22:00:48, done 22:01:31 UTC). Merge base with `dev` is `01613070` ("fix(starrocks): combine complete scan splits (#1232)"). The branch is the dev-based carve `demo/q1q6-integration` (`1e163623`) plus the fix commits:

| fix | commits (`git log 1e163623..281b13bc`) | what it does |
|---|---|---|
| 1, engine OOM fail-fast | `9c002f97` fix(pipeline): fail the query fast when an OOM retry cannot make progress | abort the OOM reschedule loop when the retry provably cannot be granted its memory |
| 2, per-query parked-output bookkeeping | `5b4ee255` fix(cn): retire a failed query's parked output instead of wiping every query's; `37360a1b` feat(cn): cancel_plan_fragment tears down the cancelled query on this CN | release only the dead query's parked slots and staging leases; refuse its later fragments |
| 4, fragment fusion | `1289a33c` feat(translator): splice a deferred sender plan over its receiver exchange; `6f87c304` feat(cn): fuse single-destination local leaf senders into their receiver plan; `1661eb5e`, `281b13bc`, plus tests `a0df9f43` `f29f96d4` `73ce2805` | on one CN, a leaf sender with a single local HASH_PARTITIONED destination is spliced into its receiver's plan instead of running and parking its rows; knob `SIRIUS_CN_FRAGMENT_FUSION = off / leaf / leaf-any`, default `leaf` (`W/experimental/starrocks/docs/TUNABLES.md:41`) |
| 3, real FILES() cardinalities | not on this branch | the FE still plans every FILES() scan at 1 row |

Engine commits over dev (`git log 01613070..281b13bc -- src`): fix 1, the exchange staging arena (`1e61c16c`), declared stream cardinality (`f1e8fb17`), byte-range scans (`a22235e1`, `94a77836`, `92e91adf`), FFI fragment bindings (`14386a77`, `b7452f67`, `98661d7d`), the stall watchdog (`d53b44b1`), pinned parquet file-subset scans (`6c0b83a1`), and VSS ANN (`98fe1a84`).

### 1.2 Baselines and references

| name | what | where |
|---|---|---|
| perf baseline (unpinned) | `perf/profile-sf1000` @ `45dab3be` = feat/pin-table-cn `d24f02c4` + `85658f09` (SIRIUS_CANONICAL_FLOAT_SUMS gate) + `9a5a4da6` (opt-in async sender dispatch) + `45dab3be` (Quent telemetry). Run 2026-09-03 on this box, same data, same knobs. 1 CN all 22; 4 CNs only the 15 that passed at 1 CN. | `P/cn1/`, `P/cn4/`, `P/results.md`, `P/capture-cn.sh` |
| standalone Sirius | DuckDB + Sirius in-process on GPU 0, the six, 100 GiB pool | `P/standalone-failed/runs/runs.csv` |
| pinned reference | feat/pin-table-cn, tables held in GPU-tier compressed pins, 1/2/4/8 GB200 CNs, warm medians. Supplied by the user; identical to `C/notes/2026-08-29-sf1000-gpu-scaleout.md`. Legend there: `†` filled, `*` partial 1 CN. Config per `C/notes/2026-08-28-sf1000-pinned-gpu-scaleout-PLAN.md:286-297`: 4-GPU arm `GPU_MEM=128GiB STAGING=32GiB HOST_MEM=112GiB`, same-host cuda_ipc, uring scan, dop 18, host gcn-18, dataset `/scratch/sirius/datasets/tpch_sf1000`. | `S/demoplus/reference-pin-table-cn.md` |
| user's 2-CN RTX PRO 6000 run | this branch, 2 CNs, NIXL, SF1000; 16/22 pass, 103.9 s; q05 q08 q09 q18 "fail (OOM)", q17 q21 "fail". No error text, no GPU_MEM/STAGING recorded. | `S/demoplus/reference-2cn-rtxpro6000.md` |

### 1.3 Box, data, harness

Host `presto-gb200-gcn-18`, 4 x GB200, 185.03 GiB HBM each (`C/bench/gb200-4gpu/HARDWARE.md:14`). Dataset `/scratch/sirius/datasets/tpch_sf1000` (decimal TPC-H, GPFS), read with the uring datasource and O_DIRECT (`W/src/include/io/uring/config.hpp:28`, `W/src/io/uring/uring_reactor.cpp:528`). Harness `S/fix/capture-arm.sh` and `S/demoplus/run-arms.sh`: one FE plus one CN per GPU, each query once cold then twice warm (`S/perf/sf1000/run-queries.sh`, which stops a query after a failing cold run), oracle comparison with `bench/rtxpro6000-2gpu/tools/compare.py` at relative tolerance 1e-6. "Warm median" below is the mean of the two warm runs (n=2), as in yesterday's report.

Knobs exported by `capture-arm.sh:12-15`: `HOST_MEM=160GiB`, `SIRIUS_QUERY_WATCHDOG_SECS=300`, `SIRIUS_CN_ENABLE_QUENT=1`, `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`, fusion knob unset (resolves to `fusion_mode=Leaf`, `A/dp-cn4/cluster.log` 22:01:55.94). The async dispatch export is a no-op on this branch: `grep -rn SIRIUS_CN_ASYNC_SENDER_DISPATCH W/experimental/starrocks/src` returns nothing, while the perf tree reads it at `compute_node_service.rs:208,271,291`.

### 1.4 Arms

| arm | CNs | GPU_MEM / STAGING per CN | queries | outcome |
|---|---|---|---|---|
| `A/dp-cn4` | 4 | 100 GiB / 16 GiB | all 22 | 16 pass; q05 q08 q09 q17 q18 q21 fail cold (arena) |
| `A/dp-cn1-six` | 1 | 100 GiB / 16 GiB | the six | 6/6 pass, 3 runs each |
| `A/dp-cn4-six-stg48` | 4 | 90 GiB / 48 GiB | the six | q05 q08 q17 q18 q21 pass; q09 fails (arena) |
| `A/dp-cn4-q09-stg72` | 4 | 64 GiB / 72 GiB | q09 | fails (GPU pool at the receiver) |
| `A/dp-cn4-q09-stg72-pool100` | 4 launched, 2 used | 100 GiB / 72 GiB | q09 | fails (GPU pool in the lineitem scan). Degenerate: the FE blacklisted two CNs whose BRPC was not yet up, so all 11 fragments ran on 9102/9112 (`cluster.log` `fragment run started` cn= counts 6+5; `engine-.cn2.log`/`cn3.log` have no query). Treat as a 2-CN measurement. `N/EXTRA-ARM.md` item 24 labels it 4-CN; that label is wrong. |

Arena sizes are logged directly (`engine-.cn0.log` "exchange staging arena: N bytes": 17,179,869,184 / 51,539,607,552 / 77,309,411,328). Pool sizes are not logged; they are supported by the `[gpu_pool] peak=` values (stg48 max 96.60 GB = 89.97 GiB; stg72 68,698,126,080 B = 63.98 GiB; pool100 107,374,182,400 B = 100 GiB exactly).

### 1.5 Tooling caveat

The carve does not carry `45dab3be` (perf-only telemetry commit). Quent query labels are all `sirius_streaming_fragment` (1010 fragments in `A/dp-cn4/quent/`), the `staging_lease`, `scan_split_read` and stream-hop events are absent, and the CN's `transmitted batches via nixl` / `declared input stream cardinality` lines carry no `query_id` or per-transmit timings. So `P/quent_bp.py` collapses each arm into one group and `P/cnlog_extract.py` prints `cards[]` and no `nixl=` for every run. Everything per-query below was re-derived by mapping timestamps onto `runs/runs.csv` windows (`N/profile_diff.py`, `N/_dispatch_skew.py`, `N/_scan_by_query.py`, `N/percn_timeline.py`, `N/arena-4cn-work/arena_summary.py`). The `batches` counts in `N/compare-warm-perf-vs-dp.txt` are 13-36% lower on demo-plus for accounting reasons only (the baseline records each remote frame as a data batch; the delta equals the transmit count exactly on q03 and ten other queries).

## 2. Results

### 2.1 The one caveat on the pinned column, stated once

The pinned reference holds the tables in GPU-tier compressed pins; it does not read parquet at query time. Demo-plus and the perf baseline read parquet from GPFS with O_DIRECT on every run, and GPU_SCAN is 63-98% of all operator Computing time in every dp-cn4 query except q16 (44%) and q22 (32%) (`N/_scan_by_query.py` over `A/dp-cn4/quent.json`; GPU_SCAN Computing covers read plus decode, the two are not split). The pinned run also used a 128 GiB pool and a 32 GiB arena, twice the demo arena. Its `†` cells are filled values, not measurements: `C/notes/2026-08-28-sf1000-pinned-gpu-scaleout-PLAN.md:303-305` records that the 4-GPU pin sweep at 128/32 "still lost q8, q9, q21 to arena fragmentation" and `:324` says to leave them empty. So the pinned 4-CN row is 19/22 measured, and ratios against `†` cells compare against fills. `*` marks partial 1-CN cells (q16, q18).

### 2.2 Merged table, warm medians in seconds

From `S/demoplus/compare-ref.md`, with the q16 oracle result and the `†`/`*` marks restored. Sources: pinned columns `S/demoplus/reference-pin-table-cn.md`; perf `P/cn1/runs/runs.csv`, `P/cn4/runs/runs.csv`; demo-plus `A/dp-cn1-six`, `A/dp-cn4`, `A/dp-cn4-six-stg48` `runs/runs.csv`.

| query | pinned 1 CN | perf 1 CN | demo-plus 1 CN | pinned 4 CN | perf 4 CN | demo-plus 4 CN, 16 GiB | demo-plus 4 CN, 48 GiB (six only) | demo-plus 4 CN / pinned 4 CN |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| q01 | 21.73 | 5.07 | - | 5.76 | 2.16 | 2.33 | - | 0.41 |
| q02 | 1.07 | 1.25 | - | 1.25 | 1.15 | 1.14 | - | 0.91 |
| q03 | 1.91 | 3.11 | - | 1.03 | 1.61 | 1.73 | - | 1.68 |
| q04 | 0.86 | 1.85 | - | 0.73 | 1.00 | 1.07 | - | 1.46 |
| q05 | 3.45† | fail | 5.56 | 1.78 | not run | fail | 4.29 | 2.41 |
| q06 | 0.61 | 1.92 | - | 0.41 | 0.85 | 1.22 | - | 2.99 |
| q07 | 1.50 | 3.48 | - | 1.29 | 2.01 | 1.97 | - | 1.53 |
| q08 | 3.97† | fail | 3.77 | 2.05† | not run | fail | 3.59 | 1.75 vs a fill |
| q09 | 5.29† | fail | 5.15 | 2.73† | not run | fail | fail | - |
| q10 | 2.91 | 3.71 | - | 1.62 | 1.79 | 1.79 | - | 1.10 |
| q11 | 1.03 | 0.97 | - | 1.03 | 0.66 | 0.67 | - | 0.65 |
| q12 | 1.09 | 2.52 | - | 1.01 | 1.28 | 1.29 | - | 1.28 |
| q13 | 1.21 | 2.20 | - | 0.85 | 1.03 | 1.04 | - | 1.22 |
| q14 | 0.75 | 2.58 | - | 0.56 | 1.23 | 1.26 | - | 2.25 |
| q15 | 1.49 | 4.68 | - | 0.60 | 2.03 | 2.07 | - | 3.46 |
| q16 | 0.75* | fail | - | 0.74 | not run | 0.66 | - | 0.90 |
| q17 | 12.69 | fail | 4.96 | 3.89 | not run | fail | 2.65 | 0.68 |
| q18 | 8.01* | fail | 3.26 | 2.89 | not run | fail | 2.03 | 0.70 |
| q19 | 1.38 | 3.08 | - | 0.88 | 1.44 | 1.46 | - | 1.66 |
| q20 | 2.36 | 4.17 | - | 1.13 | 2.00 | 1.81 | - | 1.60 |
| q21 | 6.46† | fail | 10.62 | 3.33† | not run | fail | 5.24 | 1.57 vs a fill |
| q22 | 0.59 | 0.94 | - | 0.63 | 0.83 | 0.85 | - | 1.35 |

Sums of warm medians (`N/refute-pd1-sums-15-common.txt`; recomputed from the runs.csv files):

| set | demo-plus | comparison | ratio |
|---|--:|--:|--:|
| 15 queries both unpinned arms pass at 4 CNs | 21.70 s | perf 4 CN 21.08 s | 1.029 |
| 16 queries demo-plus passes at 16 GiB | 22.36 s | pinned same 16: 19.52 s | 1.15 |
| 19 queries with a measured pinned 4-CN cell (16 + q05 q17 q18 at 48 GiB) | 31.32 s | pinned 28.08 s | 1.12 |
| 21 queries (adds q08 q21 at 48 GiB) | 40.16 s | pinned 33.46 s, of which 5.38 s are fills | 1.20, not like-for-like |

q08 and q21 pass on demo-plus at 48 GiB where the pinned 4-CN sweep at 32 GiB did not. q09 passes on neither branch at 4 CNs. q01 (0.41x), q11 (0.65x), q16 (0.90x), q17 (0.68x) and q18 (0.70x) beat the pinned times; those are engine-side differences between the dev base and feat/pin-table-cn and would presumably carry over to a pinned run (**inference**). The scan-dominated queries q06, q14, q15 (97-98% GPU_SCAN) are 2.25-3.46x the pinned time; q06 carries the dispatch regression of section 4.2 on top of the read.

Warm-run spread worth knowing: q05 at 48 GiB is the mean of 5329 and 3241 ms (64% apart), so its 4.29 s is unstable. q08 at 48 GiB is 3247/3942. Everything else in the demo-plus columns has both warm runs within 6% except q01 at 4 CNs (1846/2824 ms, GPFS scan variance, GPU_SCAN 15.9 vs 23.9 s for identical bytes, `N/prof-dp-cn4.txt`).

### 2.3 The six at 1 CN

`A/dp-cn1-six/runs/runs.csv`, standalone `P/standalone-failed/runs/runs.csv` (one warm run), pinned `S/demoplus/reference-pin-table-cn.md` 1CN column, yesterday `P/cn1/runs/runs.csv`.

| query | cold | warm r1 | warm r2 | warm median | standalone warm | ratio | pinned 1 CN | ratio | yesterday 1 CN |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| q05 | 12881 | 5403 | 5721 | 5562 | 2786 | 2.00 | 3.45† | 1.61 | fail 101,460 ms |
| q08 | 3902 | 3775 | 3765 | 3770 | 3674 | 1.03 | 3.97† | 0.95 | fail 168,285 |
| q09 | 5149 | 5206 | 5100 | 5153 | 5427 | 0.95 | 5.29† | 0.97 | fail 232,533 |
| q17 | 4803 | 4939 | 4973 | 4956 | 3786 | 1.31 | 12.69 | 0.39 | fail 103,894 |
| q18 | 3265 | 3239 | 3273 | 3256 | 2691 | 1.21 | 8.01* | 0.41 | fail 54,202 |
| q21 | 10585 | 10661 | 10580 | 10620 | 6401 | 1.66 | 6.46† | 1.64 | fail 114,485 |
| sum | | | | 33.32 s | 24.77 s | 1.35 | 39.87 s | 0.84 | 775 s to die |

Row counts match the oracle (5 / 2 / 175 / 1 / 100 / 100). Only q05 has a cold penalty (12.9 s vs 5.4-5.7 s). Yesterday's failures were engine-side: `GPU pipeline task exceeded maximum retry limit (100) ... OOM at operator GPU_SCAN` with 1800-5600 OOM reschedules per query (`P/cn1/runs/*.r0.err`, `P/cn1-cnlog.txt`).

### 2.4 Correctness vs the DuckDB oracle

`A/dp-cn4/compare.txt`, `S/demoplus/oracle-q16.log`, `A/dp-cn1-six/compare.txt`, `A/dp-cn4-six-stg48/compare.txt`, baseline `P/compare-cn4.txt`.

| arm | MATCH | VALUES-DIFFER | other |
|---|---|---|---|
| dp-cn4, 16 GiB | q02 q04 q06 q12 q13 q14 q16 q20 q22 (9) | q01 9.570e-04, q03 1.766e-03, q07 9.558e-04, q15 1.126e-03, q19 9.552e-04, q10 1.229e+01 | q11 EMPTY (the oracle TSV is header-only, so 0 rows is correct); six failed |
| dp-cn1-six | q17 q18 q21 | q05 9.575e-04, q08 8.667e-06, q09 1.486e-03 | |
| dp-cn4-six-stg48 | q17 q18 q21 | q05 9.575e-04, q08 8.667e-06 | q09 EMPTY (failed) |

Verdict labels and bad-cell counts on the 15 common queries are identical to the baseline. Raw outputs are not bit-identical: 19 of 45 common `.out` files differ in the last float digit (q01/q03/q07/q10/q22 every run, q14.r1, q15.r0/r1, q19.r2), the same run-to-run float-sum noise the baseline shows against itself (`N/refute-scaleout-4cn-4-correctness.md`). The ~1e-3 deviations are the known decimal-to-FP64 lowering on sum columns (translator `expr_translator.rs` ~816-838); the fix 4 spec accepts up to 2e-3 on sum columns. q10's 12.29 is five rows out of oracle order (a 3-cycle at rows 10-12, a swap at 15-16) caused by that revenue perturbation; the 12.29 itself is `c_acctbal` on misplaced row 15 (7925.69 vs -701.99). q15 returned 0 rows in one warm run (baseline: two of three): the `total_revenue = max(total_revenue)` equality on an FP64 sum that drifts run to run. Standalone Sirius matched all six exactly on the same data (`P/compare-standalone-failed.txt`).

## 3. What the fixes did (measured)

### 3.1 Fix 4, fragment fusion: makes the six run at 1 CN; inert at 4 CNs

1 CN (`A/dp-cn1-six/cluster.log`): 45 `fused sender fragment into its local receiver ... mode=Leaf` lines = 3 runs x 15, 0 `fragment fusion skipped` lines (policy declines log at debug and the arm captured INFO only, so 0 means no rendezvous-level decline). Per query the fused exchange ids are q05 {18, 9, 1, 4}, q08 {2, 4}, q09 {2, 4}, q17 {1, 4}, q18 {15, 1, 10}, q21 {2, 13}, identical in all three runs and exactly the goldens in `S/fix/designs/fragment-fusion-SPEC.md` section 7. Engine runs per query are 7/9/7/3/4/7 (`A/dp-cn1-six/cnlog.txt` `frags=`) = the FE's 11/11/9/5/7/9 fragments minus the fused senders, as the spec predicts.

The engine plan for q05's join fragment now reads `STREAMING_SOURCE(0) | STREAMING_SOURCE(3) | GPU_SCAN(6) | HASH_JOIN(9) -> ... -> HASH_JOIN(13) -> STREAMING_SINK(15)` (`A/dp-cn1-six/engine-.cn0.log` 22:04:20.715): the lineitem scan is inline on the probe side. Yesterday it was a bare `GPU_SCAN(0) -> STREAMING_SINK(1)` fragment that OOMed (`P/cn1/engine-.cn0.log` 13:03:43). Zero `OOM at operator`, `is futile`, `retry`, `bytes freed` lines in the 1-CN engine log.

q21 passed although the spec predicted an OOM. DuckDB did put the filtered lineitem scan on the build port of `HASH_JOIN(8)` (type LEFT, the spec's feared shape) and it completed with no OOM in a 6.7 s fused fragment (`engine-.cn0.log` lines 8546-8661, `N/refute-q21-passed-against-prediction.md`).

Where the 1-CN time over standalone goes: fragments run staged, not pipelined. Per-run fragment times sum to 89-96% of the client wall (`A/dp-cn1-six/cnlog.json`). The remaining un-fused stages park and re-read: q17's partial-avg-over-lineitem fragment (2616 ms) before its fused join (2055 ms); q18's group-by sender (1216 ms) parking 24.0 GB; q21's un-fused middle fragment (3136 ms) parking 36.6 GB before the 6783 ms fused fragment. The fused q05 fragment itself is slower than standalone's single plan (353 tasks / 237 s Queued vs 194 tasks / 130 s, `A/dp-cn1-six/quent.txt` lines 39-43 vs `P/standalone-failed-quent.txt` line 8). q05's fused fragment also runs under memory pressure: the Quent `data_batch` stream shows 98-127 GPU-to-HOST batch moves per q05 run with about 102-107 GB resident on host at peak, matching the `[host_pool] peak=106,962,092,032` line (verdict on the memory-at-the-ceiling finding). The `[gpu_pool] peak=` counter reaching exactly 100.00 GiB is cucascade's reservation-inclusive total, not resident bytes.

4 CNs: `fusion_mode=Leaf` on all four CNs, 0 fused lines in `A/dp-cn4/cluster.log`, `A/dp-cn4-six-stg48/cluster.log` and both q09 arms. Source: `fusion::sender_shape` refuses any sink with more than one destination (`W/experimental/starrocks/crates/starrocks-plan-translator/src/fusion.rs:208-212`, NotSingleDestination), and at N>1 every HASH_PARTITIONED leaf has N destinations. There is no fan-out fusion mode. So fix 4 changes nothing at 2 or 4 CNs.

### 3.2 Fix 2, per-query parked-output bookkeeping and cancel teardown: clean on every failure

| check | 1 CN (`A/dp-cn1-six`) | 4 CNs (`A/dp-cn4`) | 48 GiB / 72 GiB arms |
|---|---|---|---|
| `retired a query's parked sender outputs` lines | 0 (nothing failed) | 24 = 6 failures x 4 CNs, all `trigger=cn_err`, all `still_parked=0` (`cluster.log`, ANSI stripped) | stg48 4 (1 cn_err + 3 cancel), stg72 4 (1 engine_err + 3 cancel), all `still_parked=0` |
| `[gpu_pool] QueryBegin allocated=` at the next query after a failure | 0 bytes at the first QueryBegin of all 18 runs (`N/.pool_windows.py`) | 0 on all four CNs after each of the six failures; last QueryEnd before it 42.0-42.3 GB (q05), 48.3-48.9 (q08), 61.6-62.4 (q09), 29.8-30.2 (q17), 27.9-28.2 (q18), 23.9-24.3 (q21) (`engine-.cn*.log`, `N/arena-4cn-work/_pool_at_query_starts.py`); window numbers consecutive, so no dead-query fragment ran | stg48 q09 to q17: 33.1/61.9/61.6/61.8 GB to 0 |
| FE `cancel_plan_fragment` teardown | 156 `cancel_plan_fragment retired the query on this CN` lines, 108 QUERY_FINISHED + 48 LIMIT_REACH, all `released_leases=0` | 999 lines on the 48 passing runs, all `released_leases=0`; 67 `INTERNAL_ERROR` lines with `released_leases>0` summing to 663 (q05 77, q08 151, q09 107, q17 49, q18 104, q21 175); 0 fragments finished after the first cancel | stg48 q09 224 leases, stg72 q09 314 |
| arena clean at CN teardown | n/a | not logged (engine logs end before teardown) | stg48: all four CNs log `0 leases outstanding` at teardown after the q09 failure and 15 more runs (`engine-.cn*.log`, `exchange_staging_arena.cpp:169`) |
| queries right after a failure | n/a | q10 1786 vs perf 1789 ms, q19 1459 vs 1440, q22 852 vs 832 warm | q21 x3 pass after the q09 failure with ~43 GB of arena need |

`still_parked` is `registry.fragments()` after the retire (`W/experimental/starrocks/src/engine.rs:305`), the whole parked registry on that CN, so 0 means nothing of any query remained parked. Yesterday's B4 measured 0-38 GB left parked per failure at 1 CN. Scope caveat from source: a peer lease granted by `handle_staging_lease` is untracked until its frame lands; if the nixl write or transmit RPC failed after the grant, cancel could not see that lease. No such transport failure occurred in any arm, so this path is unexercised.

### 3.3 Fix 1, engine OOM fail-fast: never fired

`OOM at operator` = 0 and `is futile` = 0 in every engine log of all five arms; `cnlog.txt` `oom_resched=0 futile=0` on every run of dp-cn4, dp-cn1-six, dp-cn4-six-stg48 and dp-cn4-q09-stg72. The only `executor metrics` lines in the campaign read `oom_reschedules=0 futile_aborts=0` (pool100 arm, `engine-.cn0.log:226`, `engine-.cn1.log:202`).

The 4-CN failures at 16 and 48 GiB are CN-side arena refusals. The two engine-side failures are q09 with a 72 GiB arena: at 64 GiB pool the CN's `push_packed` of a staged remote frame threw `std::bad_alloc` (`A/dp-cn4-q09-stg72/runs/q09.r0.err`, `engine.rs:565`; no engine OOM line); at 100 GiB pool on the degenerate 2-CN placement the lineitem scan fragment itself hit the pool cap (`A/dp-cn4-q09-stg72-pool100/engine-.cn0.log:216-231`: three `proceeding with partial reservation` lines, then `Exception during task execution: std::bad_alloc: out_of_memory` at `gpu_pipeline_executor.cpp:523`, QueryEnd allocated 98.57 GB of 107.37 GB). That OOM came out of the sink, where by design the reschedule window is already closed (`W/src/pipeline/gpu_pipeline_task.cpp` ~714-717); it reached the generic catch at `gpu_pipeline_executor.cpp:522-527` and failed the query in 118 ms without touching fix 1's predicate (`:426-461`, consulted only for reschedule exceptions).

So the claim "the six die in ~3 s instead of 54-232 s" is untested by this data. Yesterday's 54-232 s deaths are gone at 1 CN because fusion removed the OOM, not because fail-fast shortened it. A deliberate operator-loop OOM (an `OOM at operator` reschedule, e.g. fusion off, q05 at 1 CN) is needed before the timing can be claimed.

### 3.4 Fix 3 absent: plans identical to the baseline

Declared cardinality counts and sums per run are identical between `A/dp-cn4/cluster.log` and `P/cn4/cluster.log` (q03 13 declarations / 3,500,159,817 rows, q10 21 / 1,802,745,091, q22 14 / 6,000,000,043), nixl bytes are identical (q03 60.34 GB, q07 69.01, q10 48.04, q22 18.56, q12 23.47, q04 23.64), engine pipeline-chain multisets are identical on 44/45 common runs (the one mismatch is a truncated engine log at capture end), and fragment counts per query match (`N/plan-shapes-*.txt`, `N/_dispatch_skew.py`). FILES() scans are still planned at 1 row (`P/survey/plan-summary-4cn.txt`: q05 and q09 `card_all_one=True`, lineitem is the PARTITIONED build side).

## 4. Profile differences and regressions (4 CNs, 15 common queries)

### 4.1 Warm wall, base to demo-plus

`N/refute-pd1-sums-15-common.txt` from the two `runs/runs.csv`; operator columns from `N/compare-warm-perf-vs-dp.txt` (Quent Computing seconds summed over all tasks on all 4 CNs).

| query | perf ms | demo-plus ms | delta | GPU_SCAN s (base to dp) | note |
|---|--:|--:|--:|---|---|
| q01 | 2160 | 2335 | +8.1% | 19.6 to 19.9 | dp r1/r2 1846/2824, scan variance |
| q02 | 1154 | 1141 | -1.1% | 2.65 to 1.80 | baseline r1 had a slow partsupp fragment (3.58 s vs 1.71 s in r2) |
| q03 | 1607 | 1730 | +7.6% | 11.4 to 10.1 | section 4.2, second-order |
| q04 | 1000 | 1066 | +6.6% | 7.27 to 7.23 | section 4.2, second-order |
| q06 | 853 | 1224 | +43.6% | 9.27 to 7.57 | section 4.2 |
| q07 | 2009 | 1972 | -1.9% | 14.8 to 14.5 | |
| q10 | 1789 | 1786 | -0.2% | 11.9 to 11.6 | |
| q11 | 665 | 669 | +0.7% | 1.86 to 1.84 | |
| q12 | 1284 | 1290 | +0.5% | 10.2 to 9.9 | |
| q13 | 1030 | 1035 | +0.5% | 9.21 to 9.12 | |
| q14 | 1226 | 1258 | +2.6% | 13.6 to 13.6 | |
| q15 | 2035 | 2073 | +1.9% | 23.7 to 24.8 | MERGE_GROUP_BY 0.346 to 0.054 s, no wall effect |
| q19 | 1441 | 1459 | +1.3% | 14.6 to 14.3 | |
| q20 | 2000 | 1811 | -9.4% | 14.1 to 14.3 | MERGE_GROUP_BY 2.09 to 0.41 s, section 4.3 |
| q22 | 832 | 852 | +2.5% | 1.23 to 1.23 | |
| sum | 21082 | 21700 | +2.9% | | q06 alone is +372 ms of the +618 |

Reservation requests and peak allocations are within 1% between the arms (q01 943.6 / 743.1 GB in both; q11 alloc 5.6 vs 5.8 GB). Fixes 1 and 4 left zero captured lines on the passing runs; fix 2's cancel teardown ran on every FE cancel with `released_leases=0` and no measurable cost (cancel RPC busy median 12.4 us vs 7.2 us baseline). Every difference below is tree lineage: the dev-based engine and the carved CN, not the fixes.

### 4.2 q06 +44%: the carve runs sender fragments inside the exec_plan_fragment RPC

Measured (`N/percn-q06-r1.txt`, `N/refute-pd1-percn-q06-r0r2.txt`, `N/dispatch-skew-*.txt`):

| run | three senders start | fourth sender starts | fourth's CN | result fragment CN |
|---|---|---|---|---|
| dp r0 | 22:02:35.9615 | 22:02:36.6136 (+652 ms) | 9112 | 9112 |
| dp r1 | 22:02:37.2217 | 22:02:37.8323 (+611 ms) | 9102 | 9102 |
| dp r2 | 22:02:38.4315 | 22:02:39.0940 (+662 ms) | 9112 | 9112 |
| baseline r0/r1/r2 | all four within 1.1 ms | | | |

The fourth sender's RPC arrives 0.3-0.5 ms after the third sender's RPC closed. The three concurrent scans take 489-659 ms each and their RPCs close with `idle` 524-651 ms (the RPC was held for the whole scan); the late scan runs alone in 412-437 ms. Arm-wide, 75 dp `exec_plan_fragment` RPCs had idle >= 1 s and 291 had 100 ms to 1 s (sum 215.8 s over 1251 RPCs); the baseline's maximum was 1.3 ms over 995 RPCs. Per-fragment scan time did not get worse (dp senders 412-659 ms vs baseline 623-687 ms), so the whole +372 ms is serialisation.

Source: demo-plus `exec_plan_fragment` -> `exec_single_attachment` -> `process_inline` -> `process_fragment` runs the fragment and its blocking per-destination drains on the RPC's blocking task (`W/experimental/starrocks/src/compute_node_service.rs:271-292, 592-624, 1365-1385`). The FE's `Deployer.createFragmentInstanceExecStates` puts the first instance on a worker in one stage and any further instance on an already-deployed worker in a later stage, and `deployAsync` then `waitForDeploymentCompletion` per stage (`Deployer.java:208-215, 246-268`; identical in both trees). With root-first iteration the result CN's own scan is the later-stage instance, so it waits for the other three RPCs to return. The perf branch avoids this with the opt-in async dispatch of `9a5a4da6` (`compute_node_service.rs:207-297`, whose comment describes exactly this q06 wave); the baseline had it on via `P/capture-cn.sh:12`. This branch has no such code.

q06 is the only passing query whose leaf senders feed the result fragment directly (5 fragments). Multi-stage plans start their leaves together in both arms (q01 skew 0 ms; q07 1467-1487 vs 1450-1491 ms). q03 (+122 ms) and q04 (+66 ms) pay a different price of the same inline design: the first receiver on a CN starts as soon as its inputs land and occupies the single engine thread, while that CN's leaf sender is still draining; each `export_packed_next` of the drain queues behind the running receiver, and the other CNs' receivers wait for those frames (q03.r1: F2 on 9112 runs 1185.6-1350.2 ms while 9112's orders leaf transmits only at 1350.5-1360.8; the other three F2 start 1350.7-1361.0 instead of ~1186; last F2 finish +74 ms vs baseline). In the baseline the async dispatch worker runs each sender's run plus drain sequentially and queues readied receivers behind it. A second inline-only serialisation: the CN's brpc server handles one request per connection at a time (`brpc.rs:150-195`), so a running inline sender RPC blocks the next fragment and `cancel_plan_fragment` on that connection.

### 4.3 q20 -9%, q15/q10 MERGE_GROUP_BY: the baseline's merge-side canonical float-sum sort

Measured (`N/prof-perf-cn4.json` vs `N/prof-dp-cn4.json`): q20 MERGE_GROUP_BY 2.084/2.087 s (base r1/r2) vs 0.408/0.406 s (dp), same 12 tasks and 24.4 GB; q15 0.346 vs 0.054 s; q10 0.095 vs 0.063; q11 0.041 vs 0.011. Merges with no FP64 SUM are identical across arms (q13 count-only 0.021 vs 0.021; q04; q02 MIN). Local HASH_GROUP_BY is equal in both arms. Client-visible gain on q20: 189 ms (engine span 1.70 to 1.54 s); the 1.7 s is summed per-task operator time across 4 GPUs.

Source: feat/pin-table-cn's `src/op/merge/gpu_merge_impl.cpp:125-134` sorts the partials before every FP32/FP64 ungrouped SUM and `:206-220, 284-292` runs `canonicalize_row_order` plus a sorted groupby whenever an aggregate `is_order_sensitive_sum`. Neither path checks `canonical_float_sums_enabled()`; the `SIRIUS_CANONICAL_FLOAT_SUMS` gate of `85658f09` covers only the local aggregate (`gpu_aggregate_impl.cpp:164-167`; the commit message says the merge side "stays on"). The sort entered feat/pin-table-cn with `441f05b2` (authored as `5d149277`, 2026-08-07). dev's copy of the file has none of it, and demo-plus's copy is byte-identical to dev's; demo-plus also lacks `throw_if_int64_sum_could_overflow`. Trade-off: the baseline's merged FP64 sums depend only on the multiset of partials; the carve's depend on arrival order. Neither arm is deterministic on q15 anyway (the two revenue computations produce different partial multisets; 1-CN A/B in `P/cn1-canon-q15` with both sorts on still returned 0 rows in 4 of 6 runs), so porting the sort would not fix q15's flake.

### 4.4 GPU_SCAN Computing lower on demo-plus without wall benefit

q03 GPU_SCAN 11.45/11.37 s (base) vs 9.99/10.15 s (dp), same 82 tasks and 177,618,488,778 input bytes. The engine scan path is byte-identical between the trees (`parquet_gpu_ingestible.cpp`, `parquet_byte_range.*`, `sirius_scan_manager.cpp`: 0 diff lines; the only scan-operator hunk is the perf tree's `scan_split_read` telemetry, 0.029 s per q03 run). About half of the q03 delta (-0.67/-0.46 s) is the orders scan running staggered under the inline dispatch (one CN alone at 104-111 ms per task, three CNs 165-180 ms later at 114-153 ms; baseline all four within 8 ms at 128-223 ms), the same mechanism as q06. The rest (-0.78 s on the 4-way-concurrent lineitem scans, -8.5%) is within the observed I/O variance (`N/refute-PD-5-scan-computing.md`). q02's -32% is baseline run-to-run variance. Do not read lower Computing sums on demo-plus as an engine speedup.

### 4.5 Telemetry differences

Not a regression in the strict sense: `45dab3be` (Quent labels `<query id>:<fragment id>`, `staging_lease`, `scan_split_read`, stream-hop events, and the `query_id/fragment_instance_id/elapsed_ms/lease_ms/write_ms/write_gbps` fields on `transmitted batches via nixl`) is on perf/profile-sf1000 only; it is not an ancestor of feat/pin-table-cn `d24f02c4`, dev, or this branch. Consequences: `P/quent_bp.py` and `P/cnlog_extract.py` cannot group per query on this branch (fragment counts, per-fragment elapsed, cancels and fix counters still work), Quent has no lease data (`leases_total 0` in every arm), and the baseline's per-transmit timing tables cannot be produced. Per-query work on this branch needs the time-window tools in `N/`.

### 4.6 Cold runs

q03 cold 3987 ms (dp) vs 2071 (base): the dp lineitem senders took 1337-1340 ms each vs 572-646 warm, leaf start skew 1908 ms. Other cold runs moved both ways (q02 2158 to 1971, q04 2155 to 1528, q01 3366 to 3048). One run each; not attributed.

## 5. Why the six fail with more than one CN, and what the pinned branch did differently

### 5.1 Mechanism (source, `W/experimental/starrocks/src` and `W/src`)

1. The FE hash-partitions the whole lineitem projection across the N CNs because every FILES() scan is estimated at 1 row (fix 3 absent; `P/survey/plan-summary-4cn.txt`). At 1 CN the single local destination lets fix 4 splice the scan into the join. At N>1 nothing fuses (section 3.1).
2. A sender fragment runs to completion and parks its output on the GPU; only then are the remote destinations drained, one at a time, each `send_fragment` blocking (`compute_node_service.rs:1365-1385`). Exported batches leave the parked queue as they are packed (`src/sirius_ffi.cpp:706-725` -> `sirius_physical_streaming_sink.cpp:232-239 try_pull`), so a CN's own outbound drains free its remote-destined share progressively; only the local quarter and the fragment shell wait for the last release (`engine.rs:331-337`, `parked_registry.rs:158-178`). Measured: q17 senders end at 29.8-30.2 GB and the next fragment begins at 7.46-7.56 GB on all four CNs (`A/dp-cn4-six-stg48/engine-.cn*.log`).
3. A remote frame lands in the receiving CN's staging arena under a lease (`handle_staging_lease`, `compute_node_service.rs:1442`) and stays there until the receiver fragment runs: leases are released inside the receiver's Run, after `push_packed` deep-copies the frame into pool memory (`engine.rs:547-577`). The receiver is dispatched only when every one of its N senders has sent EOS (`local_exchange.rs:408-440 take_ready`; `compute_node_service.rs:1509`).
4. The arena's `lease()` is an address-ordered first fit that throws on exhaustion, with no wait and no retry (`src/exec/exchange_staging_arena.cpp:213-251`; `nixl_transport.rs:688` propagates the error out of the drain; the sending CN also leases one pack batch in its own arena, `fragment_executor.rs:79`).

So each CN's arena must hold, for every shuffled stream whose receiver has not yet run, (N-1)/N of that stream's per-CN output, plus one outgoing pack batch. This is independent of drain order. feat/pin-table-cn's `DrainTicket`/`dispatch_then_join` overlap changes when the RPC returns, not what is held (`C/experimental/starrocks/src/compute_node_service.rs:265-300`, its own comment: "ORDER IS UNCHANGED"; memory-for-latency trade at `:273-275`). Fix 2's retire path is what cleans up after the throw.

### 5.2 Measured at 4 CNs

16 GiB arena (`A/dp-cn4/runs/qNN.r0.err`, capacity 17,179,869,184 B in every message):

| query | failing call | requested MB | leases outstanding | held GB | occupancy | time to fail |
|---|---|--:|--:|--:|--:|--:|
| q05 | peer `request_staging_lease` | 655 | 31 | 17.06 | 99.3% | 1.41 s |
| q08 | own `export_packed` (largest free block 3 KB short of the request) | 664 | 43 | 16.03 | 93.3% | 2.39 s |
| q09 | peer `request_staging_lease` | 649 | 35 | 16.84 | 98.0% | 2.12 s |
| q17 | peer `request_staging_lease` | 654 | 26 | 17.02 | 99.1% | 1.32 s |
| q18 | peer `request_staging_lease` | 659 | 31 | 16.62 | 96.7% | 1.35 s |
| q21 | own `export_packed` | 422 | 47 | 16.94 | 98.6% | 2.10 s |

Every failure is during a lineitem drain, before any join receiver started (`A/dp-cn4/cnlog.txt`: all fragments are `sender`, no `result`). 19 of the 24 CN-side failures are a peer refusing a lease, 5 are the sending CN unable to lease its own pack batch.

Per-CN arena need, from the per-destination `transmitted batches via nixl` bytes of the passing 48 GiB runs (`N/arena-4cn-work/stg48-six-summary.txt`) and the measured co-residency of streams (which streams are still leased when the receiver that consumes them starts, from the receivers' `received remote batches` lines). Declared lineitem cardinality is 1.4999e9-1.5002e9 rows per CN in every case.

| query | lineitem stream per CN, inbound GB | other inbound streams co-held, GB | arena need per CN, GB | fits 17.18 GB | fits 51.54 GB |
|---|--:|---|--:|---|---|
| q05 | s9 31.4-31.6 | s7 0.51, s4 0.51, s1 0.22, s14 0.02 (s12 4.1 is the join output, lands after s9 is released) | ~32.5 | no | yes |
| q08 | s4 35.9-36.1 | s14 1.37, s19 0.23, small | ~37.9 | no | yes |
| q09 | s4 45.0 (3 x 15.0) | s18 3.38, s13 2.41, (s7 0.82 on a slow CN) | ~51-52 | no | no: 95 leases holding 50.55 GB, request 585 MB, largest free block 463 MB (`A/dp-cn4-six-stg48/runs/q09.r0.err`) |
| q17 | s1 22.4-22.5 | s9 3.00 | ~25.5 | no | yes |
| q18 | s10 18.0 | s1 6.77, s4 4.51, s15 0.75 | ~30 | no | yes |
| q21 | s13 13.5 + s4 14.25 + s2 14.25 (three lineitem-derived streams held together) | s7 1.10, s16 0.30 | ~43 | no | yes |

Rule that follows (**inference**, matches the table): a hash shuffle of S bytes over N CNs leaves S(N-1)/N^2 on each receiving arena per held stream, 3/16 of S at N=4. The retired sizing formula `96 GiB x SF/500 / N` (`C/bench/rtxpro6000-2gpu/SIRIUS-TUNING-RUNBOOK.md:249`) gives 48 GiB here, which happens to cover five and misses q09.

48 GiB / 90 GiB (`A/dp-cn4-six-stg48`): five pass 3/3 (section 2.2 times), q09 fails at 98.1% occupancy on a 122 MB contiguity shortfall (987 MB free in 11 blocks; `exchange_staging_arena.cpp:230-246` names this external fragmentation). Arena teardown peaks on the four CNs: 50.55 / 50.88 / 51.28 / 43.65 GB of 51.54.

72 GiB / 64 GiB (`A/dp-cn4-q09-stg72`, a true 4-CN run, 5/6/5/6 fragments per CN): q09 got past the arena and died in the GPU pool at the receiver. cn3's lineitem sender ended at `allocated=61,803,680,512` (peak 68.06 GB during the scan); the last remote EOS landed 8 ms later and the receiver was dispatched at once at `allocated=61,388,723,456`; it died after ~7 GB of `push_packed` copies at peak 68,698,126,080 B = the 64 GiB cap (`engine-.cn3.log:193,201,263`; `cluster.log` 22:10:49.733-49.787). cn3's own three outbound lineitem drains had not started (no `transmitted ... sender_id=1` line). So the 61.4 GB at receiver start is a dispatch-order effect (copy-in of 45 GB racing drain-out of 45 GB on one pool), not a structural bound: had the receiver waited for cn3's drains (~0.3-0.9 s), the pool need at receiver start would have been ~15 GB local quarter + 45 GB inbound + ~1.4 GB other = ~62 GB (**inference** from the per-batch release measured on streams 18 and 13 of the same run: 4.53 GB to 1.13 GB and 4.29 GB to 1.93 GB after the three outbound drains, `engine-.cn3.log:97,103,145,151`).

100 GiB / 72 GiB (`A/dp-cn4-q09-stg72-pool100`): degenerate 2-CN placement (section 1.4). The two lineitem senders each scanned a ~122 GB share and hit the pool cap (98.57 GB allocated of 107.37 GB, `engine-.cn0.log:230`). This is the first measured instance of a 2-CN sender-phase pool OOM and says nothing about 4-CN sizing.

Is q09 feasible at 4 CNs on GB200? Not shown infeasible. Under the current dispatch order it needs ~52 GB of arena and ~100-105 GiB of pool at once; `GPU_MEM=104GiB STAGING=52GiB` is 156.8 GiB of occupancy = 85% of the runbook's 184 GiB usable (`C/bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:72-98`), below the pinned kit's own SF1000 preset (128 + 32 = 160.8 GiB), and the pool100 arm measured 172.76 GiB in use on one GPU. Untested; the only measured 4-CN pool bound is > 64 GiB.

### 5.3 What feat/pin-table-cn did differently

Nothing in the data path of the exchange. `pin_table` is a scan cache (`C/experimental/starrocks/src/fragment_executor.rs:91`, `C/src/pin_table.cpp:306` "A pin caches the table UNFILTERED"); the FE plan is the same FILES() plan and lineitem is still hash-partitioned and staged. Its engine and CN have the identical receiver-first, hold-until-receiver-runs, copy-at-receiver exchange (`C/experimental/starrocks/src/engine.rs:275, 402-403, 623-639`), and the C++ arena differs from demo-plus only in comments. The difference is configuration and the reference's bookkeeping: the pinned 4-GPU SF1000 arm ran `GPU_MEM=128GiB STAGING=32GiB` (34.4 GB of arena, `C/bench/gb200-8gpu/SIRIUS-TUNING-RUNBOOK.md:14-23`, `C/bench/gb200-8gpu/sf1000/README.md:9` "16 GiB died at q05/q07") and recorded 19/22 with q08/q09/q21 refused, which is exactly the set whose arena need in the table above exceeds 34.4 GB (37.9 / 51-52 / 43) while q05 (~32.5), q17 (~25.5) and q18 (~30) fit. The 8-CN arm at 128/32 passed 22/22 (`C/bench/gb200-8gpu/sf1000/env.sh:1-3`), consistent with the per-CN share shrinking to 7/64 of S. The reference's `†` on q09 at 8 CN conflicts with that 22/22 and is left as an open discrepancy in the reference. SF1000 cannot be pinned at 1 GPU (283 GB > 198.7 GB, `C/bench/gb200-4gpu/HARDWARE.md:99-110`), which is why the 1-CN q05/q08/q09/q21 cells are filled.

So "feat/pin-table-cn ran most of them" means: same mechanism, twice the arena, and three of the six not actually run at 4 CNs.

### 5.4 The 2 x RTX PRO 6000 box (inference; no error text or GPU_MEM/STAGING in the inputs)

Card 97,887 MiB, of which 94.97 GiB is allocatable (`C/bench/rtxpro6000-2gpu/SF500-CONFIG-AND-ARCHITECTURE.md:6-7`); the kit's rule is `GPU_MEM = CARD - STAGING - 2 GiB`, arena outside the pool (`SIRIUS-TUNING-RUNBOOK.md:67-83`). A 16 GiB arena leaves at most ~77 GiB = ~83 GB of pool. Total lineitem projection P from standalone GPU_SCAN input (`P/agent-notes/standalone-failed-quent.txt`, report P1): 171.0 / 195.75 / 244.5 / 122.25 / 97.5 GB for q05/q08/q09/q17/q18; the 4-CN per-CN sinks are P/4 within 1%, so S = P/2 at N=2 is sound.

| query | per-CN lineitem share S = P/2, GB | sender phase vs ~83 GB pool | inbound S/2 vs 17.2 GB arena | predicted class |
|---|--:|---|--:|---|
| q05 | 85.5 | exceeds the pool while scanning | 42.8 | pool OOM in the scan |
| q08 | 97.9 | exceeds | 48.9 | pool OOM in the scan |
| q09 | 122.3 | exceeds | 61.1 | pool OOM in the scan (the pool100 2-CN placement measured this shape on GB200) |
| q17 | 61.1 | fits | 30.6 | arena exhaustion |
| q18 | 48.8 (+16.2 GB local halves of customer/agg/orders) | fits (~65 GB + scan overshoot) | 24.4 lineitem + 16.2 already leased = 40.6 | arena exhaustion |
| q21 | three lineitem-derived streams, ~55.6 GB per CN at N=4, ~111 GB of shuffle per CN at N=2 | fits barely | ~56 | arena exhaustion |

The model gives 3 "fail (OOM)" + 3 "fail"; the screenshot shows 4 + 2, with q18 in the OOM group. A STAGING around 28-30 GiB would flip q18 to a scan-phase pool OOM while leaving q17 on the arena, and the run's split is unknown, so q18's class cannot be settled from here. What holds regardless: even with the arena sized to the inbound bytes, the receiver-start pool need on the last-finishing CN is ~73 GB for q17, ~106 GB for q18 and ~129 GB for q21 against a pool that is at most 66 / 60 / 45 GB once the arena holds the inbound, and q05/q08/q09 exceed the pool in the scan. No STAGING/GPU_MEM split passes any of the six at 2 CNs on this card under the current exchange (`N/refute-F3-2cn-96gb.md`). The kit's own history on that box shows both classes: q08 `arena exhausted` and q09 `OOM at operator HASH_JOIN` unchanged by a 50% larger pool at SF100 (`C/bench/rtxpro6000-2gpu/SIRIUS-TUNING-RUNBOOK.md:287-317`).

## 6. What to do next

Ordered by value for the questions asked. Memory arithmetic each option must satisfy is stated where it applies.

1. **Port async sender dispatch (`9a5a4da6`) or otherwise return `exec_plan_fragment` before the sender's run and drains.** Recovers q06's +0.37 s and most of q03/q04's +0.1 s at 4 CNs; removes the FE-stage wave for any two-fragment gather plan and the per-connection RPC blocking. Not a memory change. Also fix `capture-arm.sh:14`, which exports a knob this branch does not read.
2. **Port `45dab3be` (Quent fragment labels, staging-lease and scan-read events, transmit-line fields) before this branch replaces perf/profile-sf1000 as the measurement tree.** Otherwise every per-query tool in `P/` reports empty.
3. **The six at 4 CNs on GB200, today, by sizing.** `STAGING=48GiB GPU_MEM=90GiB` passes five (measured). Document the rule: per CN, STAGING >= sum over co-held shuffled streams of (N-1)/N x per-CN stream output + one pack batch; for these six at SF1000 that is ~32.5 / 37.9 / 51-52 / 25.5 / 30 / 43 GB. For q09 try `GPU_MEM=104GiB STAGING=52GiB` (156.8 GiB occupancy, untested) with a CN readiness gate that waits for each CN's `compute node is READY` line rather than the FE's `Alive` flag; the pool100 arm lost two CNs to that race (**inference** on cause: FE blacklisted 10002/10004 at 22:21:26.1 on a mysql preamble statement, BRPC on those CNs came up at 26.25/26.33).
4. **CN change with the best ratio of effect to effort: dispatch a remote-EOS-readied receiver only after this CN's own outbound drains have completed.** Exported batches are already freed per batch, so this cuts the receiver-start pool need from own share + inbound (61.4 + 45 GB on q09 at 4 CNs) to local quarter + inbound (~62 GB) and would let q09 fit the default 100 GiB pool with a ~50 GiB arena (**inference** from `A/dp-cn4-q09-stg72` numbers). Cost: the latency `dispatch_then_join` on feat/pin-table-cn was buying (its own measurement: 3 x 30 ms per CN on q14 SF500).
5. **Copy-out on wire arrival: push each landed frame into pool memory and release the lease at once.** Makes the arena O(frames in flight), a few x 660 MB per peer, so 16 GiB would suffice for all six at 4 CNs. Two constraints: the pool must then absorb the inbound as it lands (on the slowest-scanning CN that is own share + inbound at the sender's end: 61.8 + 45 = ~107 GB for q09, up to ~113 GB with the measured scan overshoot), so the ~40 GiB freed from the arena has to move into the pool and item 4 no longer helps; and `push_packed` needs a built Fragment (`sirius_ffi.cpp:792-794`) while the receiver is translated only when its sender set completes (`compute_node_service.rs:1524-1530`), so this needs a pool-backed holding area independent of the receiver fragment.
6. **Do not ship a blocking or bounded-wait arena lease on its own.** Leases are freed only when the receiver runs, the receiver runs only after every sender's EOS, and a sender blocked in `rpc_request_lease` never sends EOS. It converts a 1.4-2.4 s failure into a wait for the bound or the PRPC timeout.
7. **For 96 GB 2-CN boxes, remove the bytes; sizing cannot help.** Three options reach that box: (a) fix 3 with a GPU-aware broadcast decision. Caveat: fix 3 as specified adds row counts only; with unknown column statistics the FE estimates part's string filters at 200M x 0.5 (or x 0.25), so the 15M-row arm of `checkBroadcastRowCountLimit` fires for q08/q09 and the broadcast must come from the bytes arm (`EnforceAndCostTask.java:317-325`, left >= 10x right, beNum = 1 for CNs); gate with `EXPLAIN COSTS`. q21's lineitem self-joins stay partitioned either way. (b) A streaming exchange that exports during the sender's run and consumes frames on arrival; receiver-only streaming is not enough because the sender still parks S first (the pool100 2-CN placement shows the lineitem scan alone hitting a 100 GiB cap). The receiver's exact-cardinality re-plan needs every input parked or staged before `build()` (`engine.rs:455-485`), so a build/probe rule for a streamed side is part of this work. (c) Spillable parked repositories (HOST tier), which make the six runnable wherever GPU + host covers the peak, at host bandwidth. Collect the `.err` texts and GPU_MEM/STAGING from that box first; they decide q18's class and whether the run used the kit's 60/32 split.
8. **Fix 1 needs a deliberate operator-loop OOM to be claimed.** Run q05 at 1 CN with `SIRIUS_CN_FRAGMENT_FUSION=off` (the spec's Arm C) and confirm an `OOM at operator ... reschedule` appears and the query dies in seconds. A sink-side `bad_alloc` like the pool100 one says nothing about fix 1.
9. **Decide on the merge-side canonical float-sum sort.** The dev base does not have it; the carve is 1.7 s of operator time faster on q20 and its merged FP64 sums depend on arrival order. Port it behind the `SIRIUS_CANONICAL_FLOAT_SUMS` gate or record the decision. It does not affect q15's flake either way.
10. **1-CN speed of the six.** q05 is 2.0x and q21 1.66x standalone. The un-fused stages that still park and re-read (q21's middle fragment 3.1 s parking 36.6 GB, q17's partial-avg 2.6 s, q18's group-by sender 1.2 s) are the target of the spec's `all` mode (PR 2). q05's fused fragment already spills ~107 GB to host on a 100 GiB pool.
11. **Correctness follow-ups unchanged from yesterday's report item 9:** exact decimal sums instead of FP64 lowering (the 1e-3 diffs on q01/q03/q05/q07/q09/q10/q15/q19) and computing q15's CTE once.
12. **Housekeeping.** Relabel `N/EXTRA-ARM.md` item 24 as a 2-CN placement. Re-run the 100/72 q09 arm with the readiness gate of item 3.

## 7. Files

- Arms: `A/dp-cn4`, `A/dp-cn1-six`, `A/dp-cn4-six-stg48`, `A/dp-cn4-q09-stg72`, `A/dp-cn4-q09-stg72-pool100`; each has `runs/runs.csv`, `runs/qNN.rK.out|.err`, `cluster.log`, `engine-.cnN.log`, `quent/`, `cnlog.txt|json`, `quent.txt|json`, `compare.txt`.
- Merged tables: `S/demoplus/compare-ref.md`, `S/demoplus/results-cn4.md`, `S/demoplus/results-six.md`, `S/demoplus/oracle-q16.log`.
- Analysis notes: `N/scaleout-4cn.md`, `N/arena-4cn.md`, `N/why-six-fail-multi-cn.md`, `N/six-on-1cn.md`, `N/profile-diff.md`; refutation notes `N/refute-*.md` (the corrected numbers above come from these).
- Tools written for this campaign (time-window based, because the branch lacks per-query labels): `N/profile_diff.py`, `N/compare_arms.py`, `N/plan_shapes.py`, `N/timeline.py`, `N/dispatch_skew.py`, `N/_dispatch_skew.py`, `N/percn_timeline.py`, `N/_scan_by_query.py`, `N/.pool_windows.py`, `N/arena-4cn-work/arena_summary.py`, `N/arena-4cn-work/_pool_at_query_starts.py`.
- Baseline bundle: `P/results.md`, `P/cn4-cnlog.txt`, `P/cn4-quent.txt`, `P/card-compare-cn4.txt`, `P/survey/plan-summary*.txt`; report `/home/prestouser/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md`; fix specs `S/fix/designs/*-SPEC.md`.
