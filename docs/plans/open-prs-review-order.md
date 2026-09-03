# Multi-CN carve-up: status brief, open PRs by step, review order

Updated 2026-09-03 (03:20 UTC, carve-check paragraph added 12:35 UTC) from live GitHub data on `sirius-db/sirius`. Every number below was checked against the API. `pull/N` links
are pull requests and `issues/N` links are issues. `1701`, `1703`, `1712` and `1716` are GitHub stack IDs, the `N/M` badge next to a stacked PR's
title. They have no URL of their own. Open any PR in the stack and click the badge.

All fifteen PRs are Drafts. The eleven from steps 2 to 5 plus #1704 have green CI. The translator stack (#1708 to #1711) was opened
on 2026-09-03 and was still running CI when I wrote this; check `gh pr checks <N>` before reviewing. Flipping a PR to "Ready for
review" is what pings CODEOWNERS, and I have not flipped any.


## Status brief

The goal is TPC-H Q1 and Q6 across N GPUs, one StarRocks compute node per GPU, carved out of the 73k-line `feat/pin-table-cn`
branch as PRs a colleague can review in one sitting. Nineteen PRs are open on `sirius-db/sirius`, all Drafts, CI green on every PR whose checks have finished, none
merged, none reviewed yet. Wave 2 is complete; everything still uncarved needs an earlier PR merged first. Everything the Q1/Q6 milestone needs on the translator side is open; the engine and CN sides are open up
to the point where further layers need earlier ones merged. Reviews are now the critical path, not carving.

