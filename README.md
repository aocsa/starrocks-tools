# starrocks-tools: harness, analysis tools, specs and reports for StarRocks-on-Sirius

Everything needed to reproduce the 2026-09-03 StarRocks-on-Sirius work on a new box: the multi-CN carve check
(demo/q1q6-integration), the SF10/SF100/SF1000 perf campaign, the SF1000 planning / cardinality / backpressure
profiling, and the design + implementation of the four top fixes. The code lives in the `aocsa/sirius` fork
(branches listed below); this repo holds the scripts, the analysis tools, the workflow scripts, the specs, the
plans, the reports, the DuckDB oracle answers and the small evidence files.

The GB200 box these were written on (4x GB200, aarch64, CUDA 13, 1.7 TB RAM, 72 cores) went down for
maintenance on 2026-09-03 19:00 UTC. Paths below are that box's; every script has them at the top.

## Code branches on github.com/aocsa/sirius

| branch | base | what |
|---|---|---|
| `demo/q1q6-integration` (+ base `demo/q1q6-base` = origin/dev 98fe1a84) | dev | the carve check: 12 open PRs merged + 10 carved commits; Draft PR aocsa/sirius#3 (do not merge) |
| `perf/float-sum-canonicalize-flag` | d24f02c4 (feat/pin-table-cn) | SIRIUS_CANONICAL_FLOAT_SUMS gate (85658f09) + SIRIUS_CN_ASYNC_SENDER_DISPATCH (9a5a4da6) |
| `perf/quent-instrument` | d24f02c4 | Quent probes: scan reads, stream hops, staging leases, CN fragment labels, explicit enable_quent (784cf116) |
| `perf/profile-sf1000` | perf/float-sum-canonicalize-flag | + the Quent probe commit cherry-picked (45dab3be); the build every SF1000 arm used |
| `fix/oom-failfast` | perf/profile-sf1000 | fix 1: fail the query fast when an OOM retry cannot make progress (19f9eee4, engine, Catch2 tests); Draft PR aocsa/sirius#4 |
| `fix/parked-bookkeeping` | perf/profile-sf1000 | fix 2: retire a failed query's parked output; cancel_plan_fragment tears the query down (63e7e0c1, 0f4b1c19); Draft PR aocsa/sirius#5 |
| `fix/files-cardinality` @ cde7ab22 | perf/profile-sf1000 | fix 3: real FILES() cardinalities in the FE (7f38171c FE patch, 00302c5c CN footer row total, cde7ab22 review fixes); reviewed (1 major fixed, 6 minor) and verified; Draft PR aocsa/sirius#6 |
| `fix/fragment-fusion` @ 45dab3be | perf/profile-sf1000 | fix 4: not started, branch equals its base (spec in `fix/fragment-fusion-SPEC.md`) |
| `demo/q1q6-integration-plus-fixes` @ 9c002f97 | demo/q1q6-integration | the demo plus fix 1 cherry-picked (9c002f97). Fix 2, fix 3, the Quent probes and the two perf flags do NOT cherry-pick onto the carved tree (conflicts in the CN files the carve rewrote, in the aggregate code and in docs); unbuilt and untested |

## Layout of this repo

- `docs/plans/` the plans (carve-up plan `i-want-to-create-golden-crane.md` + appendix, `open-prs-review-order.md`,
  `demo-q1q6-report.md`, `starrocks-sirius-perf-plan.md`, `sf1000-top4-fixes-plan.md`, handoffs).
- `docs/reports/` the SF1000 report `sf1000-planning-cardinality-backpressure.md` (start here), the Quent instrumentation
  report, the perf sub-reports (trace schema, throttles, dataflow sim, sim-vs-run).
- `docs/session-notes/` the durable facts learned on the box (env gotchas, Quent behaviour, decimal truncation, SF1000 facts).
- `scripts/cluster/` bring a Sirius CN cluster up/down and run a bench arm: `cluster-env.sh`, `start-cluster.sh`,
  `stop-cluster.sh`, `run-step3.sh` (warm 3 + cold-restart + oracle compare + cn-distribution), `cn-build.sh`.
- `scripts/sf1000/` the SF1000 campaign: `survey.sh` (translate-only + EXPLAIN COSTS/VERBOSE x22), `run-queries.sh`,
  `capture-cn.sh`, `capture-standalone.sh` + `standalone_run_labeled.py`, `driver-rest.sh`, `driver-followup.sh`,
  `analyze-arm.sh`, and the extractors `cnlog_extract.py`, `quent_bp.py`, `card_compare.py`, `explain_summary.py`,
  `results_table.py`.
- `scripts/demo/` the carve-check Step 3 driver and the DEMO.md two-CN smoke.
- `tools/perf/` the SF10/SF100 campaign runners and YAMLs, `quent_summary.py`; `tools/dataflow-sim/`, `tools/sim-vs-run/`.
- `workflows/` the Claude Code workflow scripts (carve, Quent instrumentation, SF1000 analysis, fix design, fix implementation)
  and the per-piece carve specs.
