---
name: gb200-box-nightly-ci-window
description: The 4x GB200 box runs a nightly CI benchmark 02:00-~03:50 UTC that occupies all 4 GPUs — never measure in that window
metadata: 
  node_type: memory
  type: project
  originSessionId: 838d0459-8adc-4090-955f-0b0244a401af
  modified: 2026-08-09T02:42:42.078Z
---

`/opt/sirius-ci/scripts/cron_benchmark.sh` runs from cron at `0 2 * * *` on this box and sweeps
TPC-H SF1 through SF1000. Measured over six nights in `/opt/sirius-ci/log/cron.log`, it takes a very
consistent **1h43m–1h49m**, so the GPUs are effectively unavailable **02:00 to ~03:50 UTC daily**.

**Why:** any Sirius/StarRocks benchmark taken in that window is either refused by
`cluster4-numa.sh`'s GPU-claim preflight or, worse, silently contaminated. `ALLOW_SHARED_GPUS=1`
does not make sharing safe — the RMM pool is reserved in full at startup, so sharing is an
allocation failure or a zero-headroom cluster, not a slowdown.

**How to apply:** before a measured run, check the *driver* process
(`pgrep -af cron_benchmark`), not `nvidia-smi --query-compute-apps` — the GPUs go idle for seconds
between the nightly's individual runs and polling GPU state yields a false "free". Wait for pid of
`cron_benchmark.sh` to exit. Also note a peer interactive session may share the box.

Related: [[sirius-multicn-handoff-2026-08-09]]
