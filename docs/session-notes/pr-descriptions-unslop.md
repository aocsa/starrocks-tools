---
name: pr-descriptions-unslop
description: User wants every PR description written with the pstack "unslop" rules and for human readers: a 2-3 sentence testing note, never a machine-log Verified line; where the skill file lives
metadata:
  type: feedback
---

PR descriptions must follow the pstack `unslop` skill: no em/en dashes, no "not just X but Y", no colon-as-connector,
no metaphor nouns ("surface", "primitive", "harness", "wedge"), no AI vocabulary ("leverage", "ensure", "robust",
"crucial", "delve"), active voice, short sentences, first person where the author made a choice, sentence-case
headings, no chatbot phrases. Keep the repo PR template (Description / Checklist / References) and every fact.

**Why:** on 2026-09-02 the user asked to "unslop the PR descriptions, use the pstack unslop skill to create PR
descriptions" and to rewrite all PRs listed in `~/.claude/plans/open-prs-review-order.md` the same way.

**How to apply:** the skill is not installed as a Claude plugin here; its text is at
`~/.cursor/plugins/cache/cursor-public/pstack/*/skills/unslop/SKILL.md` (copies under `~/.claude/remote/plugins/*/skills/unslop/`).
Read it and apply the rules by hand (or hand it to a writer agent plus a read-only checker that greps for the tells and
verifies no fact was dropped). Related: [[sirius-multicn-pr-carveup-plan-2026-09-02]].

**Addendum 2026-09-03 ("Make the PRs description for humans not AI Models"):** the user rejected the long "Verified 2026-09-02 on the
GB200 (hostname, driver, commit, rustc): cargo ... N passed (per-crate counts) ... git status clean" paragraphs. A PR body's testing note
is two or three plain sentences: where (a GB200 box), what ran in words (the CI trio, the Catch2 tags on a GPU), one headline number
(tests total or new), and only the caveat a reviewer needs (GPU-only tests CI does not run, engine build compiled but not run). No
hostnames, hashes, driver or toolchain versions, assertion counts, per-crate breakdowns, step counts, timings, log paths, "git status
clean". Lists of more than four test names become a count plus three telling names. Keep the detailed run log in the plan file, not in
the PR.
