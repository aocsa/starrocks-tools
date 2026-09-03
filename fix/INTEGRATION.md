# Integration of the four SF1000 fix specs: order, conflicts, combined verification

Written 2026-09-03 after reading `oom-failfast-SPEC.md`, `parked-bookkeeping-SPEC.md`,
`files-cardinality-SPEC.md`, `fragment-fusion-SPEC.md`, the plan
(`~/.claude/plans/sf1000-top4-fixes-plan.md`) and the report sections 3B/3C/4. Every `file:line` was
re-read in `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be`; all four `fix-*` worktrees are at
the same commit and clean (`git log -1`, `git status` on each). "Measured" = copied from a named file
under `scratchpad/perf/sf1000/` (`E/`); everything else is inference and says so. Section 7 lists the
edits made to the four spec files.

## 0. Verdict in five lines

1. The four specs are compatible. Two contradictions and one wrong prediction had to be fixed in the
   specs (section 2.7); none requires a redesign.
2. Land in the order **fix 1 -> fix 2 -> fix 4a -> (item 5) -> fix 3**. Fix 1 has no shared files; fix 2
   establishes the by-query removal that fusion must reuse; fix 3 is last because its FE rebuild takes
   hours, its `EXPLAIN` gate must precede any 1-CN sweep, and its plan changes invalidate fusion's golden
   counts (so fusion is measured against today's plans first).
3. Only fix 4a changes the pass/fail set at 1 CN (q05 q08 q17 q18 pass, q09 probably, q21 probably not).
   Fixes 1-3 change time-to-fail, hygiene and plan shape. No two fixes fight on the six.
4. Two source files are edited by two specs each (`compute_node_service.rs`: fixes 2, 3B, 4a;
   `local_exchange.rs`: fixes 2, 4a) plus two docs (`TUNABLES.md`: 3, 4; `DEMO.md`: 2, 3, 4). All are
   resolvable by ordering; the one semantic overlap (`cancel_plan_fragment`) is resolved in favour of fix 2.
5. The combined verification is six arms, ~2 h of box time excluding the FE build, in the order of
   section 6. The final integration sweep is the first time the six are ever run at 4 CNs.

## 1. Which fix rescues which of the six (1 CN, 100 GiB pool)

| query | today (measured, `E/cn1/runs/runs.csv`) | fix 1 | fix 2 | fix 3 (knob on) | fix 4a `leaf` | all four |
|---|---|---|---|---|---|---|
| q05 | OOM at 101.5 s | fails at ~2.9 s | no change (leak 0 GB before it) | still hash-partitions lineitem (inference, fix 3 App. B) | **pass** (high) | pass |
| q08 | OOM 168.3 s | ~4.3 s | leftovers of q05 (4.7 GB) gone | same | **pass** (high) | pass |
| q09 | OOM 232.5 s | ~4.9 s | q08 leftovers (8.8 GB) gone | same | **pass** (medium; ~31 GB of broadcasts still parked) | pass (medium); `leaf-any` is the fallback |
| q17 | OOM 103.9 s | ~2.6 s | q09+q16 leftovers (38.2 GB) gone | same | **pass** (high) | pass |
| q18 | OOM 54.2 s | ~10.2 s (own senders first) | no leftovers today | same | **pass** (high) | pass |
| q21 | OOM 114.5 s | ~4.0 s | q17 leftover (4.0 GB) gone | same | **likely still OOM**: fused F07 builds on l3 (inference, fusion spec 9.2) | fails, but see 2.2: not necessarily fast |

Sources: fix 1 table 3.6; fix 2 section 5.1 and report B4 ("no pass/fail was decided by the leak");
fix 3 non-goal 1 and Appendix B; fusion section 1 and 7. Fix 3 at 4 CNs: the six were never run at 4 CNs
(report 5.1); the expected outcome is pass regardless of fix 3 (per-CN lineitem share 31-61 GB, inference).

**Do two fixes fight on the six?** No.

- Fix 1 and fix 4a divide labour by fix 1's rule 5 (`freed_by_downgrade != 0 -> keep retrying`): a fused
  fragment holds its join build in inter-pipeline repositories that the downgrade sweep can spill
  (`src/pipeline/repository_wiring_materializer.cpp:68-69` registers each wiring port with the per-query
  manager; TIER 1 iterates those managers, `src/downgrade/downgrade_executor.cpp:223-232`), so the
  fail-fast stays quiet while spilling makes progress and only fires when nothing is convertible. That is
  the intended division, but it also means the fusion spec's "with fix 1 in the same build q21 dies in
  seconds" was wrong (2.2).
- Fix 3 and fix 4a are complementary at 1 CN: fix 3 is predicted (inference, fix 3 risk 8.1) to flip q03
  and q12 from BROADCAST to PARTITIONED, which would park ~78 GB / ~36 GB of lineitem / orders; that
  shape (bare leaf, `HASH_PARTITIONED`, one local destination) is exactly what `leaf` fuses, so with both
  in the build those two keep streaming. Fix 3 alone at 1 CN with the knob on is a regression risk for q03.
- Fix 2 and fix 4a overlap in code, not in effect: a deferred plan parks nothing, so fusion shrinks fix 2's
  exposure at 1 CN; fix 2's cancel path must remove deferred plans, which its per-instance
  `retire_receiver` already does (2.3).

## 2. Interactions checked

### 2.1 Fix 1 (fail fast) vs fix 2 (Err-path bookkeeping): compatible

- **Same Err path, earlier and with a new text.** Fix 1 exits through `completion_handler::report_error`
  exactly like the `MAX_RETRIES` path (fix 1 spec 3.4 "same exit shape"), so the chain
  `sirius_engine.cpp` rethrow -> `streaming_fragment::run` -> `ffi::Fragment::run` ->
  `engine.rs:693-695` (`"failed to execute fragment: {err}"`) is unchanged. Fix 2's `Err` arm
  (`engine.rs:291-316` today) sees the same `Err`, ~100 s earlier. Fix 2's design does not depend on when
  the `Err` arrives (its spec section 8 said so; fix 1 spec section 8 asked for exactly that).
- **Which siblings had parked when the Err lands is decided by queue order, not time.** The dispatch
  worker is one thread popping a FIFO (`compute_node_service.rs:380-391`, `mpsc::channel` at `:259`), so the
  senders ahead of q05's lineitem sender in the inbox had finished and parked (`slots=2`, measured in the
  campaign's wipe WARN) and the ones behind it had not, whether the sender dies after 100 s or 2.7 s. Fix 2's
  acceptance A4 (`q05 trigger=engine_err fragments=2 slots=2`) therefore holds under fix 1. Inference from
  the single-thread structure; the fix1+2 arm confirms it.
- **Fix 1's text becomes fix 2's `cause`.** The ~600-character reason string (fix 1 spec 3.1) is stored per
  retired query (`RetiredQueries`, 1024 entries), copied into each poisoned-slot message and into gate 4's
  `"query {q} already failed on this CN: {cause}"`. Size is irrelevant (< 1 MB worst case); fix 1's client
  greps (`gave up after`, `held outside any task reservation`, `freed 0 bytes`) still match because the
  failing fragment's own error is what the FE reports first; gate 4's wrapped text is only returned for
  late arrivals, which the FE ignores while cancelling (`DefaultCoordinator.java:1054-1057`, fix 2 spec 5.2).
- **Fix 1's `has_error()` early-out** (`gpu_pipeline_executor.cpp:131-139` insertion) drops queued tasks of
  the failed query inside the engine; it never changes what `Fragment::run()` returns. No effect on fix 2.
- **Timing numbers that move.** Fix 2's arm C criterion C6 ("sweep wall shorter by ~11.9 s") was written
  against the campaign; with fix 1 in the build the sweep is ~745 s shorter for a different reason. Fix 1's
  arm A wall bounds (criterion 3) include leftover-fragment time that fix 2 removes (q17 -> q18 6.42 s,
  measured `E/agent-notes/refute-oom5/notes.md`). Both specs already made their primary criteria immune
  (fix 1: measured from the first OOM in the engine log; fix 2: `base@start` and per-id `fragment run
  started` counts). C6 is rewritten (section 7).
- **Landing-order contradiction resolved.** Fix 1 spec 8 says "land first"; fix 2 spec 10 said "land fix 2
  before fix 1's SF1000 time-to-fail arm". Both are satisfiable: code lands fix 1 then fix 2 (no shared
  files), fix 1's arm A/B run once on the fix-1 build for attribution, and fix 2's arm C on the fix1+2
  build re-measures the six (it is the report's before/after row). Fix 2 spec 10 edited accordingly.

### 2.2 Fix 1 vs fix 4a: fix 1 does not make a fused q21 die fast

The fusion spec (8, arm A; 10) assumed a fused q21 that OOMs "with fix 1 in the same build dies in
seconds". That holds only when the downgrade frees 0 bytes. Today's six die with `freed == 0` because a
`GPU_SCAN -> STREAMING_SINK` leaf registers no repository (`src/exec/streaming_fragment.cpp:103-112`,
report B2). A fused F07 is a join fragment: its build-side partitions sit in inter-pipeline port
repositories (`repository_wiring_materializer.cpp:68-69`), which TIER 1 converts to HOST (160 GiB
configured, empty). So the reschedule path will see `freed > 0`, rule 5 keeps retrying, and q21 either
passes slowly through spilling or spends up to 100 retries x spill rounds before the cap. Which of the
two happens is not predictable from the evidence (report 5.10: spill throughput never measured). Consequences:

- Fusion arm A must record for q21: the `HASH_JOIN (id=10)` build port, the count of `after downgrade
  (N bytes freed)` lines with N > 0, `[host_pool]` growth, and time-to-fail or pass. It is a data point, not
  a pass criterion (fusion decision 1 already says q21 is recorded, not required).
- Fix 1's arm A bound ("one retry round after the first OOM") is a claim about today's plan shapes only.
  Fix 1 spec section 8 edited to say so.
- If q21 turns out to thrash (many rounds, each freeing a little), that is fix 4b's / the spill policy's
  problem, not a fix 1 regression: today q21 takes 114.5 s to die; fix 1 never makes anything slower.

### 2.3 Fix 2 vs fix 4a: the same two files, one semantic overlap

Verified anchors (`compute_node_service.rs`): `exec_single_attachment :705-717` calls
`try_dispatch_sender` (`:309-329`) **before** `process_fragment` (`:918-959`); `run_ready_fragment :864-911`
wraps `execute_ready_fragment :1325-1394`; `cancel_plan_fragment :457-479`. `local_exchange.rs`:
`SenderSource` enum `:26-48` with `names() :52-56` and `is_complete() :59-64`; `take_ready` removals `:282-308`.

| place | fix 2 | fix 4a | resolution |
|---|---|---|---|
| `cancel_plan_fragment` | full teardown: `results.cancel_query`, `exchanges.retire_receiver(id)` -> `release_sources`, `executor.retire_query` (commit 2, 4.4g) | inserts `exchanges.forget_query(query_id)` (4.6) | **fix 2 owns it.** `retire_receiver` removes every `sources` entry of the cancelled receiver, which includes a deferred `LocalPlan`; a `LocalPlan` holds no GPU memory and no lease, so `release_sources` needs no arm for it (write the release as `if let SenderSource::Remote {..}` so a third variant compiles). Fusion 4.6 and `forget_query` are dropped; the fusion spec's own section 10 already offered this. |
| `execute_ready_fragment` | restructures: split `ready.inputs` before translation, `StagedLeases` RAII guard (4.4e) | prepends `fold_deferred_plans(ready)` returning `(params, streamed, fused)` (4.5) | fold first, then fix 2's split/guard over `streamed` and `&params`; `dump_fragment(&params)` dumps the fused plan. Pure sequencing; fusion rebases. |
| `run_ready_fragment` | gate 3 at the top: `failure_of(query_id)` -> `release_staged(ready.inputs)`, skip (4.4c) | fused receivers arrive here through `dispatch`/`dispatch_then_join` like any ready receiver | gate 3 covers fused receivers for free; `release_staged` sees a `LocalPlan` in `ready.inputs` and must ignore it (same `if let` rule). |
| `process_fragment` | gate 4 after the translate-only block, before `receiver_exchanges` (4.4d) | `try_defer_sender` hook after the receiver branch, before `translate_fragment_logged` (4.4) | gate 4 runs first on the inline and batch paths, so a dead query's leaf is refused before it can be deferred. |
| `try_dispatch_sender` (async path, the campaign setting) | untouched by fix 2 | hook after `resolve_descriptor_table`, before `dispatch` | **gap:** on the async path the fusion hook runs before any fix 2 gate. A leaf of a dead query would be deferred, the receiver readied, and only then skipped by gate 3 (no GPU work, but a misleading `fused sender fragment` INFO line and a needless `LocalPlan` clone). Fixed by adding a `failure_of` check as the first policy step of `try_defer_sender` (fusion spec 4.4 edited; fix 2 spec 10 asked for exactly this). |
| `SenderSource` | `release_staged`/`release_sources` iterate `Remote`; `retire_receiver` returns `Vec<SenderSource>` | adds `LocalPlan(LocalPlan)`; extends `names()`/`is_complete()` | additive; fix 2 writes its matches non-exhaustively. |
| `ExchangeState` | `retired: HashSet`, `retired_order`, `retire_receiver`, `is_retired` | `offer_local_plan`, (`forget_query` dropped) | disjoint methods on one struct; textual merge only. |
| `engine.rs` | rewritten around `ParkedRegistry` | not edited (fusion spec 4.8) | none. |
| `lib.rs` (CN) | `mod parked_registry;` | none (fusion adds `pub mod fusion;` to the **translator** crate's `lib.rs`) | none. |
| test fixtures | `Retiring<E>` wrapper; tests built on `propagation_chain :3311` (sinks are `data_stream_sink`, UNPARTITIONED, `:3806`) | `hash_partitioned_data_stream_sink`, `RecordingRunExecutor`, `FailingExecutor` (no name clash: today only `FailingIntermediateExecutor :2000`, `RecordingExecutor :2914`, `CountingExecutor :1810` exist) | fusion's `Tunables` default is `Leaf`, which fuses only `HASH_PARTITIONED` sinks, so every fix 2 test keeps parking. Rule for both: fix 2 tests never use a hash-partitioned single-destination local leaf unless they call `set_fragment_fusion(Off)`. |
| `DEMO.md:37-42` | new bullet under "What it does not exercise yet" | rewrites the "Sequential fragments" bullet at `:39-40` | adjacent lines: fusion rebases. |

Gate 1/gate 2 (engine thread) and the `RetireQuery` request are untouched by fusion. A fused receiver that
fails is attributed to the receiver's ids (fusion 4.8), so `fail_fragment` -> `retire_query` works
unchanged; a deferred sender never ran, so there is nothing of it for fix 2 to drop.

### 2.4 Fix 3 vs fix 4a: complementary, but fusion's goldens are for today's plans

- **Six at 1 CN.** Fix 3 predicts (Appendix B, inference re-derived by both judges) that the FE keeps
  hash-partitioning lineitem at 1 CN whenever the other side exceeds ~5M rows; the six keep the P1 shape and
  fusion still applies. If the prediction is wrong and lineitem ends up inside the join fragment, there is
  nothing to fuse and the query passes anyway.
- **Newly flipped joins.** Fix 3 predicts q03 / q12 (and possibly q07 / q10) flip to PARTITIONED at 1 CN
  with the knob on (risk 8.1, Appendix A). Those become `HASH_PARTITIONED` single-destination leaves -> fused
  by `leaf`. So fix 4a must be in any 1-CN build that runs with the knob on (fix 3 spec step 4 edited).
- **Goldens.** Fusion's arm A/B expectations (`fused` counts q05 4 / q08 2 / ... ; `fragment run started`
  7 / 9 / 7 / 3 / 4 / 7; "11 of 15 plans byte-identical") are computed from `E/cn1/dump` = today's FE
  plans. They are invalid once the knob is on. Fusion's arms run before fix 3's FE is deployed (or with the
  knob off); the integration sweep records new counts as the new goldens (fusion spec 8 edited).
- **RIGHT SEMI.** Fusion needs no `RIGHT_SEMI` translator arm (the flip happens inside DuckDB after the
  splice, as it does for q04 today, `E/cn1/engine-cn0.log:48480`). Fix 3 does need it (plan item 5,
  `node_translator.rs:1743-1762`) before the knob is on in any sweep, because `JoinCommutativityRule`
  (`JoinCommutativityRule.java:34-44`) becomes cost-relevant. Both statements are true; item 5 is a fix 3
  prerequisite that lives in the translator crate next to (not in) fusion's `fusion.rs`. No textual overlap.
- **4 CNs.** Nothing fuses at 4 CNs (every shuffle sink has 4 destinations, every gather expects 4 senders,
  `E/survey/plan-summary-4cn.txt`), and fix 3's 4-CN payoff (q02/q11/q22 nixl volume) is independent of fusion.
- **`leaf` vs `leaf-any` after fix 3.** With real statistics small dimensions are broadcast as today
  (`UNPARTITIONED`, declined by `leaf`), so the decision on `leaf-any` (fusion arm E) is unaffected by fix 3
  in direction; re-check after the integration sweep.

### 2.5 Fix 3 vs fixes 1 and 2: orthogonal, with two harness notes

- No code overlap beyond `compute_node_service.rs`'s test module (fix 3 PR-B extends
  `get_file_schema_attachment_infers_across_multiple_ranges :3624-3640`; fix 2 adds tests elsewhere in the
  same `mod tests`): offsets only.