- `fix/` the four implementation specs, `INTEGRATION.md` (landing order + SF1000 verification matrix), `DECISIONS.md`,
  the verification arm runner `capture-arm.sh` and `check-V1.py`, `prep-worktrees.sh`, design drafts, implementer/reviewer notes.
- `evidence/sf1000/` the pre-digested SF1000 evidence (results tables, plan summaries, FE estimate vs actual per exchange,
  per-run CN-log extracts, Quent extracts, compares, all 22 EXPLAIN COSTS/VERBOSE at 1 and 4 CNs, analysts' notes and REPORT.md);
  `evidence/campaign/` the carve-check and perf-campaign notes.
- `oracle/` DuckDB CPU answers (TSV) per dataset for `compare.py`.

## Replicating on a new box

1. **Machine.** Linux, NVIDIA GPUs with CUDA 13 driver, pixi, Java 21 for the FE runtime, a fast local disk for outputs.
   Clone `aocsa/sirius` as `sirius-stacks` and use `git worktree add ../sirius-stacks-wt/<name> <branch>` per branch; in each
   worktree: `ln -s <clone>/.pixi .pixi`, `git submodule update --init --recursive`,
   `bash experimental/starrocks/scripts/apply-starrocks-patches.sh`. See `fix/prep-worktrees.sh` for the exact sequence.
2. **Engine and CN.** `pixi run make` at the worktree root (extension at `build/release/extension/sirius/sirius.duckdb_extension`).
   CN: `scripts/cluster/cn-build.sh <worktree>/experimental/starrocks build --release` run through
   `env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn bash ...`; it sources
   `scripts/cn-env.sh`. nixl/UCX must be installed under `TOOLS_DIR` (`cn-env.sh` derives `<repo>/../tools`; export
   `TOOLS_DIR` explicitly for worktrees). Rust bindings tests on this box needed
   `RUSTFLAGS="-C link-arg=-Wl,--allow-shlib-undefined"` and `LD_LIBRARY_PATH` to the extension dir and the pixi env lib.
3. **FE.** `cd experimental/starrocks && pixi install -e fe && pixi run -e fe fe-build` (about 280 s; output under
   `starrocks/output/fe`). The FE reads `conf/fe.conf`; delete `output/fe/meta` for a fresh metadata store.
4. **Data.** Decimal TPC-H parquet, one directory per table (`<root>/<table>/*.parquet`); generated with
   `bench/common/gen-tpch.sh` from the perf branch. f64 copies for oracle-exact q01: `scripts/demo/make-f64-datasets.py`.
   Oracle answers: `bench/rtxpro6000-2gpu/tools/oracle.py <queries_dir> <data> <out> qNN...` (48 threads, 380 GB).
5. **Cluster.** `source scripts/cluster/cluster-env.sh <worktree>` then `experimental/starrocks/benchmarks/cluster8.sh`
   (NUM_CNS, GPU_MEM, HOST_MEM, STAGING; `CUDA_VISIBLE_DEVICES` must be unset; one CN per GPU). `scripts/cluster/run-step3.sh`
   is one complete bench arm. Rules that cost us time: never run two FEs (port 9030); never share a GPU between a cluster and a
   standalone run; SIGTERM the CNs and wait for exit so Quent flushes; ad-hoc CN launches must source `cn-env.sh` or die on libnixl.
6. **SF1000 profiling.** Follow `evidence/sf1000/WORKFLOW-PLAN.md` (what was run, in what order, with which knobs) and the
   scripts in `scripts/sf1000/`. Quent is on with `SIRIUS_CN_ENABLE_QUENT=1` on `perf/profile-sf1000` builds; sessions land
   under `<engine-dir>/telemetry/<session>/<record type>/*.ndjson`; `quent_bp.py` and `cnlog_extract.py` read them.
7. **Fixes.** `fix/INTEGRATION.md` section 6 is the verification matrix (arms V1..V6) with pass criteria; `fix/capture-arm.sh`
   runs one arm; `fix/check-V1.py` checks fix 1. Landing order: fix 1, fix 2, fix 4 (on fix 2), RIGHT_SEMI translator arm, fix 3.

## Where the numbers are

`docs/reports/sf1000-planning-cardinality-backpressure.md` (SF1000 findings, ordered to-do), `docs/plans/starrocks-sirius-perf-plan.md`
(SF10/SF100/SF1000 baselines and the two measured fixes), `docs/plans/demo-q1q6-report.md` (carve check),
`evidence/sf1000/results.md`, `results-ratios.md`, `card-compare-*.txt`.

## Raw evidence archive

The complete session scratchpad (raw Quent sessions, fragment dumps, cluster and engine logs, 1.4 GB uncompressed, 126 MB zstd) and
the Claude session transcript are attached to release `v2026-09-03-shutdown` of this repo, and also copied to
`/scratch/prestouser/aocsa/session-archive-2026-09-03/` on the GB200 box (with `claude-plans/` and `claude-memory/`).
