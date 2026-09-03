---
name: gb200-shutdown-and-publish-2026-09-03
description: "The GB200 box is down for maintenance 2026-09-03 19:00 UTC to 2026-09-14; where everything was published before shutdown (aocsa/sirius branches, aocsa/starrocks-tools replication kit) and what was left mid-flight (fix 3 WIP, fix 4 not started, SF1000 fix arms not run)"
metadata:
  type: project
---

The DAL GB200 cluster was shut down 2026-09-03 12:00 PST (19:00 UTC) until Monday 2026-09-14 (new images with
CDMM + GDS; slurm/network cleanup). Everything was published in the last hour:
- `aocsa/sirius` branches: `demo/q1q6-integration` (+ `demo/q1q6-base`, Draft PR #3), `perf/float-sum-canonicalize-flag`,
  `perf/quent-instrument`, `perf/profile-sf1000`, `fix/oom-failfast` (19f9eee4, verified), `fix/parked-bookkeeping`
  (63e7e0c1 + 0f4b1c19, verified), `fix/files-cardinality` (7f38171c + 00302c5c, implementer done, review unfinished), `fix/fragment-fusion` (no commits).
- `aocsa/starrocks-tools` (private): the replication kit: scripts, analysis tools, workflow scripts, the four fix specs +
  INTEGRATION.md + DECISIONS.md, all plans and reports, session notes, oracle answers, small evidence. README.md is the
  replication guide for a new box.
Left undone: fix 3 review/verify, fix 4 (fragment fusion + RIGHT_SEMI arm) not started, the SF1000 verification arms
V1..V6 (fix/INTEGRATION.md section 6) not run, the 2-GPU contention check for fix 1, Draft PRs on the fork for the fixes: aocsa/sirius#4 (fix 1), #5 (fix 2), #6 (fix 3), all against perf/profile-sf1000; fix 4 has none.

**Why:** the next session starts on a different machine with none of the worktrees.

**How to apply:** clone `aocsa/starrocks-tools`, read README.md, then `fix/INTEGRATION.md`; rebuild worktrees with
`fix/prep-worktrees.sh` adapted to the new paths. See [[starrocks-sirius-sf1000-planning-findings-2026-09-03]],
[[sirius-demo-q1q6-carve-check-2026-09-03]].
