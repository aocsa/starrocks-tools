# Carve `feat/pin-table-cn` into clean upstream PRs (Q1/Q6 across many GPUs first)

## Context

`origin/feat/pin-table-cn` (SOT, head `efa312f4`, 13 commits on top of `origin/dev`) holds the whole
multi-CN campaign: Sirius as a StarRocks compute node (CN), one CN per GPU, TPC-H over byte-range
splits with a nixl GPU-to-GPU exchange, plus pin_table over the FFI and bench kits. It is a 73k-line
delta (305 files) and lives upstream only as ten unreviewable draft PRs (#1686, #1674, #1672, #1644,
#1598, #1636-#1639, #1296). Upstream already merged the C++ streaming fragment + Fragment FFI (#1481).

Goal: replace those drafts with self-contained PRs against `sirius-db/sirius` `dev`, each passing
CONTRIBUTING's "PR reviewability" bar (one sitting, motivation stated, self-sufficient description,
draft-first), pushed as `stacked/<name>` branches and merged bottom-up with `gh stack`. Priority is
the minimal chain that makes TPC-H **Q1 and Q6 run across N CNs (one per GPU)**; everything else
(hash-join wedge, pin_table, docs) lands after that milestone.

Evidence base: a 26-agent read-only workflow mapped every slice to files/hunks, adversarially checked
self-containment, reviewed mbrobbel's five translator drafts against SOT, scouted CI/conflicts, and
produced two orderings (critical-path vs independence). This plan is the synthesis. Hunk-level notes
per slice are in the appendix file next to this plan (`i-want-to-create-golden-crane-appendix.md`).

> **Update 2026-09-02:** `feat/pin-table-cn` was rebased onto upstream `dev` (`84ea4ab5`); its head is now
> `c1842df3` (13 commits, same content). All `efa312f4` references below mean the pre-rebase head; use
> `aocsa/feat/pin-table-cn` @ `c1842df3` when carving — `git diff origin/dev aocsa/feat/pin-table-cn` is now free of
> upstream noise. Step 2 is being executed from the dedicated clone `/home/prestouser/aocsa/sirius-stacks`.

## Decisions

From you: SOT = `origin/feat/pin-table-cn`; PR items = 1 exchange staging arena (1.1 NIXL, 1.2 Arrow
in/out for Doris), 2 parquet byte-range splits, 3 hash-join wedge fixes, 4 FFI surface, 5 two-phase
agg + CN exchange runtime, 6a exchange input cardinality, 6b pin_table series; docs/bench separate;
stream_lifecycle dropped; close all old drafts and open fresh PRs; push to sirius-db as `stacked/`.

Forced by the skeptic pass (not optional, so they are in the plan even though you did not pick them):
- **CN cluster bring-up** (`engine_settings.rs`, `main.rs` `--gpu-device/--gpu-memory-limit/
  --host-memory-limit`, readiness HTTP listener, `GPU_ENGINE_TEST_LOCK`): SOT's `engine.rs` does not
  compile without `EngineSettings`, and `cluster8.sh` launches every CN with those flags. It is the
  bottom layer of the CN stack (C1).
- **Zero-task query completion + opt-in watchdog** (`task_creator.cpp`, `task_scheduler.*`,
  `sirius_engine.cpp`): Q1's hash-partitioned merge gives most CNs zero keys; without the completion
  fix the query hangs forever. The watchdog (`SIRIUS_QUERY_WATCHDOG_SECS`, off by default) is what
  every hang-class test uses to fail in 20 s instead of hanging CI. One PR (L1).
- **mbrobbel's #1236 (decimal→FP64)** is on the Q1/Q6 critical path: `dev` refuses every decimal
  ARITHMETIC_EXPR and decimal `avg`. Merge it first rather than re-carve it.

Method choice: partition PRs by **file ownership** (each shared hot file — `sirius_ffi.*`, rust
`lib.rs`, `sirius_scan_manager.*`, `task_scheduler.*`, `compute_node_service.rs`,
`node_translator.rs` — is edited by exactly one stack), then order inside and across stacks by the
Q1/Q6 critical path. Standalone PRs wherever a slice touches no shared file.

## Stacking policy (the maintainers' notes, applied)