- Fix 3 Gate A runs the CN in `SIRIUS_CN_TRANSLATE_ONLY=1`; fix 2's gate 4 is placed after the
  translate-only early return (`process_fragment :927-934`), so survey mode still accepts and dumps every
  fragment. Fusion's hook is also after that return. Verified placement in both specs.
- Fix 3's harness changes (`SET GLOBAL cbo_cte_reuse = false` with read-back, knob read-back) are written
  into `bench.sh` / `run-abc.sh`. The SF1000 arms use the scratchpad's `capture-cn.sh` ->
  `start-cluster.sh` -> `cluster8.sh` instead, so the integration sweep must issue the same two `SET`s and
  `ADMIN SET FRONTEND CONFIG` itself (section 6, V4/V5).
- Fix 3's `.github/workflows/experimental.yml` change (apply all patches) and `build.rs` second guard are
  fix-3-only files.

### 2.6 Docs and tooling edited by more than one spec

| file | fix 2 | fix 3 | fix 4a | expect |
|---|---|---|---|---|
| `experimental/starrocks/docs/TUNABLES.md` | none | new section "Front end (patched StarRocks)" after "Engine-side" (`:37`) | new table "Dispatch" next to "Engine-side" | textual conflict at the same insertion point. Order: "Dispatch" (CN knob) directly after "Engine-side", then "Front end" before "Debug" (`:49`). |
| `experimental/starrocks/DEMO.md` | `:37-42` new bullet | `:173` patch list | `:39-40` bullet rewrite | fix 2 / fusion adjacent (fusion rebases); fix 3 far away. |
| `experimental/starrocks/benchmarks/tpch/README.md:42` | cancel caveat rewrite | none | none | none. |
| `E/cnlog_extract.py` (scratchpad tooling) | add `skips=`, `retired=` counters | none | add `fused=` counter | one edit adding all three; do it before V2. |
| `E/capture-cn.sh` | copy as `capture-cn-fix2.sh` (`WT=` changed) | Gate B uses it with knob/CTE `SET`s | copy as fusion arm script, exports `SIRIUS_CN_FRAGMENT_FUSION` | one parametrised `capture-cn-fix.sh` taking `WT` and extra `export`s from the environment instead of three copies (`WT=` is line 7; the env block is lines 11-13). |
| `docs/super-sirius/*.md` | none | none | none | fix 1 only (`pipeline-execution.md`, `execution-flow.md`, `memory-management.md`). |

