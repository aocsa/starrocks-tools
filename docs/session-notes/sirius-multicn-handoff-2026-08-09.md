---
name: sirius-multicn-handoff-2026-08-09
description: Where the CN fragment-distribution + NUMA investigation stands — read HANDOFF.md at the repo root before resuming
metadata: 
  node_type: memory
  type: project
  originSessionId: 838d0459-8adc-4090-955f-0b0244a401af
  modified: 2026-08-09T02:43:43.371Z
---

Work on "distribute q14 evenly across N CNs + make it NUMA-aware" is paused mid-investigation as of
2026-08-09. Full state, including six retracted claims, is in **`HANDOFF.md` at the repo root**, with
the raw agent findings in `/home/prestouser/aocsa/benchmark-results/investigate-phase-results.json`.

**The headline, so it is not lost:** the reported "2 of 4 CNs, ~13x task imbalance" is almost
certainly an **artifact**. A Sirius CN that fails one RPC is blacklisted by the FE *permanently* —
`HostBlacklist.remove()` requires a TCP connect to the CN's advertised `http_port`, and the Rust CN
advertises that port (`src/lib.rs:462`) without ever binding it, so removal never succeeds.

**Why it matters:** any Engine A measurement taken after a failed query, without an FE restart, ran
on a silently reduced cluster. Settle it for free with `SHOW COMPUTE NODE BLACKLIST;` on a fresh FE
before running any new distribution experiment.

**How to apply:** do not resume the stopped workflow's script as-is — its FACTS block still asserts
premises that were refuted (`parallelInstanceNum=1`, the `prefer_compute_node` hypothesis). Fix
FACTS first, or the measurement agents inherit them.

Related: [[gb200-box-nightly-ci-window]]