| Step | Scope | State |
|---|---|---|
| 1 | mbrobbel's translator fixes | merged (#1232, #1233, #1235, #1236), #1242 closed |
| 2 | independent engine pieces (S1, F1, K, X, L1) | 5 Drafts open |
| 3 | ffi stack | F1 #1697, F2 #1702 open. F4 (byte ranges on the Substrait plan) waits for #1700 to merge. |
| 4 | scan stack | stack 1701 = S1 #1696 -> S2 #1700 -> S3 #1717 open. |
| 5 | CN fork PRs (C0, C4, C3) | 3 Drafts open |
| 6 | translator | T0 #1704 fork PR; stack 1712 = #1708 to #1711 plus #1713 open (T1 to T5). T6 (byte-range splits) held until F4 merges. |
| 7 | staging FFI (F5: push_packed, cardinality FFI) | waits for #1693, #1694 and F4 to merge |
| 8 | CN stack | stack 1716 = #1714 C1 (cluster bring-up) -> #1715 C2p (result-store failures) open. C2a/C2b wait for #1702 and #1708. Transport layers wait for C3, C4, F5. |
| liveness | L1 #1699 open; L2 (never-buildable join slots) waits for #1699 | |
| standalone | D1 docs (any time), A deterministic sums (needs a benchmark first), AR1 + F9 Arrow in/out for Doris (waiting on the #1590 reply) | not started |

**Carve check, 2026-09-03 12:35 UTC.** The carve reproduces the working system. `demo/q1q6-integration` = `origin/dev` 98fe1a84 + the twelve
open Draft PRs merged in stack order + one commit per still-uncarved piece (F4, F5, T6, C2a, C2b, C5, C6a, C6b, C7, and the q01/q06 subset of
D2a/D2b/D2c), each carved from `feat/pin-table-cn` d24f02c4 with the carve-sheet corrections and reviewed (approve, minor findings only). On the
4x GB200 box with one CN per GPU, q01 and q06 match the DuckDB oracle at SF1 and SF10 (one-file and multi-file lineitem), warm and after cold
restarts, with the two-phase plan shapes, nixl transfers and no failure markers; the CN CI trio and the rust bindings checks pass on its HEAD.
Do-not-merge Draft PR on the fork: https://github.com/aocsa/sirius/pull/3. Report and cherry-pick plan (which commit becomes which layer, on
which base, behind which merge gate): `~/.claude/plans/demo-q1q6-report.md`. Caveat: the decimal-typed datasets fail q01 on the CN path by
9.6e-4 (`1 - 0.07` lowered to FP64, cast back to DECIMAL(16,2) with truncation); that is on `dev` (#1236), not in any open PR, and needs an owner.

Decisions that are yours, not mine: flipping Drafts to Ready (nothing pings CODEOWNERS until then); the patch-vs-vendor question #1707
asks; the two calls in the Doris reply draft (`cudf::from_arrow`, `push_arrow` during `run()`).

## Recommended review order

Small and independent first. Stack layers after their base. The CN and translator PRs route to a different CODEOWNERS team than
the engine PRs, so those two groups can be reviewed in parallel.

| # | PR | Step | Team | Why here |
|---|---|---|---|---|
| 1 | [#1704](https://github.com/sirius-db/sirius/pull/1704) CLONE_EXPR and FE-narrowed builtins (T0) | 6 | sirius-integrations | 300 lines, five tests, no dependency. TPC-H q01 does not translate without it. |
| 2 | [#1696](https://github.com/sirius-db/sirius/pull/1696) byte-range ownership rule (S1) | 2 | sirius-io | Pure function plus tests, 355 lines. Bottom of the scan stack, gates #1700. |
| 3 | [#1697](https://github.com/sirius-db/sirius/pull/1697) FFI transaction scope (F1) | 2 | sirius-core | 126-line bug fix that unblocks `Fragment::build()` on DuckDB 1.5.5. Bottom of the ffi stack, gates #1702. |
| 4 | [#1694](https://github.com/sirius-db/sirius/pull/1694) stream input cardinality (K) | 2 | sirius-core | 268 lines, additive, engine only. A later ffi layer depends on it. |
| 5 | [#1705](https://github.com/sirius-db/sirius/pull/1705) FILES() schema across ranges (C0) | 5 | sirius-integrations | Two files, eight tests. Removes a planning-time refusal on multi-file tables. |
| 6 | [#1706](https://github.com/sirius-db/sirius/pull/1706) transport tunables registry (C4) | 5 | sirius-integrations | Self-contained module with ten tests. Gates the PRPC client PR. |
| 7 | [#1707](https://github.com/sirius-db/sirius/pull/1707) StarRocks proto patch (C3) | 5 | sirius-integrations | 174 lines but one design question to settle before the transport stacks on it: patch the submodule or vendor a proto. |
| 8 | [#1693](https://github.com/sirius-db/sirius/pull/1693) exchange staging arena (X) | 2 | sirius-core | 960 lines, one allocator with 13 tests. The review is the design argument: an allocator outside the RMM pool. |
| 9 | [#1699](https://github.com/sirius-db/sirius/pull/1699) opt-in stall watchdog (L1) | 2 | sirius-core, with Jedi18 | Read with the open [#1642](https://github.com/sirius-db/sirius/pull/1642). Same `execute()` try/catch region. |
| 10 | [#1700](https://github.com/sirius-db/sirius/pull/1700) ingestible honors a byte range (S2) | 4 | sirius-io | Layer 2 of the scan stack. Review after #1696. |
| 11 | [#1702](https://github.com/sirius-db/sirius/pull/1702) Rust Fragment bindings (F2) | 3 | sirius-integrations | Layer 2 of the ffi stack, 891 lines. Every CN PR imports it. Review after #1697. |
| 12 | [#1708](https://github.com/sirius-db/sirius/pull/1708) EXCHANGE_NODE as a stream read (T1) | 6 | sirius-integrations | Bottom of the translator stack. Supersedes the closed #1242. Review after #1704 for context, though it does not depend on it. |
| 13 | [#1709](https://github.com/sirius-db/sirius/pull/1709) two-phase aggregation (T2) | 6 | sirius-integrations | Layer 2. The atomic guard replacement and the wire-type model are the review. Q6 translates after it. |
| 14 | [#1710](https://github.com/sirius-db/sirius/pull/1710) materialized-slot wire order (T3) | 6 | sirius-integrations | Layer 3. A correctness fix with q03-shaped tests. Q1's GROUP BY and ORDER BY translate after it. |
| 15 | [#1711](https://github.com/sirius-db/sirius/pull/1711) two-phase avg expansion (T4) | 6 | sirius-integrations | Layer 4. The four fences around the wider row are the review. Q1 translates after it. |
| 16 | [#1714](https://github.com/sirius-db/sirius/pull/1714) CN cluster bring-up (C1) | 8 | sirius-integrations | Bottom of the CN stack. Flags, derived YAML, readiness gate. 34 tests, engine-feature build compiles. |
| 17 | [#1715](https://github.com/sirius-db/sirius/pull/1715) result-store failure propagation (C2p) | 8 | sirius-integrations | Layer 2 of the CN stack, one file, additive. Review after #1714. |
| 18 | [#1713](https://github.com/sirius-db/sirius/pull/1713) carried common slots (T5) | 6 | sirius-integrations | Layer 5 of the translator stack, the q14 shape. Not on the Q1/Q6 path. Review after #1711. |
| 19 | [#1717](https://github.com/sirius-db/sirius/pull/1717) pinned tables serve file-subset scans (S3) | 4 | sirius-io | Layer 3 of the scan stack, 921 lines, C++ with GPU tests. Review after #1700. The provenance rule and the subset match are the review. |

Merge order follows the same sequence. Stacked layers merge bottom-up only: "Enqueue pull request" on the bottom PR, wait,
`gh stack sync --prune`, `gh stack view`, repeat. Never "Enqueue stack" or `gh stack merge`.

## Step 1, mbrobbel's translator fixes (done)

All merged into `dev` (head `01613070`). Nothing to review.

| PR | State |
|---|---|
| [#1235](https://github.com/sirius-db/sirius/pull/1235) fix(starrocks): lower anti joins | merged |
| [#1236](https://github.com/sirius-db/sirius/pull/1236) fix(starrocks): lower decimal operations to fp64 | merged |
| [#1233](https://github.com/sirius-db/sirius/pull/1233) fix(starrocks): materialize common project slots | merged |
| [#1232](https://github.com/sirius-db/sirius/pull/1232) fix(starrocks): combine complete scan splits | merged |
| [#1242](https://github.com/sirius-db/sirius/pull/1242) feat(starrocks): translate materialized exchanges | closed, superseded by the translator stack (step 6) |

## Step 2, independent engine pieces

### [#1696](https://github.com/sirius-db/sirius/pull/1696) feat(scan): a deterministic byte-range -> row-group ownership rule
`sirius-db:stacked/scan-byte-range-rule` -> `dev`. +355/-0, 4 files. Bottom of the scan stack (badge 1/2, stack ID 1701).

Adds `row_group_start_offset` and `row_groups_in_byte_range` in `src/op/scan/parquet_byte_range.{hpp,cpp}`. This is the rule that
decides which row groups a byte range `[start, start+length)` owns. It matches the StarRocks BE reader convention, so N compute
nodes splitting one parquet file read every row group exactly once. Nothing calls it yet, on purpose. Four Catch2 cases tagged
`[parquet_byte_range]`, one of them a real-footer cross-check against cudf on `lineitem.parquet`. What to look at: does the
start-offset definition match `be/src/formats/parquet/utils.cpp`, the zero-offset-means-absent rule, and the exact-tiling property.
Refs issue [#1635](https://github.com/sirius-db/sirius/issues/1635).

### [#1697](https://github.com/sirius-db/sirius/pull/1697) fix(ffi): own a transaction while lowering a fragment's Substrait plan
`sirius-db:stacked/ffi-transaction-scope` -> `dev`. +126/-29, 3 files. Bottom of the ffi stack (badge 1/2, stack ID 1703).

On `dev`, every well-formed `Fragment::build()` throws `TransactionContext::ActiveTransaction called without active transaction`.
`build()` commits its view-creation transaction before lowering the Substrait plan, and DuckDB 1.5.5
([#1328](https://github.com/sirius-db/sirius/pull/1328)) no longer tolerates that. `lower_substrait()` now opens a transaction on
`ClientContext::transaction` when none is active, commits on success and rolls back on failure. `Context::execute_substrait`
already had one, so that path is unchanged. The FFI `Context` also honors `SIRIUS_LOG_BACKEND`, `SIRIUS_LOG_DIR` and
`SIRIUS_LOG_LEVEL`. One new Catch2 case hand-builds a Substrait `ReadRel` and requires `build()` not to throw. What to look at:
why `client.transaction` and not `Connection::BeginTransaction()` (the lifecycle mutex), and the rollback path.

### [#1694](https://github.com/sirius-db/sirius/pull/1694) feat(exec): declare stream input cardinality to DuckDB's optimizer
`aocsa:exec-stream-cardinality` -> `dev`. +268/-4, 8 files. Fork PR.

A stream source binds with no rows behind it, so DuckDB estimates cardinality 1 for every exchange input and picks hash-join build
sides blind. The two-CN q07 regression went from 14.8 s to 164 s that way. Adds an optional `estimated_rows` on
`stream_input_spec` and `stream_input_binding`, appended last so existing brace-inits compile, a non-throwing
`stream_bind_catalog::estimated_rows(id)`, and a `TableFunction::cardinality` callback for `sirius_stream_source` that returns
`NodeStatistics` only when a count was declared. Tests CAT-10 and FRAG-10; the latter asserts the small stream lands on DuckDB's
build side in both directions. C++ engine only. The FFI, Rust and CN plumbing that declares the count comes later. What to look
at: the null-returning callback contract, the field position, and that undeclared streams behave bit for bit as today.

### [#1693](https://github.com/sirius-db/sirius/pull/1693) feat(exec): exchange staging arena for cross-node batch transfer
`aocsa:exec-exchange-staging-arena` -> `dev`. +960/-0, 5 files. Fork PR.

A second device allocator outside the RMM pool. UCX's `cuda_ipc` cannot export `cudaMallocAsync` memory and silently degrades
about 220x to staged host copies, so a cross-process exchange needs one plain `cudaMalloc` region that a transport registers once.
Address-ordered coalescing free list, 256-byte aligned leases, loud errors on exhaustion that name the env var. The opt-in
`fabric` path (VMM, cross-host MNNVL) is kept but untested in CI. Env knobs `SIRIUS_EXCHANGE_STAGING_BYTES` and
`SIRIUS_EXCHANGE_STAGING_ARENA`, documented in `configuration.md`. 13 GPU Catch2 cases tagged `[staging_arena]`. No in-tree
consumer yet; the ffi layer F5 follows. What to look at: the design argument above, the free-list invariants, and whether env vars
rather than YAML keys are acceptable.

### [#1699](https://github.com/sirius-db/sirius/pull/1699) feat(pipeline): opt-in stall watchdog (SIRIUS_QUERY_WATCHDOG_SECS)
`aocsa:pipeline-stall-watchdog` -> `dev`. +222/-1, 6 files. Fork PR.

With `SIRIUS_QUERY_WATCHDOG_SECS` unset or `0` the engine keeps today's `future.get()`, byte for byte. When set, it polls the query
future and fails the query loudly after that many seconds of zero scheduling progress, so hang-class regressions fail in seconds
instead of hanging CI. Strict parsing, digits only, `0..86400`. Adds `task_scheduler::fail_stalled_query`, a `watchdog_guard` test
helper with a `SIRIUS_TEST_WATCHDOG_SECS` override, and FRAG-11, a fragment whose only input stream closes empty before `run()`.
The body states an honest finding: FRAG-11 passes on current `dev` without the source branch's completion hunk, because merged
[#1624](https://github.com/sirius-db/sirius/pull/1624) already covers that shape, so I dropped the hunk. Design overlap with the
open [#1642](https://github.com/sirius-db/sirius/pull/1642), Jedi18's completion-quiescence work ledger, in the same `execute()`
region. Opened as Draft to settle that first. What to look at: the opt-in semantics, the #1642 interaction, and the untested
non-front-source shape flagged as an open question.

## Step 3, ffi stack layer 2

### [#1702](https://github.com/sirius-db/sirius/pull/1702) feat(ffi): Rust bindings for sirius::ffi::Fragment and Context
`sirius-db:stacked/ffi-fragment-rust` -> `stacked/ffi-transaction-scope`. +891/-39, 3 files. Layer 2 of the ffi stack (badge 2/2, stack ID 1703).

`dev`'s `sirius-sys` binds only `Context` and `execute_substrait`. This adds the cxx bridge for `Fragment` and `stream_view_name`
and the safe `Fragment<'ctx>` wrapper: `declare_input_column`, `declare_input_sender`, `declare_output`,
`declare_output_broadcast`, `declare_output_hash_key`, `build`, `relay_from`, `run`, `result_to_arrow`, `output_batch_count`,
`output_types`, `close_input`. `SiriusContext` moves its `UniquePtr` behind a `RefCell` so `fragment(&self)` can hand out several
live fragments of one query. GPU tests: broadcast fan-out, hash-key routing determinism for INT64 and DECIMAL, a relay schema guard,
plus the three tests from the closed [#1598](https://github.com/sirius-db/sirius/pull/1598), which this supersedes. Left out on
purpose: staging and packed batches, cardinality, pin_table, byte-range helpers. What to look at: `&self` plus `RefCell` (a
re-entrant borrow panics instead of failing to compile; no in-tree caller does that) and the `result_to_arrow` name, which matches
the C++. CI only compiles the Rust tests; the GPU run is in the body.

## Step 4, scan stack layer 2

### [#1700](https://github.com/sirius-db/sirius/pull/1700) feat(scan): the parquet ingestible honors a per-file byte range
`sirius-db:stacked/scan-byte-range-ingestible` -> `stacked/scan-byte-range-rule`. +242/-12, 6 files. Layer 2 of the scan stack (badge 2/2, stack ID 1701).

Threads the S1 rule into the real scan. `parquet_ingestible_table_info::resolved_file_ranges` runs parallel to the file list, with
`(0,0)` meaning whole file; a pairing guard in the constructor; and `build_file_scan_info` keeps only the owned row groups right
after `all_row_groups()` and before stats pruning. One cache fact follows: a ranged scan neither serves nor is served by a pinned
entry (`cache_entry_info::has_byte_ranges`), because matching on the file set alone would silently return extra or missing rows.
Two `[parquet_byte_range][scan]` cases plus a new `can_serve` guard test. Still inert in production until the ffi layer F4 carries
ranges from the Substrait plan. What to look at: filter placement relative to stats pruning, and the cache guard in both directions.

### [#1717](https://github.com/sirius-db/sirius/pull/1717) feat(scan): serve pinned parquet tables to file-subset scans
`sirius-db:stacked/scan-pinned-file-subset` -> `stacked/scan-byte-range-ingestible`. +921/-65, 12 files. Layer 3 of the **scan stack** (badge 3/3, stack ID 1701).

A pinned parquet table only served a scan whose file set equaled the pinned set. StarRocks hands each CN a per-query subset of a table's
files, so a whole-glob pin per CN never matched and served nothing while holding the memory. Pin time now records each chunk's source
files (a coalescer mode keeps one file per chunk). Serve time classifies a scan as exact, strict subset or miss, and a subset is served
from exactly the chunks whose provenance it covers, restricted before zone-map pruning. Subset matching demands duplicate-free canonical
sets on both sides, because a duplicated pinned path would double a file's rows. Entries without provenance keep exact-only matching, and
byte-range scans still never serve (the #1700 guard). Five new Catch2 cases including an end-to-end pin-3-query-2 test; every existing
pin_table suite still passes on a GPU. The source branch measured q06 at SF100 on 2 CNs from 0.63 s to 0.09 s; not re-measured here.
What to look at: the provenance-is-required rule on the subset branch (necessary but not sufficient), and the sentinel choice.

## Step 5, compute-node fork PRs

### [#1705](https://github.com/sirius-db/sirius/pull/1705) fix(cn): infer a FILES() schema across every assigned range
`aocsa:cn-files-schema-multi-range` -> `dev`. +269/-26, 2 files. Fork PR.

On `dev` the CN reads the FILES() schema from the first range and refuses a request with several, so a table written as one
parquet file per generator chunk fails at planning. `parquet_files_schema` infers from the first file and requires every other
file to agree on column count, names (ASCII case-insensitive) and types, naming the offending file on a mismatch. I chose whole-set
agreement over StarRocks' sampling because this scan reads every file with the inferred schema. Eight tests, all in the CPU-only CI
job. What to look at: the fail-closed contract and its error messages. The later dispatch rewrite (C2b) must rebase onto this.

### [#1706](https://github.com/sirius-db/sirius/pull/1706) feat(cn): a validated registry for the CN's transport tunables
`aocsa:cn-transport-tunables` -> `dev`. +496/-1, 4 files. Fork PR.

Six env knobs for the coming transport (RPC and nixl transfer timeouts, canary bytes and floor, warmup budget and peer count) in
one `tunable.rs` registry, resolved once at startup. Reject, never clamp or ignore. A bad value fails the process before it binds
a port. The resolved set is logged. Motivation is a real defect: `.parse().ok()` turned `SIRIUS_CN_NIXL_WARMUP_TIMEOUT_SECS=6O`
(letter O) into the silent default. Nothing on `dev` reads the values yet; the PRPC client and nixl PRs will, and this PR exists so
they stay small. Ten tests, new `docs/TUNABLES.md`. What to look at: process-global `OnceLock` config, and public fields versus
accessors.

### [#1707](https://github.com/sirius-db/sirius/pull/1707) build(cn): carry the Sirius exchange RPCs as a checked-in StarRocks proto patch
`aocsa:cn-exchange-proto-patch` -> `dev`. +174/-4, 6 files. Fork PR.

Three Sirius-only RPCs (`exchange_nixl_md`, `request_staging_lease`, `transmit_packed`) added to StarRocks' `PInternalService` by
`patches/nixl-exchange-proto.patch`, an idempotent apply script, `ignore = dirty` on the submodule, a build.rs guard that fails
with the exact `git apply` remedy when the tree is unpatched, and the CI step that applies the patch after submodule init. Behaviour
is unchanged: the generated trait methods default to "not implemented". The body asks the one question that matters up front,
whether reviewers accept a dirty-by-design submodule or would rather vendor a Sirius-owned proto. Please answer it before the
transport PRs stack on this. The CI step is mandatory; without it every later CN PR is red.

## Step 6, translator

### [#1704](https://github.com/sirius-db/sirius/pull/1704) fix(starrocks): unwrap CLONE_EXPR and cast FE-narrowed builtins to their declared type
`aocsa:translator-clone-expr-narrowed-builtins` -> `dev`. +298/-2, 4 files. Fork PR, independent of the translator stack.

Two small fixes the plan's map had missed. TPC-H q01 reads `l_extendedprice` twice, so the FE wraps the second use in
`CLONE_EXPR`, which `dev` refuses; the node has no value semantics and now returns its child. And `year`, `month`, `day`,
`length`, `char_length` return BIGINT through DuckDB while the FE declares narrower slots, so the next hop's schema guard refused
the column; they are cast back to the declared type. Five tests. Once merged it will conflict on one doc line and two helpers with
the translator stack's layer 2, which adds the same `i64_type` and `cast_to`; the text is identical so the rebase is mechanical.

### Translator stack, #1708 -> #1709 -> #1710 -> #1711 -> #1713 (stack ID 1712)

Five layers, one commit each, pushed to `sirius-db` as `stacked/translator-*` branches and merged bottom-up. Each layer's body
carries its own verified line; the trio (fmt, clippy, `cargo test --workspace --no-default-features`) is green on every layer in
the stacking clone.

#### [#1708](https://github.com/sirius-db/sirius/pull/1708) feat(starrocks): translate EXCHANGE_NODE as a stream read of the engine's exchange view
`sirius-db:stacked/translator-exchange-stream-read` -> `dev`. Layer 1 (badge 1/4).

An `EXCHANGE_NODE` is a fragment boundary and `dev` refuses it, so every multi-fragment plan fails to translate. This layer lowers
the receiver's exchange to a `ReadRel` over the engine's `sirius_stream_<node_id>` view, the name the CN and the engine already
share, and records each stream's schema on `TranslatedPlan.stream_inputs` so the CN can declare it. A merging exchange becomes a
`SortRel` over the read. Also carries `slot_global_index`'s slot-id fallback (the q16 shape) and `output_partition_columns` for
hash-partitioned sinks, bare slot refs only. Translator integration tests go from 116 to 127, including the three guard tests
ported from the closed [#1242](https://github.com/sirius-db/sirius/pull/1242). What to look at: the positional sender-name
contract and the refusals around it.

#### [#1709](https://github.com/sirius-db/sirius/pull/1709) feat(starrocks): translate both halves of a two-phase aggregation
`sirius-db:stacked/translator-two-phase-agg` -> `stacked/translator-exchange-stream-read`. Layer 2 (badge 2/4).

`agg_phase::classify` replaces both legacy guards in one commit; a half-landed change would double-aggregate silently, which is
why they cannot be relaxed one at a time. `partial_state::wire_columns` models what the engine binds for each partial state
(FP64 for a decimal sum, I64 for count), not what the FE declares, and both fragments derive their side of the hop from it. Merge
nodes substitute count with sum and leave through a projection that casts back to the FE-declared types. avg is refused with an
error naming layer 4. Tests 127 to 135 plus 8 unit tests. Q6 translates after this layer. What to look at: the atomic guard
replacement and the wire-type table.

#### [#1710](https://github.com/sirius-db/sirius/pull/1710) fix(starrocks): emit aggregation keys and sort tuples in materialized slot order
`sirius-db:stacked/translator-wire-order` -> `stacked/translator-two-phase-agg`. Layer 3 (badge 3/4).

The FE lists grouping expressions in GROUP BY order but materializes the output tuple in ascending slot id, and every consumer
above resolves the row through that order. Emitting keys in GROUP BY order made the engine row and the descriptor view
permutations of each other, so the next hop read a wrong column with no error. Same fix for the sort tuple. The pairing is a strict
bijection and refuses anything else. Seven q03-shaped tests, 135 to 142. What to look at: the bijection rule and the single-key
exemption.

#### [#1711](https://github.com/sirius-db/sirius/pull/1711) feat(starrocks): expand a two-phase avg into sum and count states
`sirius-db:stacked/translator-avg-expansion` -> `stacked/translator-wire-order`. Layer 4 (badge 4/4).

avg is the one state that is two columns, so the partial emits `sum(cast(arg AS DOUBLE))` and `count(arg)` for one FE slot, and the
merge sums both and divides, with NULL for an empty group. The wider row breaks the one-slot-per-column invariant, so the body
fences where an expansion may appear: fragment root only, no conjuncts, no `output_exprs`, one expansion per fragment,
hash-partition keys must be grouping keys. One test per fence. Tests 142 to 148. Q1 translates after this layer. What to look
at: the fences, and be aware that a shape past them would return wrong rows rather than raise.

#### [#1713](https://github.com/sirius-db/sirius/pull/1713) feat(starrocks): carry consumed common-expr slots past the project that computes them
`sirius-db:stacked/translator-carried-common-slots` -> `stacked/translator-avg-expansion`. Layer 5 (badge 5/5).

TPC-H q14 computes its revenue expression once as a common slot in the project below the aggregate and references it from both
measures; below this layer that fragment is refused. The BE outputs a project's common slots whenever an ancestor references them, and
this layer reproduces that: a pre-pass finds consumed common slots, the project emits them as trailing columns and records them as
carried, the slot-ref resolution ladder can read them, and `confine_carried` drops them at the fragment root. A carried slot entering a
join is refused rather than guessed. Eight q14-shaped integration tests plus three unit tests for the ladder, 148 to 156. dev's #1233 tests
are unchanged. Not needed for Q1 or Q6. What to look at: the row-layout invariant (descriptor row first, carried tail) and the join refusal.

Rebase note: when #1704 merges, layer 2 conflicts on three spots it copied byte for byte from #1704 (`i64_type`, `cast_to`, the
test helper `cast_parts`); the first two resolve to identical lines and the duplicate helper is deleted once.

## Step 8, CN stack, #1714 -> #1715 (stack ID 1716)

#### [#1714](https://github.com/sirius-db/sirius/pull/1714) feat(cn): run one compute node per GPU with explicit memory carve-outs and a readiness gate
`sirius-db:stacked/cn-cluster-bringup` -> `dev`. Layer 1 (badge 1/2).

Two measured failures from the source branch: N CNs launched on one box all primed the same default GPU pool, and the FE auto-blacklisted
2 of 4 CNs about 2 s after every cluster start because the engine came up after the ports it probes (q14 at SF100 ran 48.9/0/0/51.1
percent across four CNs). Adds `--gpu-device`, `--gpu-memory-limit` or `--gpu-memory-fraction`, `--host-memory-limit`, `--engine-dir`, a
derived Sirius YAML, NUMA-socket CPU affinity from sysfs, listeners bound before the engine starts, and a heartbeat that answers NOT READY
until the engine is up. One rule I tightened: an exported `CUDA_VISIBLE_DEVICES` that disagrees with `--gpu-device` is an error, not a
warning, because that mistake silently collapsed every CN onto one GPU. 34 tests; the engine-linked build compiles. What to look at: the
readiness interlock and the device rule. Conflicts mechanically with #1706 on `main.rs`.

#### [#1715](https://github.com/sirius-db/sirius/pull/1715) feat(cn): fail the result instances the frontend polls when an intermediate fragment fails
`sirius-db:stacked/cn-result-store-failure-propagation` -> `stacked/cn-cluster-bringup`. Layer 2 (badge 2/2).

When an intermediate fragment fails, nothing told the result the FE polls, so `fetch_data` answered not-ready until the FE's 600 s timeout.
The result store now reserves result instances per query, records the first failure per query, fails every reserved instance of that
query, and offers a Condvar `wait_ready` for the dispatch layer. Additive: dev's callers keep working through a thin `take_next` wrapper
that the dispatch layer removes. One file, nine tests. What to look at: per-query attribution and the `insert` refusing to overwrite a
failure.

## Not part of the steps

| PR | What to do |
|---|---|
| [#1644](https://github.com/sirius-db/sirius/pull/1644) Stream fragment execution (aocsa, Draft, 54 files, +17580) | Old umbrella draft. Do not review. Kept open until every piece it carries has a replacement, then closed with pointers. |
| [#1642](https://github.com/sirius-db/sirius/pull/1642) fix(pipeline): make query completion and teardown wait for all in-flight work (Jedi18, Draft) | Not ours. Read alongside #1699; the design overlap is called out in #1699's body. |

Closed by us with pointers: [#1598](https://github.com/sirius-db/sirius/pull/1598) -> #1702, [#1698](https://github.com/sirius-db/sirius/pull/1698)
(duplicate of #1699; its `stacked/` branch was deleted). Closed earlier: [#1686](https://github.com/sirius-db/sirius/pull/1686),
[#1674](https://github.com/sirius-db/sirius/pull/1674), [#1672](https://github.com/sirius-db/sirius/pull/1672),
[#1636](https://github.com/sirius-db/sirius/pull/1636) to [#1639](https://github.com/sirius-db/sirius/pull/1639),
[#1296](https://github.com/sirius-db/sirius/pull/1296).

## Issues referenced (these are issues, not PRs)

- [#1590](https://github.com/sirius-db/sirius/issues/1590) non-blocking fragment execution and a push/pull FFI. Apache Doris's `push_arrow` request lives in its comments (a future Arrow in/out PR).
- [#1635](https://github.com/sirius-db/sirius/issues/1635) read one parquet file split by byte range across compute nodes. The plan #1696 and #1700 implement.
- [#839](https://github.com/sirius-db/sirius/issues/839) stream session support (open umbrella).
- [#1486](https://github.com/sirius-db/sirius/issues/1486) query-completion race, closed by #1624.

## What comes next, in short

**Wave 2 is done.** T5 #1713, C1 #1714, C2p #1715 and S3 #1717 are open. Nothing is carving.

**Unblocked only by merges (wave 3), in dependency order.** F4 after #1700. L2 after #1699. F5 (step 7) after #1693, #1694 and F4.
T6 after F4. C2a (fragment park and relay) after #1702 and C2p. C2b (exchange dispatch) after #1708 and C2a. C5 (PRPC client) after #1706
and C2b. C6a (nixl agent tier) after #1707 and C5. C6b (nixl exchange) after F5 and C6a. C7 (session warmup) after C6b. F6 (pin_table
FFI) after F5. C9 (pin_table admin channel) after F6 and S3.

**Q1/Q6 milestone.** Needs, beyond what is open: F4, F5, C2a, C2b, C5, C6a, C6b, C7. Then the end-to-end run from the plan (SF1 and
SF10, one lineitem file and many files, `cluster8.sh` with one CN per GPU, oracle compare with zero mismatches, cold and warm).

**After the milestone.** Docs and bench PRs (D2a to D2c, D3), A (deterministic float sums, after a q01/q09 single-GPU benchmark),
C8 (drain overlap), C10 (wire-type parity gate), and the Doris Arrow path (AR1, F9) once the #1590 thread settles the two design calls.
Old draft #1644 is closed with pointers once every piece it carried has a replacement.