### 2.7 Contradictions and wrong statements found, and what was done

| # | where | statement | problem | action |
|---|---|---|---|---|
| 1 | fusion 4.6 / 11.4 vs fix 2 4.4g | fusion adds `forget_query` in `cancel_plan_fragment`; fix 2 rewrites the same function | two owners of cancel teardown; fusion's by-query removal duplicates fix 2's per-instance `retire_receiver` | fusion spec: 4.6 dropped (fix 2 lands first), test `cancel_drops_deferred_plans` re-targeted, decision 11.4 resolved |
| 2 | fix 1 8 vs fix 2 10 | "land first" vs "land fix 2 before fix 1's arm" | order of arms conflated with order of code | fix 2 spec 10 rewritten: code fix 1 then fix 2; arms as in section 6 |
| 3 | fusion 8 arm A, 10 | "with fix 1 in the same build q21 dies in seconds" | fix 1 rule 5 stays quiet while the fused join build spills (2.2) | fusion spec arm A row and section 10 corrected; fix 1 spec 8 gains the caveat |
| 4 | fix 2 8 arm C C6 | "sweep wall shorter by ~11.9 s" | fix 1 in the build makes it ~745 s shorter | C6 rewritten as a per-row gap criterion |
| 5 | fix 3 9 | "Interactions with the other three fixes: none in code" | `TUNABLES.md`/`DEMO.md` insertion points collide with fusion; the `compute_node_service.rs` test module is shared; step 4 omits fix 4a although the knob-on 1-CN arm needs it | fix 3 spec 9 amended |
| 6 | fix 2 10 | "fix 4a edits `process_fragment` and `register_receiver` ... the fusion path must also consult `failure_of` before fusing" | correct request, but the reason is the async path ordering (`exec_single_attachment :705-717`), which neither spec stated | fusion 4.4 gains the check; fix 2 10 states the mechanism |
| 7 | fusion 8 arms A/B | golden `fused` / `fragment run started` counts | valid only for today's FE plans; fix 3 changes them | fusion spec 8 header states the FE precondition |

