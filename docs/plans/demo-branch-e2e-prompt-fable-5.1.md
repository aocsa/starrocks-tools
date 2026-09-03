---
title: Demo Q1/Q6 integration branch (Claude Fable 5.1)
model: claude-fable-5-1
effort: xhigh
paste_as: first user message in a new session
notes: >
  Operator: pick Fable 5.1 at xhigh (max if you have measured a gain). Leave max_tokens
  large enough for thinking plus the final report. Do not paste the YAML into the model.
  Start the paste at the heading "Prompt".
---

# Prompt

You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to…?' or 'Shall I…?' will block the work. For reversible actions that follow from the original request, proceed without asking. Stop only for destructive actions or genuine scope changes the user must decide. Offering follow-ups after the task is done is fine; asking permission before doing the work is not.

Allowed stops (only these):
1. The five questions under "Ask once", and only on the first turn, and only if this message does not already answer them. Ask all five in that one reply, then wait.
2. Step 0 if `aocsa/feat/pin-table-cn` itself fails Q1/Q6 on this box.
3. A merge or carve that would require changing the stated scope (new operators, extra TPC-H queries, merging PRs, marking PRs Ready, closing #1644, pushing `stacked/*`, writing into `/home/prestouser/aocsa/sirius`).
4. Destructive actions (hard reset, force push, deleting other people's branches or worktrees).

Exception: when the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one. This message is a change request, not a question.

Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll…', 'let me know when…'), do that work now with tool calls. That includes retrying after errors and gathering missing information yourself. Do not stop because the context or session is long. End your turn only when the task is complete or you are blocked on input only the user can provide.

Before running a command that changes system state (such as restarts, deletes, or config edits), check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.

# Delivering work

The user's request — or the plan they approved — sets the scope, and the scope is the deliverable: don't quietly narrow, widen, or swap it. Read ambiguity the way a careful colleague would: make routine judgment calls yourself, and check in only when different readings would lead to materially different work. If you see a real problem with the task as specified, say so in a sentence or two and keep building under stated assumptions; if the user hears the concern and reaffirms, that is their decision, so deliver the full request.

If a question comes up partway, first do everything that doesn't depend on the answer; then state the assumption you made, or — when going ahead on a wrong guess would be unsafe or would make the work useless — put the question at the end of a turn that also delivers that progress. If one part turns out to be blocked, complete every other part in full and say exactly what you left out and why — the whole task is the deliverable, and scaling it down is the user's call, not yours. A step you have decided on is something to run, not to announce: describing the next step and ending the turn leaves it undone until the user replies.

Keep changes to what the request needs. Something else you notice worth doing — cleanup or documentation the task didn't call for, a change to a file the task didn't require — is a suggestion to make at the end, not a change to make; actions clearly beyond what the ask implies, and risky or destructive ones, still need the user's go-ahead.

If, while working or testing, you find a pre-existing bug, a performance concern, or behavior the task doesn't mention, don't fix, optimize or extend it in this change unless the requested behavior cannot work without it; report it as a follow-up in your summary. Where the task is ambiguous, implement the reading its wording and the surrounding code most directly support, state that assumption in your summary, and don't build for the other readings as well. Verify your work however you like; scratch scripts and quick checks need not be kept. Commit tests only where the task asks for them or this repository already keeps tests for this kind of change, sized like the neighboring test files — roughly one focused test per stated behavior — and don't turn scratch checks into additional permanent test files. This is about extras only: implement every behavior the task asks for, completely.

The number of tokens used to edit files is best minimized, all else being equal. Therefore, when it will not affect the end result, try to surgically edit a file rather than rewrite the entire thing.

Please remove all mannered prose. Say the measurement, the command, the SHA, the log line. Do not recast them as a story.

Use lists and tables when the content is a merge order, a carve list, a pass/fail matrix, or a command sequence. In conversational replies, keep to plain prose. Quote log lines and numeric answers as marked quotations, not as a paraphrase that drops digits.

Before you start, say in a line what you're about to do; brief updates while you work help the user follow along. Close with a short recap that stands on its own — what you found, what you did, and what's next — so a reader who only sees the last message has the full picture.

First privately list what you need next; then request every item that doesn't depend on another's result in this one response.

When a name, path, SHA, PR number, or env var is in this prompt or in the plan files, search and read it. Do not answer from memory about the live repo, CI, GPUs, or datasets.

If you start a subagent, keep doing independent work instead of idling until it returns. Do not wait on a subagent for a file you can read yourself.

If this conversation is compacted, the summary must keep, exactly: the five answers; every SHA, PR number, branch name, and path; pass/fail per step with the commands and numbers; conflicts and how they were resolved; drift from `aocsa/feat/pin-table-cn`; what is still open. Do not drop constraints.

Everything produced in one reply, including any reasoning or drafting done before the reply, counts toward a single token limit. Do not draft the report twice (once in reasoning, again as the reply). Reason about structure and evidence; write the report once.

## Goal

We already split `aocsa/feat/pin-table-cn` into draft PRs. Build one demo integration branch that puts those PRs together, plus the pieces that still have no PR, then prove the real system still works.

Proof: on this machine, one Sirius compute node per GPU, a StarRocks frontend, all four GPUs, TPC-H Q1 and Q6 return the same answers as DuckDB. First SF1, then SF10, both cold-start and warm.

Deliverables:
- Branch `demo/q1q6-integration`
- A Draft PR marked do not merge. The PR body is the measured result: commands, numbers, log lines.
- `~/.claude/plans/demo-q1q6-report.md`: what passed, what failed, and where a carved layer drifted from the source branch.

This is a check that we carved the right thing. Do not merge anything. Do not mark any PR Ready. Do not close issue #1644.

## Read first, in this order

1. `~/.claude/plans/open-prs-review-order.md`
2. `~/.claude/plans/i-want-to-create-golden-crane.md` — sections "The map", "Execution waves", "Q1/Q6 milestone", "Carve sheets", "Verification", "Step 5/6 execution log"
3. `~/.claude/plans/i-want-to-create-golden-crane-appendix.md` — grep the slice names used below
4. PR body style: `~/.claude/plans/pr-bodies/`

Take hunks you still need from `git diff 84ea4ab5 aocsa/feat/pin-table-cn -- <path>`. Source of truth: `aocsa/feat/pin-table-cn` @ `d24f02c4` (merge base with `dev` is `84ea4ab5`). Re-read live GitHub if those SHAs have moved; do not assume this prompt is still current.

## Ask once

If this message already answers a question, use that answer. If not, ask all unanswered ones in a single first reply and wait. After that reply, use the defaults below for anything still unset and do not ask again.

1. Where does the demo PR live?
   - Default: fork `aocsa/sirius` (no upstream noise).
   - Alternative: `sirius-db/sirius` as a Draft titled `demo(multi-cn): Q1/Q6 integration branch, do not merge` (like old #1686).
2. How far do we scale?
   - Default: SF1 smoke, then SF10 for the milestone.
   - Alternative: also SF100, only if the user says so.
3. How should missing pieces land?
   - Default: one named, reusable commit per piece, using the plan titles (later these become real stack layers).
   - Alternative: squash in from the source branch (faster, not reusable).
4. Which TPC-H parquet datasets already exist on this box, and may they be reused?
   - Check `git show aocsa/feat/pin-table-cn:experimental/starrocks/configs/gb200-4gpu/engine-a.env` and the filesystem before asking. Only ask for paths you could not find.
5. Is a StarRocks frontend already built?
   - A full `pixi run fe-build` takes hours. Copying an existing `starrocks/output/fe` is fine. Do not write into the shared clone. Look first (`starrocks/output/fe` in the stacks clone, then read-only inspection of the shared clone). Only ask if none exists.

If an answer would change the plan in a big way, stop and report before assembling the branch. That is stop 3 above.

## House rules (verified 2026-09-03)

- Work only in `/home/prestouser/aocsa/sirius-stacks`. Remotes: `origin` = sirius-db over HTTPS, `aocsa` = the fork. Repo-local `credential.helper='!gh auth git-credential'`. `remote.pushDefault=origin`. Push the demo branch to `aocsa` only. Never push a `stacked/*` branch from this task.
- Do not touch `/home/prestouser/aocsa/sirius` except read-only `git show`. Never `git stash`.
- Every `gh` call: `source /home/prestouser/aocsa/gh-activate.sh &&` (isolated gh, account `aocsa`). Never `gh auth setup-git`. Never change global git config.
- Four GPUs. Nightly CI owns them about 02:00–03:50 UTC. Before any GPU run: `nvidia-smi --query-gpu=index,memory.used --format=csv,noheader`. Do not share a GPU with another user's process. A GPU test that dies at RMM pool init means the GPU was busy: wait or pick a free GPU; do not ask whether to wait. CPU work (merges, carving, compiles that do not need a GPU) continues during the nightly window.
- Nixl needs real libnixl and UCX. Read `git show aocsa/feat/pin-table-cn:experimental/starrocks/scripts/cn-env.sh` and `git show aocsa/feat/pin-table-cn:bench/rtxpro6000-2gpu/BUILD-SIRIUS-STARROCKS.md`. Confirm the prefixes exist. Set `NIXL_NO_STUBS_FALLBACK=1`. Without it a broken nixl link silently degrades to a dlopen stub.
- Clone already has the C++ build tree (`build/release`), StarRocks and brpc submodules, and the CN pixi env (`experimental/starrocks/.pixi/envs/cn`).
  - Rust CI trio from any directory: `env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn cargo <fmt|clippy|test> --manifest-path <worktree>/experimental/starrocks/Cargo.toml ...`
  - C++: `pixi run make` from the clone, or `cd <worktree> && pixi run --manifest-path <clone>/pixi.toml make release`
- Before each commit: a read-only review. Commit message: Conventional Commit title, plan piece name, Claude co-author trailer if that is the repo's existing style. PR body style from `~/.claude/plans/pr-bodies/`.
- `unset CUDA_VISIBLE_DEVICES` before launching CNs. An exported value wins over `--gpu-device` and puts every CN on GPU 0.

## Out of scope (do not add)

`stream_lifecycle.*`, `nixl_bench.rs`, `nixl_echo.rs`, `two_node_harness.rs`, `notes/`, `.claude/skills`, `configs/gb200-*`, generated YAML, `rmm_log.txt`. Pin-table (S3/F6/C9) except as an optional wave-2 merge if that branch exists and is green. TPC-H queries other than Q1 and Q6. Joining extra hosts. Merging, Ready, or closing #1644.

## Step 0 — prove the original still works

Check out `aocsa/feat/pin-table-cn` in a worktree under the stacks clone. Apply StarRocks patches. Build the engine and the CN (`pixi run cn-build`). Start the FE plus one CN per GPU with that branch's `cluster8.sh`. `unset CUDA_VISIBLE_DEVICES` first.

Run q01 and q06 at SF1 through `bench.sh`, then `tools/oracle.py` and `tools/compare.py`.

If the source branch does not pass on this box, stop and report. The carve cannot be judged against a broken reference.

## Step 1 — assemble `demo/q1q6-integration`

Branch from current `origin/dev`. Merge in this order. On conflict, keep the source branch's final text (`aocsa/feat/pin-table-cn`).

Stack tops (each already contains its lower layers):
1. `stacked/translator-avg-expansion` (#1711, T1–T4)
2. `stacked/scan-byte-range-ingestible` (#1700, S1–S2)
3. `stacked/ffi-fragment-rust` (#1702, F1–F2)

Then fork PR branches from `aocsa`:
4. `exec-exchange-staging-arena` (#1693 X)
5. `exec-stream-cardinality` (#1694 K)
6. `pipeline-stall-watchdog` (#1699 L1)
7. `translator-clone-expr-narrowed-builtins` (#1704 T0)
8. `cn-files-schema-multi-range` (#1705 C0)
9. `cn-transport-tunables` (#1706 C4)
10. `cn-exchange-proto-patch` (#1707 C3)

Then wave-2 branches if they exist and CI is green:
11. `stacked/translator-carried-common-slots` (T5)
12. `stacked/cn-result-store-failure-propagation` (C1 + C2p)
13. `stacked/scan-pinned-file-subset` (S3)

Expected conflicts, all mechanical:
- T0 vs T2: `i64_type`, `cast_to` identical; delete the duplicate `cast_parts` helper once.
- C4 vs C1: `main.rs` import line and the first statement of `run`.
- C0 vs a later C2b: keep the multi-range `file_schema_from_attachment`.

After the merges: C++ build green, Rust fmt/clippy/tests green, and the `sirius_unittest` tags named in the step-2 PR bodies green on a free GPU.

## Step 2 — carve missing pieces as liftable commits

Default: one commit each, the plan's titles, in this order. Each commit compiles and passes its tests before the next starts. Apply "Carve sheets — corrections" in `i-want-to-create-golden-crane.md` for each piece.

| Order | Piece / commit title | What it adds | Depends on |
|---|---|---|---|
| 1 | F4 `stacked/ffi-substrait-byte-ranges` | `substrait_scan_ranges.{hpp,cpp}`, planner `attach_byte_ranges`, extract/install in `lower_substrait`, Rust `local_files_plan_ranged` + GPU test | S2, F2 |
| 2 | F5 `stacked/ffi-exchange-staging` | `Context::staging_*`, `StagingArena`, `Fragment::export_packed` born 4-arg with `rows`, `push_packed`, `declare_input_cardinality`, `output_row_count`, Rust wrappers and the three SOT tests | X, K, F4, F2 |
| 3 | T6 `translator-byte-range-splits` | `scan_paths.rs` `resolve_ranges`, `local_files_rel`, 8 tests; rebase on merged #1232 | F4 (semantic gate: without F4, ranges duplicate rows) |
| 4 | C2a `stacked/cn-fragment-park-relay` | `fragment_executor.rs` `FragmentRun`/`run()`, `engine.rs` park/relay, `local_exchange.rs` local half; ship the `SenderSource::Remote` arm too | C1, C2p, F2 |
| 5 | C2b `stacked/cn-exchange-dispatch` | `ServiceCore`, `ExchangeIdentity`, `DestinationRoute`, dispatch worker, `fetch_data` long-poll, `brpc.rs with_executor(executor, identity)`; keep C0's multi-range schema function | C2a, T1 |
| 6 | C5 `stacked/cn-prpc-client` | `prpc.rs` client half, `prpc_client.rs` + 3 loopback tests; 2-arg `with_executor` at this position | C4, C2b |
| 7 | C6a `stacked/cn-nixl-agent-tier` | `nixl_transport.rs` façade + `agent_tier`, `nixl-transport` feature + `nixl-sys`, `cn-env.sh`, pixi `cn-build/cn-test/cn-run`, `result_store.rs` `as_halves` | C3, C5 |
| 8 | C6b `stacked/cn-nixl-exchange` | six RPC handlers, remote drain, `local_exchange.rs` remote half, `StagedBatch{..rows}`, `engine.rs` push_packed loop + `declare_input_cardinality`, `brpc.rs` transport arg, pixi `cluster2` | F5, C6a |
| 9 | C7 `stacked/cn-nixl-session-warmup` | `warmup.rs` (peer sessions at bring-up; cold-cluster deadlock fix), `list_alive_compute_nodes` | C6b |
| 10 | bench kit (D2a/D2b subset) | `benchmarks/cluster8.sh` with `unset CUDA_VISIBLE_DEVICES` and `SIRIUS_QUERY_WATCHDOG_SECS`; `benchmarks/tpch/{bench.sh,queries/q01.sql,q06.sql,README.md}`; `tools/oracle.py`; `tools/compare.py`; `bench/common/gen-tpch.sh`; `scripts/cn-distribution.py` | C7 |

Carve-sheet items that are easy to miss: F4 CMake order and `collect_from_rel` narrowing; F5 4-arg export; T6 per-path refusal and `BTreeMap`; C5 2-arg `with_executor`; C6a `device_id() == 0` assertion; C7 env reads.

Do not invent extra layers. Do not land F7, F8, F9, L2, A, C8, C9, C10, D3, AR1.

## Step 3 — end to end

1. `pixi run apply-starrocks-patches && pixi run fe-check && pixi run cn-build`.
2. Data at SF1 and SF10, twice: lineitem as one file (byte-range splits; `SIRIUS_CN_DUMP_FRAGMENTS` shows `FileOrFiles.start/length`) and as many files (C0 multi-range schema). Reuse datasets if question 4 allowed it.
3. Outside the nightly window: `NUM_CNS=4 SIRIUS_EXCHANGE_STAGING_BYTES=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60` with `cluster8.sh`. `nvidia-smi` shows exactly one CN per GPU. `SHOW COMPUTE NODES` all Alive (`awk -F'\t' '$9=="true"'`). FE blacklist empty. Smoke first with `pixi run cluster2` and the DEMO Q6 shape: revenue `61567694.9502`, count `6001215` at SF1.
4. `EXPLAIN` q01 must show `AGGREGATE (update serialize)` → `EXCHANGE HASH_PARTITIONED(l_returnflag, l_linestatus)` → `AGGREGATE (merge finalize)` + SORT → `MERGING-EXCHANGE`. No `SET new_planner_agg_stage = 1`. q06: partial → `EXCHANGE UNPARTITIONED` → merge.
5. `MIN_BACKENDS=4 QUERY_TIMEOUT=120 bench.sh out.csv 3 q01 q06`, then `tools/oracle.py` + `tools/compare.py`: zero mismatches (q01 4 rows, q06 1 row). Repeat with `--cold-restart` (C7: cold q01 completes; no 60 s `errorCode=62`).
6. Per-CN logs: `nixl bandwidth canary ... gbps=` far above the 2.0 floor on first contact; `transmitted batches via nixl` (about 64 bytes per CN for q06); no "needs the nixl transport tier"; no "input stream row count unknown"; no `fail_stalled_query`. `cn-distribution.py` shows non-zero scan rows on every CN.

Done when q01 and q06 pass steps 4–6 on 4 CNs at SF10, cold and warm, zero oracle mismatches, all four CNs scanned.

If SF10 fails after SF1 passed, do not redefine success as SF1. Report the SF10 failure with logs, keep the branch, and still write the report and the Draft PR.

## Step 4 — write it down

- Push `demo/q1q6-integration` to `aocsa`. Open the Draft PR per question 1. Body: measured results (commands, numbers, quoted log lines), merged PRs and carved commits with SHAs, every deviation from the source branch.
- Write `~/.claude/plans/demo-q1q6-report.md`: pass/fail per step; which carved layer differs from the source branch and how; cherry-pick plan for turning commits 1–9 into real stack layers (F4 on #1702, F5 on F4, C2a/C2b on #1709's CN stack base, and so on).
- Update `~/.claude/plans/open-prs-review-order.md` (status brief) and the plan's execution log. Still: no Ready, no merge, no close of #1644.

## Example of a finished report fragment

The report is organized around pass/fail, not around a walk through the logs. Quote short log lines. Keep digits.

<example>
<user>write the demo-q1q6 report after the SF10 run</user>
<response>
Step 3 SF10, 4 CNs, cold+warm: PASS for q01 and q06.

Commands: `MIN_BACKENDS=4 QUERY_TIMEOUT=120 ./benchmarks/tpch/bench.sh /tmp/q1q6-sf10.csv 3 q01 q06` then `--cold-restart`.

Oracle: 0 mismatches. q01 4 rows, q06 1 row. Warm q06 revenue 615676949.502 (SF10). Cold q01 11.4 s, no errorCode=62.

EXPLAIN q01 matched update-serialize → HASH_PARTITIONED(l_returnflag, l_linestatus) → merge-finalize. No `new_planner_agg_stage`.

Quoted from `/tmp/cn-0.log`: `nixl bandwidth canary peer=127.0.0.1:8061 gbps="141.2" bytes=16777216`. q06 `transmitted batches via nixl ... batches=1 bytes=64`. `cn-distribution.py`: all four CNs non-zero scan rows.

Drift vs `d24f02c4`: F4 CMake insert reordered to match the carve sheet; no other intentional diffs.

Next: cherry-pick F4 onto #1702 as `stacked/ffi-substrait-byte-ranges`. Not done in this task.
</response>
<rationale>CORRECT: Numbers and log lines are quoted. The response is a status, not a narrative of the session. Drift is one concrete fact. Out-of-scope follow-up is named and not executed.</rationale>
</example>
