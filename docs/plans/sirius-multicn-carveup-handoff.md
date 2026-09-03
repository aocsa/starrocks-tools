# Handoff: carving Sirius `feat/pin-table-cn` into upstream PRs (written 2026-09-02 21:30 UTC; delta section added 2026-09-03 03:40 UTC)

Audience: a fresh Claude Code session continuing this work. Read the referenced files instead of this one where they
exist; this document only says where things are, what is done, what is next, and what bit us.

## What the work is

Split the 73k-line branch `aocsa/feat/pin-table-cn` (Sirius as a StarRocks compute node, one GPU per node, TPC-H
over byte-range splits with a nixl exchange) into one-sitting PRs against `sirius-db/sirius` `dev`, following the
repo's stacked-PR rules, with TPC-H Q1/Q6 across N compute nodes as the first milestone.

Authoritative artifacts (do not re-derive):
- Master plan + execution log: `~/.claude/plans/i-want-to-create-golden-crane.md`
  (sections "Stacking policy", the per-item PR map, "Step-2 execution log", "Execution waves", carve sheets).
- Appendix with hunk-level maps, skeptic verdicts, CI/conflict scout, translator-draft reviews (690 KB):
  `~/.claude/plans/i-want-to-create-golden-crane-appendix.md`.
- Open PRs by step with review order and verified links: `~/.claude/plans/open-prs-review-order.md`.
- Stack publishing procedure + gh-stack lessons: `~/.claude/plans/stacked-publish-when-permitted.md`.
- PR bodies as posted (durable copies) + workflow result JSON: `~/.claude/plans/pr-bodies/`.
- mbrobbel drafts plan (done) and its runner prompt: `~/.claude/plans/mbrobbel-translator-prs-ready-to-merge{,.prompt}.md`.
- Project memory (auto-loaded): `~/.claude/projects/-home-prestouser-aocsa-sirius/memory/sirius-multicn-pr-carveup-plan-2026-09-02.md`.

## Where things stand

Done:
- Step 1: mbrobbel's #1232, #1233, #1235, #1236 merged into `dev` (head `01613070`); #1242 closed.
- Step 2: five PRs open as Drafts, CI green: #1696 (S1, bottom of the scan stack), #1697 (F1, bottom of the ffi
  stack), fork PRs #1693 (X arena), #1694 (K cardinality), #1699 (L1 watchdog).
- Step 3: #1702 (F2 Rust Fragment bindings) open, layer 2 of the ffi stack (GitHub stack ID 1703 — a badge, not a PR).
- Step 4: #1700 (S2 ingestible byte range) open, layer 2 of the scan stack (stack ID 1701).
- Closed by us: #1598 (→ #1702), #1698 (duplicate of #1699; its `stacked/` branch deleted). Still open on purpose:
  #1644 (old umbrella; close when every piece has a replacement). #1642 (Jedi18) overlaps #1699 by design; not ours.
- Nobody has flipped any PR to "Ready for review" yet (that pings CODEOWNERS); the user decides.

