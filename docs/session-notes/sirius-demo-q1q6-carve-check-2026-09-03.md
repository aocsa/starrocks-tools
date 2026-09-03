---
name: sirius-demo-q1q6-carve-check-2026-09-03
description: "Outcome of the 2026-09-03 carve check (demo/q1q6-integration proves the carved Q1/Q6 multi-CN stack end to end; Draft PR aocsa/sirius#3) plus the harness gotchas it cost: ad-hoc CN launches need cn-env.sh, pkill self-match kills the wrapper, fork dev is behind origin/dev"
metadata:
  type: project
---

State on 2026-09-03 12:35 UTC: `demo/q1q6-integration` (worktree `/home/prestouser/aocsa/sirius-stacks-wt/demo`,
HEAD 1e163623) = `origin/dev` 98fe1a84 + the 12 open Draft PRs merged in stack order + 10 carved commits
(F4 92e91adf, F5 b7452f67, T6 d88265aa, C2a 85da8f6f, C2b 37b48a46, C5 86b3f9a6, C6a 36072ef8, C6b 9e547c89,
C7 49d23e8a, bench 1e163623). It passes q01/q06 against the DuckDB oracle at SF1 and SF10 with 4 CNs (one per
GPU), warm and cold. Published only to the fork: Draft PR https://github.com/aocsa/sirius/pull/3, base
`demo/q1q6-base` (= 98fe1a84, pushed because `aocsa/dev` is 6 commits behind `origin/dev`). Report with the
per-piece cherry-pick plan (future branch, base, gate) and drift: `~/.claude/plans/demo-q1q6-report.md`;
raw per-piece evidence in that session's scratchpad `carve-step2-final.md`. Nothing merged, no Ready flips,
#1644 open. Other worktrees of the same clone: `sot` (d24f02c4 build), `perf` (`perf/float-sum-canonicalize-flag`),
`quent` (`perf/quent-instrument`), all with `.pixi -> ../../sirius-stacks/.pixi`.

Harness lessons:
- A CN launched by hand (not via `benchmarks/cluster8.sh`) dies at exec with `libnixl.so: cannot open shared
  object file` unless the shell first does `export TOOLS_DIR=/home/prestouser/aocsa/tools; source scripts/cn-env.sh`
  (cn-env.sh derives TOOLS_DIR as `<repo>/../tools`, wrong for worktrees).
- `pkill -f <path>` from a wrapper whose own command line contains that path kills the wrapper (exit 144)
  while the child script keeps running; use `setsid` + `kill -TERM -- -$pid` on the launcher's process group.
- `SIRIUS_CN_DUMP_FRAGMENTS=<dir>` per CN (distinct dirs, the file names are per-process `fragment-<seq>.txt`)
  shows the FE's byte-range splits as `start_offset:`/`size:`; with 2 CNs a single parquet file arrives as two
  equal halves.
- `cn-distribution.py` reads the newest Quent session per CN; after `bench.sh --cold-restart` that is a
  two-query generation, so a 0% CN there is a sampling artifact at SF1, not a refusal (`--all-runs` to check).

**Why:** each cost a failed run or a false alarm during the carve check.

**How to apply:** when rerunning or extending the demo, start from the report's Step 3 commands and these
notes; publish the carved commits with the report's cherry-pick table once their gates merge. See
[[sirius-multicn-pr-carveup-plan-2026-09-02]], [[tpch-decimal-datasets-cn-truncation]],
[[starrocks-cn-perf-findings-2026-09-03]].
