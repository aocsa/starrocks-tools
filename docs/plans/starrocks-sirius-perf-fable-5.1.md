---
title: StarRocks vs standalone Sirius — 1-GPU parity and scale-out (Claude Fable 5.1)
model: claude-fable-5-1
effort: xhigh
paste_as: first user message in a new session
notes: >
  Operator: pick Fable 5.1 at xhigh. Leave max_tokens large enough for thinking plus the
  plan. Do not paste the YAML into the model. Start the paste at the heading "Prompt".
---

# Prompt

You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to…?' or 'Shall I…?' will block the work. For reversible actions that follow from the original request, proceed without asking. Stop only for destructive actions or genuine scope changes the user must decide. Offering follow-ups after the task is done is fine; asking permission before doing the work is not.

Allowed stops (only these):
1. The questions under "Ask once", and only on the first turn, and only for items this message does not already answer. Ask them in one reply, then wait.
2. A genuine fork that would make the work useless if guessed wrong (wrong dataset, wrong GPU count, implementing a planner rewrite). Put that question at the end of a turn that already delivered inventory or measurements.
3. Destructive actions (hard reset, force push, killing another user's GPU jobs, writing into a shared clone another session owns).

Do not stop to ask for paths, scale factor, query lists, or "should I look at the logs?" Look first. Use the defaults below.

Exception: when the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one. This message is a change request, not a question.

Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll…', 'let me know when…'), do that work now with tool calls. That includes retrying after errors and gathering missing information yourself. Do not stop because the context or session is long. End your turn only when the task is complete or you are blocked on input only the user can provide.

Before running a command that changes system state (such as restarts, deletes, or config edits), check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.

# Delivering work

The user's request — or the plan they approved — sets the scope, and the scope is the deliverable: don't quietly narrow, widen, or swap it. Read ambiguity the way a careful colleague would: make routine judgment calls yourself, and check in only when different readings would lead to materially different work. If you see a real problem with the task as specified, say so in a sentence or two and keep building under stated assumptions; if the user hears the concern and reaffirms, that is their decision, so deliver the full request.

If a question comes up partway, first do everything that doesn't depend on the answer; then state the assumption you made, or — when going ahead on a wrong guess would be unsafe or would make the work useless — put the question at the end of a turn that also delivers that progress. If one part turns out to be blocked, complete every other part in full and say exactly what you left out and why — the whole task is the deliverable, and scaling it down is the user's call, not yours. A step you have decided on is something to run, not to announce: describing the next step and ending the turn leaves it undone until the user replies.

Keep changes to what the request needs. Something else you notice worth doing — cleanup or documentation the task didn't call for, a change to a file the task didn't require — is a suggestion to make at the end, not a change to make; actions clearly beyond what the ask implies, and risky or destructive ones, still need the user's go-ahead.

If, while working or testing, you find a pre-existing bug, a performance concern, or behavior the task doesn't mention, don't fix, optimize or extend it in this change unless the requested behavior cannot work without it; report it as a follow-up in your summary. Where the task is ambiguous, implement the reading its wording and the surrounding code most directly support, state that assumption in your summary, and don't build for the other readings as well. Verify your work however you like; scratch scripts and quick checks need not be kept. Commit tests only where the task asks for them or this repository already keeps tests for this kind of change, sized like the neighboring test files — roughly one focused test per stated behavior — and don't turn scratch checks into additional permanent test files. This is about extras only: implement every behavior the task asks for, completely.

The number of tokens used to edit files is best minimized, all else being equal. Therefore, when it will not affect the end result, try to surgically edit a file rather than rewrite the entire thing.

Please remove all mannered prose. Say the measurement, the command, the path, the log line. Do not recast them as a story.

Use lists and tables for the baseline matrix, bottleneck ranking, and the plan. In conversational replies, keep to plain prose. Quote timings and log lines with their digits. Label every time as profiled or non-profiled.

Before you start, say in a line what you're about to do; brief updates while you work help the user follow along. Close with a short recap that stands on its own — what you found, what you did, and what's next — so a reader who only sees the last message has the full picture.

First privately list what you need next; then request every item that doesn't depend on another's result in this one response.

When a name, path, or env var is in this prompt or in a skill file, search and read it. Do not answer from memory about live GPUs, datasets, or result directories.

If you start a subagent, keep doing independent work instead of idling until it returns. The subagents under "Subagents (required)" are part of the deliverable, not optional. Launch every agent in a wave in one response. Do not wait on a subagent for a file you can read yourself.

If this conversation is compacted, the summary must keep, exactly: the Ask-once answers; every result directory, SHA, query, SF, GPU count; Quent `output_directory` paths and whether `enable_quent` was on; the baseline table (standalone vs 1-CN vs N-CN, cold and warm, non-profiled); the ranked bottlenecks; agent 1's Quent extract table and gap list; the I/O and memcpy throttle designs from agents 3 and 4; the simulation sketch from agent 6; the local experiment list from agent 7; agent 8 results if it ran; what was implemented and the before/after numbers; what was left in the plan. Do not drop constraints.

Everything produced in one reply, including any reasoning or drafting done before the reply, counts toward a single token limit. Do not draft the plan twice. Reason about structure and evidence; write the plan once.

## Goal

StarRocks-on-Sirius (one compute node per GPU, StarRocks frontend) should be as close as possible to standalone Sirius on one GPU, and should scale near-linearly as GPUs are added.

Standalone Sirius runs a pipeline plan in-process. StarRocks cuts the same SQL into plan fragments, with exchange hops between them. That difference is a given, not a bug to "fix" by rewriting StarRocks into a pipeline engine. Quantify the tax. Improve what we own: scan splits, fragment dispatch, park/relay, exchange/nixl, cardinality, launch overhead, and anything the profiles show as the actual 1-GPU gap or the scale-out knee.

Two comparisons, both required:

1. **1-GPU parity.** Non-profiled warm time: standalone Sirius vs StarRocks with `NUM_CNS=1` on the same GPU, same dataset, same queries. Report `T_sr1 / T_sirius`.
2. **Scale-out.** StarRocks `NUM_CNS=1` vs `2` vs as many GPUs as this box has (default 4). Report `T_1 / T_N` and efficiency `T_1 / (N · T_N)`. Near-linear means efficiency close to 1.0 on scan-heavy work; hash-shuffled work will be lower — measure it, don't assume it.

Do not quote a `bench.sh` "pass" as correctness. Confirm answers with the DuckDB oracle (`tools/oracle.py` + `tools/compare.py`, or `performance_test.py --validation` on the standalone path).

## Deliverables (all of them)

1. A baseline table (non-profiled timings, cold and warm, with commands and paths).
2. A profiling analysis of both systems (nsys + Sirius/CN logs). Profiled timings are for *why*; they are not the performance number.
3. Ranked bottlenecks, each mapped to a source file, with evidence from this run (or from reused artifacts you name).
4. A concrete plan at `~/.claude/plans/starrocks-sirius-perf-plan.md`: what to change, in what order, expected effect on 1-GPU parity vs scale-out, how to verify.
5. The high-confidence scale-out (and 1-GPU, if the same change) implementations this session can land and re-time. Everything else stays in the plan.
6. A data-flow simulation sketch plus the six subagent artifacts named below. Ground truth for engine-side timelines is **Quent** (`docs/super-sirius/quent-telemetry.md`), not nsys and not `parse_logs.py`. The simulation is a model of fragment data movement (scan → compute → park/relay or nixl hop → next fragment). It is not a rewrite of StarRocks into a pipeline engine.

Do not stop after the write-up if there is a high-confidence change you can make and re-measure. Do not start rewriting the planner, the fragment model, or unrelated operators.

## Read first, in this order

1. `docs/super-sirius/quent-telemetry.md` — **required for agent 1 and the simulation track.** Quent is the Sirius engine timeline (plan, operators, ports, edges, executor / task-manager threads, task queues, per-query activity). Schema is experimental and may change; read this file, do not guess event names.
2. `.claude/skills/tpch-bench/SKILL.md` and `.claude/skills/tpch-cn-sweep/SKILL.md` (StarRocks cluster, traps).
3. `.claude/skills/benchmark/SKILL.md` and `test/tpch_performance/CLAUDE.md` (standalone Sirius).
4. `.claude/skills/profile-analyzer/SKILL.md` and `.claude/skills/optimization-advisor/SKILL.md` (nsys). Skip the skills' "confirm every parameter with the user" gates; this prompt replaces them. nsys is hardware (kernels, memcpy, I/O), not a substitute for Quent.
5. `.claude/skills/log-analyzer/SKILL.md` (Sirius logs). CN logs are `/tmp/cn-*.log` or the path `cluster8.sh` prints.
6. `.claude/skills/cn-tuning/SKILL.md` and `notes/OPEN.md`. Do not rediscover closed items. Do not treat "nixl is the bottleneck" as the answer without a canary well above 2 GB/s and exchange bytes in the logs.
7. `experimental/starrocks/DEMO.md` and `experimental/starrocks/benchmarks/tpch/README.md`.

## Ask once

If this message already answers a question, use that answer. If not, look on disk first; only ask what you still cannot resolve. After that one reply, use the defaults and do not ask again.

1. **Existing artifacts.** Inventory `reports/`, `test/tpch_performance/output/`, `experimental/starrocks/benchmarks/tpch/results/`, `/scratch/prestouser/aocsa/bench-results/`, Quent dirs (`telemetry_data/`, any `sirius.telemetry.output_directory` in YAML), and any path in `engine-a.env`. Reuse them when the git SHA, dataset, and GPU count match this checkout. Only re-run what is missing.
   - Default: reuse what matches; fill gaps with new runs. A baseline without Quent is still a valid wall-clock baseline; agent 1 then lists Quent as missing and the lead fills it with `enable_quent: true` (standalone: `run_tpch_parquet_and_generate_telemetry.sh` in the Quent doc).
2. **Scale factor and dataset path.**
   - Default: the parquet already on this box at the smallest SF that still shows the 1-GPU gap (SF1 if present, else SF10 / SF100). Do not generate a dataset.
3. **Queries.**
   - Default: q01 (two-phase agg + hash shuffle) and q06 (scan + gather). Add q03 only if 1 and 4 CN already exist in the artifacts or a third query is free. Not the full 22 unless the user said so.
4. **Implementation in this session.**
   - Default: write the plan, then implement only items that are (a) evidenced by this analysis, (b) in Sirius engine / CN / bench harness we own, (c) aimed at 1-GPU overhead or scale-out, (d) small enough to re-time here.
   - Alternative, only if the user says so: plan only, no code changes.

## House rules

- `unset CUDA_VISIBLE_DEVICES` before any CN launch. After launch, `nvidia-smi` must show one CN per GPU.
- Nightly CI owns the GPUs about 02:00–03:50 UTC. Check `nvidia-smi` before GPU work. If a GPU is busy, wait or skip that device; keep going on CPU analysis of existing artifacts.
- nsys inflates wall time. Quent (`enable_quent: true`) is also instrumentation — do not use a Quent-on run as the 1-GPU parity or scale-out wall clock. Non-profiled, Quent-off for those numbers. Quent-on (and nsys) for *why* and for the sim.
- Enable Quent with the YAML in `docs/super-sirius/quent-telemetry.md` (`sirius.telemetry.enable_quent`, `output_directory`, `exporter`). Label queries (`sirius_set_query_label` / `query_label`) so q01/q06 iterations are findable. Default exporter `ndjson`. Offline tree: `cargo run -p sirius-telemetry-analyzer --example print_resource_tree -- <output_dir>/<session_uuid>`. UI: `pixi run quent <output_dir>` is optional; agents must parse the files, not depend on a browser.
- Alive count: `awk -F'\t' '$9=="true"'` on `SHOW COMPUTE NODES`. Not `grep -c true`.
- `bench.sh` has no correctness gate. Oracle every number you quote.
- Do not run engine A (Sirius CN) and engine B (stock StarRocks BE) at the same time (port 9030).
- Standalone Sirius and the CN cluster must not share a GPU in the same window.
- Set `NIXL_NO_STUBS_FALLBACK=1` on CN runs. Canary `gbps=` must be far above 2.0; below that the hop is host copies.
- Surgical edits. No extra markdown in the repo except the plan file named above. No GB200 config churn, no generated YAML commits, no `notes/` essays.

## Method

### Step 0 — inventory

Find existing standalone and StarRocks result dirs. Record SHA, SF, queries, CN count, pin mode, whether nsys was on. Build the gap list of runs you still need.

### Step 1 — baseline (non-profiled)

Same SQL, same parquet, same pin mode (default: unpinned parquet).

| Arm | How |
|---|---|
| Standalone Sirius, 1 GPU | `performance_test.py --engine gpu` (see benchmark skill). Iterations ≥ 3. |
| StarRocks 1 CN | `NUM_CNS=1` via `cluster8.sh` / `bench.sh`. `MIN_BACKENDS=1`. |
| StarRocks N CN | `NUM_CNS=2` then `NUM_CNS=<GPUs>`. `MIN_BACKENDS` matches. |

`EXPLAIN` the StarRocks plans. q01 should be two-phase agg with `EXCHANGE HASH_PARTITIONED`. q06 should be partial → `EXCHANGE UNPARTITIONED` → merge. No `SET new_planner_agg_stage = 1`.

Oracle compare. Wrong answers are not a performance result.

If a needed arm is missing and GPUs are free, run it. If GPUs are busy, analyze what you have and leave the hole in the table.

### Step 2 — profiles, Quent, and logs

For the slowest 1-GPU gap query and the worst scale-out query:

- **Quent first** (see `docs/super-sirius/quent-telemetry.md`). Same YAML on standalone and, if the CN honors `SIRIUS_CONFIG_FILE` / `--sirius-config`, on each CN with a distinct `output_directory`. Read ndjson (or the configured exporter). Use labels `tpch_q01_iterN` / `tpch_q06_iterN`. This is the engine plan + operator + thread + queue timeline. Agent 1 owns the schema/gap table.
- Standalone extra: `nsys_report.sh` / `nsys_hotspots.sh` for kernels, memcpy, I/O that Quent does not emit.
- StarRocks extra: CN logs (`relayed native batches`, `transmitted batches via nixl`, canary, `fail_stalled_query`, fragment start/end). FE `EXPLAIN` / query profile. `cn-distribution.py`. nsys on one CN only if it does not wreck the cluster.
- Sirius `parse_logs.py` only as a fallback when Quent files are absent and `[trace]`/`[debug]` are on.

Split time into: scan, compute (agg/join), fragment setup, exchange (in-process vs nixl), FE wait, CPU sync. Attribute engine-side slices from Quent; attribute hops the engine does not see (nixl, FE) from CN/FE logs. The 1-GPU gap should mostly be fragment + FE + exchange-of-one, not "the GPU kernel is slower" — check that instead of assuming it.

### Step 3 — rank bottlenecks

Top 3–5. For each: 1-GPU vs scale-out, evidence (operator %, bytes, log line), source file, class (GPU / CPU / sync / I/O / exchange / dispatch). Map to `notes/OPEN.md` if it is already a named item (PLAN-01 park copy-out, PLAN-04 fragment HOL, harness correctness, etc.). Do not reopen closed items.

Known non-goals: making StarRocks emit a single pipeline plan; blaming nixl when the canary is healthy and q06 ships ~64-byte partials; retuning pool/arena as a substitute for a real bottleneck.

### Step 4 — write the plan

`~/.claude/plans/starrocks-sirius-perf-plan.md` must contain:

- The baseline table (non-profiled).
- Ranked bottlenecks with files.
- Ordered work: quick 1-GPU overhead cuts, then scale-out (scan split balance, exchange pipeline, dispatch concurrency, cardinality so DuckDB join order is not 1-row-blind, warmup).
- Verification for each item: non-profiled re-time of the same arms, oracle still clean.
- Explicit out-of-scope list.
- A "Simulation" section that points at the six subagent artifacts and says whether the model is sketched, coded, or validated.

### Step 5 — implement what this session can prove

Land the high-confidence items from the default implementation rule. Re-time the affected arms without nsys. Put before/after in the plan. Leave the rest ranked, not started.

If nothing is both evidenced and small enough, ship the plan with that sentence. Do not invent a refactor so the session "did code".

## Subagents (required)

The lead runs the baseline and bottleneck work above. In parallel it launches the agents below. Keep working while they run. Each agent writes one file and returns; it does not implement the whole engine, open PRs, or merge anything.

**Quent** (user: "quenta") is Sirius's instrumentation toolkit. Spec: `docs/super-sirius/quent-telemetry.md`. Agent 1 extracts from Quent files. nsys, CN logs, FE profile, and `parse_logs.py` are only for events Quent does not emit (typical: nixl hops, FE fragment compile, host disk I/O bytes). Do not treat nsys as the primary timeline.

Numbering follows the user (1, 3, 4, 6, 7, 8). There is no agent 2 or 5.

| Wave | Who | When | Writes |
|---|---|---|---|
| A | 1, 3, 4 | After Step 0 inventory. 3 and 4 may start from code; they refine once 1's schema exists. | `~/.claude/plans/starrocks-sirius-perf/01-trace-schema.md`, `03-io-throttle.md`, `04-memcpy-throttle.md` |
| B | 6 | After 1 has a schema (a gap list is enough). Fold in 3 and 4 when they arrive. | `~/.claude/plans/starrocks-sirius-perf/06-dataflow-sim.md` |
| C | 7 | After 6 has a model sketch. Can overlap coding of the sim. | `~/.claude/plans/starrocks-sirius-perf/07-local-experiments.md` |
| D | 8 | Only after a runnable sim exists (even a toy) **and** at least one throttle knob or a recorded trace to replay. Not before implementation. | `~/.claude/plans/starrocks-sirius-perf/08-sim-vs-run.md` |

Create `~/.claude/plans/starrocks-sirius-perf/` if needed. Lead: paste each agent's findings into the plan's Simulation section; do not wait for D to write the rest of the plan.

### Agent 1 — extract from Quent, determine what is missing

Read `docs/super-sirius/quent-telemetry.md` in full, then the configuration.md telemetry section it points at. Understand what Sirius already writes, and what the data-flow sim still lacks.

Inputs:
- Quent output dirs from the inventory (`enable_quent: true`, default `telemetry_data`, exporter `ndjson` unless YAML says otherwise).
- `test/tpch_performance/tpch_telemetry_sirius.yaml` and `run_tpch_parquet_and_generate_telemetry.sh` if a capture must be produced.
- One real session on disk: parse ndjson (or run `print_resource_tree` on `<output_dir>/<session_uuid>`). Do not describe the UI; describe fields.
- For gaps only: nsys, CN logs, FE `EXPLAIN` / profile.

The doc says Quent currently covers: engine, plan (operators, ports, edges), executor / task-manager threads, task queues, per-query activity, GPU-grouped resource tree (`parent_group_id`), operator/pipeline stats (duration). Schema may change; quote field names from the files you opened.

Return:
- Extract table: sim event → Quent record (file, field, example). Cover at least: query label, operator/pipeline start-end, which GPU / executor thread / task queue, edges/ports, task-queue occupancy if present, batch probes if present (`quent_data_batch_probe` in operators).
- Missing for the sim: fragment identity (StarRocks fragment vs Sirius pipeline), scan bytes and byte ranges, park/relay, nixl bytes/duration, H2D/D2H/D2D, inflight I/O, inflight packed exports, CN id. Say "in Quent" / "in CN log" / "nowhere".
- Whether the CN path actually emits Quent today (YAML through `--sirius-config` vs derived flags that drop telemetry). If the CN writes nothing, that is a gap, not a guess.
- Smallest extra flags or probes to close the gaps. Do not add that instrumentation in this agent. Implementation belongs to the sibling prompt `~/.claude/plans/quent-instrument-fable-5.1.md`.

If no Quent dir exists, run the TPC-H helper in the doc for q01 and q06 (one iteration) with the dataset from question 2, then extract. That capture is not the wall-clock baseline.

### Agent 3 — throttle I/O by scheduling I/O

Design a way to throttle I/O by **scheduling** I/O (not by sleeping in the scan with a mystery constant).

Inputs: scan path (`use_sirius_datasource` / uring / parquet byte ranges), `scan_task_batch_size`, how N CNs split one file, agent 1's Quent extract (operator/queue overlap) plus any I/O events Quent lacks.

Return:
- The scheduling unit (row group, byte range, scan task, CN-wide inflight cap).
- Where the queue would sit in code.
- How a rate or inflight cap becomes "this scan waits, that scan runs".
- A local way to see it work (drop inflight from N to 1 and watch scan overlap in nsys or logs).
- Out of scope: GDS-on-GB200, rewriting the parquet reader.

### Agent 4 — throttle CPU and GPU memory speed by scheduling memcpys between fragments

Design a way to throttle host and device memory bandwidth by **scheduling copies between plan fragments** (park/relay, `push_packed`, staging arena, D2H/H2D if any, nixl pack into the arena).

Inputs: `engine.rs` park/relay, `Fragment::export_packed` / `push_packed`, staging arena, `local_exchange.rs`, agent 1's Quent extract (when operators and queues are busy) plus memcpy events Quent lacks (nsys H2D/D2H/D2D, staging leases).

Return:
- Which copies are on the 1-GPU path vs the multi-CN path.
- The scheduling unit (one packed batch, one destination, one outstanding staging lease).
- How a memcpy inflight cap or a reserved bandwidth slice would delay the next fragment without dropping rows.
- A local way to see it work (cap outstanding packed exports to 1 and watch fragment overlap).
- Out of scope: changing CUDA memcpy algorithms; inventing a new transport.

### Agent 6 — data-flow simulation

Start putting the simulation of the data flow together so we can model it.

Inputs: agent 1 Quent schema (required); agents 3 and 4 when present; q01 and q06 fragment shapes from `EXPLAIN`. Replay should consume Quent records first, then stitch CN/nixl events agent 1 marked as missing.

Return:
- A discrete-event sketch: nodes (scan, compute, park, nixl hop, merge), queues, resources (disk, HBM, staging, NIC/NVLink).
- How a q06 gather and a q01 hash shuffle look as graphs.
- What the sim predicts that we can check (time, bytes, which CN is busy).
- Whether this session should code a tiny replay (Python over extracted events) or stop at the sketch. Default: a tiny replay if agent 1 found enough events; otherwise the sketch plus the gap list.

Do not simulate the SQL. Simulate movement of batches between fragments.

### Agent 7 — local experiments for sim accuracy

Come up with local experiments that validate the simulation against the box.

Inputs: agent 6 sketch; agent 1 gaps; q01/q06; 1 CN and N CN.

Return:
- 4–8 experiments, each: what we change (SF, CN count, inflight I/O, memcpy cap, one-file vs many-files), what the sim must predict, what we measure on the box (non-profiled time, oracle, `cn-distribution.py`, canary, Quent operator/queue occupancy, nsys memcpy/I/O totals only for events Quent lacks).
- Which of those can run on this machine this week (4 GPUs, nightly CI window, existing parquet).
- A pass rule: e.g. sim wall time within 20% of non-profiled warm median, and the same CN is the straggler.

Do not run the suite in this agent.

### Agent 8 — after implementation, sim vs experiment

Run the experiments and the simulation and say whether the simulation is working.

Gate: a runnable sim (agent 6) and at least one implemented throttle or a replay of a real trace. If that gate is false, write `08-sim-vs-run.md` with "not run, blocked on …" and stop. Do not invent results.

If the gate is true:
- Run agent 7's experiments that fit this box.
- Run the sim on the same knobs.
- Table: experiment, measured, predicted, error, oracle pass/fail.
- Verdict: working / not / working only for q06 gather / etc.
- Non-profiled, Quent-off timings only for the "is it faster" column. Quent for "did operator/queue overlap match the sim". nsys only for "did the throttle move I/O or memcpy" when Quent has no such event.

## Done when

- Baseline table exists with standalone 1-GPU, StarRocks 1-CN, and StarRocks N-CN for q01 and q06 (or a named hole if GPUs were busy).
- Bottlenecks are ranked with files and evidence.
- The plan file is written.
- Either a verified code change with non-profiled before/after, or an explicit statement that no high-confidence change fit this session.
- Oracle mismatches, if any, are reported as correctness, not as slowness.
- Agents 1, 3, 4, 6, 7 have written their files. Agent 8 has either a results table or a blocked-on line.
- The plan's Simulation section names those files.

## Example of a finished fragment

<example>
<user>write the plan after the 1-GPU and 4-CN runs</user>
<response>
1-GPU parity (SF10 parquet, unpinned, warm median, non-profiled): standalone q06 0.41 s, StarRocks 1-CN 0.62 s, ratio 1.51. q01 0.88 s vs 1.31 s, ratio 1.49.

Scale-out q06: 1-CN 0.62 s, 4-CN 0.21 s, T1/T4 = 2.95, efficiency 0.74. q01: 1.31 s → 0.58 s, T1/T4 = 2.26, efficiency 0.56.

Oracle: 0 mismatches. q01 4 rows, q06 1 row.

Bottleneck 1 (scale-out, q01): merge fragment waits on hash-partitioned inputs; CN logs `input stream row count unknown` until EOS, so DuckDB cardinality stays 1. File: F5 `declare_input_cardinality` / C6b push_packed `rows`. Not implemented here — needs the packed-batch `rows` field on the wire.

Bottleneck 2 (1-GPU): fragment launch + park/relay of the partial agg (~64 bytes) is 180 ms of the 210 ms gap. File: `experimental/starrocks/src/engine.rs` park path. Left in the plan; a rewrite is out of scope for this session.

No scale-out code change this session. Plan: `~/.claude/plans/starrocks-sirius-perf-plan.md`. Simulation: agent 1 extracted Quent operator/queue spans from `telemetry_data/<session>`; fragment identity and nixl bytes are not in Quent (CN logs only); agent 6 is a sketch; agent 8 blocked on a runnable replay.
</response>
<rationale>CORRECT: Non-profiled numbers, both comparisons, oracle, ranked bottlenecks with files, explicit non-implementation, and the simulation agents reported without inventing a validated model.</rationale>
</example>
