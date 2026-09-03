**Draft: not yet measured at SF1000.** Base is `perf/profile-sf1000`. CN-only (Rust), two commits.

## What
An engine error used to clear every parked sender output on the CN and poison every slot with that query's error, and nothing else ever released a dead query's output: q16's translation failure carried 6.6 GB into q17, and a failed query's queued senders kept running, leaving up to 38 GB parked under later queries in the 1-CN SF1000 sweep.

Commit 1: parked output is owned per query (`ParkedRegistry`); a query is retired by its own error or by the CN's `fail_fragment`, and its runs are refused at the engine dequeue, at park time, on the dispatch worker and on arrival. A `StagedLeases` guard returns the staged leases of a fragment that never runs.

Commit 2: `cancel_plan_fragment` now tears the query down on this CN: it retires the parked output, purges the instance's rendezvous state, releases its staged leases, refuses a peer's late frames for the retired receiver, and records a query-level failure for the failure reasons only, so a repeat `fetch_data` after `QUERY_FINISHED` still reports EOS.

## How I tested it
9 registry, 5 service (both dispatch modes) and 4 engine tests on a GPU for commit 1; 8 more (store, exchange, service) for commit 2; the CI trio (fmt, clippy with warnings denied, tests without the engine feature) is green. An independent reviewer re-ran everything and found only minor issues.

## Left
The SF1000 arms (`fix/INTEGRATION.md` V2a to V2c) that show the pool returning to baseline after each failure. Spec: `fix/parked-bookkeeping-SPEC.md` in aocsa/starrocks-tools.