- **Source of the rules.** CONTRIBUTING.md at `1da0fe8f` (the version merged by #1662, the repo's
  first stacked PR, branch `stacked/docs-contributing-gh-stacks` on sirius-db/sirius) is a strict
  subset of CONTRIBUTING.md on current upstream `dev` (`84ea4ab5`), which adds the stack
  navigation/management/merging guides (#1663) and the PR reviewability checklist (#1664). This plan
  follows the `dev` version; nothing in the older version contradicts it.
- **Where branches live.** Stacked layers (`stacked/<name>`) are pushed to
  `github.com/sirius-db/sirius` itself — that is `origin` after the remote rename below — never to
  the fork. Self-contained PRs keep their branch on the fork `aocsa/sirius` but the PR is opened on
  sirius-db/sirius against `dev`. Merged branches auto-delete in both repos.
- **Gate first.** Stacking is maintainer-only and not the default. `gh auth status` shows account
  `aocsa`, listed in MAINTAINERS.md (line 16) and a member of the `sirius-maintainers` team with
  `push` on sirius-db/sirius (`maintain`/`admin` false). **Found 2026-09-02:** pushing
  `stacked/scan-byte-range-rule` to sirius-db was refused by a protected-branch hook ("You're not
  authorized to push to this branch") while no `stacked/*` branch existed — a branch-creation
  restriction that only admins bypass (#1662's `stacked/` branch was pushed by mike-wendt). Until an
  admin adds the `sirius-maintainers` team (or aocsa) to the allow-list for `stacked/**`, stack bottoms
  cannot be published; the fallback is self-contained fork PRs with documented gates. Default every PR to a
  **self-contained fork PR** (branch on `aocsa/sirius`, PR against `sirius-db:dev`); use a stack only
  where the work genuinely reviews better as ordered layers, each approvable without reading ahead.
  Size alone is never a reason to stack.
- Applying that rule to the map: **10 PRs are self-contained fork PRs** (no `stacked/` prefix):
  D1, X, K, A, C0, C3, C4, T6, AR1, D4. **Six stacks** remain, each a real dependency chain (the
  layer above uses code or test helpers the layer below introduces):
  - `scan`: S1 → S2 → S3 (S2 calls S1's rule; S3 rewrites the `can_serve` hunk S2 lands)
  - ~~`liveness`: L1 → L2~~ **dissolved 2026-09-02** (rule: a stack must have more than one layer at
    publication). L1 is the fork PR `pipeline-stall-watchdog`; L2 lands later as a fork PR gated on L1
    (its FRAG-6..9 reuse `watchdog_guard`), or as its own stack only if L2 itself needs splitting.
  - `ffi`: F1 → F2 → F4 → F5 → F6 → F7 → F8 → F9 (F2's tests need F1's fix; F4/F5 tests need F2's
    helpers; F5 needs F4's `local_files_plan_ranged`; F6 needs F2's RefCell; F7/F8/F9 are tests and
    new API on top). Sub-check per layer: each is approvable on its own diff.
  - `translator`: T1 → T2 → T3 → T4 → T5 (each builds on the previous translation surface)
  - `cn`: C1 → C2p → C2a → C2b → C5 → C6a → C6b → C7 → C8 → C9 → C10 (successive rewrites of
    `engine.rs` / `compute_node_service.rs`; each layer compiles and tests alone)
  - `docs-bench`: D2a → D2b → D2c (later docs link files the earlier PR adds; rumdl MD057)
  If a reviewer judges a layer not approvable alone, `gh stack unstack` is the escape hatch back to
  self-contained PRs (the file-ownership partition keeps that survivable).
- **Tooling**: `gh-stack` only. Graphite, `git-spice` and `ghstack` are banned (mixed tools on the
  same branches conflict). **Use the isolated gh from the conda env**: prefix every gh command with
  `source /home/prestouser/aocsa/gh-activate.sh &&` (gh 2.99, `GH_CONFIG_DIR=/home/prestouser/aocsa/gh-config`,
  logged in as `aocsa`, gh-stack v0.1.0 installed). Never run `gh auth setup-git` in that env (it would
  rewrite the global git credential helper). Both remotes in the stacking clone are SSH, so `git push`
  and `gh stack submit`'s pushes need no gh credential; only PR/issue API calls use the token. The
  pixi-global gh at `~/.pixi/bin/gh` reads `~/.config/gh` (the CI token) — do not use it for this work.
- **Remote setup is sticky.** `gh stack` only operates on `origin` (`--remote` is broken,
  gh-stack#381). Do it in a **dedicated clone**, not the shared `/home/prestouser/aocsa/sirius`
  (its worktrees and other Claude sessions share the remote config):
  ```bash
  git clone git@github.com:aocsa/sirius.git /home/prestouser/aocsa/sirius-stacks && cd /home/prestouser/aocsa/sirius-stacks && git remote rename origin aocsa && git remote add origin git@github.com:sirius-db/sirius.git && git fetch --all && git submodule update --init --recursive
  ```
  After this `origin` is upstream. **Always run `git remote -v` before pushing anything
  self-contained from that clone** — fork PRs push to `aocsa`, never to `origin`.
- **Branch naming**: every stack layer is `stacked/<name>` (mandatory; those branches push directly
  to upstream). Fork PRs use plain `<name>` on `aocsa`.
- **Draft first.** Every PR (stacked or fork) opens as Draft and is flipped to "Ready for review"
  only when it passes CONTRIBUTING "PR reviewability" (line 265 on upstream dev): motivation stated
  (`Closes/Refs #NNN` or the why), one reviewable unit, self-sufficient description (what, why, how
  verified, what is intentionally not handled). The flip is what pings CODEOWNERS.
- **Merging**: never "Enqueue stack" nor `gh stack merge` (tested-broken with the merge queue).
  Bottom-up: enqueue the bottom PR alone → wait for it to land → `gh stack sync --prune` (author's
  clone only) → `gh stack view` (✓ under the merged marker, no ⚠) → repeat. Merged branches
  auto-delete. Cross-stack gates (`DO NOT MERGE before #NNN`) are checked by hand at enqueue time.
- Fallback if push access were ever lost: manual fork-based stacking (each layer a fork PR whose
  description names its base PR; rebase by hand after each merge).

## Prerequisites (one-time, before any push)

1. Dedicated clone with the sticky remote rename (command above). Base every branch on **current
   upstream dev** (`origin/dev` in that clone; `84ea4ab5` on 2026-08-31 was 12 commits ahead of the
   fork's dev; refetch at execution time). Four files need real conflict resolution vs upstream:
   `gpu_aggregate_impl.cpp` (#1605), `sirius_scan_manager.cpp` (#1547/#1555), `sirius_extension.cpp`
   (#1553/#1205), `sirius_physical_partition.cpp` (#1606). None is on the Q1/Q6 chain.
2. `gh stack --help` works; `gh auth status` shows `aocsa`.
3. Titles are Conventional Commits (Validate check) and become the squash commit.
4. Reference example of the convention in practice: #1662 (`stacked/docs-contributing-gh-stacks`,
   stack badge 1/3, merged bottom-up into `dev` by its author). Our stacks look the same: badge
   `N/M` on every layer, each layer's page "wants to merge into" the layer below (diff base only;
   merges always land bottom-up into `dev`).

### Carving and publishing method (per PR)

- Whole files: `git checkout aocsa/feat/pin-table-cn -- <path>` (in the dedicated clone the SOT is
  `aocsa/feat/pin-table-cn`).
- Partial files: `git diff origin/dev aocsa/feat/pin-table-cn -- <path>` → keep only the hunks named
  in the carve sheet (`git checkout -p aocsa/feat/pin-table-cn -- <path>` or edit the patch), then
  apply the sheet's fixes.
- Format and lint: `pixi run pre-commit run --files <changed>` (src/test/docs) and
  `pixi run -e cn cargo fmt` (experimental/) or `pixi run cargo fmt --manifest-path rust/Cargo.toml`.
- Build/test locally on a GPU box (nothing GPU runs in upstream CI; every GPU-touching PR body
  names the box and pastes the run). C++: `pixi run make` then
  `build/release/extension/sirius/test/cpp/sirius_unittest "<tags>"`; also `make clang-debug` before
  submitting a C++ layer (arm64/vcpkg rows run only in the merge queue).
- **Fork PR**: `git checkout -b <name> origin/dev` … commit … `git remote -v` (confirm) …
  `git push aocsa <name>` … `gh pr create --repo sirius-db/sirius --base dev --head aocsa:<name>
  --draft --title "<type>(scope): ..." --body-file <desc.md>`.
- **Stack**: `gh stack init stacked/<b1> stacked/<b2> ...` (bottom-up order; `gh stack add
  stacked/<bN>` to grow later), commit on each layer (`gh stack up/down/switch`), then
  `gh stack submit` (pushes all layers to upstream, opens/updates Draft PRs); `gh stack sync` after
  `dev` moves; `gh stack rebase` for conflicts (never the Web UI "Rebase stack").
- Description: use the repo's `.github/pull_request_template.md` verbatim and fill it so the
  reviewability checklist is satisfied by the text itself:
  ```markdown
  <!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->
  ## Description
  <motivation: why this change is needed (Closes/Refs #NNN or the reason)>
  <what changed, one reviewable unit; if >1500 lines, why it cannot be split>
  <how it was verified: exact commands, GPU box/driver/date, pasted key output>
  <intentionally not handled: "X is tracked in #NNN / lands in <next layer>">
  <for stacked layers: "Layer N/M of the <stack> stack; base is stacked/<below>">
  <for gated PRs: "DO NOT MERGE before #NNN (<reason>)">
  <for config changes: the new knob, default, and where it is documented inline>
  ## Checklist
  - [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
  - [ ] Cover changes with new or existing tests
  - [ ] Document configuration changes in code and summarize in the description above
  - [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)
  ## References
  <superseded draft(s) this replaces, e.g. "Supersedes #1637 (closed)"; design notes; upstream issue links>
  ```
  Title: Conventional Commits (`<type>(scope): <description>`), it becomes the squash commit.
- Reviewer routing is by CODEOWNERS, so keep each PR inside one team's area where possible:
  `@sirius-db/sirius-core` (src/exec, pipeline, creator, planner, op, test/**/exec|op|pipeline|
  planner|utils) → liveness stack, K, X, F1's C++ half, T-independent engine PRs;
  `@sirius-db/sirius-io` (src/**/scan*, helper, io, memory, test/**/scan*) → scan stack, AR1;
  `@sirius-db/sirius-integrations` (experimental/, rust/crates/sirius*, test/**/integration) →
  ffi stack (Rust half), translator stack, cn stack, docs-bench under experimental/. Mixed PRs
  (F4, F5, L2's FRAG tests) ping two teams — say so in the description.

### Stack init commands (bottom-up; open each stack when its first layer is ready)

```bash
gh stack init stacked/scan-byte-range-rule stacked/scan-byte-range-ingestible stacked/scan-pinned-file-subset
gh stack init stacked/pipeline-zero-task-liveness stacked/join-never-buildable-slots
gh stack init stacked/ffi-transaction-scope stacked/ffi-fragment-rust stacked/ffi-substrait-byte-ranges stacked/ffi-exchange-staging stacked/ffi-pin-table stacked/ffi-engine-regression-tests stacked/ffi-merge-shape-oracle
gh stack init stacked/translator-exchange-stream-read stacked/translator-two-phase-agg stacked/translator-wire-order stacked/translator-avg-expansion stacked/translator-carried-common-slots
gh stack init stacked/cn-cluster-bringup stacked/cn-result-store-failure-propagation stacked/cn-fragment-park-relay stacked/cn-exchange-dispatch stacked/cn-prpc-client stacked/cn-nixl-agent-tier stacked/cn-nixl-exchange stacked/cn-nixl-session-warmup stacked/cn-nixl-drain-overlap stacked/cn-pin-table-admin stacked/cn-wire-type-parity
gh stack init stacked/docs-cn-runbooks stacked/bench-tpch-harness-oracle stacked/bench-cn-distribution
```
Grow the ffi stack with `gh stack add stacked/ffi-push-arrow` once the Doris design question is
settled; grow docs-bench with `gh stack add stacked/bench-pinned-kit` after C9 merges.

## The map: your 7 items → stacks

Legend: **[Q]** = required for the Q1/Q6 milestone. Sizes are changed lines (approx). Base is the
layer below unless "dev". Branches without the `stacked/` prefix are **self-contained fork PRs**
(pushed to `aocsa`, PR against `sirius-db:dev`); `stacked/` branches are gh-stack layers pushed to
upstream.

### Item 4 — FFI surface (Fragment Rust bindings) → stack **ffi** (bottom layers)
| # | branch | base | size | contents |
|---|---|---|---|---|
| F1 [Q] | `stacked/ffi-transaction-scope` | dev | ~130 | `lower_substrait` owns a transaction (DuckDB 1.5.5 throws `TransactionContext::ActiveTransaction` after `Fragment::build()` commits its view transaction — latent break on dev today); the `SIRIUS_LOG_BACKEND/DIR/LEVEL` block in `Context::Impl::bring_up`; Catch2 case in `test_sirius_ffi_fragment.cpp` (+3 substrait include dirs on `sirius_unittest` in CMake) |
| F2 [Q] | `stacked/ffi-fragment-rust` | F1 | ~900 | cxx bridge for `Fragment` + `stream_view_name`; `Fragment<'ctx>`, `SiriusContext::fragment(&self)`, `RefCell<UniquePtr<Context>>`, `execute_substrait(&self)`, `collect_arrow_stream`; fan-out tests (broadcast, hash-key INT64 + DECIMAL determinism, relay schema guard) + the three #1598 tests SOT dropped |

### Item 2 — Parquet byte-range splits → stack **scan** (S1, S2) + ffi layer F4 + standalone T6
| # | branch | base | size | contents |
|---|---|---|---|---|
| S1 [Q] | `stacked/scan-byte-range-rule` | dev | ~360 | `parquet_byte_range.{hpp,cpp}` + 4 Catch2 cases + 2 CMake lines (byte-identical to old #1636) |
| S2 [Q] | `stacked/scan-byte-range-ingestible` | S1 | ~260 | `resolved_file_ranges`/`has_byte_ranges()` on the ingestible, range filter before stats pruning, ctor pairing guard, `cache_entry_info::has_byte_ranges` + can_serve guard, 2 scan cases + NEW has_byte_ranges test |
| F4 [Q] | `stacked/ffi-substrait-byte-ranges` | F2 | ~470 | `substrait_scan_ranges.{hpp,cpp}`, planner `attach_byte_ranges`/S3 refusal/`assert_all_consumed`, the extract/install block at the top of `lower_substrait`, Rust `local_files_plan_ranged` + GPU test; gate: S2 merged |
| T6 [Q] | `translator-byte-range-splits` (fork PR) | dev | ~525 | `scan_paths.rs` `resolve_ranges`/`ScanFile`, `scan_rel`/`local_files_rel`, 8 translate tests; generalizes #1232. **HOLD until F4 merged** (CN emitting ranges into an engine that ignores them duplicates rows N times, silently) |

### Item 1 — Exchange staging arena → standalone X + ffi layer F5
| # | branch | base | size | contents |
|---|---|---|---|---|
| X [Q] | `exec-exchange-staging-arena` (fork PR) | dev | ~930 | `exchange_staging_arena.{hpp,cpp}`, 13 `[staging_arena]` cases, 2 CMake lines, `configuration.md` rows for `SIRIUS_EXCHANGE_STAGING_BYTES` / `_ARENA=fabric` (fabric path kept, labelled opt-in and CI-untested) |
| F5 [Q] | `stacked/ffi-exchange-staging` | F4 | ~950 | one touch of the FFI for the whole exchange surface: `Context::staging_*`, `StagingArena` handle (Send/Sync argument), `Fragment::export_packed` **born 4-arg with `rows`**, `push_packed`, `declare_input_cardinality`, `output_row_count`, `PackedBatch{..rows}`, sirius-sys/sirius wrappers, the three SOT Rust tests verbatim, `streaming-fragments.md` section; gates: X and K merged |

### Item 1.1 — NIXL integration → stack **cn** upper layers (+2 standalones)
| # | branch | base | size | contents |
|---|---|---|---|---|
| C3 [Q] | `cn-exchange-proto-patch` (fork PR) | dev | ~180 | `patches/nixl-exchange-proto.patch` (incl. `optional uint64 rows = 10`), `apply-starrocks-patches.sh`, `build.rs` guard, `.gitmodules ignore = dirty`, pixi task, **and the mandatory `.github/workflows/experimental.yml` patch-apply step** (CI checks out an unpatched submodule; without it every CN PR is red) |
| C4 [Q] | `cn-transport-tunables` (fork PR) | dev | ~520 | `tunable.rs` reject-not-clamp registry + 10 CI tests, `Tunables::resolve()` first in `main`, `docs/TUNABLES.md` transport section |
| C5 [Q] | `stacked/cn-prpc-client` | C2b | ~415 | `prpc.rs` client half promoted from cfg(test), `prpc_client.rs` + 3 loopback tests; gate C4 |
| C6a [Q] | `stacked/cn-nixl-agent-tier` | C5 | ~1150 | `nixl_transport.rs` façade + `agent_tier` (single-thread agent owner, arena registered as VRAM, 16 MiB bandwidth canary), Cargo `nixl-transport` feature + `nixl-sys` (regenerate Cargo.lock on upstream's syn bump), `cn-env.sh`, pixi `cn-build/cn-test/cn-run`, `result_store.rs` `as_halves` + pub `FragmentInstanceId`, `main.rs` `build_nixl_transport` cfg pair; gate C3 |
| C6b [Q] | `stacked/cn-nixl-exchange` | C6a | ~1250 | the six RPC handlers + blocking remote-drain loop, `local_exchange.rs` REMOTE half, `StagedBatch{..rows}`/staging in `fragment_executor.rs`, `engine.rs` push_packed loop + declare_input_cardinality block, `brpc.rs` transport arg, DEMO.md nixl section, pixi `cluster2`/`fe-check`; gate F5 merged |
| C7 [Q] | `stacked/cn-nixl-session-warmup` | C6b | ~560 | `warmup.rs`: pre-establish every peer session at bring-up (reproduced cold-cluster deadlock: q14 cold FAILED after 121 s errorCode=62, warm 751 ms); `list_alive_compute_nodes`; move `parse_peer_list`/backoff out of the feature gate so their tests run in CI |
| C8 | `stacked/cn-nixl-drain-overlap` | C7 | ~350 | `DrainTicket/SenderDrains/dispatch_then_join` (3×30 ms per CN on q14 SF500); wire order unchanged |

### Item 1.2 — Arrow in/out for Doris (NEW work, not in SOT) → standalone AR1 + ffi top layer F9
| # | branch | base | size | contents |
|---|---|---|---|---|
| AR1 | `helper-arrow-host-import` (fork PR) | dev | ~570 | `src/helper/arrow_host_import.{hpp,cpp}`: `cudf::from_arrow(ArrowSchema*, ArrowArray*)` (settle vs `from_arrow_host` with @morningman first; `from_arrow` needs no `ArrowDeviceArray` and deletes the ODR-hazard shim) → validate/normalize vs declared stream schema (decimal128→DECIMAL64 cast policy) → `cudf::table`; Catch2 round-trip via `cudf::to_arrow_host`; 2 CMake lines |
| F9 | `stacked/ffi-push-arrow` | F8 | ~450 | `Fragment::push_arrow(stream_id, schema_addr, array_addr)` mirroring `push_packed` (guards, H2D copy, synchronize, `make_data_batch`, `session().push()`, throw on false); Rust `push_arrow(&mut self, &RecordBatch)` via `arrow_array::ffi::to_ffi`; tests reuse F2 helpers; doc section. Decide before coding: undeclared-stream behaviour, and whether push during `run()` from other threads is in scope (Doris asks for it; store-and-forward is acceptable to them as a first step) |

### Item 3 — Hash-join wedge fixes → stack **liveness**
| # | branch | base | size | contents |
|---|---|---|---|---|
| L1 | `stacked/pipeline-stall-watchdog` (was `pipeline-zero-task-liveness`) | dev | ~222 | **Carved as `d53b44b1`, verified, awaiting `stacked/**` push access.** Watchdog only: `SIRIUS_QUERY_WATCHDOG_SECS` (`query_progress_fingerprint`, `wait_for_query_future`, strict digits-only parse 0..86400, `fail_stalled_query`; unset/0 = byte-identical `future.get()`), `watchdog_guard` + `SIRIUS_TEST_WATCHDOG_SECS` test knob, FRAG-11 regression case, `configuration.md` subsection, two-sentence `task-creator.md` note. **The SOT zero-task completion hunk (`task_creator.cpp` re-check + `complete_query_if_finished`) was dropped: FRAG-11 passes with `task_creator.cpp` at dev, because #1624 already completes a pipeline that finishes before run() through the transition signal.** Consequence for the milestone: L1 is no longer Q1-critical on its own; the CN fan-out hang must be re-checked on the CN path once C2b exists. Open question for #1642: a non-front streaming source that closes empty before run() is never scheduled — not reachable by FRAG-11. Draft, to settle with Jedi18 |
| L2 | `stacked/join-never-buildable-slots` | L1 | ~700 | `build_only_slots_to_discard` (generalized from broadcast), `never_buildable_slots`/`resolve_never_buildable_slots`, `drain_and_free_partition`, partition zero-byte strategy through the consumer (join + partition halves MUST ship together), 6 `[build_probe]` unit tests, FRAG-6..9 (strip the FRAG-9 `std::cerr` dump + `<iostream>`), `operators.md` two sentences; rebase on #1606 (`partition_sizing_input` gains `combined_total_bytes`) |

### Item 5 — Two-phase aggregation + CN exchange runtime → mbrobbel merges, stack **translator**, stack **cn** lower layers, standalone A
| # | branch | base | size | contents |
|---|---|---|---|---|
| — [Q] | mbrobbel #1236 → #1235 → #1233 → #1232 | dev | | update-then-merge in that order; close #1242 (see "mbrobbel drafts") |
| T1 [Q] | `stacked/translator-exchange-stream-read` | dev (after #1236/#1235/#1233) | ~1300 | `translate_exchange` → `ReadRel` over `sirius_stream_<node_id>` (`stream_read_rel`), `named_struct_for_tuples` (no names param), `duckdb_type_name`, `ExchangeInput{node_id,stream_view,names}`/`StreamInputSchema`/`TranslatedPlan.stream_inputs`/`translate_fragment_with_exchange_inputs`, `output_partition_columns` hash keys, merging exchange → `SortRel`, descriptor slot-id fallback (+2 unit tests), port #1242's three guard tests rewritten for streams, fix the two stale "materialized" doc strings in lib.rs |
| T2 [Q] | `stacked/translator-two-phase-agg` | T1 | ~1400 | `agg_phase.rs` classify (replaces BOTH legacy guards atomically), `partial_state.rs` wire model + 7 tests, partial/merge translation with `count→sum` substitution, `merge_projection` casting back to FE types, `merge_exchange_overrides`, `expr_translator` `merge: bool`/`raw_arguments`/`cast_to`/`cast_to_fp64` on top of #1236, `type_mapper` fp64/i64, phase/wire-type tests. Retires `SET new_planner_agg_stage = 1`. **Q6 translates after this layer** |
| T3 [Q] | `stacked/translator-wire-order` | T2 | ~700 | grouping/sort tuples emitted in materialized wire order, not slot id (`grouping_materialization_order`, `sort_materialization_order`, q03-shaped tests). Q1's GROUP BY/ORDER BY key order ≠ slot order |
| T4 [Q] | `stacked/translator-avg-expansion` | T3 | ~700 | `expand_avg`/`PartialExpansion`/`merge_state_columns`/`avg_from_state` (SQL empty-input NULL), lib.rs `partial_expansion` arms; body must state the fences (expansion only at fragment root, no conjuncts above, no output_exprs, hash keys must be grouping keys) guard a row-width invariant. **Q1 translates after this layer** |
| T5 | `stacked/translator-carried-common-slots` | T4 | ~900 | SOT's extension of #1233 (`common_slots_consumed_above`, `CarriedSlot`, `refuse_carried_join_child`, `confine_carried`, 8 tests) + back-port of #1233's `reject_common_slots` narrowed to non-materialized ids. q14 shape |
| C0 [Q] | `cn-files-schema-multi-range` (fork PR) | dev | ~200 | `file_schema.rs` `parquet_files_schema` over every range in a `get_file_schema` request (dev infers from `range[0]` only) |
| C1 [Q] | `stacked/cn-cluster-bringup` | dev | ~1230 | `engine_settings.rs` (+ derived Sirius YAML), `main.rs` flags/`EngineConfig::resolve`/`ensure_gpu_unclaimed`/listeners-before-engine/`SHUTDOWN_GRACE`, `SiriusEngine::start(EngineSettings)` + `configure_engine_environment`, lib.rs `EngineReadiness` + HTTP readiness listener + heartbeat NOT-READY interlock + `GPU_ENGINE_TEST_LOCK`, `gpu_affinity.rs` (carve into a follow-up if a reviewer asks), engine-link half of `cn-env.sh`, `.gitignore`, TUNABLES engine-side rows |
| C2p [Q] | `stacked/cn-result-store-failure-propagation` | C1 | ~400 | `result_store.rs` reserve/wait_ready/fail/fail_query/cancel + 8 tests (additive, dev callers keep working) |
| C2a [Q] | `stacked/cn-fragment-park-relay` | C2p | ~1370 | `fragment_executor.rs` (`FragmentRun/SenderSlot/run()`, keep an `execute()` shim), `engine.rs` park/parked_slots/poisoned state machine + `run_fragment_inner` (declare inputs/senders/outputs/broadcast/hash keys, build, relay_from, run, park-once-claim-per-destination), `local_exchange.rs` LOCAL half (ship the `SenderSource::Remote` arm too — it is load-bearing in `names()`/`is_complete()`); gate F2 merged |
| C2b [Q] | `stacked/cn-exchange-dispatch` | C2a | ~1550 | `compute_node_service.rs` `ServiceCore`/`ExchangeIdentity`/`DestinationRoute` (Remote = loud "needs the nixl transport tier" until C6b)/dispatch worker + mpsc/`exec_plan_fragment` spawn_blocking/`fetch_data` long-poll/`execute_fragment_with_inputs` (result sink, DATA_STREAM_SINK validation, UNPARTITIONED broadcast, HASH_PARTITIONED keys), `brpc.rs with_executor(executor, identity)`, `main.rs` identity, lib.rs `mod local_exchange`, cancelPlanFragment OK stub, DEMO.md single-host; removes the shim; >1500 lines justified in body (rendezvous + dispatch are one protocol); gate T1 merged |
| A | `agg-deterministic-sums` (fork PR) | dev | ~570 | `aggregate_op_util.{hpp,cpp}` (`is_order_sensitive_sum`, `canonicalize_row_order`, `throw_if_int64_sum_could_overflow`), sorted-groupby path in `gpu_aggregate_impl.cpp`/`gpu_merge_impl.cpp`/ungrouped, 3 `[deterministic_agg]` tests + NEW overflow-guard test. **Rebase onto upstream #1605 first (2 conflict markers). Measure q01/q09 SF100 single-GPU before/after — canonicalize_row_order adds a full sort+gather on every FLOAT-SUM aggregate and disables the STRING dict-encode path; gate behind a flag if material. Do not claim it fixes q15 (per-batch determinism only)** |
| C10 | `stacked/cn-wire-type-parity` | C9 | ~740 | `wire_type_parity.rs` engine-derived conformance gate; make `partial_state` public so `rows()` is derived, not hand-copied; gates T2, T3 |

### Item 6a — Exchange input cardinality (55e63371) → standalone K (+ rows in F5, CN plumbing in C6b)
| # | branch | base | size | contents |
|---|---|---|---|---|
| K [Q-enabler] | `exec-stream-cardinality` (fork PR) | dev | ~300 | C++ half only: `stream_input_binding::estimated_rows` **appended AFTER `built`** (keeps the 4-field brace-init in dev's `sirius_ffi.cpp` compiling, so this PR touches no FFI file), `stream_bind_catalog::estimated_rows`, `stream_source_cardinality` callback → `LogicalGet::EstimateCardinality`, `streaming_fragment` forward, CAT-10, FRAG-10 (drop the `watchdog_guard` line; inert on dev), `streaming-fragments.md` paragraph. `Fragment::declare_input_cardinality/output_row_count` land in F5; the wire `rows` field in C3/C6b. Consider returning optional from `output_row_count` instead of throwing on spilled batches |

### Item 6b — pin_table series → scan top layer S3 + ffi layer F6 + cn layer C9 + bench D3
| # | branch | base | size | contents |
|---|---|---|---|---|
| S3 | `stacked/scan-pinned-file-subset` | S2 | ~540 | `chunk_file_paths` provenance at pin time (coalescer file-boundary mode), `matches_parquet_file_set` exact/subset/miss, `allowed_chunks` in `build_cached_scan_plan` before zone-map pruning, `try_match_cached_entry` subset block, 2 existing tests + 3 gap tests (insert arity/adopt/mismatch, coalescer boundaries, integration pin-3-query-2); rebase on #1547 (re-read its preference order — the subset path must not resurrect an entry #1547 rejects on type drift); fix stale `canonical_scan_file_path` doc + clang-format alignment |
| F6 | `stacked/ffi-pin-table` | F5 | ~260 | `RegisterPinTableFunctions` split out of `RegisterGPUFunctions` (rebase over #1553/#1205 anchor), `Context::pin_table/unpin_table`, sirius-sys + `PinTableSpec` on the `&self` RefCell context (why it sits above F2), a pin-through-FFI test |
| C9 | `stacked/cn-pin-table-admin` | C8 | ~590 | `admin_command.rs` closed grammar (lead the body with "NOT the BE's arbitrary debug-script semantics"), `execute_command`/`handle_execute_command` + 3 tests (incl. `execute_command_propagates_executor_error_with_command_index`), `EngineRequest::PinTable`, `patches/files-query-whole-file-ranges.patch`, 600 s ceiling doc, CN section of `pinned-tables-repro.md`; gates F6, S3 |
| D3 | `stacked/bench-pinned-kit` | D2b | ~1400 | `benchmarks/pinned/*` (exclude `generated/cn*.yaml`, add .gitignore), decide ONE driver: `starrocks_performance_test.py` (912) or the shell kit; `tpch_pin_columns.py` docstring: name the function, not line numbers; gate C9 |

### Docs / bench (docs-only for CI; content true only after the cn stack)
| # | branch | base | size | contents |
|---|---|---|---|---|
| D1 | `docs-pinned-tables-repro` (fork PR) | dev | ~190 | `docs/pinned-tables-repro.md` §0-4 + measured reference (every statement exists on dev; hold the StarRocks section for C9), `docs/README.md` link, `docs/super-sirius/README.md` index row + reading order (D1 owns this file), `.claude/settings.json` pre-commit hook scoped to `git commit` (real bug: every unrelated Bash call pays a 180 s agent round trip). Open day one |
| D2a [Q-verify] | `stacked/docs-cn-runbooks` | dev | ~1300 | `experimental/starrocks/docs/BUILDING.md` (relocated from `bench/rtxpro6000-2gpu/BUILD-SIRIUS-STARROCKS.md`, 0 hardcoded paths), `cluster8.sh`/`-cfg`/`-numa` (**fix: `unset CUDA_VISIBLE_DEVICES` before launch — an exported value wins over `--gpu-device` and collapses all N CNs onto one GPU; export `SIRIUS_QUERY_WATCHDOG_SECS`**), `collect-host-facts.sh`, `clean-telemetry.sh`, TUNABLES engine rows, DEMO.md de-branched; gate C7 |
| D2b [Q-verify] | `stacked/bench-tpch-harness-oracle` | D2a | ~1900 | `benchmarks/tpch/README.md`, `bench.sh` (**fix Alive count: `awk -F'\t' '$9=="true"'` not `grep -c true`; wire `tools/compare.py` so a 1-row answer is not a fast WIN**), `analyze.py` (guard matplotlib import), `run-comparison.sh`, `setup-engine-b.sh` (mem_limit rewrite first-setup-only), `QUERY-DEVIATIONS.md`, `queries/q01..q22.sql`, `tools/oracle.py` + `compare.py` relocated from `bench/rtxpro6000-2gpu/tools/`, `bench/common/gen-tpch.sh` + `RETARGETING.md` (six dead links rewritten). `run-abc.sh` (1542 lines, box-entangled) stays out |
| D2c | `stacked/bench-cn-distribution` | D2b | ~730 | `scripts/cn-distribution.py` — the proof that every CN did work |
| D4 (defer) | `bench-two-host` (fork PR) | dev | ~850 | `cn-2host.sh`, `stop-cn-2host.sh`, `2NODE-REPLICATE.md`, `bench/gb200-8gpu/sweep.sh` only after de-hostnaming and dropping the `sed -i` on a tracked query |

Totals: ~40 PRs (10 self-contained fork PRs + 6 stacks of 2-11 layers) + 5 mbrobbel dispositions.
Only C2b, D2b and (borderline) T1/T2 exceed ~1500 lines; each body carries the CONTRIBUTING item-2
justification and a pre-planned split.

## Step-2 execution log (2026-09-02)

| PR | Branch @ commit | State |
|---|---|---|
| X | `aocsa/exec-exchange-staging-arena` @ `1e61c16c` | **Draft #1693 open** on sirius-db (fork PR) |
| K | `aocsa/exec-stream-cardinality` @ `f1e8fb17` | **Draft #1694 open** on sirius-db (fork PR) |
| S1 | `stacked/scan-byte-range-rule` @ `94a77836` | **Draft #1696**, bottom of GitHub stack #1701 (verified `[parquet_byte_range]` 4/4) |
| S2 | `stacked/scan-byte-range-ingestible` @ `a22235e1` | **Draft #1700**, layer 2 of stack #1701 (verified `[parquet_byte_range]` 6/6, `[can_serve]` 12/12, `[scan]` 282/282) |
| F1 | `stacked/ffi-transaction-scope` @ `98661d7d` | **Draft #1697**, bottom of GitHub stack #1703 (verified `[sirius_ffi]` 3/3 incl. new case, `[streaming_fragment]` 5/5) |
| F2 | `stacked/ffi-fragment-rust` @ `14386a77` | **Draft #1702**, layer 2 of stack #1703 (fmt/clippy clean; `cargo test -p sirius` 10/10 on GPU; the bare `cargo test --no-run` needs conda rpath flags on this aarch64 box — pre-existing toolchain quirk, x64 CI is the authority). Supersedes #1598 (closed) |
| — | `stacked/pipeline-stall-watchdog` on sirius-db | #1698 (created by the user from the earlier instructions) closed as duplicate of fork PR #1699 under the one-layer rule; upstream branch deleted |
| L1 | `aocsa/pipeline-stall-watchdog` @ `d53b44b1` | **Draft #1699 open** on sirius-db (fork PR; one-layer stacks are plain PRs). Verified (`[streaming_fragment]` 6/6 incl. FRAG-11; full suite 3066/3066 on the pre-amend commit) |

Upstream `dev` moved to `c7b21ae7` (mbrobbel's #1235, #1236, #1233 merged; #1232 still open; #1242 closed).
The SOT gained a 14th commit `d24f02c4` ("fix(ci): green up the lint, build, rust, and CN gates": clippy loop
fixes, parquet `set_max_row_group_row_count(Some(n))`, clang-format, stream_lifecycle deleted, pin_table
`on_conflict = IGNORE`). Checked against the five step-2 branches: no code drift (only comment wording in the arena
files, where X's carved text is the intended fix); F2/S2 are carved from `d24f02c4`. Step 3/4 in progress: F2
`stacked/ffi-fragment-rust` (on F1) and S2 `stacked/scan-byte-range-ingestible` (on S1), to be opened as fork
Drafts whose diff includes the base commit; stack publication instructions:
`~/.claude/plans/stacked-publish-when-permitted.md`.

All five: one commit, author aocsa, Conventional Commit title, Claude co-author trailer, pre-commit clean,
built incrementally on this GB200 (aarch64, CUDA 13.0) over the baseline `dev` build. Bodies are in the session
scratchpad `pr-bodies/` and reproduced in the appendix. The dedicated clone's remotes were switched to HTTPS with a
**repo-local** `credential.helper='!gh auth git-credential'` (SSH failed under the conda gh env); global git config
untouched. `gh stack init` needed `-b dev` (`git remote set-head origin dev` was also run). Blocked on the
`stacked/**` creation restriction — see "Gate first"; user chose to wait for an admin rather than fall back to
fork PRs. Once granted: `gh stack submit --auto` on each of S1/F1/L1, then `gh pr edit` title+body, then close
#1636 (superseded by S1) with a pointer.

## Execution waves

| wave | open (all Draft) | notes |
|---|---|---|
| 0 | Fork PRs: D1, X, K, A (after #1605 rebase), C3, C4, C0. Stack bottoms (`gh stack init`): F1 (ffi), S1 (scan), L1 (liveness), C1 (cn). Post update requests on #1236, #1235, #1232; approve/merge #1233 after #1235; close #1242 with a pointer to T1 | 11 independent PRs, none shares a file; 9 have zero upstream overlap |
| 1 | S2 (on S1), F2 (on F1), L2 (on L1, rebased on #1606), T1 (after #1236/#1235/#1233 merge), C2p (on C1) | |
| 2 | F4 (on F2; gate S2), T2 (on T1), C2a (on C2p; gate F2), S3 (on S2; rebase on #1547) | **Q6 translates after T2** |
| 3 | F5 (on F4; gates X, K), T3 (on T2), C2b (on C2a; gate T1), C5 (on C2b; gate C4) | |
| 4 | T4 (on T3), T6 standalone (gate F4), F6 (on F5; rebase on #1553/#1205), C6a (on C5; gate C3) | **Q1 translates after T4** |
| 5 | C6b (on C6a; gate F5), C7 (on C6b), F7 `stacked/ffi-engine-regression-tests` (on F6; gates L1, L2, A: the Rust hang-class/empty-build/int64 tests), F8 `stacked/ffi-merge-shape-oracle` (on F7) | |
| 6 | D2a → D2b → D2c (gate C7), T5, C8, C9 (gates F6, S3), C10 (gates T2, T3), D3 (gates C9, D2b), AR1 / F9 once the Doris design question is answered | |

Merge bottom-up as layers get approved (enqueue one PR, wait, `gh stack sync --prune`, `gh stack
view`); several bottom layers can sit in the queue the same day because the stacks own disjoint
files. Fork PRs merge whenever approved, subject only to their documented gates.

### Q1/Q6 milestone — minimal set

#1236, F1, F2, S1, S2, F4, T6, X, K, F5, L1, T1, T2, T3, T4, C0, C3, C4, C1, C2p, C2a, C2b, C5,
C6a, C6b, C7. Verification scripts come from D2a/D2b (run them out of tree from SOT until merged).
Q6 alone does not need L1, T3, T4, C7 (N→1 gather; the receiver's own sender is local) — but ship the
milestone with them. Not on the path: L2, A, S3/F6/C9, C8, T5, C10, docs, Arrow.

Fallback if C6a/C6b stall in review: the Q1/Q6 exchange payload is ~64 bytes per CN, so a ~200-line
brpc-attachment host-bounce transport (export to lease → D2H attachment → H2D into the peer's lease →
push_packed) would reach the milestone without libnixl. Not in SOT; keep NIXL as the plan.

## Carve sheets — corrections the skeptics found (apply while carving)

- **F2**: `let mut ctx` → `let ctx` in `executes_local_files_plan_on_gpu` (clippy -D warnings after
  `&self`); rewrite `close_input` docs that intra-link `push_packed`; restore #1598's fuller bridge doc
  comments; decide once: `into_arrow` (SOT, `&mut self`, does not consume) vs `result_to_arrow`
  (#1598, matches C++) — the CN renames ~10 call sites at carve time; `fragment_over_an_empty_input_
  stream_terminates` is EXCLUDED here (needs L1; lands in F7). `rust/Cargo.lock` is byte-identical
  to dev — do not take #1598's uuid downgrade.
- **S1/S2**: S1 (as carved, commit `94a77836`) dropped six includes nothing in its half uses:
  `io/kvikio/kvikio_context.hpp`, `op/scan/parquet_gpu_ingestible.hpp`, `cudf/column/column_factories.hpp`,
  `cudf/table/table_view.hpp`, `cudf/utilities/default_stream.hpp`, `rmm/device_buffer.hpp` (and the
  `// rmm` section header). **S2 must re-add them** when it appends `write_multi_row_group_parquet`,
  `ranged_info`, `scan_selection` and the two `[parquet_byte_range][scan]` cases; its diff of that file
  is not a pure append against SOT any more.
  `has_byte_ranges` has zero tests on SOT — add a ~15-line case in `test_can_serve_with_columns.cpp`;
  carve the `can_serve_with_columns` hunk at line level (keep dev's `matches_parquet_files` line, add
  only the `has_byte_ranges` guard — SOT's hunk is fused with S3's `matches_parquet_file_set`).
- **F4**: fix SOT's out-of-order CMake insertion and the `leaf =` clang-format nit; state in the body
  that `collect_from_rel` throws on unknown Rel types even for plans with no ranges (a narrowing of the
  accepted plan surface) — align with `from_substrait.cpp` or make it a logged skip when no LocalFiles
  item carries a range.
- **T6**: rebase onto merged #1232 (`resolve_ranges` replaces `validate_complete_files`); port #1232's
  descending-order / disagreeing-file-size / missing-size-after-first tests; refuse per path (not per
  node) when a file's ranges are all empty; `BTreeMap` for deterministic error text; add an
  adjacent-range coalescing test.
- **X**: fix two dangling comment paths (`bench/a100x8/TUNING.md`, `two_node_harness.rs`) and the stale
  "bump head" wording (four places incl. F5's docs); ARENA-6 setenv/unsetenv is process-global (note
  it); `cudf::chunked_pack`/`unpack` are first-use in the tree — say so in F5; `kPackChunkBytes` 8 MiB
  over-lease per in-flight export is an arena-sizing consequence to state.
- **F5**: order resolves the skeptic's compile breaks — K supplies `estimated_rows`, F4 supplies
  `local_files_plan_ranged` (the zero-row test needs it), F2 supplies `write_users_parquet`/
  `stream_read_plan`/`rows()`. Pre-planned split if a reviewer objects to ~950 lines: F5a
  (`staging_*` + `StagingArena`) / F5b (packed + cardinality).
- **K**: append `estimated_rows` after `built`; drop `watchdog_guard watchdog("20")` from FRAG-10; the
  commit message's "CAT-7/9" claim is wrong — say CAT-10 + FRAG-10.
- **L1**: `complete_query_if_finished` deliberately does not drain (caller is the manager thread);
  check whether `update_pipeline_status(false)` is redundant after #1624; make the watchdog test
  thresholds env-configurable (20-30 s may be too low on a slow CI GPU).
- **L2**: the loud throw lands in `get_next_task_input_data_for_build_probe` (not
  `get_next_task_input_data`); the zero-byte hint path omits `set_build_arrives_whole` and the
  BUILD_PROBE-without-concat throw that the task-time block does — either add them or explain; add a
  direct unit test of `compute_hash_join_partition_strategy(total_bytes=0)` (BUILD_PROBE hinges on
  `build_foldable`); drop `pipeline_conversion_test_utils.hpp`'s stray `<optional>`; `_never_buildable_
  destroyed_slots`/`_build_only_discarded_slots` grow unbounded and re-drain every pass — note or dedup.
- **A**: `to_cudf_aggregation_kind` already exists on dev (map error); drop duplicate `<algorithm>`.
- **T1**: port #1242's guards (empty `input_row_tuples`, names/width arity, exchange offset → FetchRel)
  as stream tests; add an empty-`stream_view` guard; document the positional sender-name contract.
- **T2**: `expr_translator::aggregate_call`'s `merge: bool` must travel with this layer (else a merged
  decimal sum re-casts its FP64 partial state on top of #1236); the `map_scalar_type` precision>18 hunk
  belongs to #1236, not here.
- **C1**: `engine.rs:207-216` lets an exported `CUDA_VISIBLE_DEVICES` win over `--gpu-device` with only
  a warn — the launcher fix is in D2a; consider erroring.
- **C3**: decide submodule-patch (`ignore = dirty`) vs vendoring a Sirius-owned proto BEFORE opening;
  `build.rs`'s guard runs unconditionally and `compute_node_service.rs` imports the patched types with
  no feature gate — hence the workflow step is mandatory in this PR. Confirm nixl-sys 1.3 license /
  provenance (links a non-redistributable NVIDIA library) before C6a.
- **C5**: its tests call `with_executor` with THREE args on SOT; at this position the signature is the
  2-arg form from C2b — adapt here, C6b adds the transport arg.
- **C6a**: `ArenaRegion::device_id()` is hardcoded 0 — assert the one-CN-per-GPU invariant. State that
  `agent_tier` compiles in NO CI job.
- **C7**: fold `SIRIUS_CN_NIXL_WARMUP`/`_PEERS` into the C4 registry (currently raw `env::var`).
- **D2a/D2b**: rumdl MD057 fails a docs PR that links to a file landing in a LATER PR — every relative
  link must resolve inside the PR.
- **Global**: never include `src/exec/stream_lifecycle.*`, `test/cpp/exec/test_stream_lifecycle.cpp`
  (unregistered → `check-orphan-tests` would fail Check/lint), `.pixi`, `experimental/starrocks/
  rmm_log.txt`, `notes/**` (52 files), `.claude/skills/*`, `configs/gb200-*`, `tpch/plans/*`,
  `tpch/results/*`, `benchmarks/pinned/generated/*`, `bench/a100x8`, `bench/gb200-4gpu`,
  `bench/sf500-gb200`, `bench/rtxpro6000-2gpu/{results,STATUS,TPCH-STATUS,SF500-*,README}`,
  `nixl_bench.rs`/`nixl_echo.rs`/`two_node_harness.rs`/`nixl-echo-2node.sh` (2291 lines that never
  compile in CI — propose separately later, if at all), `run-abc.sh`, `io-mode-demo.py`,
  `2NODE-ENGINE-B-*`, `8GPU-NVLINK-RUNBOOK.md`, `host-facts-*.txt`. The `DECODE_PUSHDOWN_PLAN.md` →
  `notes/` rename is dropped with notes/.

## mbrobbel's drafts — disposition and tasks (post as review comments / description edits)

All five still apply cleanly to upstream dev (pre-image blobs byte-identical). Merge order:

1. **#1236 decimal→fp64 — update-then-merge (critical path).** Tasks: rebase onto `84ea4ab5`; add
   tests for the `DECIMAL_LITERAL` precision>18 → Fp64 arm and the `map_scalar_type` 18/19 boundary;
   reword the avg refusal to SOT's "avg is only supported where it lowers to the GPU's FP64 avg (DOUBLE
   and DECIMAL inputs)" (the PR's "temporal avg is not supported" mislabels the cause); route both
   casts through one `cast_to_fp64` helper; keep the `translate_arithmetic` rationale comment and the
   lib.rs "decimal arithmetic is not exact" paragraph (SOT dropped them — port them back onto T2).
   Follow-ups to file: decimal arithmetic with FE precision ≤18 ships FP64 into a DECIMAL-declared
   stream column (cast root outputs back or refuse); the precision>18 → FP64 rule also rewrites scan
   and GROUP BY slots (silent precision loss beyond 2^53, untested).
2. **#1235 anti joins — update-then-merge.** Strictly ahead of SOT: SOT lacks the null-aware NOT IN
   refusal (silent too-few-rows) and its `anti_hash_joins_are_lowered` test only checks `names.len()`.
   Tasks: rewrite the one-line description (motivation: `from_substrait.cpp:TransformJoinOp` has no
   LEFT_ANTI arm; why outer+IS NULL and LeftMark+NOT; why the refusal); drop stale "Depends on #1111".
   On SOT side (T-stack rebase): take the PR's guard and test verbatim; reconcile `emit_columns`/
   `filter_rel` taking `TranslatedRel`; delete the duplicate `emit_mapping` helper. Later hardening:
   `filter_is_null` on every equality key or refuse non-strict key exprs; refuse/handle `eq.opcode ==
   None` (null-safe EQ_FOR_NULL mistranslated as plain equal on anti paths); e2e Q16/Q21/Q22 test.
3. **#1233 common slots — merge as-is after #1235** (aocsa already approved). SOT-side tasks (T5):
   add `.with_carried(&input.carried_slots)` to the two `expr_context_with_slots` call sites; port the
   PR's tightened emit-mapping assertions, `nested_common_slots_are_appended_in_slot_id_order`, and the
   `reject_common_slots` guard narrowed to non-materialized ids (SOT silently ignores a non-PROJECT
   `common_slot_map`, and `slot_global_index`'s cross-tuple fallback can return a wrong same-id column).
4. **#1232 complete scan splits — update-then-merge** (aocsa approved; or close and fold into T6 if T6
   is <2 weeks out). Tasks: description with motivation + "partial splits are enabled later by the
   byte-range stack"; add `has_more == Some(true)` refusal and `empty == Some(true)` skip in
   `add_ranges` (SOT wording + `has_more_scan_ranges_are_refused` test); reword the
   `validate_complete_files` doc ("cannot be plumbed today", not permanent).
5. **#1242 materialized exchanges — close, superseded by T1.** Comment: GPU→file→GPU round trip,
   node-local paths cannot express a remote sender, rejects the merging exchanges Q1/Q3 need; its
   `translate_fragment_with_exchange_inputs`/`ExchangeInput` API survives in T1; its three guard tests
   are ported. Not stale — replaced on design grounds.

> **Update 2026-09-02 (merge-readiness pass):** all five dispositions above were executed; #1242 is closed and #1236/#1235/#1233/#1232
> are Ready with aocsa-pushed fixes (heads `e7b1b6ec`, `1c0ba171`, `510193e2`, `b8dd2ae0`). Correction to items 2-4: the three
> sibling PRs *did* conflict pairwise (`translate_hash_join` top, tests appended at EOF, both #1235 and #1233 moving
> `fn emit_mapping`); the pushed heads are restructured so all four merge onto `dev` in any order to the identical tree
> (116 translator tests green locally). Remaining blockers are approvals only (#1236, #1235). Follow-up issues: #1687-#1692.

## Old aocsa drafts to close (harvest descriptions first)

#1686 (→ S3/C9 motivation), #1674/#1672 (→ C2b/C6b), #1644 (29 commits → C2a/C2b, carries L1's hunk
verbatim), #1598 (→ F2; keep its doc comments + 3 tests, not its lockfile), #1636/#1637/#1638/#1639
(→ S1/S2/F4/T6; content byte-identical, only the base moved), #1296. Close each with a link to its
replacement when the replacement is opened as a draft.

## Verification

Per PR class (state the GPU box, driver, date in every body — upstream CI runs no Rust test and no
GPU C++ test outside `gpu-2xt4`'s full `sirius_unittest` run):
- C++ engine layers: `sirius_unittest "[staging_arena]"` (13), `"[parquet_byte_range]"` (6),
  `"[stream_bind_catalog]"` (CAT-10), `"[streaming_fragment]"` (FRAG-1..5, empty-stream case,
  FRAG-6..10), `"[build_probe]"`, `"[deterministic_agg]"`, `"[cached_serving]"`, `"[can_serve]"`;
  full suite green; `make clang-debug` locally.
- rust/ crates: `pixi run cargo fmt/clippy --all-targets -D warnings/test --no-run` (what CI does) and
  on a GPU box `cargo test -p sirius` (fan-out, packed_hop_matches_relay_hop, zero_row_export,
  byte_range_splits_read_every_row_exactly_once; F7/F8 add the hang-class and oracle tests).
- experimental/starrocks: `pixi run -e cn cargo fmt --check`, `cargo clippy --all-targets
  --no-default-features -D warnings`, `cargo test --workspace --no-default-features` (translator,
  local_exchange, result_store, dispatch, prpc_client, tunable tests all run in CI); on a GPU box
  `cargo test -p sirius-starrocks-cn --features sirius-engine,nixl-transport` incl. `--ignored
  nixl_cross_agent_write_between_arena_leases` and `engine_executes_local_files_and_sequential_exchange`.
- Docs: `pixi run pre-commit run --files ...` (rumdl MD051/MD057, codespell), `bash -n`/shellcheck.

Milestone end to end (multi-GPU box; GB200 is aarch64 and doubles as the merge-queue arm64 check):
1. `pixi run apply-starrocks-patches && pixi run fe-check && pixi run cn-build`; `make clang-debug`.
2. `bench/common/gen-tpch.sh` at SF1 and SF10 — once with lineitem as ONE file (exercises byte-range
   splits: `SIRIUS_CN_DUMP_FRAGMENTS` shows `FileOrFiles.start/length`, no `assert_all_consumed`
   throw) and once with many files (exercises C0).
3. `NUM_CNS=<N> GPU_MEM=... STAGING=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 cluster8.sh`; `nvidia-smi`
   shows exactly one CN per GPU; `SHOW COMPUTE NODES` all Alive (`awk -F'\t' '$9=="true"'`); FE
   blacklist settles empty. Smoke first with `pixi run cluster2` and DEMO.md's Q6 shape
   (revenue 61567694.9502, count(*) 6001215 at SF1).
4. `EXPLAIN` q01 shows `AGGREGATE (update serialize)` → `EXCHANGE HASH_PARTITIONED(l_returnflag,
   l_linestatus)` → `AGGREGATE (merge finalize)` + SORT → `MERGING-EXCHANGE` WITHOUT
   `SET new_planner_agg_stage = 1`; q06 shows partial → `EXCHANGE UNPARTITIONED` → merge.
5. `MIN_BACKENDS=<N> QUERY_TIMEOUT=120 bench.sh out.csv 3 q01 q06`, then `tools/oracle.py` +
   `tools/compare.py` → 0 mismatches (q01 4 rows, q06 1 row); repeat with `--cold-restart` (C7 proof:
   cold q01 completes, no 60 s errorCode=62); run Q1 with N > 4 CNs (L1 proof: no hang).
6. Per-CN logs: `nixl bandwidth canary ... gbps=` far above the 2.0 floor on first contact (≈0.4 means
   the pool-memory degradation); `transmitted batches via nixl` (bytes≈64 per CN for q06); NO "needs
   the nixl transport tier", NO "input stream row count unknown" for exchange inputs, NO
   `fail_stalled_query`. `cn-distribution.py` shows non-zero scan rows on every CN.
7. Done when q01 and q06 pass 4-6 on N≥4 CNs at SF10, cold and warm, 0 oracle mismatches, all N CNs
   scanned.

## Risks

- Open #1642 (Jedi18, completion-quiescence work ledger) rewrites `manager_loop`, `task_scheduler.*`
  and the same `future.get()` try/catch L1 edits — a design collision. Comment on #1642 before
  opening L1; if it lands first, re-express L1 in ledger terms. L1 is Q1-critical.
- Experimental CI is path-filtered to `experimental/**`; C3 must ship the patch-apply step, and no
  handler may land before C3. `default = ["sirius-engine","nixl-transport"]` makes a plain
  `cargo build` need libnixl — say so.
- Cross-stack and fork-PR gates are documented, not enforced by gh stack: T6 after F4 (silent N×
  duplication), C6b after F5, C6a after C3, F5 after X+K, F4 after S2, C9 after F6+S3, C10 after
  T2+T3. Put a `DO NOT MERGE before #NNN` line at the top of each gated body and check it by hand at
  enqueue time.
- The remote rename is sticky: after it, `origin` is upstream in the stacking clone. A fork PR pushed
  to `origin` by habit would create a stray branch on sirius-db. `git remote -v` before every
  self-contained push; keep stacking work in the dedicated clone only.
- The ffi stack is 8-9 layers deep and rust `lib.rs` is touched by 7 of them: settle F2's names
  (`result_to_arrow` vs `into_arrow`), `&self`/RefCell shape and doc text BEFORE opening F4+.
- Merge-queue-only CI rows (arm64, vcpkg 240 min, cuda-12) can fail a layer after PR CI is green and
  block the whole stack above it — build on the GB200 (aarch64) before submitting C++ layers.
- Hang-class tests (`under_watchdog` / `watchdog_guard`) exit the whole `cargo test` binary on a
  wedge; L1 must land before any of them (F7), thresholds env-configurable.
- Q6's revenue is FP64 after #1236 (~1e-14 relative drift across N CNs); `compare.py`'s relative
  tolerance handles it; A is intentionally off the milestone.
- Correct-but-slow cliffs invisible to tests: cudaMallocAsync memory over cuda_ipc (~220×, caught only
  by the canary floor), 8 MiB over-lease per in-flight export, a parked Fragment pinned for the
  process lifetime when a receiver never arrives (no GC in C2a; only `drop_parked` on failure).
- mbrobbel coordination: if #1236 does not merge within the window, T1+ must be rebased to include
  its hunks (Q1/Q6 do not translate without it) — offer to push the requested updates to his branch.

## Step 5/6 execution log (2026-09-02 late evening → 2026-09-03)

User instruction: "now start working on step 5 and 6, use workflows ultracode as needed, unslop the PR descriptions, use the pstack
unslop skill to create PR descriptions. All PRs at ~/.claude/plans/open-prs-review-order.md should be written using unslop as well."

Done so far:
- **Unslop pass on the seven open PRs** (#1693, #1694, #1696, #1697, #1699, #1700, #1702): a 21-agent workflow (writer → read-only
  checker → fix, per PR) rewrote each body from the live GitHub text and the PR diff, verified no fact/link/number/Verified-line was
  lost and no tell remained, then I pushed them with `gh pr edit --body-file`. Finding: the live bodies of the stacked layer-2 PRs
  #1700 and #1702 were the 53-word stubs `gh stack submit --auto` writes (the full bodies never reached GitHub), so their rewrites
  were built from the diffs plus the durable copies' Verified lines and references. Durable copies: `~/.claude/plans/pr-bodies/`
  (previous versions under `before-unslop/`). Rule recorded in memory: every PR body follows the unslop rules (skill text at
  `~/.cursor/plugins/cache/cursor-public/pstack/*/skills/unslop/SKILL.md`).
- **Environment for the Rust/CN slices**: stacking clone `dev` reset to origin/dev 01613070 (#1232/#1233/#1235/#1236 all merged);
  StarRocks + brpc submodules initialised in the stacking clone (the shared clone's module store is corrupt: "inflate: data stream
  error"; `/home/prestouser/aocsa/sirius-mbrobbel/.git/modules/starrocks/*` works as `--reference`); CN pixi env installed at
  `experimental/starrocks/.pixi/envs/cn` (rustc 1.96.0). CI trio runs from any directory with
  `env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path <clone>/experimental/starrocks/pixi.toml -e cn cargo … --manifest-path <worktree>/experimental/starrocks/Cargo.toml`.
  Baseline on dev: 116 translator integration tests, all green. Per-slice worktrees under `/home/prestouser/aocsa/sirius-stacks-wt/`
  each get the two submodules via `git submodule update --init --reference <clone>/.git/modules/starrocks/<sub>`.
- **Decisions taken under "start working"** (flag to the user): C3 keeps SOT's design (submodule patch + `ignore = dirty` + build.rs
  guard + a CI step that runs `scripts/apply-starrocks-patches.sh`); the body asks reviewers whether they would rather vendor a
  Sirius-owned proto. A slice the map missed was added as fork PR **T0** `translator-clone-expr-narrowed-builtins`
  (`translate_clone` for CLONE_EXPR, which q01's projection emits, plus the year/month/day/length/char_length cast back to the
  FE-declared type); it is independent of the translator stack.
- **Running**: workflow `step5-t1-carve-review-verify` (C0 `cn-files-schema-multi-range`, C4 `cn-transport-tunables`, C3
  `cn-exchange-proto-patch`, T1 `stacked/translator-exchange-stream-read`, each carve → review → fix → CI-trio verify in its worktree)
  and workflow `t0-clone-expr-carve-review-verify`. Next: T2 → T3 → T4 chain (script drafted at the session scratchpad
  `translator-chain-t2-t4.js`; launched with `args.t1 = {branch, sha, notes}` once T1 is green), then publish: fork PRs from `aocsa`
  (C0, C4, C3, T0) and the translator stack via `gh stack init -b dev <T1> <T2> <T3> <T4>` + `gh stack submit --auto` + `gh pr edit`.
- **T0 published**: Draft #1704 `aocsa:translator-clone-expr-narrowed-builtins` @ 27528385 → dev (fork PR). Verified in the worktree:
  CI trio green, 176 tests (translator 121 incl. the 5 new). Reviewer nit kept as is: `matching_return_type_function_stays_cast_free`
  uses `like`, so it pins "non-narrowed builtin is not cast". Rebase note for T2: T0's `type_mapper::i64_type` doc reads "the width
  the engine returns for builtins the frontend declares narrower", not SOT's aggregate wording; T0 also adds `cast_parts` and
  `cast_to`, so T2 (carved on T1 over dev) will see one-line conflicts on those when the stack syncs after #1704 merges.
- **Step 5 published (all Drafts on sirius-db, fork PRs from `aocsa`, CI trio green in their worktrees):** #1705 C0
  `cn-files-schema-multi-range` @ 3056cda5 (179 tests; file_schema 14, compute_node_service 13); #1706 C4 `cn-transport-tunables`
  @ e2d6058d (181 tests; tunable 10/10; fields and `Tunables::get` made `pub` so clippy stays green without consumers; later C5/C6a/C7
  carves must keep `pub`); #1707 C3 `cn-exchange-proto-patch` @ a04bf5bc (171 tests; negative check: unpatched submodule fails
  `cargo check` in build.rs with the one-line remedy; script idempotent; CI step runs the script; `apply-starrocks-patches` also a
  dependency of `cn-test-no-engine`). Note for the C2b carve: it must rebase onto #1705 so the ServiceCore move relocates the
  multi-range `file_schema_from_attachment` instead of reintroducing dev's single-range body. The repo's `.pre-commit-config.yaml`
  excludes `^experimental/.*`, so cargo fmt/clippy/test are the only gates on these files (rumdl run directly on TUNABLES.md).
- **T1 carved and verified, not yet published** (a stack needs >1 layer): `stacked/translator-exchange-stream-read` @ 4389e3f9
  (20da8947 plus a one-word module-doc fix), one commit over origin/dev; 127 translator integration tests (+11) and 2 new
  descriptor_table unit tests; CN crate touched only in 3 test literals. Also ports #1242's three guard tests (incl. exchange offset
  -> FetchRel, which made `apply_fetch` read `exchange_node.offset`). Chain T2 -> T3 -> T4 launched on it
  (workflow `translator-chain-t2-t4`, args.t1 = 4389e3f9). Body: `~/.claude/plans/pr-bodies/stacked-translator-exchange-stream-read.md`.
- **Step 6 published (2026-09-03 ~01:00 UTC): translator stack = GitHub stack ID 1712** on sirius-db, all Drafts, merged bottom-up:
  #1708 T1 `stacked/translator-exchange-stream-read` @ 4389e3f9 (127 translator tests) -> #1709 T2 `stacked/translator-two-phase-agg`
  @ ce41439d (135 + 8 partial_state unit tests; avg refused with a layer-pointing error) -> #1710 T3 `stacked/translator-wire-order`
  @ 50f6636a (142; q03-shaped tests) -> #1711 T4 `stacked/translator-avg-expansion` @ 5f9349d5 (148; four fence tests are new, not SOT).
  Bodies in `~/.claude/plans/pr-bodies/stacked-translator-*.md`. Rebase note: after #1704 merges, T2 conflicts on `i64_type`,
  `cast_to` (identical lines) and the duplicate `cast_parts` test helper (delete once). Shapes the T5 carve must know are in the
  chain workflow result (`tasks/ws9tl6zhs.output`): AggregateCall gained `raw_arguments` in T4; `translate_plan` keeps the 5-arg
  signature (no `root_output_exprs`; T5 adds it with `consumed_above`); PartialExpansion/TranslatedFragment.partial_expansion exist;
  no `carried_slots` anywhere (T5 introduces it). gh-stack lesson: `git worktree remove` refuses worktrees that contain submodules;
  `rm -rf` the clean worktree then `git worktree prune`. `gh stack init -b dev <l1..l4>` from dev adopted the four branches once the
  worktrees were gone; `submit --auto` and `view` from the bottom branch.
- **Wave 2 started 2026-09-03 ~02:05 UTC** (user: "create the PRs T5, C1 + C2p, S3"): workflow `wave2-rust-t5-c1-c2p` (T5 on
  `stacked/translator-avg-expansion`; C1 on dev; C2p on C1; worktrees t5/c1/c2p) and workflow `wave2-s3-pinned-file-subset` (S3 on
  `stacked/scan-byte-range-ingestible`, worktree s3 with duckdb/substrait/cucascade submodules; full C++ build in the worktree via
  `pixi run --manifest-path <clone>/pixi.toml make release`; GPU tests wait for the nightly window to end ~03:50 UTC). Decisions in the
  sheets: C1 carries no docs/TUNABLES.md (owned by #1706), no cn-env.sh, and errors when an exported CUDA_VISIBLE_DEVICES disagrees with
  `--gpu-device`; C2p puts targeted `#[allow(dead_code)]` on methods only C2b calls; S3 keeps S2's has_byte_ranges guard above the 3-way
  match and must honour #1547's preference order; T5 keeps dev's #1233 tests. F4 and L2 were NOT started: F4 needs #1700 merged (uses
  the scan stack's `resolved_file_ranges`), L2 needs #1699 merged (`watchdog_guard`). Publication plan: T5 via `gh stack add` on stack
  1712 (from the T4 branch), `gh stack init -b dev C1 C2p` for the cn stack, S3 via `gh stack add` on stack 1701 (from the S2 branch);
  remove the worktrees first (`rm -rf` + `git worktree prune`; `git worktree remove` refuses worktrees with submodules).
- **Wave 2 (part) published 2026-09-03 ~03:00 UTC:** T5 #1713 `stacked/translator-carried-common-slots` @ 268b7592 added to stack 1712
  (5/5; 156 translator tests, 19 unit; dev's #1233 tests untouched, `reject_common_slots` unchanged); new **cn stack 1716** = #1714 C1
  `stacked/cn-cluster-bringup` @ 9738b256 (34 tests; engine-feature `cargo check` compiled against the clone's build tree; CUDA_VISIBLE_DEVICES
  disagreement is now an error; SHUTDOWN_GRACE deferred) -> #1715 C2p `stacked/cn-result-store-failure-propagation` @ a1ac4c58 (9 tests;
  deliberate departure: `take_next` keeps its call shape via a `FetchProgress` wrapper over the new `poll`; C2b must rename poll -> take_next
  and drop the wrapper; six targeted dead_code allows to remove in C2b). S3 still verifying (C++ build in worktree s3 done, GPU tests running).
- **Wave 2 complete 2026-09-03 ~03:15 UTC:** S3 #1717 `stacked/scan-pinned-file-subset` @ 6c0b83a1 added to scan stack 1701 (3/3). Built
  in its worktree (`make release`, 1414 steps) and GPU-tested on an idle GPU (the nightly window did not take the GPUs this night):
  [can_serve] 13, [cached_serving] 25, [scan_manager] 81, [scan] 284, [parquet_byte_range] 6, all pin_table suites, and the new
  `test_pin_table_file_subset.cpp` (registered in CMakeLists). Review fixes: header docs on can_serve_with_columns/matches_parquet_files;
  matches_parquet_files has no production caller now. Known behaviour change: every parquet pin now yields at least one chunk per file.
  Open question for F6/C9: #1547's preference order lives in find_pinned_entry_for_duckdb_table only; the parquet gate stays file-set-only.
  All nine wave-2 worktrees removed. 19 Drafts open. Next: wave 3 needs merges (F4 after #1700, L2 after #1699, F5 after #1693/#1694/F4,
  C2a after #1702 + #1715, C2b after #1708 + C2a).
- **PR body race (found 2026-09-03 03:10 UTC):** `.github/workflows/validate.yml` job `clean-pr-body` strips HTML comments from the body
  on `opened`/`edited` and writes the stripped text back using the EVENT payload. `gh stack submit --auto` opens the PR with a stub body;
  a `gh pr edit` within ~10 s races that job and gets overwritten by the stripped stub (this hit #1700/#1702 on 2026-09-02 and
  #1708/#1711/#1717 on 2026-09-03). Rule: after `gh stack submit`, wait ~20 s (or until the `pr-body` check finishes) before `gh pr edit`,
  then re-fetch the body and compare. The saved bodies in `~/.claude/plans/pr-bodies/` are the reference; the live body legitimately lacks
  only the `<!-- NOTE ... -->` line. Audit script pattern: diff live vs saved ignoring that line and blank lines.
- **2026-09-03 ~03:40 UTC, bodies rewritten for humans** (user: "remove messages like Verified 2026-09-02 on the GB200 ... Make the PRs
  description for humans not AI Models"): every "Verified ..." log paragraph in the 19 open PRs became a 2-3 sentence "How I tested it"
  note (box, commands in words, one headline number, the reviewer caveat); test-name lists over four names trimmed to a count plus three;
  #1707's duplicate negative-check paragraph merged; #1699's quoted Catch2 output replaced by "FRAG-11 still passes". The full run logs
  stay in this file's execution logs. Saved bodies updated in `~/.claude/plans/pr-bodies/` (previous versions in `before-humanize/`).
- **Carve check complete 2026-09-03 ~12:35 UTC** (task: "build demo/q1q6-integration from origin/dev, merge the open PRs, carve the rest, prove
  q01/q06 end to end"): worktrees `sirius-stacks-wt/{sot,demo}`. Step 0 (source branch d24f02c4 on this box, 4 CNs): PASS on f64 data, q01
  FAILS the oracle on the decimal datasets (`1 - 0.07` → FP64 → DECIMAL(16,2) truncation, on dev via #1236). Step 1: 12 PRs merged in stack
  order over 98fe1a84 (3 mechanical conflict sets). Step 2: 10 carved commits, one workflow chain each (carve → adversarial review → fix),
  all approve/minor: F4 92e91adf, F5 b7452f67, T6 d88265aa, C2a 85da8f6f, C2b 37b48a46, C5 86b3f9a6, C6a 36072ef8, C6b 9e547c89, C7 49d23e8a,
  bench 1e163623 (HEAD). Step 3: 4 CNs one per GPU, SF1 and SF10, 1-file and multi-file lineitem, warm 3 + cold-restart, 16/16 oracle
  MATCH, two-phase shapes, canary 78–432 GB/s, nixl transmits, zero negative markers; DEMO.md 2-CN smoke reproduces the Q6 shape and its
  fragment dumps show the single file split into two byte-range halves; CN CI trio + rust bindings checks green on HEAD. Step 4: pushed
  `demo/q1q6-integration` and `demo/q1q6-base` (= 98fe1a84) to `aocsa`, Draft PR https://github.com/aocsa/sirius/pull/3 (do not merge).
  Cherry-pick plan per piece (future branch, base, gate) in `~/.claude/plans/demo-q1q6-report.md`; drift per piece and open maintainer
  decisions (F4 `collect_from_rel` strictness, SHUTDOWN_GRACE homeless, compare.py exit code, cancelPlanFragment stub in C2b) in the report
  and `scratchpad/carve-step2-final.md`. No Ready flips, no merges, #1644 untouched.
