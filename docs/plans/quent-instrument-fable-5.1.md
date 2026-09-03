---
title: Instrument Sirius with Quent for the data-flow sim (Claude Fable 5.1)
model: claude-fable-5-1
effort: xhigh
paste_as: first user message in a new session
notes: >
  Operator: pick Fable 5.1 at xhigh. Sibling of starrocks-sirius-perf-fable-5.1.md
  (that prompt forbids adding probes; this prompt is the session that adds them).
  Do not paste the YAML into the model. Start the paste at the heading "Prompt".
---

# Prompt

You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to…?' or 'Shall I…?' will block the work. For reversible actions that follow from the original request, proceed without asking. Stop only for destructive actions or genuine scope changes the user must decide. Offering follow-ups after the task is done is fine; asking permission before doing the work is not.

Allowed stops (only these):
1. The questions under "Ask once", and only on the first turn, and only if this message does not already answer them. Ask them in one reply, then wait.
2. A genuine fork that would make the work useless if guessed wrong (new Quent entity types vs extending an existing FSM; CN cannot emit Quent at all). Put that question at the end of a turn that already listed what exists.
3. Destructive actions (hard reset, force push, killing another user's GPU jobs, writing into a shared clone another session owns).

Do not stop to ask whether to read the telemetry code or the Quent doc. Read them. Use the defaults below.

Exception: when the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one. This message is a change request, not a question.

Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll…', 'let me know when…'), do that work now with tool calls. That includes retrying after errors and gathering missing information yourself. Do not stop because the context or session is long. End your turn only when the task is complete or you are blocked on input only the user can provide.

Before running a command that changes system state (such as restarts, deletes, or config edits), check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.

# Delivering work

The user's request — or the plan they approved — sets the scope, and the scope is the deliverable: don't quietly narrow, widen, or swap it. Read ambiguity the way a careful colleague would: make routine judgment calls yourself, and check in only when different readings would lead to materially different work. If you see a real problem with the task as specified, say so in a sentence or two and keep building under stated assumptions; if the user hears the concern and reaffirms, that is their decision, so deliver the full request.

If a question comes up partway, first do everything that doesn't depend on the answer; then state the assumption you made, or — when going ahead on a wrong guess would be unsafe or would make the work useless — put the question at the end of a turn that also delivers that progress. If one part turns out to be blocked, complete every other part in full and say exactly what you left out and why — the whole task is the deliverable, and scaling it down is the user's call, not yours. A step you have decided on is something to run, not to announce: describing the next step and ending the turn leaves it undone until the user replies.

Keep changes to what the request needs. Something else you notice worth doing — cleanup or documentation the task didn't call for, a change to a file the task didn't require — is a suggestion to make at the end, not a change to make; actions clearly beyond what the ask implies, and risky or destructive ones, still need the user's go-ahead.

If, while working or testing, you find a pre-existing bug, a performance concern, or behavior the task doesn't mention, don't fix, optimize or extend it in this change unless the requested behavior cannot work without it; report it as a follow-up in your summary. Where the task is ambiguous, implement the reading its wording and the surrounding code most directly support, state that assumption in your summary, and don't build for the other readings as well. Verify your work however you like; scratch scripts and quick checks need not be kept. Commit tests only where the task asks for them or this repository already keeps tests for this kind of change, sized like the neighboring test files — roughly one focused test per stated behavior — and don't turn scratch checks into additional permanent test files. This is about extras only: implement every behavior the task asks for, completely.

The number of tokens used to edit files is best minimized, all else being equal. Therefore, when it will not affect the end result, try to surgically edit a file rather than rewrite the entire thing.

Please remove all mannered prose. Say the field name, the file, the ndjson line. Do not recast them as a story.

Use lists and tables for the gap list, the probe map, and the verification capture. In conversational replies, keep to plain prose. Quote schema field names from files you opened.

Before you start, say in a line what you're about to do; brief updates while you work help the user follow along. Close with a short recap that stands on its own — what you found, what you did, and what's next — so a reader who only sees the last message has the full picture.

First privately list what you need next; then request every item that doesn't depend on another's result in this one response.

When a name, path, or env var is in this prompt or in `docs/super-sirius/quent-telemetry.md`, search and read it. Do not answer from memory about the live Quent schema.

If you start a subagent, keep doing independent work instead of idling until it returns. Launch every agent in a wave in one response.

If this conversation is compacted, the summary must keep, exactly: the Ask-once answers; every probe added (file, event, Quent entity/FSM); events still missing; CN `enable_quent` / label behavior; the verification capture path and the ndjson lines that prove new events; test names. Do not drop constraints.

Everything produced in one reply, including any reasoning or drafting done before the reply, counts toward a single token limit. Do not draft the report twice. Reason about structure; write the report once.

## Goal

Instrument Sirius so **Quent** emits the events a fragment data-flow simulation needs. Spec for how to turn telemetry on, labels, exporters, and the UI: `docs/super-sirius/quent-telemetry.md`. The sibling analysis prompt (`~/.claude/plans/starrocks-sirius-perf-fable-5.1.md`) **must not** add probes; this session is the one that does.

Quent already models engine, plan (operators, ports, edges), GPU resource tree, executor / task-manager threads, task queues, Task FSM, and DataBatch (`constructed` / `stationary` / `in_transit` / `destructed`, including tier-conversion copies). Extend that model. Do not invent a second tracing system, nsys wrappers, or `parse_logs.py` replacements.

The sim needs to see, with timestamps and bytes, at least:

1. **Query identity** — a stable label (q01 iter N, StarRocks query/fragment ids on the CN).
2. **I/O** — when a scan issues or completes a read (byte range or split, bytes, which GPU/CN). Enough to schedule/throttle I/O later without guessing.
3. **Copies between fragments** — park/relay, `export_packed` / `push_packed`, staging-arena leases, DataBatch `in_transit` (already exists for representation conversion). Enough to schedule/throttle memcpy later.
4. **Hops the engine does not own today** — nixl send/recv (bytes, peer, duration) and StarRocks fragment start/end, stitched onto the same query label. If a hop cannot be a Quent entity yet, emit a Quent event the analyzer can ingest or document the CN log line as a temporary stitch — prefer a real Quent event.

This session makes those events **visible**. It does not implement I/O inflight caps or memcpy inflight caps (those are agents 3 and 4 of the analysis prompt).

## Deliverables

1. Gap table vs current Quent files (what exists, what this change adds, what is still missing).
2. Code: probes + any model/bridge/analyzer bits required so ndjson contains the new events when `enable_quent: true`.
3. CN path: derived YAML actually enables Quent (`enable_quent: true` when we want captures; per-CN `output_directory` already under the engine dir in `engine_settings.rs`), and fragment/query labels reach `sirius_iface.query_label` (today unlabeled queries are `unnamed_query`).
4. Tests in `test/cpp/telemetry/` (and CN tests only if you touch Rust emit paths), sized like the neighboring files. One focused test per new event family.
5. Update `docs/super-sirius/quent-telemetry.md` with the new events, how to enable them on the CN, and that the schema is still experimental.
6. A verification capture: q01 and q06, one iteration, Quent on, labels set. Show the ndjson (or `print_resource_tree`) lines for the new events. That capture is **not** a wall-clock baseline.

Write a short status to `~/.claude/plans/starrocks-sirius-perf/quent-instrument-report.md`.

## Read first, in this order

1. `docs/super-sirius/quent-telemetry.md`
2. `docs/super-sirius/configuration.md` section Telemetry
3. `rust/crates/telemetry/model/src/lib.rs`, `data_batch.rs`, `task.rs`, `TASK_FSM.md`
4. `src/include/telemetry/data_batch_probe.hpp`, `src/telemetry/telemetry_context.cpp`, `test/cpp/telemetry/test_telemetry_context.cpp`
5. `experimental/starrocks/src/engine_settings.rs` (derived `telemetry.output_directory`; it does **not** currently emit `enable_quent`)
6. If present: `~/.claude/plans/starrocks-sirius-perf/01-trace-schema.md` from the analysis session — treat as a gap list, re-check against the code.

## Ask once

If this message already answers a question, use that answer. Look at the code first. After one reply, use the defaults.

1. **Checkout.** Default: this working tree. Do not touch `/home/prestouser/aocsa/sirius` if you are in `sirius-stacks`.
2. **CN labels.** Default: yes — set `query_label` from StarRocks query id + fragment instance id so two CNs on one query are distinguishable in Quent.
3. **New Quent entity types.** Default: no. Add attributes or transitions on DataBatch / Task / Operator / Channel. A new entity only if an existing FSM cannot carry the event without lying.

## House rules

- Surgical edits. No throttle schedulers. No nsys. No GB200 config churn. No generated YAML commits.
- `enable_quent` costs time (`notes` and the RTX runbook say so). Keep it off in performance YAMLs that are used for wall-clock numbers. On for telemetry YAML and for CN captures that feed the sim.
- When `telemetry_info.context` is null, keep the no-op probe (unit tests must stay telemetry-free).
- Quent schema is experimental; do not claim UI stability. Agents parse files. `pixi run quent` is optional.
- Nightly CI owns GPUs ~02:00–03:50 UTC. The verification capture needs a GPU; if busy, land the tests that read ndjson from a fixture and leave the live capture as a named hole.
- `unset CUDA_VISIBLE_DEVICES` before any CN launch. One CN per GPU.
- Tests: Catch2 next to `test_telemetry_context.cpp`; do not turn scratch dumps into extra committed ndjson corpora unless a tiny fixture is required.

## Method

### Step 0 — inventory (subagent A)

List every Quent emit site in C++ and the CN. For each sim event in the Goal list: already emitted (file + field) / missing. Note DataBatch `in_transit` already covers representation conversion copies — do not duplicate it.

### Step 1 — model (subagent B, only if Step 0 needs new attributes)

Edit `rust/crates/telemetry/model/` and regenerate the C++ bridge the way this crate already does (`telemetry-bridge`). Keep analyzer examples (`print_resource_tree`) compiling. If you only add C++ calls to existing `handle_->in_transit` / task transitions, skip a model change.

### Step 2 — engine probes (subagent C)

Add probes at the emit sites Step 0 named. Likely:

- Scan split / byte-range start and complete (bytes, path or range, GPU).
- Fragment `run` / park / `relay_from` if those are C++ (`src/exec/streaming_fragment.cpp`, FFI).
- Packed export / push (`export_packed`, `push_packed`) as DataBatch `in_transit` or Channel use, with byte count.

Wire through `batch_telemetry_info` / `telemetry_context`. No-op when Quent is disabled.

### Step 3 — CN (subagent D)

- Derived YAML: set `enable_quent` explicitly (true for sim captures; keep a way to turn it off).
- Distinct `output_directory` per CN (already `engine_dir/telemetry`).
- Pass query/fragment identity into `query_label` on `Fragment::build` / execute.
- Nixl send/recv: Quent event or documented stitch. Prefer Quent Channel / DataBatch `in_transit` with peer as attribute if the model allows it.

### Step 4 — tests and doc (subagent E)

- Catch2: enable Quent to a temp dir, run a tiny query or synthetic batch conversion, assert ndjson contains the new event names/fields (pattern from `test_telemetry_context.cpp`).
- Update `docs/super-sirius/quent-telemetry.md`: new events, CN enablement, labels, "schema may change".

### Step 5 — prove it

Standalone: `run_tpch_parquet_and_generate_telemetry.sh` for q01 and q06, one iteration, existing parquet, `--config` that sets `enable_quent: true` and a unique `output_directory`. Label via the helper.

Then:

```bash
pixi run bash -c "cd rust && cargo run -p sirius-telemetry-analyzer --example print_resource_tree -- <output_dir>/<session_uuid>"
```

Grep ndjson for the new events. Paste example lines into the report.

If the CN is up and GPUs are free, one 1-CN q06 with Quent on and a non-`unnamed_query` label. If not, say blocked.

## Subagents

| Wave | Who | Writes |
|---|---|---|
| A | Inventory | `~/.claude/plans/starrocks-sirius-perf/quent-gaps.md` |
| B | Model (skip if unused) | model diff only |
| C and D | Engine probes and CN | code |
| E | Tests + `quent-telemetry.md` | tests + doc |

Lead implements or reviews C/D; does not idle. Lead owns Step 5 and the report.

## Out of scope

I/O inflight schedulers, memcpy inflight schedulers, the Python data-flow sim, nsys, Quent UI redesign, planner rewrites, merging PRs, committing `telemetry_data/` dumps.

## Done when

- Gap table exists.
- New events appear in a real or test ndjson capture, with quoted lines.
- CN path can enable Quent and label queries, or the report says exactly why not.
- `quent-telemetry.md` describes the new events.
- Tests for the new event families pass.
- Report written. No throttle code.

## Example of a finished fragment

<example>
<user>report after probes</user>
<response>
Added DataBatch `in_transit` on `export_packed` (`src/exec/streaming_fragment.cpp`) with `channel_capacity_bytes` = packed size. Scan split start/complete on `sirius_scan_manager.cpp` with byte range attributes.

CN derived YAML now sets `enable_quent: true` and `query_label` = `{query_id}:{fragment_instance_id}`.

Capture: `telemetry_data/q01q06-instrument/` session `…`. ndjson: `"in_transit"` with `"channel_capacity_bytes":457856` on q06 1-CN; scan event `"length":134217728`. Still missing: nixl peer id on the Channel (CN log stitch). Tests: `test_telemetry_context.cpp` new case `packed_export_emits_in_transit`. Doc: `quent-telemetry.md` §5.
</response>
<rationale>CORRECT: Field names, files, quoted capture, remaining gap named, no throttle implementation.</rationale>
</example>
