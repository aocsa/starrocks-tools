---
name: quent-cn-telemetry-gotchas
description: "How Quent telemetry behaves on the StarRocks CN path (on by default before 2026-09-03, sessions per CN restart, per-type ndjson files, bring-up declarations lost at CN exit) and how to read a session quickly"
metadata: 
  node_type: memory
  type: project
  originSessionId: 25f3e903-c551-4a98-b7fc-a6e0ed53ade6
  modified: 2026-09-03T07:43:51.186Z
---

Quent ndjson sessions live under `<engine_dir>/telemetry/<session-uuid>/<record type>/*.ndjson`
(`.cn0/telemetry/...` for CN 0). One session per engine start, so every cluster restart makes new
ones; match them by directory mtime. Record types are directories: `query`, `task`, `operator`,
`plan`, `port`, `data_batch`, `batch_placement`, `memory`, `channel`, `engine`, `gpu_device`, ...
Every line is `{"id","timestamp"(ns),"data":{"seq","state":{"<State>":{...}}}}`; per-operator time
comes from consecutive `task` `Computing` states (`instance_name`, `input_bytes`).

Findings from 2026-09-03 (branch `perf/quent-instrument`, worktree `sirius-stacks-wt/quent`):
- On `aocsa/feat/pin-table-cn` the CN's derived YAML has no `enable_quent`, and the C++ default is
  true, so every CN run (and my standalone runs that copied that YAML) paid the exporter. The
  instrumentation branch makes it explicit and off by default; `SIRIUS_CN_ENABLE_QUENT=1` or
  `--enable-quent` turns it on and labels each fragment `<StarRocks query id>:<fragment instance id>`.
- On the CN exit path the once-per-process declarations (`engine`, `query_group`, `memory`,
  `channel`, `gpu_device`) never reach disk (0-line files), even after a clean SIGTERM and a 45 s
  wait; standalone and FFI-test processes flush them at exit. The per-query files are fine. Agent 8's
  `repair.py` (session scratchpad `sim8/`) rebuilds pipelines from tasks as a workaround.
- Killing a CN 3 s after SIGTERM (old `stop-cluster.sh`) also truncates the engine teardown; wait for
  the processes to exit before SIGKILL.

**Why:** these three cost an hour of "where did the events go" during the instrumentation session.

**How to apply:** for CN captures, set `SIRIUS_CN_ENABLE_QUENT=1`, run several queries so the
per-query buffers flush, stop with SIGTERM and wait, and expect to reconstruct resources from
`task`/`operator` rather than the declaration files until the exporter flush is fixed.
See [[starrocks-cn-perf-findings-2026-09-03]].
