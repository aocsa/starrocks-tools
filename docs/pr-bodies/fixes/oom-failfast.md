**Draft: not yet measured at SF1000.** Base is `perf/profile-sf1000` (the SF1000 profiling build); the engine files it touches are identical on `dev`, so it can be rebased there for the real PR.

## What
At SF1000 on one CN, q05 q08 q09 q17 q18 q21 each replayed a certain OOM 100 times and took 54 to 232 seconds to die (775 s of a 910 s sweep). The pool was full of parked fragment output the downgrade executor cannot see, every retry gate granted the same partial reservation, and the error named the retry cap instead of the cause.

The reschedule path now consults a pure predicate (`retry_futility.hpp`): partial grant, downgrade freed 0 bytes, the OOM needed more than the grant, and nothing completed or ran anywhere since the gate. When all hold, the query fails at that retry with a message naming bytes needed vs granted and bytes held outside any task reservation vs the pool. First attempts, CUDA-launch reschedules and full-grant OOMs keep the 100-retry budget and the 50 ms backoff, so the batch-lock contention case (#732) is untouched. A failed query's queued tasks are dropped at the gate.

## How I tested it
A GPU-free predicate table plus four component cases in Catch2; the full suite is green apart from a pre-existing float-sum case that needs `SIRIUS_CANONICAL_FLOAT_SUMS=1`. An independent reviewer re-ran the suites and found only minor issues. Predicted effect: the six queries fail in a few seconds instead of minutes; the fifteen passing queries never enter the path.

## Left
The SF1000 arms (`fix/INTEGRATION.md` V1a/V1b in aocsa/starrocks-tools) and the 2-GPU q11 contention check before this is marked Ready. Spec: `fix/oom-failfast-SPEC.md` in aocsa/starrocks-tools.
