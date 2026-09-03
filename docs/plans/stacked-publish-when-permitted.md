# Publishing the stacks once `stacked/**` can be pushed to sirius-db

> **Done 2026-09-02 (evening):** permission granted; published as GitHub stacks — scan stack #1701 = #1696 (S1) → #1700 (S2);
> ffi stack #1703 = #1697 (F1) → #1702 (F2). Fork PRs: #1693 (X), #1694 (K), #1699 (L1). Closed: #1598 (→ #1702),
> #1698 (duplicate L1 stacked PR; branch deleted). Lessons for the next layers (S3, F4, F5, …): the clone needs
> `git config remote.pushDefault origin` (two remotes); `gh stack add <existing-branch>` from the layer below ADOPTS an
> existing local branch (no need to delete/re-init); run `gh stack view`/`submit` from a stack branch, never from `dev`
> ("dev belongs to multiple stacks"). The rest of this file is the original procedure, kept for reference.

State on 2026-09-02: `aocsa` has `push` on sirius-db/sirius and is in `sirius-maintainers`, but creating a
`stacked/*` branch there is refused by branch protection ("protected branch hook declined"). Request to an admin:
`~/.claude/plans/stacked-access-request-to-admin.md`. Until granted, layers are published as **fork PRs from
`aocsa/sirius`** whose diff includes the unmerged base commit(s).

All branches live in the dedicated clone `/home/prestouser/aocsa/sirius-stacks` (remotes: `origin` = sirius-db
over HTTPS, `aocsa` = fork over HTTPS, repo-local `credential.helper='!gh auth git-credential'`). Every `gh` call
must be prefixed with `source /home/prestouser/aocsa/gh-activate.sh &&` (isolated gh 2.99, account `aocsa`,
gh-stack v0.1.0). Never run `gh auth setup-git` in that env.

## Branch inventory

| Stack | Layer | Branch (local, in the clone) | Fork PR today | Notes |
|---|---|---|---|---|
| scan | 1 | `stacked/scan-byte-range-rule` (S1, `94a77836`) | none (waiting) | `gh stack init -b dev` already run locally for this one |
| scan | 2 | `stacked/scan-byte-range-ingestible` (S2, on S1) | Draft from fork (see execution log) | fork PR diff includes S1's commit |
| ffi | 1 | `stacked/ffi-transaction-scope` (F1, `98661d7d`) | none (waiting) | |
| ffi | 2 | `stacked/ffi-fragment-rust` (F2, on F1) | Draft from fork (see execution log) | fork PR diff includes F1's commit |
| (fork PRs) | – | `exec-exchange-staging-arena` (X), `exec-stream-cardinality` (K), `pipeline-stall-watchdog` (L1, `d53b44b1`) | #1693, #1694, L1's PR | stay fork PRs; nothing to do. L1 was a one-layer "stack" and is therefore a plain fork PR (rule: stacks need >1 layer) |

## Test that access was granted

```bash
source /home/prestouser/aocsa/gh-activate.sh && cd /home/prestouser/aocsa/sirius-stacks && git push origin stacked/scan-byte-range-rule
```

If that is rejected with "protected branch hook declined", access is not there yet; stop.

## Publish each stack (bottom-up order inside a stack; stacks are independent)

`gh stack init` adopts existing branches; each branch is based on the previous one. `--auto` creates the PRs as
**Drafts** with auto titles, which you then fix with `gh pr edit`. Check `gh stack view` after each step.

```bash
source /home/prestouser/aocsa/gh-activate.sh && cd /home/prestouser/aocsa/sirius-stacks && git checkout dev
# scan stack. S1 was `gh stack init`-ed alone earlier (never submitted); drop that one-layer record first so init can
# adopt both existing branches (`gh stack add` only creates new branches, it does not adopt). Worktrees holding the
# branches must be removed first: git worktree remove /home/prestouser/aocsa/sirius-stacks-wt/s2 (and .../f2).
git checkout stacked/scan-byte-range-rule && gh stack delete && git checkout dev
gh stack init -b dev stacked/scan-byte-range-rule stacked/scan-byte-range-ingestible && gh stack submit --auto && gh stack view
# ffi stack
git checkout dev && gh stack init -b dev stacked/ffi-transaction-scope stacked/ffi-fragment-rust && gh stack submit --auto && gh stack view
```
(No liveness stack: L1 is a fork PR. Only stacks with two or more layers ready are published as stacks.)

Then for every new PR (numbers from `gh pr list --repo sirius-db/sirius --author aocsa --state open`):

```bash
gh pr edit <N> --repo sirius-db/sirius --title "<title below>" --body-file <body below>
```

| Branch | Title | Body file |
|---|---|---|
| `stacked/scan-byte-range-rule` | `feat(scan): a deterministic byte-range -> row-group ownership rule` | `pr-bodies/stacked-scan-byte-range-rule.md` |
| `stacked/scan-byte-range-ingestible` | `feat(scan): the parquet ingestible honors a per-file byte range` | `pr-bodies/stacked-scan-byte-range-ingestible.md` |
| `stacked/ffi-transaction-scope` | `fix(ffi): own a transaction while lowering a fragment's Substrait plan` | `pr-bodies/stacked-ffi-transaction-scope.md` |
| `stacked/ffi-fragment-rust` | `feat(ffi): Rust bindings for sirius::ffi::Fragment and Context` | `pr-bodies/stacked-ffi-fragment-rust.md` |

Body files: the session scratchpad `pr-bodies/` directory (copy it somewhere durable) and the plan appendix. For
layer-2 bodies, delete the "base commit is INCLUDED in this PR's diff" paragraph: in a real stack the PR page diffs
against the layer below.

## After the stacked PRs exist

- A fork PR cannot be re-pointed at an upstream head branch, so close the fork Drafts of S2 and F2 with a comment
  pointing at their stacked replacements (copy any review comments across first).
- Close #1598 (replaced by the ffi stack layer 2) and #1644 once every piece it carried has a replacement.
- Merge bottom-up only: "Enqueue pull request" on the bottom PR, wait, `gh stack sync --prune`, `gh stack view`
  (✓ under the merged marker, no ⚠), repeat. Never "Enqueue stack" or `gh stack merge`.
- Keep the fork PRs X (#1693) and K (#1694) as they are.