Next (in the plan's order):
1. Step 5 — fork PRs C3 (StarRocks proto patch + the mandatory `.github/workflows/experimental.yml` patch-apply step),
   C4 (transport tunables registry), C0 (FILES() multi-range schema). Ready to start; needs local setup first (below).
2. Step 6 — translator stack T1 → T2 → T3 → T4 (unblocked; reviewer = mbrobbel; watch integrations' load).
3. F4 (byte ranges ride the Substrait plan) after #1700 merges; F5 (staging FFI) after #1693, #1694, F4 merge; C1 (cn
   stack bottom) any time; rest per "Execution waves".

## Environment facts a fresh session must know

- Shared clone `/home/prestouser/aocsa/sirius` is used by other sessions: read-only for this work (`git show`,
  `git diff`, never checkout/fetch/stash there). Its worktree for this session was
  `.claude/worktrees/last-10-commits-summary-f8fc56` (may be gone).
- Dedicated stacking clone `/home/prestouser/aocsa/sirius-stacks`: `origin` = sirius-db (HTTPS), `aocsa` = fork
  (HTTPS), repo-local `credential.helper='!gh auth git-credential'`, `remote.pushDefault=origin`, commit identity set
  to the SOT author (aocsa). Local `dev` is at `84ea4ab5`; `origin/dev` fetched to `01613070` — reset local dev
  before carving new layers (`git checkout dev && git reset --hard origin/dev`). Build tree exists (`pixi run make`
  incremental, ~5–10 min on this 144-core GB200); baseline full build ~10 min. Submodules duckdb/substrait/cucascade
  initialised; StarRocks submodules (`experimental/starrocks/{starrocks,brpc}`) and the CN pixi env (`-e cn`) are
  NOT yet initialised — step 5 needs both (init with `--reference /home/prestouser/aocsa/sirius --dissociate`).
- gh: always `source /home/prestouser/aocsa/gh-activate.sh &&` first (isolated gh 2.99, own config dir, account
  `aocsa`, gh-stack v0.1.0). Never `gh auth setup-git` there. The pixi-global `~/.pixi/bin/gh` reads the CI token —
  do not use it. SSH pushes fail inside that env (conda `ssh`), hence HTTPS + credential helper.
- gh-stack: `gh stack init -b dev <l1> <l2>` adopts existing branches; `gh stack add <existing-branch>` from the layer
  below also adopts; run `view`/`submit` from a stack branch, never from `dev`; `--auto` creates Drafts; then
  `gh pr edit --title --body-file`. Merge bottom-up only (Enqueue one PR, wait, `gh stack sync --prune`,
  `gh stack view`); never "Enqueue stack"/`gh stack merge`.
- Branch protection on sirius-db refused `stacked/*` creation for `aocsa` until an admin opened it on 2026-09-02;
  it now works. Rule from the user: a stack must have >1 layer at publication, else it is a plain fork PR.
- GPUs: idle most of the day; all four are taken ~02:00–03:50 UTC (memory note). Pick an idle one with
  `nvidia-smi --query-gpu=index,memory.used --format=csv,noheader`; a busy GPU makes the unittest die at RMM pool init.
- aarch64 quirk: bare `pixi run cargo test --no-run --manifest-path rust/Cargo.toml ...` fails to link on this box
  (conda `ld` does not follow libsirius's rpath); it links with
  `RUSTFLAGS='-C link-arg=-Wl,--allow-shlib-undefined -C link-arg=-Wl,-rpath-link,$CONDA_PREFIX/lib'`. x64 CI is the
  authority for that command. GPU Rust tests need `LD_LIBRARY_PATH=$PWD/build/release/extension/sirius`.
- The SOT branch keeps moving (rebased onto `84ea4ab5`, then a CI-greening commit `d24f02c4`). Re-fetch
  `aocsa/feat/pin-table-cn` and diff the slice hunks before carving; the plan records how (md5 of `+/-` lines).
- Ultracode is on in this project: substantive work ran as Workflow scripts (carve agents in per-branch worktrees →
  read-only reviewer → fix → sequential build/test in the main checkout). Script files under
  `~/.claude/projects/-home-prestouser-aocsa-sirius--claude-worktrees-last-10-commits-summary-f8fc56/.../workflows/scripts/`
  (`step2-carve-review-verify-*.js`, `step34-carve-review-verify-*.js`) are reusable templates.

## Decisions taken (do not reopen without the user)

- Source of truth is `aocsa/feat/pin-table-cn`; stream_lifecycle is dead and never ships; `.pixi`, `rmm_log.txt`,
  notes/, bench/, .claude/skills are excluded from every PR.
- Rust drain is named `result_to_arrow` (not SOT's `into_arrow`); CN call sites rename at carve time.
- L1 dropped the SOT "zero-task completion" hunk: FRAG-11 passes without it on post-#1624 dev (stated in #1699).
- Old drafts get closed with pointers only when replacements exist.
- Still undecided (ask before the relevant PR): StarRocks proto — submodule patch with `ignore = dirty` vs a
  Sirius-owned vendored proto (blocks C3's design); Arrow import API for the Doris request in issue #1590 —
  `cudf::from_arrow` vs `from_arrow_host`.

## Suggested skills for the next session

- `workflow-authoring` — before writing the step-5/6 carve→review→verify Workflow (reuse the scripts above).
- `superpowers:verification-before-completion` — before claiming any branch is green or any PR is published.
- `pstack:babysit` and `pstack:get-pr-comments` / `pstack:fix-ci` — once reviewers engage on #1693–#1702.
- `code-review` (or `engineering:code-review`) — self-review a carved layer before `gh stack submit`.
- `module-context` — before touching engine code in later layers (F4/F5 scan and FFI, C-stack Rust).
- `superpowers:using-git-worktrees` — the per-branch worktree pattern used for carving (remove worktrees before
  `gh stack` adopts the branches).
- `update-docs` — after any of these PRs merge, to refresh `docs/super-sirius/`.

Redactions: personal e-mail addresses and the gh token are intentionally omitted; identities are referred to by
GitHub handle or role only.

## Delta since the sections above (2026-09-03, 03:40 UTC)

Read this section first; the "Where things stand" and "Next" sections above are the 2026-09-02 state.

- **19 Draft PRs are open on `sirius-db/sirius`, all CI green, none merged, none reviewed.** Steps 2 to 6 are published, wave 2 too.
  The authoritative list, by step and in review order, with a status table and a short "what comes next":
  `~/.claude/plans/open-prs-review-order.md`. The plan's "Step 5/6 execution log" (bottom of
  `~/.claude/plans/i-want-to-create-golden-crane.md`) has the per-PR commits, test counts, decisions and lessons.
- New since the 2026-09-02 text: #1704 T0 (CLONE_EXPR fix, a slice the map missed), #1705 C0, #1706 C4, #1707 C3 (step 5 fork PRs);
  translator stack 1712 = #1708 T1 -> #1709 T2 -> #1710 T3 -> #1711 T4 -> #1713 T5; cn stack 1716 = #1714 C1 -> #1715 C2p; S3 #1717 on
  scan stack 1701. Everything still uncarved (F4, F5, T6, L2, C2a, C2b, C5 to C7) needs an earlier PR merged first, so reviews are the
  critical path. `dev` moved to 98fe1a84 (#1206); every stack's bottom shows gh-stack's behind-trunk marker until `gh stack sync`, which
  I did not run unasked.
- **PR body rules from the user (durable, also in memory):** unslop style (skill text at
  `~/.cursor/plugins/cache/cursor-public/pstack/*/skills/unslop/SKILL.md`), written for humans: a 2-3 sentence "How I tested it" note,
  never a machine-log "Verified ..." line (no hostnames, hashes, driver versions, assertion counts, per-crate breakdowns). Saved bodies:
  `~/.claude/plans/pr-bodies/` (older versions under `before-unslop/` and `before-humanize/`).
- **Bot race:** `.github/workflows/validate.yml` job `clean-pr-body` rewrites a PR body from the event payload about 10 s after
  open/edit. A `gh pr edit` right after `gh stack submit --auto` gets overwritten with the stub. Wait ~20 s after submit, edit, then
  re-fetch and diff against the saved body (ignore the template comment line, which the bot strips).
- **Environment additions:** the stacking clone now has the StarRocks and brpc submodules and the CN pixi env
  (`experimental/starrocks/.pixi/envs/cn`); the shared clone's StarRocks object store is corrupt, use
  `/home/prestouser/aocsa/sirius-mbrobbel/.git/modules/starrocks/*` as `--reference`. Rust CI trio from any directory:
  `env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn cargo <fmt|clippy|test> --manifest-path <worktree>/experimental/starrocks/Cargo.toml ...`.
  C++ in a worktree: `cd <worktree> && pixi run --manifest-path <clone>/pixi.toml make release`. `git worktree remove` refuses worktrees
  with submodules: `rm -rf` the clean worktree, then `git worktree prune`. Worktrees left: c0, c3, c4, t0 (fork branches already pushed).
- **Doris `push_arrow` (#1590):** reply draft at `~/.claude/plans/doris-push-arrow-reply-draft.md`, not posted. It recommends
  `cudf::from_arrow(ArrowSchema const*, ArrowArray const*)` and allowing `push_arrow` during `run()` from other threads, with
  store-and-forward as the fallback. "T5b" in morningman's comment means #1644. AR1/F9 start once the user confirms.
- **Integration branch prompt (not executed):** `~/.claude/plans/demo-branch-e2e-prompt.md`. Baseline the source branch on the box first,
  then dev + all open PR branches + the missing pieces as liftable commits, then the plan's Q1/Q6 end-to-end run on 4 CNs.
- Workflow scripts reusable as templates live under the session's `workflows/scripts/` directory (wave2-*, translator-chain-*,
  step5-t1-*, unslop-open-prs-*, humanize-pr-verified-lines-*).
