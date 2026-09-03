# Plan: best solutions for the four SF1000 blockers, and the workflows that deliver them

Written 2026-09-03 15:20 UTC from the report `starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md`
(sections 3B, 3C, 4). Evidence bundle: session scratchpad `perf/sf1000/` (indexed by `WORKFLOW-PLAN.md` there).
Working tree for all code: `/home/prestouser/aocsa/sirius-stacks-wt/fix-*` worktrees of the sirius-stacks clone,
one branch per fix off `perf/profile-sf1000` (45dab3be = d24f02c4 + aggregate gate + async dispatch + Quent probes).
Never the shared clone `/home/prestouser/aocsa/sirius`. No pushes, no PRs until asked.

## The four issues

| # | issue | where it lives | measured cost today (SF1000, 1 CN, 100 GiB pool) |
|---|---|---|---|
| 1 | The OOM reschedule loop has no feasibility check | `src/pipeline/gpu_pipeline_executor.cpp` (:263 reservation gate, :330-420 reschedule, MAX_RETRIES=100, 50 ms sleep) | q05 q08 q09 q17 q18 q21 take 54-232 s to die (775 s of a 910 s sweep); 17,917 "0 bytes freed, proceeding with partial reservation" lines |
| 2 | No per-query parked-output bookkeeping | `experimental/starrocks/src/engine.rs` (:291-316 process-wide wipe on Err), `compute_node_service.rs` (:449-476 cancel is result-store only), `local_exchange.rs` (removals only in take_ready) | 0-38 GB of a dead query's parked output survives into later queries; dead query's queued senders still run (q22 cold 4110 vs 943 ms) |
| 3 | The FE plans every FILES() scan at 1 row | StarRocks FE `StatisticsCalculator.computeFileScanNode` (:664-676), `TableFunctionTable.java` (file sizes at :611, schema RPC), `PGetFileSchemaResult` (proto :647-650), CN `file_schema.rs` | every distribution decision is data-blind: 1 CN broadcasts 3.79B lineitem rows into q04's semi join, 4 CNs shuffle 3.23B rows for q03; all six failures are one plan shape |
| 4 | Six queries cannot run on 1 CN | fragment cut (FE) + receiver-first full materialisation (`engine.rs` run_fragment_inner, `local_exchange.rs`) + parked repositories outside the downgrade sweep (`src/exec/streaming_fragment.cpp:103-112`, `src/downgrade/downgrade_executor.cpp:223-300`) | q05 q08 q09 q17 q18 q21 OOM; standalone Sirius runs them in 2.7-6.5 s |

## Candidate solutions to weigh (input to the design workflow, not the decision)

1. **Fail fast.** (a) Abort at the reservation gate when `retry_count > 0 && freed == 0 && retry floor > grant`; (b) abort in the reschedule path when downgrade freed 0 and pool usage == limit and nothing completed since the previous attempt; (c) a bounded wall-clock/attempt budget per original task that scales with observed progress; (d) keep 100 retries for the batch-lock contention case (arrives via `rmm::out_of_memory` from `lock_or_prepare_batch`, not via the gate). Must produce an error that names parked bytes vs pool.
2. **Bookkeeping.** (a) `query_id` on every `ParkedOutput`, drop only the failing query's slots; (b) failed-query set on the engine thread that skips pending `Run`s; (c) `cancel_plan_fragment` drops the cancelled query's parked outputs and dequeues its fragments (needs a by-query removal in `LocalExchange`); (d) `drop_parked` in `run_ready_fragment`'s Err arm for pre-run translation failures; (e) a periodic sweep as a safety net. Order of landing matters: (a)+(d) are pure hygiene, (b)/(c) touch the dispatch contract.
3. **Cardinalities.** (a) FE-only: rows = total bytes / row width from the inferred schema in `computeFileScanNode`, using `TableFunctionTable` file sizes (no wire change; column stats stay UNKNOWN); (b) exact `num_rows` from parquet footers: new field in `PGetFileSchemaResult`, filled by the CN's `file_schema.rs` (proto + FE + CN); (c) later, per-column min/max from row-group stats; (d) no-FE alternative: CN-side translator refusal/hints, SQL join hints. Delivered as a patch in `experimental/starrocks/patches/` applied by `apply-starrocks-patches.sh`, so the FE must be rebuilt (`pixi run fe-build`). Predicted effect must be checked with `EXPLAIN COSTS` at 1 and 4 CNs: the source skeptic warns that `BROADCAST_JOIN_MEM_EXCEED_PENALTY` and zero single-node shuffle cost may still put lineitem across an exchange on 1 CN.
4. **Run the six on 1 CN.** (a) Same-node fragment fusion before translation: replace a receiver's `EXCHANGE_NODE` with its single local sender's node list at the `TPlan` level when every destination is this CN, so the fused plan is the standalone plan (needs `RIGHT_SEMI` in the translator for q21, finding P5); (b) spillable parked repositories: register streaming-sink outputs and receiver inputs with a registry the downgrade executor sweeps as a third tier, HOST-tier `output_row_count` and `export_packed` (five of six then fit in GPU+HOST = 279 GB; q09 marginal); (c) streaming receivers (build side first, probe streamed) is the long-term fix but touches the engine's single-flight lifecycle; (d) FE-side: real cardinalities (issue 3) may already move lineitem to the probe side of a broadcast join, which the engine can stream. The design must say which combination actually makes each of the six pass and what each costs.

