---
name: sirius-multicn-pr-carveup-plan-2026-09-02
description: Plan (2026-09-02) to carve origin/feat/pin-table-cn into ~40 stacked upstream PRs for Q1/Q6 multi-GPU via StarRocks CNs; user decisions and where the plan lives
metadata:
  type: project
---

On 2026-09-02 the user asked for clean upstream PRs carved from `origin/feat/pin-table-cn` (the
source of truth; upstream draft #1686). The full plan is at
`~/.claude/plans/i-want-to-create-golden-crane.md` with a 690 KB appendix
(`...-appendix.md`) holding hunk-level slice maps, skeptic verdicts, CI/conflict scout and reviews
of mbrobbel's translator drafts #1232/#1233/#1235/#1236/#1242.

User decisions: PR 5/6 = two-phase agg + CN exchange runtime, exchange input cardinality
(55e63371), pin_table series; close all old aocsa drafts (#1686 #1674 #1672 #1644 #1598
#1636-#1639) and open fresh PRs; push to sirius-db/sirius as `stacked/<name>` branches merged
bottom-up with gh stack; priority is TPC-H Q1 and Q6 across N CNs (one per GPU); stream_lifecycle
is dead and never ships.

**Why:** the 73k-line delta only exists upstream as unreviewable drafts; #1481 (Fragment FFI C++)
is already merged, and upstream dev moved 12 commits past origin/dev (#1547, #1555, #1605, #1606
conflict with scan-manager, aggregate and partition hunks).

**How to apply:** start from the plan's "Execution waves"; wave 0 is 11 independent PRs plus
update requests on mbrobbel's #1236/#1235/#1232. Stacking policy (user's notes, 2026-09-02):
self-contained fork PRs by default, gh-stack only for genuine dependency chains, `stacked/`
prefix, sticky `origin` rename to sirius-db in a dedicated clone, bottom-up manual merging.
gh: use the isolated conda-env gh via `source /home/prestouser/aocsa/gh-activate.sh` (gh 2.99, own
GH_CONFIG_DIR, logged in as aocsa, a maintainer; gh-stack v0.1.0 installed). Never `gh auth setup-git`
there. The pixi-global ~/.pixi/bin/gh reads ~/.config/gh (the CI token) — do not use it. Stacking
clone: /home/prestouser/aocsa/sirius-stacks (origin = sirius-db, aocsa = fork, SSH remotes). Doris's `push_arrow` request is issue #1590 comment 5494647357
(morningman, 2026-09-01). Related: [[sirius-multicn-handoff-2026-08-09]].

**Step 2 status (2026-09-02 ~05:40 UTC):** fork Drafts open on sirius-db: #1693 exec-exchange-staging-arena (X),
#1694 exec-stream-cardinality (K). Verified local branches awaiting `stacked/**` push access in
/home/prestouser/aocsa/sirius-stacks (remotes switched to HTTPS + repo-local `credential.helper='!gh auth git-credential'`):
stacked/scan-byte-range-rule 94a77836 (S1, gh stack already init'ed locally), stacked/ffi-transaction-scope 98661d7d (F1),
L1 = fork PR #1699 `pipeline-stall-watchdog` d53b44b1 (watchdog only: FRAG-11 proved the SOT completion hunk redundant
after #1624). Rule from the user: a stack must have >1 layer at publication, else it is a plain fork PR against
sirius-db dev. Stacked push permission was granted 2026-09-02 (evening); scan (S1+S2) and ffi (F1+F2) stacks publish
via gh stack once the F2/S2 carve workflow releases the main checkout.
(Earlier blocker — aocsa could not create stacked/* on sirius-db — was lifted the same day.) PR bodies live in the session scratchpad
pr-bodies/ and in the plan appendix.

**Published 2026-09-02 evening (all Drafts on sirius-db):** fork PRs #1693 X arena, #1694 K cardinality, #1699 L1 watchdog
(`aocsa/pipeline-stall-watchdog`); GitHub stack #1701 = #1696 S1 → #1700 S2 (scan); stack #1703 = #1697 F1 → #1702 F2 (ffi).
Closed #1598 (→#1702) and #1698 (dup of #1699). gh-stack lessons: set `remote.pushDefault origin` in a two-remote clone;
`gh stack add <existing-branch>` adopts; run view/submit from a stack branch, not dev. Old drafts still open: #1644, #1296.
Next per plan: T1 (translator exchange, after mbrobbel merges — done), C3/C4/C0 fork PRs, C1 cn stack bottom, F4 after S2 merges.

**Step 5/6 started 2026-09-02 (late evening):** user said "start working on step 5 and 6" (C0/C4/C3 fork PRs; translator
stack T1→T4) with Ultracode workflows and unslop bodies (see [[pr-descriptions-unslop]]). Environment facts learned:
the shared clone's StarRocks submodule object store (`/home/prestouser/aocsa/sirius/.git/modules/starrocks/starrocks`) is
CORRUPT ("inflate: data stream error"); use `/home/prestouser/aocsa/sirius-mbrobbel/.git/modules/starrocks/{starrocks,brpc}`
as `--reference`. A half-failed `git submodule update` leaves `submodule.<name>.*` config plus a `.git` pointer file in the
submodule dir; remove both before retrying. The stacking clone now has StarRocks+brpc submodules and the CN pixi env
(`experimental/starrocks/.pixi/envs/cn`, rustc 1.96.0); CI trio from anywhere:
`env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path /home/prestouser/aocsa/sirius-stacks/experimental/starrocks/pixi.toml -e cn cargo <fmt|clippy|test> --manifest-path <worktree>/experimental/starrocks/Cargo.toml ...`
(pixi keeps the caller's cwd). Per-slice worktrees under `/home/prestouser/aocsa/sirius-stacks-wt/<id>` need their own
`git submodule update --init --reference <main>/.git/modules/starrocks/<sub> -- experimental/starrocks/<sub>`.
Baseline on dev 01613070: 116 translator integration tests. Decision taken without asking (user said start): C3 keeps the
SOT approach (submodule patch + `ignore = dirty` + build.rs guard + CI step) and asks reviewers about vendoring in the body.
Extra slice discovered: CLONE_EXPR unwrap + FE-narrowed builtin cast (Q1 needs CLONE_EXPR) is not in the plan's map; carved
as fork PR `translator-clone-expr-narrowed-builtins` (T0). Local ref `pr-1242` in the stacking clone holds the closed #1242 head.

**Published 2026-09-03 ~01:00 UTC:** step 5 fork Drafts #1705 C0, #1706 C4, #1707 C3 (asks patch-vs-vendor); #1704 T0 CLONE_EXPR fix;
translator stack ID 1712 = #1708 T1 -> #1709 T2 -> #1710 T3 -> #1711 T4 (all Drafts, CI trio green locally). Review-order doc:
`~/.claude/plans/open-prs-review-order.md`. Doris reply draft: `~/.claude/plans/doris-push-arrow-reply-draft.md` (recommends
`cudf::from_arrow` and allowing `push_arrow` during `run()`; "T5b" in morningman's comment means #1644). Next: T5 carried common slots
on #1711; F4 after #1700; C1 cn bring-up; AR1/F9 for Doris after the ffi stack's push_packed layer.

**Wave 2 published 2026-09-03 ~03:15 UTC:** T5 #1713 (translator stack 1712 now 5 layers), cn stack 1716 = #1714 C1 -> #1715 C2p,
S3 #1717 (scan stack 1701 now 3 layers). 19 Drafts open, none merged. Everything left (F4, F5, T6, L2, C2a, C2b, C5..C7) needs an earlier
PR merged first; reviews are the critical path. Status brief: `~/.claude/plans/open-prs-review-order.md`. Improved prompt for the Q1/Q6
integration branch (not executed): `~/.claude/plans/demo-branch-e2e-prompt.md`. Worktree lesson: `git worktree remove` refuses worktrees
with submodules; `rm -rf` the clean worktree then `git worktree prune`.

**gh-stack + validate.yml race:** the repo's `clean-pr-body` job rewrites a PR body from the event payload ~10 s after open/edit (it only
strips HTML comments, but it uses the stale payload). A `gh pr edit` right after `gh stack submit --auto` gets overwritten with the stub.
Wait ~20 s after submit, then edit, then re-fetch and diff against `~/.claude/plans/pr-bodies/` (ignore the template comment line).
