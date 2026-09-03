Execute the plan at `/home/prestouser/.claude/plans/mbrobbel-translator-prs-ready-to-merge.md`: make five upstream
draft PRs by mbrobbel merge-ready with the minimal changes the plan specifies, open the deferred issues, and close #1242.
Read the plan first, then follow it; the section "3. Reviews of mbrobbel's translator drafts" in
`/home/prestouser/.claude/plans/i-want-to-create-golden-crane-appendix.md` has the underlying analysis if you need it.

# Environment (verified 2026-09-02)
- Upstream repo: `sirius-db/sirius`, default branch `dev`. Fork: `aocsa/sirius`. PR branches live on `mbrobbel/sirius`.
- Use the isolated gh: prefix every gh command with `source /home/prestouser/aocsa/gh-activate.sh &&` (gh 2.99 in a conda
  env with its own `GH_CONFIG_DIR`, logged in as `aocsa`, a maintainer per MAINTAINERS.md). Never run `gh auth setup-git`
  there. Do not use `~/.pixi/bin/gh` (it reads `~/.config/gh`, the CI token). The GitHub REST API is rate-limited from this
  box for unauthenticated calls; always go through `gh`. Pushes to `mbrobbel/sirius` go over SSH (`git@github.com:`).
- Local clone `/home/prestouser/aocsa/sirius` (remote `origin` = `aocsa/sirius`) is shared with other sessions: never
  checkout, fetch into it, stash, reset, or write there. For any local build/test, make a dedicated clone:
  `git clone git@github.com:aocsa/sirius.git /home/prestouser/aocsa/sirius-mbrobbel` and add
  `git remote add upstream git@github.com:sirius-db/sirius.git`; fetch PR heads with `git fetch upstream pull/<N>/head:pr-<N>`.
  Init submodules there before building (`git submodule update --init --recursive experimental/starrocks/starrocks experimental/starrocks/brpc`).
- Source of truth for the intended code is branch `feat/pin-table-cn` on `aocsa/sirius` (head `efa312f4`); the plan already
  quotes the exact strings to copy from it.
- CI for the translator crate (run from `experimental/starrocks/` in the dedicated clone):
  `pixi run -e cn cargo fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check`,
  `pixi run -e cn cargo clippy --all-targets --no-default-features -- -D warnings`,
  `pixi run -e cn cargo test --workspace --no-default-features`.

# Step 1 — re-verify before touching anything (the plan's facts are from 2026-09-02)
For each of #1236, #1235, #1233, #1232, #1242 run
`gh pr view <N> --repo sirius-db/sirius --json number,state,isDraft,mergeable,maintainerCanModify,headRefName,headRepositoryOwner,commits,reviews,comments,body`
and `gh pr diff <N> --repo sirius-db/sirius`. Confirm: still open drafts, no new commits since 2026-08-26, the strings the
plan quotes still appear in the diffs (e.g. `"temporal avg is not supported"` in #1236, `fn add_ranges` loop shape in
#1232, `fn emit_mapping` moved in both #1235 and #1233). Check whether upstream `dev` moved and whether any of the five
merged or changed. If anything differs from the plan, stop and report the difference before acting.

# Step 2 — prepare locally (no GitHub writes yet)
- #1236: on `pr-1236` in the dedicated clone apply the plan's three code edits (avg refusal string; the
  `wide_decimal_literal_is_lowered_to_fp64` test in `tests/translate.rs`; the `type_mapper::tests::decimal_precision_boundary_is_18`
  unit test). Commit as `test(starrocks): pin the fp64 lowering boundary and fix the avg refusal reason`.
- #1232: on `pr-1232` apply the `has_more`/`empty` block at the top of `add_ranges`, port `has_more_scan_ranges_are_refused`,
  reword the `validate_complete_files` doc as the plan says. Commit as
  `fix(starrocks): refuse incremental scan-range delivery and skip empty placeholders`.
- Run the three CI commands on both branches; every test the plan lists under "Verification" must pass. Paste the results
  into the PR bodies' "Verified" lines.
- #1235, #1233: no code. Prepare the bodies from the plan verbatim.
- Draft the six issues from the plan's "New issues to open" table (title + body as given), and #1242's closing comment.

# Step 3 — one confirmation gate, then act
Show me a compact summary (what will be posted where, and whether `maintainerCanModify` is true on #1236/#1232) and ask
once. After my go-ahead, in this order:
1. Open the six issues with `gh issue create --repo sirius-db/sirius` (label `starrocks`); record their numbers.
2. Replace the `<placeholders>` in the four PR bodies with those issue numbers.
3. For #1236 and #1232: if `maintainerCanModify` is true, push the prepared commit to mbrobbel's branch
   (`git push git@github.com:mbrobbel/sirius.git pr-<N>:<headRefName>`) and update the body with `gh pr edit`; otherwise
   post the plan's review comment with the pasteable body and test code and leave the code change to mbrobbel.
4. For #1235 and #1233: post the plan's review comment with the pasteable body (`gh pr comment`); do not push anything.
5. Close #1242 with the plan's closing comment (`gh pr comment` then `gh pr close`).
Do NOT merge any PR, do NOT flip Draft to Ready (that is mbrobbel's call, and the flip pings CODEOWNERS), do NOT approve
via `gh pr review --approve` unless I say so, and do NOT touch any other PR or branch. Never use Graphite, git-spice or
ghstack. If mbrobbel has already addressed an item, skip it and say so.

# Step 4 — report
End with a table: PR → actions taken → links (comments, pushed commit SHAs, issue numbers), plus what remains for
mbrobbel (rebase #1233 after #1235 merges; flip to Ready) and the merge order #1236 → #1235 → #1233 → #1232.