## Evaluation criteria (judges score 1-5 each)

1. Does it fix the measured failure at SF1000 on this box (which queries, predicted numbers, how verified)?
2. Correctness risk and blast radius (which paths change for the 15 passing queries; determinism; cancellation).
3. Size and reviewability as an upstream PR (target layer in the carve plan: engine `src/` -> dev PR; CN `experimental/starrocks` -> fork PR; FE -> checked-in patch), tests it needs.
4. Effort and dependencies (what must land first; FE rebuild; proto change).
5. Measurability: an acceptance test that a reviewer can rerun in minutes plus the SF1000 arm.

## Acceptance tests (must exist before implementation is "done")

| fix | unit / component test | SF1000 verification (orchestrator-run, one arm at a time) |
|---|---|---|
| 1 | Catch2 test in `test/cpp/pipeline/` driving a task through the reschedule path with a memory space that never frees: query fails within a bounded number of attempts with the new error text; contention case still retries | 1 CN, q05 q08 q09 q17 q18 q21: time-to-fail <= 5 s each (was 54-232 s), error names parked bytes; the 15 passing queries unchanged (warm medians within noise, `compare.py` set unchanged) |
| 2 | Rust tests in `engine.rs`/`local_exchange.rs`/`compute_node_service.rs`: failure drops only that query's slots; queued senders of a failed query are skipped; cancel drops parked outputs; translation failure drops slots in hand | 1 CN sweep in the same order as before: `[gpu_pool] allocated` returns to baseline after each failure (was +4.7..+38 GB), q22 cold no longer inflated, no `fragment run started` for a dead query after its failure |
| 3 | FE unit test (Java) for the new statistics path; CN test if the proto changes; `EXPLAIN COSTS` golden outputs for q03 q04 q05 q22 at 1 and 4 CNs showing real cardinalities and the new join sides | 1 CN and 4 CN sweeps of the 15 passing queries plus the six: plan shapes recorded, timings vs the campaign table, oracle compares |
| 4 | Fusion: translator/CN tests that a single-destination local exchange fuses and the fused plan matches the sender+receiver plan; spill: Catch2 test that a parked repository is swept to HOST and relayed back | 1 CN: q05 q08 q09 q17 q18 q21 pass with oracle MATCH; report their times against standalone (2.7-6.5 s) |

## Workflows

**A. Design exploration (`fix/design-workflow.js`).** Per issue, three independent designers with different angles (surgical minimum, robust design, what upstream would accept), each reads the report finding, the evidence and the code and returns a design (mechanism, files, tests, risks, predicted effect). Two judges per issue score the three against the criteria and pick or merge. One synthesizer per issue writes the spec `scratchpad/fix/designs/<issue>-SPEC.md`. A final integrator checks the four specs for interactions (fail-fast vs bookkeeping ordering; fusion vs spill vs cardinalities) and writes the build order.

**B. Implementation (`fix/implement-workflow.js`).** Per issue in its own worktree: implementer (tests first, then code, build, run the unit tests; commits with Conventional Commits and the Claude trailer), adversarial reviewer, fixer, verifier (fresh build + tests + clippy/fmt/pre-commit). Agents never start clusters or use GPUs other than the one assigned to their worktree (fix 1: GPU 0, fix 2: GPU 1, fix 3: none, fix 4: GPU 2). The orchestrator then runs the SF1000 arms from the table above, one at a time, and an integration branch with all four for the final sweep.

**C. Report.** `starrocks-sirius-perf/sf1000-top4-fixes-report.md`: per fix, what changed, tests, SF1000 before/after, what is left; plus the PR plan (branch, base, size) for when pushing is asked for.

## Status log
- 15:20 UTC: worktrees `fix-oom-failfast`, `fix-parked-bookkeeping`, `fix-files-cardinality`, `fix-fragment-fusion` being created and built; design workflow launched.
- 16:55 UTC: design workflow done (25 agents): specs in scratchpad `fix/designs/{oom-failfast,parked-bookkeeping,files-cardinality,fragment-fusion}-SPEC.md`, `INTEGRATION.md` (landing order fix 1 -> fix 2 -> fix 4 on fix 2 -> RIGHT_SEMI translator arm -> fix 3), open items resolved with spec defaults in `DECISIONS.md`. FE builds from a fix worktree in 278 s (`pixi run -e fe fe-build`). Implementation workflow launched for fixes 1, 2, 3; fix 4 follows on fix 2's branch.