## 3. Build and landing order

All code in the `fix-*` worktrees off `perf/profile-sf1000` (`45dab3be`), one branch per fix, then an
integration branch `fix/integration-sf1000` that merges them in this order. No pushes, no PRs until asked
(plan rule). GPU assignments from the plan: fix 1 GPU 0, fix 2 GPU 1, fix 3 none, fix 4 GPU 2; SF1000 arms
are orchestrator-run, one at a time, never inside 02:00-03:50 UTC.

| step | what | worktree / branch | rebase onto | why here | build cost |
|---|---|---|---|---|---|
| 1 | Fix 1: `retry_futility.hpp`, `gpu_pipeline_{task,executor}.{hpp,cpp}`, `task_scheduler.{hpp,cpp}`, Catch2 tests, 3 engine docs | `fix-oom-failfast` / `fix/oom-failfast` | `45dab3be` | no shared files with anyone; every later arm is ~12 min shorter | `pixi run make` incremental + `cn-build` relink |
| 2 | Fix 2 commit 1 (retire on Err, gates 1-4, `StagedLeases`, `parked_registry.rs`) and commit 2 (cancel teardown, `retire_receiver`, inline recording) | `fix-parked-bookkeeping` / `fix/parked-bookkeeping` | fix 1 (trivial: no overlap) | owns cancel teardown and the `SenderSource` release rules fusion relies on | Rust only; `cn-build` |
| 3 | Fix 4a PR 1: translator `fusion.rs`, `tunable.rs`, `local_exchange.rs` (`LocalPlan`, `offer_local_plan`), `compute_node_service.rs` (`try_defer_sender` with the `failure_of` check, fold), docs | `fix-fragment-fusion` / `fix/fragment-fusion` | fix 2 (expect conflicts in `compute_node_service.rs` `execute_ready_fragment`/`cancel_plan_fragment`, `local_exchange.rs`, `DEMO.md`; drop 4.6) | reuses fix 2's cancel path; measured against today's FE before fix 3 changes the plans | Rust only; `cn-build` |
| 4 | Plan item 5: `TJoinOp::RIGHT_SEMI_JOIN => (JoinType::RightSemi, JoinOutput::Right)` in `node_translator.rs:1743-1762` + `JoinOutput::Right` arm; validate q04 (5 rows) and q22 (7 rows) vs the oracle | small commit on the fix 3 branch or its own | fix 4a (no overlap: different translator file) | hard prerequisite for any sweep with fix 3's knob on (fix 3 risk 8.2) | Rust only |
| 5 | Fix 3 PR-A (FE patch `files-scan-row-count.patch`, harness, docs) and PR-B (proto patch, `file_schema.rs`, `get_file_schema`, `build.rs`, CI yml) | `fix-files-cardinality` / `fix/files-cardinality` | fix 4a + item 5 (overlaps: `TUNABLES.md`, `DEMO.md`, `compute_node_service.rs` tests only) | FE rebuild is hours (`pixi run fe-build`); Gate A must precede any 1-CN sweep; its plan changes re-baseline fusion | `fe-build` (start as soon as FE UTs pass; can run while steps 1-4 proceed, since fix 3's code does not depend on them) |
| 6 | Integration branch: 1 + 2 + 4a + item 5 + 3; `cn-build`; FE from step 5 | `fix/integration-sf1000` | all | the final sweep (V5) | relink |

Development can be parallel (four worktrees, disjoint GPUs); only the **merge** order above matters.
Fix 3's FE build (step 5) is the long pole and should start on day one; nothing in steps 1-4 changes what
it builds.

## 4. Shared-file conflicts to expect at merge time

| file | specs | kind | resolution rule |
|---|---|---|---|
| `experimental/starrocks/src/compute_node_service.rs` | 2, 4a, 3B | `cancel_plan_fragment` (2 vs 4a: semantic), `execute_ready_fragment` (2 vs 4a: sequencing), `process_fragment` (2 gate 4 vs 4a hook: adjacent), `run_ready_fragment` (2 gate 3; 4a unaffected), `get_file_schema`/`file_schema_from_attachment` (3B only), `mod tests` (all three add tests) | fix 2's version of `cancel_plan_fragment`; fold before split in `execute_ready_fragment`; gate 4 before the fusion hook; `release_staged`/`release_sources` written as `if let SenderSource::Remote` |
| `experimental/starrocks/src/local_exchange.rs` | 2, 4a | `SenderSource` (4a adds a variant; 2 adds nothing to the enum), `ExchangeState` (2: `retired`, `retired_order`; 4a: nothing new in state), new methods (2: `retire_receiver`, `is_retired`; 4a: `offer_local_plan`), `mod tests` | additive; drop 4a's `forget_query` |
| `experimental/starrocks/docs/TUNABLES.md` | 3, 4a | same insertion point after "Engine-side" | "Dispatch" first, then "Front end" |
| `experimental/starrocks/DEMO.md` | 2, 3, 4a | `:37-42` (2 and 4a), `:173` (3) | 4a rebases over 2 |
| `experimental/starrocks/benchmarks/tpch/bench.sh`, `run-abc.sh` | 3 | | none |
| `experimental/starrocks/patches/*.patch`, `build.rs`, `.github/workflows/experimental.yml` | 3 | | none |
| `crates/starrocks-plan-translator/src/{lib.rs,fusion.rs}` | 4a | `lib.rs` gains `pub mod fusion;` | none (item 5 edits `node_translator.rs`, a different file) |
| `src/**`, `test/cpp/**`, `CMakeLists.txt`, `docs/super-sirius/**` | 1 | | none |
| `E/cnlog_extract.py`, `E/capture-cn.sh` (tooling) | 1, 2, 4a | counters; per-fix copies | one edit / one parametrised script (2.6) |

## 5. Test suites: where they collide and what must stay true

- **Catch2 (fix 1 only).** New `test/cpp/pipeline/test_retry_futility.cpp` must be added to
  `CMakeLists.txt` `TEST_SOURCES` (the list around `:846-852` already carries `test_oom_reschedule.cpp`);
  `check-orphan-tests` fails otherwise. No other spec touches C++ tests.
- **CI Rust trio** (`cargo fmt --check`, `clippy --all-targets --no-default-features -D warnings`,
  `cargo test --workspace --no-default-features`): fixes 2, 3B, 4a all add tests to
  `compute_node_service.rs::tests` and `local_exchange.rs::tests`. Same module, distinct names (checked:
  fusion's `FailingExecutor`, `RecordingRunExecutor`, `hash_partitioned_data_stream_sink` and fix 2's
  `Retiring<E>` do not exist today). Fix 2's `parked_registry.rs` and the registry's dead-code
  `cfg_attr` are fix-2-only.
- **Fusion default in tests is `Leaf`** (fusion 4.2: `Tunables::DEFAULTS`). Every fix 2 test is built on
  `data_stream_sink` (UNPARTITIONED) fixtures and so never fuses. Any future fix 2 test using a
  hash-partitioned single-destination local leaf must call `set_fragment_fusion(Off)` or expect one run.
- **Fix 2's mode loop** (`set_async_sender_dispatch` over `[false, true]`) and fusion's
  `fusion_applies_on_the_async_sender_path` both toggle the same `AtomicBool`; tests run in one process,
  so each must restore the flag (fusion 4.2 / fix 2 6.4 already use per-test setters; keep it that way).
- **GPU tests.** Fix 2's four `engine.rs` tests (GPU 1) and fusion's
  `engine_executes_a_fused_leaf_like_the_sender_receiver_pair` (GPU 2) both hold `GPU_ENGINE_TEST_LOCK`
  (`lib.rs:80-81`) and are in different worktrees until integration; on the integration branch run
  `cn-test` on one GPU serially. Fix 3 PR-B needs no GPU.
- **FE JUnit (fix 3 only):** `TableFunctionTableTest`, `StatisticsCalculatorTest`, new
  `FilesScanStatisticsTest`; run with `run-fe-ut.sh`, no collision.
- **Fusion's `cancel_drops_deferred_plans`** is re-targeted at fix 2's cancel line (section 7).

## 6. Combined SF1000 verification matrix

Environment for every arm unless stated: `E/capture-cn.sh` configuration (100 GiB GPU pool, 160 GiB
host, 16 GiB staging, watchdog 300 s, Quent on, `SIRIUS_CN_ASYNC_SENDER_DISPATCH=1`, fragment dumps at
1 CN), data `/scratch/sirius/datasets/tpch_sf1000`, queries in campaign order, cold + 2 warm unless
stated, oracle compare at rel tol 1e-6 against `scratchpad/oracle/tpch_sf1000/`. Analysis tools:
`cnlog_extract.py` (with the three new counters), `retry_stats.py`, `_bucket_oom.py`, `headroom.txt`
recipe, `card_compare.py`, `results_table.py`, `compare.py`. 1-CN arms use GPU 0; 4-CN arms need the whole
box. Durations are the specs' estimates.

| arm | build | FE state | CNs | queries | proves | ~time |
|---|---|---|---|---|---|---|
| **V1a** | fix 1 only | today's FE | 1 | six, cold only | fix 1 (attribution) | 3 min |
| **V1b** | fix 1 only | today's | 1 | fifteen | fix 1 has no effect on passing queries | 6 min |
| **V2a** | fix 1+2 | today's | 1 | `q05 q06 q16 q17 q21 q22` | fix 2 arm A (smoke, async on) | 4 min |
| **V2b** | fix 1+2, `SIRIUS_CN_ASYNC_SENDER_DISPATCH` unset | today's | 1 | `q05 q06`, cold only | fix 2 arm B (default dispatch) | 2 min |
| **V2c** | fix 1+2 | today's | 1 | all 22 | fix 2 arm C **and** fix 1's before/after row (arm A criteria re-checked in one session) | 5 min |
| **V3a** | fix 1+2+4a, `SIRIUS_CN_FRAGMENT_FUSION` unset (= `leaf`) | today's | 1 | six | fix 4a arm A | 6 min |
| **V3b** | same | today's | 1 | fifteen | fix 4a arm B (goldens vs today's plans) | 10 min |
| **V3c** | same, `SIRIUS_CN_FRAGMENT_FUSION=off` | today's | 1 | `q03 q05`, cold | fix 4a arm C (switch restores today's path) | 4 min |
| **V3d** | same, `leaf` | today's | 4 | `q03 q04 q07 q22`, cold + 1 warm | fix 4a arm D (nothing fuses at 4 CNs) | 6 min |
| **V3e** | same, `leaf-any` | today's | 1 | `q09 q05 q08` | fix 4a arm E (experiment, not gating) | 6 min |
| **V4** | integration CN in `SIRIUS_CN_TRANSLATE_ONLY=1` (item 5 included) | **rebuilt FE (PR-A+B)**, clean meta | 1, then 4 | `EXPLAIN COSTS` + `VERBOSE` x 22, knob off then on (+ optional lever arm) | fix 3 Gate A; produces the flip list and the new fusion goldens | 15 min + FE build |
| **V5a** | integration (1+2+4a+5+3) | rebuilt FE, knob per V4 decision, `cbo_cte_reuse=false` | 1 | all 22 | fix 3 Gate B (1 CN), fusion re-baseline, fix 2 C-criteria under new plans, fix 1 on whatever still fails | 8 min |
| **V5b** | integration | same | 4 | all 22 (q16 in the middle) | fix 3 Gate B (4 CN), **first run of the six at 4 CNs**, fix 2 arm D (4-CN cancel/fault path via q16) | 15 min |
| **V6** (optional, user decision) | fix 1 | n/a (standalone DuckDB) | GPUs 0+1 | SF100 q11, `cache=table_gpu`, `num_gpus=2` | fix 1 contention guard (rules 7-8 across executors) | 10 min |

