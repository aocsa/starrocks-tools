# Prompt: integration branch for the Q1/Q6 multi-GPU milestone (do not execute yet)

Improved from: "Create a single pull request on top of the previous steps (2 to 6) as a demo branch, and add the missing pieces from
steps 7, 8, and 9. I want to verify that we are building the right thing..."

Mapping the original wording to the plan: "steps 2 to 6" = the fifteen open Draft PRs (plus T5, C1, C2p and S3 if they are green when
you start). "Steps 7, 8, 9" = the pieces the Q1/Q6 milestone still needs that no PR carries yet: F4 (step 3), F5 (step 7), T6 (step 6),
and the CN runtime and transport layers C2a, C2b, C5, C6a, C6b, C7 (step 8), plus the launch and bench scripts from D2a/D2b that the
end-to-end run needs. There is no step 9 in the plan's schema; the list above is complete.

---

## Goal

Prove that the carved PRs, taken together with the pieces not yet carved, reproduce the working system on `aocsa/feat/pin-table-cn`:
TPC-H Q1 and Q6 run across all GPUs of this box, one Sirius compute node per GPU, driven by a StarRocks frontend, with correct answers.
The deliverable is an integration branch, a Draft "do not merge" PR whose description is the measured status, and a short report of
what worked, what did not, and which carved layer (if any) diverges from the source branch.

Read first, in this order: `~/.claude/plans/open-prs-review-order.md` (status brief and the open PRs),
`~/.claude/plans/i-want-to-create-golden-crane.md` (sections "The map", "Execution waves", "Q1/Q6 milestone", "Carve sheets",
"Verification", "Step 5/6 execution log"), and `~/.claude/plans/i-want-to-create-golden-crane-appendix.md` (grep the slice names below).

## Ask before starting (one round, then proceed under the answers)

1. Where does the demo PR live: on the fork `aocsa/sirius` (default, no upstream noise) or on `sirius-db/sirius` as a Draft titled
   `demo(multi-cn): Q1/Q6 integration branch, do not merge` like the old #1686?
2. Scale: SF1 smoke then SF10 for the milestone (default), or also SF100?
3. Should the missing pieces be carved as separate, liftable commits with the plan's titles (default; each becomes the real stack layer
   later), or squashed in from the source branch for speed (faster, not reusable)?
4. Which TPC-H parquet datasets already exist on the box, and may they be reused? Check the paths in
   `git show aocsa/feat/pin-table-cn:experimental/starrocks/configs/gb200-4gpu/engine-a.env` before asking.
5. Is a StarRocks FE already built somewhere on the box (`starrocks/output/fe` in the shared clone or a bench directory)? A fresh
   `pixi run fe-build` takes hours. Copying an existing package out of the shared clone is fine; writing into it is not.

If any answer changes the plan materially, stop and report before carving.

## Environment and rules (verified on 2026-09-03)

- Work in the dedicated clone `/home/prestouser/aocsa/sirius-stacks` (remote `origin` = sirius-db over HTTPS, remote `aocsa` = the fork,
  repo-local `credential.helper='!gh auth git-credential'`, `remote.pushDefault=origin`). Push the demo branch to `aocsa` only. Never push
  a `stacked/*` branch from this task.
- Never touch `/home/prestouser/aocsa/sirius` (shared clone, other sessions) except read-only `git show`. Never run `git stash`.
- gh: prefix every call with `source /home/prestouser/aocsa/gh-activate.sh &&` (isolated gh, account `aocsa`). Never `gh auth setup-git`.
- Source of truth for behaviour and for every hunk you still need: `aocsa/feat/pin-table-cn` @ d24f02c4 (merge base with dev 84ea4ab5).
  Use `git diff 84ea4ab5 aocsa/feat/pin-table-cn -- <path>` for clean hunks.
- The clone has the C++ build tree (`build/release`), the StarRocks and brpc submodules, and the CN pixi env
  (`experimental/starrocks/.pixi/envs/cn`). CI trio for Rust from any directory:
  `env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn cargo <fmt|clippy|test> --manifest-path <worktree>/experimental/starrocks/Cargo.toml ...`.
  C++ build: `pixi run make` from the clone, or `cd <worktree> && pixi run --manifest-path <clone>/pixi.toml make release`.
- GPUs: four. All four are taken by the nightly CI from about 02:00 to 03:50 UTC. Check `nvidia-smi --query-gpu=index,memory.used --format=csv,noheader`
  before any GPU run and do not share a GPU with another user's process. A GPU test that dies at RMM pool init means the GPU was busy.
