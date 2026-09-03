# Decisions taken by the orchestrator for the open items in the four specs (2026-09-03 16:55 UTC)

The user asked for the plan and the workflows to be executed autonomously; every item below takes the spec's own default
unless stated, and is recorded here so implementers do not re-open it. The user can overturn any of them later.

## oom-failfast
1. 2-GPU contention check (SF100 q11, num_gpus=2): NOT run inside the implementation workflow (GPUs are assigned per fix). The
   orchestrator runs it after implementation, before any PR is marked Ready. The PR may exist as Draft without it.
2. Client-facing error wording: keep the attribution clause (parked fragment output / pinned tables / idle batches).
3. Sink-deferral OOM path (gpu_pipeline_task.cpp:721-751): leave on today's 100-retry path (rule dormant there).
4. PR base: dev via cherry-pick later; implement on the fix branch now.
5. Arms A/B run as soon as the fix builds; arm A re-run after fix 2 for the before/after table.
6. No new Quent event for the terminal decision.

## parked-bookkeeping
1. Packaging: one branch, two commits (commit 1 engine-side retire, commit 2 cancel/rendezvous); stacking decided at PR time.
2. Cancel scope: spec default (retire parked output and rendezvous state on every cancel reason; record a query-level
   failure only for INTERNAL_ERROR/TIMEOUT/USER_CANCEL/none). Commit 2's inline-path recording ships as specified.
3. Gate 2 (park-time refusal): ON, as specified.
4. Arm D (4-CN fault injection): on the integration branch's final sweep.

## files-cardinality
1. Config.files_scan_estimate_row_count default: TRUE in code. Gate A (EXPLAIN COSTS at 1 and 4 CNs, orchestrator-run)
   precedes any 1-CN sweep; the sweep runs with the knob in whichever state Gate A justifies, and both states are recorded.
2. Plan item 5 (translator RIGHT_SEMI_JOIN arm): implemented in the fragment-fusion worktree as its own commit after fusion
   (landing order per INTEGRATION.md), NOT in the cardinality worktree.
3. PR-A and PR-B both, stacked in the cardinality worktree, one fe-build at the end.
4. Gate A includes the no-code session-variable arm (exec_mem_limit 64 GiB, enable_local_shuffle_agg=false,
   broadcast_row_limit 2e9); whether it becomes "fix 3b" is decided from its output.
5. SET GLOBAL cbo_cte_reuse=false: FE-global; used only in the orchestrator's bench bring-up and stated in the report; not
   baked into the patch.

## fragment-fusion
1. Acceptance: 5/6 (q21 may still OOM) is accepted for this PR; q21 is routed to PR 2 (`all` mode) and recorded as such.
2. Oracle wording: the known ~1e-3 decimal-lowering deviation on sum columns is accepted for q05/q08/q09 (as for q01/q03/q07/q19).
3. Default mode: `leaf`; `leaf-any` decided from arm E.
4. Cancellation: entirely fix 2's (no forget_query), as resolved in INTEGRATION.md.
5. One PR, two commits (translator module, then CN), plus the separate RIGHT_SEMI commit (item 5 above).

## Sequencing (from INTEGRATION.md)
Fixes 1, 2 and 3 are implemented in parallel in their own worktrees (no shared files). Fix 4 starts only after fix 2 is
verified: its worktree branch is reset onto fix/parked-bookkeeping's head and rebuilt first. SF1000 arms are run by the
orchestrator afterwards, one cluster at a time, in the matrix order of INTEGRATION.md; the integration branch carries all four.