Sequencing rule: V1 -> V2 -> V3 are cumulative on the CN build and need no FE change; V4 needs the
rebuilt FE and can be interleaved as soon as `fe-build` finishes and item 5 is in the CN; V5 last.
`cnlog_extract.py` and the parametrised capture script must be in place before V2a.

### Pass criteria per arm

**V1a** (fix 1 spec 6.1): per query, in the engine log `t(first "is futile" ERROR) - t(first "OOM at
operator")` <= 2 x that run's round-1 span (`retry_stats.py`); `runs/qNN.r0.err` contains `gave up after`,
`held outside any task reservation`, `freed 0 bytes` and not `exceeded maximum retry limit`; per query
`reschedule (retry` <= 2 x distinct originals, `exceeded 100 retries` = 0, exactly one `is futile`;
secondary wall bounds q05 <= 4.0 s, q08 <= 6.8, q09 <= 7.3, q17 <= 4.0, q18 <= 11.0, q21 <= 5.3 (these
include fix 2's leftover time and are re-measured in V2c). Six total ~30 s (was 774.9 s).

**V1b** (fix 1 spec 6.2): all fifteen `pass`; oracle set identical to `E/compare-cn1.txt` (MATCH q02 q04 q06
q12 q13 q14 q20 q22; known VALUES-DIFFER q01 q03 q07 q10 q19 q15; EMPTY q11); warm medians within ~6% of
`E/results.md` cn1; 0 `reschedule (retry`, 0 `is futile`, 0 `proceeding with partial reservation` in the
fifteen windows. Separate session from V1a (the leak is still present without fix 2).

**V2a** (fix 2 spec 8 arm A, A1-A10): `base@start` <= 1 MiB for q06.r0/r1/r2, q17.r0, q22.r0/r1/r2; 0
`fragment run started` for a failed query id after its `fragment run failed`; `skips=` >= 1 for q05 q17
q21; exactly one `retired a query's parked sender outputs` WARN per failure with `still_parked=0`, q05
`trigger=engine_err fragments=2 slots=2`, q16 `trigger=cn_err fragments=2 slots=2`; 0 relays for a dead
query; client-start-to-first-fragment <= 100 ms for q06.r0 and q22.r0; q22.r0 wall 1.0-1.3 s; q17's
`QueryBegin allocated` == q16's; 0 `discarding every parked`; 0 `could not retire` / `failed to release`.

**V2b** (fix 2 arm B): A1 for q06; A4; every later `fragment run failed` of q05's id has `elapsed_ms <= 5`
and "already retired"; no `fragment run finished` for q05 after the failure; 11 `cancel_plan_fragment
retired the query` lines with `reason=INTERNAL_ERROR`.

**V2c** (fix 2 arm C + fix 1 re-run): `base@start` <= 1 MiB on all 66 rows; oracle set as V1b; warm medians
within 6% of `E/results.md` (q10/q12/q14 may improve <= 0.18 s); `cancels=` per run unchanged (fix 2 C4
list); pass/fail set unchanged (15 pass, six OOM, q16 translator); `oom_resched=0` for the fifteen; **and**
V1a's engine-log and client criteria for the six, with the wall bounds now expected at the fix 1 table 3.6
values (q05 ~2.9 s ... q18 ~3.6 s rather than ~10.2 s, since q17's leftovers no longer run). This arm is
the report's before/after row for fixes 1 and 2.

**V3a** (fusion spec 8 arm A, as corrected): `runs.csv` q05 q08 q17 q18 `pass` x3; q09 `pass` (medium);
q21 recorded: build port of `HASH_JOIN (id=10)`, count and sizes of `after downgrade (N bytes freed)` with
N > 0, `[host_pool]` peak, time-to-fail or pass (section 2.2: fix 1 does **not** guarantee seconds here).
Oracle: q17 q18 q21 MATCH; q05 q08 q09 MATCH or VALUES-DIFFER confined to sum columns with `maxreldiff <=
2e-3`, identical row count and keys (the known decimal lowering). `cluster.log`: `fused sender fragment into
its local receiver` = q05 4, q08 2, q09 2, q17 2, q18 3, q21 2; `fragment fusion skipped` = 0; `fragment run
started` = 7, 9, 7, 3, 4, 7; `resolved CN transport tunables ... fusion_mode=Leaf` present. Engine log:
`oom_resched=0` for the passers; lineitem `GPU_SCAN` feeds the `HASH_JOIN` on the probe port; `QueryEnd
allocated` back to baseline after each pass (fix 2 already in the build). Quent: no lineitem-leaf
`STREAMING_SINK`. Expected warm medians (inference): q05 3.2-4.0 s, q08 4-5, q09 6-7.5, q17 7-10, q18 4.5-6.5.

**V3b** (fusion arm B): oracle set as V1b; `fused` = q02 2, q07 3, q13 2, q20 2, others 0; `fragment
fusion skipped` = 0; `fragment run started` q02 11, q07 7, q13 3, q20 6, the other eleven as `E/cn1-cnlog.txt`
`frags=`; warm medians of the eleven unchanged queries within noise (> 1.15x and > 200 ms slower =
triage); q02/q07/q13/q20 within noise or faster; q07's largest `STREAMING_SINK in=` < 25 GB; build sides for
q02 join 9/31, q07 join 4/9, q13 join 5, q20 join 16 unchanged vs `E/cn1/engine-cn0.log`.

**V3c**: q03 passes with 5 fragment runs and 0 fused lines; q05 reproduces today's shape (OOM at GPU_SCAN in
the lineitem sender, now dying in ~3 s under fix 1) with 0 fused lines. **V3d**: 0 fused lines on every CN;
per-CN `fragment run started` counts identical to `E/cn4-cnlog.txt`; results and warm timings within
noise of cn4. **V3e**: record only (q09 gains fused 8, 12, 16, 20; `[gpu_pool]` peak; medians vs V3a).

**V4** (fix 3 spec 7.1): precondition `SHOW COMPUTE NODES` lists exactly 1 (then 4) CNs (wipe
`output/fe/meta` or `DROP COMPUTE NODE` the stale ids; `E/survey/cluster.log:345-363` shows the campaign's
"1-CN" survey had four registered). Knob off: plan summaries equal `E/survey/explain/` and `explain4/`
modulo the clean-cluster difference. Knob on (+ `SET GLOBAL cbo_cte_reuse = false`): `card_all_one=False`
x22; predicate-free leaves print the footer counts (lineitem 5,999,989,709, orders 1,500,000,000,
partsupp 800,000,000, customer 150,000,000, part 200,000,000, supplier 10,000,000, nation 25, region 5);
predicated leaves follow the coefficients in fix 3 spec 7.1; q02 join 31 / q11 join 5 broadcast
region/nation; q22 join 11 no longer broadcasts orders; translate-only CN log has no `hash join type is
unsupported` (item 5 present) and no `MULTI_CAST_DATA_STREAM_SINK` refusal; every 1-CN BROADCAST ->
PARTITIONED flip listed with predicted parked bytes (q03 ~78 GB, q12 ~36 GB expected) and marked
"fusable" if the new sender is a hash-partitioned single-destination leaf. Optional lever arm recorded.
This arm decides the knob state for V5 (fix 3 decision 11.1).

**V5a** (fix 3 spec 7.2 at 1 CN, integration): oracle statuses as V1b for the fifteen plus q17 q18 (q21)
MATCH and q05 q08 q09 as in V3a; the FE INFO line `FILES() statistics ... source=footer` for all eight
tables (PR-B) on every statement; no translator refusal on the fifteen; **new fusion goldens recorded**
(`fused`, `fragment run started` per query) and the V4 flip list confirmed against `card_compare.py`
(q03/q12 leaves fused if they flipped; `STREAMING_SINK` of lineitem/orders absent); `base@start` <= 1 MiB
on every row (fix 2 C1 under the new plans); `oom_resched=0` for every passing query; for anything that
still fails, fix 1's V1a engine-log criterion or, if `freed > 0` lines appear, the 2.2 record. No
wall-clock number is promised by fix 3; warm medians recorded against `E/results.md` and V3b.

**V5b** (4 CNs, integration): the fifteen MATCH/VALUES-DIFFER as `E/compare-cn4.txt`; q05 q08 q09 q17 q18
q21 outcomes recorded (expected pass, inference: per-CN lineitem share 31-61 GB; q09 and q21 are the
doubtful ones); nixl GB per run: q02 (43.6-45.7 today), q11 (0.40), q22 (18.56) drop; q03 (60.34) and q12
(23.47) recorded; 0 fused lines on any CN; fix 2 arm D criteria around q16 (D1-D6: `QueryBegin allocated`
at the next query equals the previous query's end on every CN; no `fragment run finished` for q16 after
its failure; `cancel_plan_fragment retired the query ... reason=INTERNAL_ERROR` on every CN with
`released_leases` matching the staged frames; `QUERY_FINISHED` retires are DEBUG "nothing parked", never
WARN; 0 `no parked sender output` / `failed to release` / `skipped from frame seq` errors).

**V6** (fix 1 spec 6.4, user decision 11.1): same pass/fail as before the change, no `is futile` line,
reschedule count unchanged within noise. Only this arm exercises rules 7-8 across executors.

### What each arm cannot prove

- V1-V3 use today's FE; nothing about fix 3 is measured before V4.
- V3a-V3e goldens are invalid after V4's knob is on; V5a re-baselines them.
- The six at 4 CNs are measured for the first time in V5b; there is no pre-fix baseline (never run).
- q21 at 1 CN is not made to pass by this stack (fusion PR 2, `all` mode, is the route); its time-to-fail
  under fusion + fix 1 is uncertain (2.2).
- Spill throughput (fix 4b) is not measured anywhere; V3a's q21 `freed > 0` lines are the first data on it.

## 7. Spec edits made in place (what and why)

| file | section | change |
|---|---|---|
| `fragment-fusion-SPEC.md` | 4.4 `try_defer_sender` | first policy step is `self.results.failure_of(query_id)` -> decline (`debug!`, reason "query already failed on this CN"); explains the async-path ordering (`exec_single_attachment :705-717`) |
| | 4.3 | `forget_query` / `ForgottenQuery` marked "not shipped: fix 2's `retire_receiver` covers deferred plans"; kept only as the fallback if fix 2 were absent |
| | 4.6 | replaced: fix 2 lands first (INTEGRATION.md), so nothing is added to `cancel_plan_fragment`; the `LocalPlan` variant needs no release; fix 2's `release_sources`/`release_staged` must be written non-exhaustively |
| | 5.4 `cancel_drops_deferred_plans` | asserts fix 2's `cancel_plan_fragment retired the query on this CN` line and that a late leaf 8 is refused by gate 4 / skipped by gate 3, with no receiver run; falls back to the old assertion only if fix 2 is absent |
| | 8 header | goldens hold for today's FE plans only (knob off / pre-fix-3 FE); re-baseline in the integration sweep |
| | 8 arm A q21 row | "with fix 1 in the same build it dies in seconds" replaced by the rule-5 caveat and the four things to record |
| | 9 risk 2 | pointer to the corrected expectation |
| | 10 | lands after fix 2; the rebase list; fix 1 caveat |
| | 11 decision 4 | resolved: fix 2 owns cancellation |
| | header (line 10), 5.3, 9 risk 6, 10 PR shape | every remaining `forget_query` reference re-worded: the 5.3 test is replaced by `retire_receiver_returns_a_deferred_plan_with_the_other_sources` (exercises fix 2's removal over a `LocalPlan`); commit 2 carries no cancellation code |
| `parked-bookkeeping-SPEC.md` | 8 arm C C6 | rewritten as "client-start-to-first-fragment gap <= 100 ms on every row" (the wall sum is dominated by fix 1 once it is in the build) |
| | 10 fix 1 bullet | order reconciled (code: fix 1 then fix 2; arms per INTEGRATION.md); single-thread inbox argument for A4; fix 1's text as `cause` |
| | 10 fix 4a bullet | the concrete rebase contract: `LocalPlan` variant, non-exhaustive release matches, `retire_receiver` covers deferred plans, gate ordering on the async path, test-fixture rule (`Leaf` default) |
| `files-cardinality-SPEC.md` | 7.2 Gate B | requires fixes 1, 2 **and 4a** in the build for the knob-on 1-CN arm; records fusion counts; the capture script must issue the `SET`s itself |
| | 9 step 4 and "Interactions" paragraph | adds fix 4a and why; lists the docs collisions and the shared test module |
| `oom-failfast-SPEC.md` | 6.1 | one session suffices once fix 2 is in the build |
| | 8 fix 4a bullet | rule-5 caveat for fused shapes; arm A bound applies to today's plan shapes |

No spec's mechanism, files or tests changed beyond these; the four decision lists for the user are
consolidated below.

## 8. Decisions still needed from the user (consolidated, deduplicated)

1. Fix 1: run the 2-GPU contention guard (V6) before "Ready for review"? (fix 1 11.1); wording of the
   attribution clause (11.2); sink-deferral path (11.3); PR base `dev` via cherry-pick (11.4).
2. Fix 2: one PR with two commits or a stacked pair (12.1); cancel scope every-reason vs failure-reasons
   (12.2); gate 2 on/off (12.3). Arm D is now folded into V5b (12.4 resolved by this document).
3. Fix 3: knob default `true`/`false` in the shipped patch (11.1); who lands item 5 (11.2; this document
   places it as step 4 on the fix 3 branch); PR-B now (11.3, recommended yes); the no-code lever arm in V4
   (11.4, recommended yes: ten minutes, answers fix 4(d)); `cbo_cte_reuse=false` scope (11.5).
4. Fix 4a: accept 5/6 with q21 recorded (11.1; this document assumes yes); oracle wording for q05/q08/q09
   (11.2; assumed the campaign's VALUES-DIFFER tolerance); default `leaf` vs `leaf-any` after V3e (11.3);
   one PR vs a stack (11.5). Decision 11.4 (`forget_query`) is resolved here.
5. New: whether V4's knob-on plans justify running V5a with the knob on (fix 3's recommendation) or off.