- The engine-linked CN with the nixl transport needs libnixl and UCX on the box. Read
  `git show aocsa/feat/pin-table-cn:experimental/starrocks/scripts/cn-env.sh` and
  `git show aocsa/feat/pin-table-cn:bench/rtxpro6000-2gpu/BUILD-SIRIUS-STARROCKS.md`, confirm the prefixes exist, and set
  `NIXL_NO_STUBS_FALLBACK=1` (without it a broken nixl link silently degrades to a dlopen stub).
- Use Ultracode workflows for the carving (per-piece agents where pieces are independent, a sequential chain where one piece's code
  depends on the previous), and read-only reviewer agents before each commit. Follow the unslop writing rules from
  `~/.claude/plans/pr-bodies/` for the PR body.

## Step 0: baseline the box on the source branch

Before judging the carve, prove the reference system still runs here. Check out `aocsa/feat/pin-table-cn` in a worktree, build the
engine and the CN (`pixi run cn-build` after `apply-starrocks-patches`), start the FE plus one CN per GPU with its `cluster8.sh`
(`unset CUDA_VISIBLE_DEVICES` first; an exported value wins over `--gpu-device` and collapses every CN onto one GPU), and run q01 and
q06 at SF1 through its `bench.sh` with `tools/oracle.py` + `tools/compare.py`. If the source branch itself does not pass on this box,
stop and report: the carve cannot be judged against a broken reference.

## Step 1: assemble the integration branch

Branch `demo/q1q6-integration` from current `origin/dev`. Merge, in this order, resolving conflicts toward the source branch's final text:

1. Stack tops (each contains its lower layers): `stacked/translator-avg-expansion` (#1711, T1 to T4), `stacked/scan-byte-range-ingestible`
   (#1700, S1 to S2), `stacked/ffi-fragment-rust` (#1702, F1 to F2).
2. Fork PR branches from `aocsa`: `exec-exchange-staging-arena` (#1693 X), `exec-stream-cardinality` (#1694 K), `pipeline-stall-watchdog`
   (#1699 L1), `translator-clone-expr-narrowed-builtins` (#1704 T0), `cn-files-schema-multi-range` (#1705 C0), `cn-transport-tunables`
   (#1706 C4), `cn-exchange-proto-patch` (#1707 C3).
3. Wave-2 branches if they exist and are green: `stacked/translator-carried-common-slots` (T5), `stacked/cn-result-store-failure-propagation`
   (C1 + C2p), `stacked/scan-pinned-file-subset` (S3).

Expected conflicts, all mechanical: T0 vs T2 (`i64_type`, `cast_to` identical, duplicate `cast_parts` helper to delete once); C4 vs C1
(`main.rs` import line and the first statement of `run`); C0 vs a later C2b (keep the multi-range `file_schema_from_attachment`).
After the merges: C++ build green, CI trio green, `sirius_unittest` tags from the step-2 bodies green on a GPU.

## Step 2: carve the missing pieces as liftable commits

One commit each, the plan's titles, in this order (each compiles and passes its tests before the next starts):

| Order | Piece | Plan row | What it adds | Depends on |
|---|---|---|---|---|
| 1 | F4 `stacked/ffi-substrait-byte-ranges` | item 2 | `substrait_scan_ranges.{hpp,cpp}`, planner `attach_byte_ranges`, the extract/install block in `lower_substrait`, Rust `local_files_plan_ranged` + GPU test | S2, F2 |
| 2 | F5 `stacked/ffi-exchange-staging` | item 1 | `Context::staging_*`, `StagingArena`, `Fragment::export_packed` (4-arg with `rows`), `push_packed`, `declare_input_cardinality`, `output_row_count`, Rust wrappers and the three SOT tests | X, K, F4, F2 |
| 3 | T6 `translator-byte-range-splits` | item 2 | `scan_paths.rs` `resolve_ranges`, `local_files_rel`, 8 tests; rebased on merged #1232 | F4 (semantic gate) |
| 4 | C2a `stacked/cn-fragment-park-relay` | item 5 | `fragment_executor.rs` `FragmentRun`/`run()`, `engine.rs` park/relay state machine, `local_exchange.rs` local half (ship the `SenderSource::Remote` arm too) | C1, C2p, F2 |
| 5 | C2b `stacked/cn-exchange-dispatch` | item 5 | `compute_node_service.rs` `ServiceCore`, `ExchangeIdentity`, `DestinationRoute`, dispatch worker, `fetch_data` long-poll, `brpc.rs with_executor(executor, identity)`; keeps C0's multi-range schema function | C2a, T1 |
| 6 | C5 `stacked/cn-prpc-client` | item 1.1 | `prpc.rs` client half promoted, `prpc_client.rs` + 3 loopback tests; 2-arg `with_executor` at this position | C4, C2b |
| 7 | C6a `stacked/cn-nixl-agent-tier` | item 1.1 | `nixl_transport.rs` façade + `agent_tier`, `nixl-transport` feature + `nixl-sys`, `cn-env.sh`, pixi `cn-build/cn-test/cn-run`, `result_store.rs` `as_halves` | C3, C5 |
| 8 | C6b `stacked/cn-nixl-exchange` | item 1.1 | the six RPC handlers, remote drain loop, `local_exchange.rs` remote half, `StagedBatch{..rows}`, `engine.rs` push_packed loop + `declare_input_cardinality`, `brpc.rs` transport arg, pixi `cluster2` | F5, C6a |
| 9 | C7 `stacked/cn-nixl-session-warmup` | item 1.1 | `warmup.rs` (pre-establish every peer session at bring-up; the cold-cluster deadlock fix), `list_alive_compute_nodes` | C6b |
| 10 | bench kit (D2a/D2b subset) | docs/bench | `benchmarks/cluster8.sh` (+ the `unset CUDA_VISIBLE_DEVICES` and `SIRIUS_QUERY_WATCHDOG_SECS` fixes), `benchmarks/tpch/{bench.sh,queries/q01.sql,q06.sql,README.md}`, `tools/oracle.py`, `tools/compare.py`, `bench/common/gen-tpch.sh`, `scripts/cn-distribution.py` | C7 |

Apply the plan's "Carve sheets — corrections" for each (F4's CMake order and `collect_from_rel` narrowing; F5 born 4-arg; T6's per-path
refusal and `BTreeMap`; C5's 2-arg `with_executor`; C6a's hardcoded `device_id() == 0` assertion; C7's env reads). Excluded everywhere:
`stream_lifecycle.*`, `nixl_bench.rs`, `nixl_echo.rs`, `two_node_harness.rs`, `notes/`, `.claude/skills`, `configs/gb200-*`, generated
YAML, `rmm_log.txt`.

## Step 3: end-to-end run (the plan's "Milestone end to end")

1. `pixi run apply-starrocks-patches && pixi run fe-check && pixi run cn-build`.
2. Data at SF1 and SF10, twice: lineitem as one file (exercises byte-range splits; `SIRIUS_CN_DUMP_FRAGMENTS` shows `FileOrFiles.start/length`)
   and as many files (exercises C0's multi-range schema). Reuse existing datasets if question 4 allows.
3. `NUM_CNS=4 SIRIUS_EXCHANGE_STAGING_BYTES=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 cluster8.sh` outside the nightly window. `nvidia-smi` shows
   exactly one CN per GPU; `SHOW COMPUTE NODES` all Alive (`awk -F'\t' '$9=="true"'`); the FE blacklist settles empty. Smoke first with
   `pixi run cluster2` and the DEMO Q6 shape (revenue 61567694.9502, count 6001215 at SF1).
4. `EXPLAIN` q01 must show `AGGREGATE (update serialize)` -> `EXCHANGE HASH_PARTITIONED(l_returnflag, l_linestatus)` -> `AGGREGATE (merge finalize)`
   + SORT -> `MERGING-EXCHANGE` with no `SET new_planner_agg_stage = 1`; q06 shows partial -> `EXCHANGE UNPARTITIONED` -> merge.
5. `MIN_BACKENDS=4 QUERY_TIMEOUT=120 bench.sh out.csv 3 q01 q06`, then `tools/oracle.py` + `tools/compare.py`: zero mismatches (q01 4 rows,
   q06 1 row). Repeat with `--cold-restart` (C7: cold q01 completes, no 60 s errorCode=62).
6. Per-CN logs: `nixl bandwidth canary ... gbps=` far above the 2.0 floor on first contact; `transmitted batches via nixl` (about 64 bytes
   per CN for q06); no "needs the nixl transport tier", no "input stream row count unknown", no `fail_stalled_query`.
   `cn-distribution.py` shows non-zero scan rows on every CN.

Done when q01 and q06 pass steps 4 to 6 on 4 CNs at SF10, cold and warm, zero oracle mismatches, all four CNs scanned.

## Step 4: report

- Push `demo/q1q6-integration` to `aocsa`; open the Draft PR per question 1 with a body that states the measured results (commands,
  numbers, log lines), the list of merged PRs and carved commits with SHAs, and every deviation from the source branch you had to make.
- Write `~/.claude/plans/demo-q1q6-report.md`: pass/fail per step, which carved layer differs from the source branch and how, and the
  cherry-pick plan for turning commits 1 to 9 into the real stack layers (F4 on #1702, F5 on F4, C2a/C2b on #1709's cn stack base, and
  so on).
- Update `~/.claude/plans/open-prs-review-order.md` (status brief) and the plan's execution log. Do not flip any PR to Ready, do not
  merge anything, do not close #1644.
