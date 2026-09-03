# demo/q1q6-integration — carve check report

Status: COMPLETE 2026-09-03 12:35 UTC. Steps 0–3 PASS on f64 data (Step 0 fails q01 on decimal data for a `dev`-side reason, see below); Step 4 done: Draft PR https://github.com/aocsa/sirius/pull/3 (do not merge, on the fork, base `demo/q1q6-base` = `origin/dev` 98fe1a84). Not merged, not marked Ready, #1644 untouched. Written 2026-09-03 from the session's evidence notes.

Source of truth: `aocsa/feat/pin-table-cn` @ `d24f02c4` (merge base with `dev` `84ea4ab5`). Demo base: `origin/dev` @ `98fe1a84`.
Worktrees under the stacks clone: `sirius-stacks-wt/sot` (source branch), `sirius-stacks-wt/demo` (branch `demo/q1q6-integration`).
Box: 4x GB200 (aarch64, CUDA 13), nixl/UCX under `/home/prestouser/aocsa/tools`.

## Setup answers (the five "ask once" questions)

1. Demo PR: fork `aocsa/sirius` (default).
2. Scale: SF1 smoke, SF10 milestone (default). No SF100.
3. Missing pieces: one named commit each, plan titles (default).
4. Datasets: `/scratch/sirius/datasets/tpch_sf1` and `tpch_sf10` exist (one parquet per table, DECIMAL(15,2) money columns) and were reused read-only. They expose a pre-existing decimal cast bug (Step 0 below), so f64-typed copies were generated under `/scratch/prestouser/aocsa/demo-q1q6/tpch_sf{1,10}_f64_{1file,multi}` (lineitem as 1 file, and as 8/16 files).
5. FE: copied from the shared clone's `experimental/starrocks/starrocks/output/fe` (jars byte-identical, `meta/` excluded, fresh metadata); no `fe-build`.

## Step 0 — source branch on this box: PASS on f64 data, FAIL on decimal data (q01)

Engine build in `sot`: 1418 ninja steps, exit 0. CN build: `Finished release in 1m 32s`; `readelf -d` NEEDED `libnixl.so`, `libnixl_build.so`, `sirius.duckdb_extension`.
Gotcha: `scripts/cn-env.sh` derives `TOOLS_DIR` as `<repo>/../tools`, which for a worktree is `sirius-stacks-wt/tools` (absent); `TOOLS_DIR=/home/prestouser/aocsa/tools` must be exported.

Cluster: `NUM_CNS=4 GPU_MEM=64GiB HOST_MEM=128GiB STAGING=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 benchmarks/cluster8.sh` with `CUDA_VISIBLE_DEVICES` unset.

> CN0 gpu=0 heartbeat=9100 brpc=9102 … CN3 gpu=3 heartbeat=9130 brpc=9132

nvidia-smi: `74455 MiB` used on each of GPUs 0–3 (one CN each). `SHOW COMPUTE NODES`: 9100/9110/9120/9130 all `Alive=true`.

Smoke (SF1): DEMO Q6 shape revenue `61567694.9502`, `count(*)` `6001215`.

EXPLAIN q01: `2:AGGREGATE (update serialize)` → `3:EXCHANGE HASH_PARTITIONED: 9: l_returnflag, 10: l_linestatus` → `4:AGGREGATE (merge finalize)` → `5:SORT` → `6:MERGING-EXCHANGE`; no `new_planner_agg_stage`. q06: `update serialize` → `EXCHANGE UNPARTITIONED` → `merge finalize`.

| dataset | q01 warm ms (r1/r2/r3) | q06 warm ms | compare |
|---|---|---|---|
| decimal SF1 `/scratch/sirius/datasets/tpch_sf1` | 310 / 127 / 160 | 62 / 59 / 55 | q01 `VALUES-DIFFER rows=4 maxreldiff=9.603e-04 badcells=8`; q06 `MATCH` |
| f64 SF1, lineitem 1 file | 211 / 301 / 245 | 277 / 225 / 131 | q01 `MATCH maxreldiff=1.437e-12`; q06 `MATCH maxreldiff=4.840e-16` |
| f64 SF1, lineitem 8 files | 212 / 218 / 214 | 234 / 201 / 115 | q01 `MATCH maxreldiff=1.269e-12`; q06 `MATCH maxreldiff=3.630e-16` |

Decimal failure, root cause: the 8 bad cells are `sum_disc_price` and `sum_charge` in all four groups. Grouping by `l_discount` shows only the `0.07` group differs (Sirius `19238026771.625206`, DuckDB `19447135758.2733`, ratio 0.92/0.93). `1 - 0.07` in FP64 is `0.9299999999999999`; the FE plan casts it back (`30 <-> cast([29: subtract, DECIMAL64(16,2)] as DECIMAL128(16,2))`) and the cast truncates to `0.92`. The lowering (#1236) and the cast are on `dev`, so this is not a multi-CN or carve defect. Decision: the milestone runs on f64 data (the campaign's own data type, `tpch_parquet_sf1000_f64`); the decimal result is reported here as a finding.

Logs: `nixl bandwidth canary peer=127.0.0.1:9112 gbps="389.5" bytes=16777216 floor_gbps=2.0` (peer 9102 measured 105.8–109.5, 9112 98.2–392.7, 9122 345.4–432.2, 9132 262.8–401.1); `transmitted batches via nixl stream_id=3 sender_id=0 dest=127.0.0.1:9112 batches=1 bytes=64`; zero hits for "needs the nixl transport tier", "input stream row count unknown", `fail_stalled_query`. `cn-distribution.py`: .cn0 21.1%, .cn1 21.1%, .cn2 15.5%, .cn3 42.2%, all non-zero.

## Step 1 — merges: PASS (HEAD `3755f719`, 32 commits over `origin/dev`)

Order: `stacked/translator-avg-expansion` 5f9349d5 (#1711), `stacked/scan-byte-range-ingestible` a22235e1 (#1700), `stacked/ffi-fragment-rust` 14386a77 (#1702), `exec-exchange-staging-arena` 1e61c16c (#1693), `exec-stream-cardinality` f1e8fb17 (#1694), `pipeline-stall-watchdog` d53b44b1 (#1699), `translator-clone-expr-narrowed-builtins` 27528385 (#1704), `cn-files-schema-multi-range` 3056cda5 (#1705), `cn-transport-tunables` e2d6058d (#1706), `cn-exchange-proto-patch` a04bf5bc (#1707), `stacked/translator-carried-common-slots` 268b7592 (#1713), `stacked/cn-result-store-failure-propagation` a1ac4c58 (#1715, brings #1714), `stacked/scan-pinned-file-subset` 6c0b83a1 (#1717, merged after its `test-run` passed).

Conflicts and resolutions:
- `test/cpp/exec/test_streaming_fragment.cpp` (K vs L1): FRAG-10 and FRAG-11 appended at the same spot; resolved as an ordered union (K's block, then L1's), `#include <string>` kept.
- Translator (T0 vs T2): `expr_translator.rs` `cast_to_fp64` block and `type_mapper.rs` `duckdb_type_name` block kept; `tests/translate.rs` imports merged into one list, both test blocks kept. No duplicate `i64_type`/`cast_to`/`cast_parts` remained.
- `main.rs`/`lib.rs` (C4 vs C1): imports unioned (`Tunables` plus `EngineReadiness`/`HttpServer`); `Tunables::resolve()` stays the first statement of `run`; C4's pre-C1 executor block dropped because C1 builds the executor after the listeners.

Verification: CN CI trio green (fmt, clippy `-D warnings`, 290 tests); C++ build 1419 steps exit 0; Catch2 on GPU: `[staging_arena]` 13, `[parquet_byte_range]` 6, `[stream_bind_catalog]` 11, `[streaming_fragment]` 7, `[sirius_ffi]` 3, `[can_serve]` 13, `[cached_serving]` 25, `[pin_table_file_subset]` 1, `[scan]` 284 cases, all passed; rust `cargo test -p sirius` 10/10 on a GPU (needs `RUSTFLAGS="-C link-arg=-Wl,--allow-shlib-undefined"` plus `LD_LIBRARY_PATH` to the extension dir and the pixi env lib on this box; fmt and clippy clean).

## Step 2 — carved pieces: PASS (10 commits, HEAD `1e163623`, tree clean)

Method: one workflow chain per piece (carve → adversarial review → fix stage) on the demo worktree, in plan order, each piece taken from the source branch with the carve-sheet corrections. Every review ended "approve" with minor-only findings (0 blocker, 0 major). Per-piece evidence: `scratchpad/carve-step2-final.md` (188 lines) and `scratchpad/carve-specs/*.md`.

| # | piece | commit | title | review | verification in the fix stage |
|---|---|---|---|---|---|
| 1 | F4 | `92e91adf` | feat(ffi): byte ranges ride the Substrait plan into the parquet scan | approve, 4 minor | ninja 384/384 exit 0; pre-commit clean; `[parquet_byte_range]` + Rust `byte_range_splits_read_every_row_exactly_once` on a GPU |
| 2 | F5 | `b7452f67` | feat(ffi): staging-arena leases, packed batch export/push and declared stream cardinality | approve, 5 minor | make 378/378 exit 0; Catch2 20291 assertions in 13 cases; the three Rust packed-hop tests on a GPU |
| 3 | T6 | `d88265aa` | feat(starrocks): emit byte-range splits instead of refusing them | approve, 3 minor | translator crate fmt/clippy `-D warnings`/tests clean (`--no-default-features`) |
| 4 | C2a | `85da8f6f` | feat(cn): park a fragment's output on the GPU and relay it into the next fragment | approve, 5 minor | fmt, clippy clean; CN lib 111 passed (103 baseline + 8 new); engine test on GPU 2 |
| 5 | C2b | `37b48a46` | feat(cn): receiver-first exchange rendezvous and off-RPC fragment dispatch | approve, 4 minor | fmt, clippy clean; CN lib 123 passed; engine-linked test on GPU 1 |
| 6 | C5 | `86b3f9a6` | feat(cn): a blocking PRPC client for CN-to-CN calls | approve, 2 minor | fmt, clippy clean; CN lib 126 passed (3 loopback tests) |
| 7 | C6a | `36072ef8` | feat(cn): nixl agent tier with arena registration and a bandwidth canary | approve, 5 minor | fmt, feature-on clippy clean; CN lib 129 passed; nixl smoke test canary 526.4 GB/s on GPU 1 |
| 8 | C6b | `9e547c89` | feat(cn): move exchange batches GPU-to-GPU over nixl | approve, 6 minor | fmt, clippy clean; CN lib 145 + main 7 + translator tests passed; two new GPU engine tests |
| 9 | C7 | `49d23e8a` | fix(cn): pre-establish nixl peer sessions so a cold cluster cannot deadlock | approve, 3 minor | fmt, clippy clean, no warnings; no live-cluster run in the piece (Step 3's `--cold-restart` is the proof) |
| 10 | bench | `1e163623` | chore(bench): TPC-H q01/q06 harness, DuckDB oracle and one-CN-per-GPU launcher | approve, 4 minor | `bash -n` 3/3, py_compile 3/3, compare.py exit codes exercised on SF1 fixtures; shellcheck not installed on the box |

Remaining delta demo HEAD vs `aocsa/feat/pin-table-cn` on the in-scope paths: 45 files, +6907/−3072. It is dominated by modules the milestone excludes on purpose (`admin_command.rs` 215, `wire_type_parity.rs` 723, `nixl_transport/{nixl_bench,nixl_echo,two_node_harness}.rs` 2165 lines, `stream_lifecycle.*`, F6 pin-table FFI in `rust/crates/sirius/src/lib.rs`) and by the translator tests that the merged stack PRs carry in their dev-preserving form (`tests/translate.rs` 1929 lines of churn, `node_translator.rs` 384). None of the four excluded modules exists in the tree (checked).

Open items a maintainer must decide (full list in `carve-step2-final.md`, "Open issues per piece"):
- F4: `collect_from_rel` in `substrait_scan_ranges.cpp` throws on Substrait rel kinds outside the handled set even when a plan carries no byte ranges; `extract_scan_byte_ranges` re-parses the plan bytes once per `lower_substrait` call.
- C2b: the thrift `cancelPlanFragment` OK stub rode with C2b (map row says so; the piece spec's lib.rs line did not).
- C6b/C7: `SHUTDOWN_GRACE` (SOT `main.rs`, 15 s force-exit bound) is in no piece; a CN stopped mid-warmup against an unreachable peer can block on the PRPC reply timeout (60 s default).
- C6b: until C8, a failed remote send leaves the later destinations of the same sink undrained (their claims pin the parked output until the next failure wipe).
- bench: `compare.py` now exits non-zero on any mismatch (SOT always exited 0); SOT callers not in this tree (`run-abc.sh`, `run-comparison.sh`) must be checked when carved. `gen-tpch.sh` keeps SOT's `/opt/dlami/nvme/tpch` default.
- Commit bodies of F5, C2a, C6a, C6b run over the 3–10 line guideline (11–22 wrapped lines); amending was forbidden by the carve rules.

## Step 3 — end to end: PASS (4 CNs, one per GPU, SF1 then SF10, f64 data, warm and cold restart)

Build on HEAD `1e163623`: CN `cargo build --release` `Finished release`; `readelf -d` NEEDED `libnixl.so`, `libnixl_build.so`, `sirius.duckdb_extension`; `make release` relinked the extension (378/378). Driver: `scratchpad/demo-step3.sh` → `run-step3.sh` per arm; outputs in `scratchpad/step3/demo-<dataset>/{summary.txt,cluster.log,warm/,cold/}`.

Cluster per arm: `NUM_CNS=4 GPU_MEM=64GiB HOST_MEM=128GiB STAGING=8GiB SIRIUS_EXCHANGE_STAGING_BYTES=8GiB SIRIUS_QUERY_WATCHDOG_SECS=60 benchmarks/cluster8.sh`, `CUDA_VISIBLE_DEVICES` unset. Every arm: 4 alive compute nodes after 9–12 s (heartbeat 9100/9110/9120/9130), nvidia-smi 74.45–74.47 GB used on each of GPUs 0–3, exactly one CN pid per GPU UUID, blacklist empty. EXPLAIN q01: `2:AGGREGATE (update serialize)` → `3:EXCHANGE HASH_PARTITIONED: 9: l_returnflag, 10: l_linestatus` → `4:AGGREGATE (merge finalize)` → `5:SORT` → `6:MERGING-EXCHANGE`; q06: `update serialize` → `EXCHANGE UNPARTITIONED` → `merge finalize`. Same shapes as Step 0.

Bench: `bench.sh out.csv 3 q01 q06` (r0 cold + r1–r3 warm), then `bench.sh --cold-restart` (cluster restarted before each query, r0 cold + r1 warm), `compare.py` against the DuckDB oracle (rel tol 1e-6). All 16 compares MATCH.

| dataset (lineitem layout) | q01 warm r1–r3 ms (median) | q06 warm r1–r3 ms (median) | warm compare q01 / q06 | cold-restart q01 / q06 ms (then warm) | cold compare q01 / q06 |
|---|---|---|---|---|---|
| SF1, 1 file (188.8 MiB, 6 row groups) | 277 / 203 / 191 (203) | 239 / 143 / 229 (229) | MATCH 1.437e-12 / MATCH 4.840e-16 | 1168 (264) / 1014 (286) | MATCH 1.439e-12 / MATCH 4.840e-16 |
| SF1, 8 files (192.9 MiB) | 182 / 235 / 162 (182) | 199 / 124 / 200 (199) | MATCH 1.269e-12 / MATCH 3.630e-16 | 1102 (199) / 1116 (211) | MATCH 1.270e-12 / MATCH 2.420e-16 |
| SF10, 1 file (1974.8 MiB, 60 row groups) | 270 / 244 / 260 (260) | 299 / 290 / 288 (290) | MATCH 2.293e-12 / MATCH 1.163e-15 | 3287 (289) / 1233 (327) | MATCH 2.291e-12 / MATCH 9.691e-16 |
| SF10, 16 files (1977.0 MiB) | 251 / 272 / 224 (251) | 298 / 280 / 294 (294) | MATCH 2.131e-12 / MATCH 1.357e-15 | 1055 (276) / 1115 (307) | MATCH 2.130e-12 / MATCH 1.357e-15 |

Against the source branch on the same box (Step 0, f64 SF1 1 file: q01 211/301/245, q06 277/225/131; SF10 baselines warm q01 323/284/284, cold 1391): the demo tree is within run-to-run noise. The one odd sample is the SF10 single-file cold-restart q01 at 3287 ms (every other cold first run is 1.0–1.2 s); it is a single sample and the following warm run was 289 ms.

Log checks per arm (current process generation of `cluster.log`): nixl bandwidth canary against every peer, 77.9–431.9 GB/s (floor 2.0), 12 lines per arm; `transmitted batches via nixl stream_id=3 sender_id=<n> dest=127.0.0.1:91{1,2}2 batches=1 bytes=64` (2 lines in the SF1 single-file arm, 4–6 in the others); negative markers `needs the nixl transport tier`=0, `input stream row count unknown`=0, `fail_stalled_query`=0, `errorCode=62`=0, ERROR lines=0 in all four arms.

Work distribution (`scripts/cn-distribution.py`, newest run-uuid per CN = the last cold-restart generation, two q06 runs): SF10 single file .cn0 14.3% / .cn1 35.7% / .cn2 35.7% / .cn3 14.3% (28 tasks), SF10 16 files 14.8/37.0/33.3/14.8 (27); SF1 single file 22.2/38.9/38.9/0.0 (18), SF1 8 files 0.0/41.7/41.7/16.7 (24). Over all 12 generations (`--all-runs`) 3 of 48 CN lifetimes had zero `task` work (.cn0 once, .cn3 twice), all of them SF1 cold-restart generations that ran only two queries; every SF10 lifetime had work on all four CNs. Two of the three silent lifetimes recorded no `query` or `plan` event either (the FE sent that CN nothing in those two queries); the third (.cn3, 12:24:13) recorded 2 plans and 8 query events but no task lines, which is either a fragment with no tasks or the known CN-exit flush gap in the Quent exporter. The 2-CN smoke's fragment dumps (below) show the FE splits a single parquet file by byte range in node-count pieces, so at SF1 the idle CN is the FE's assignment for a two-query sample, not a CN that refused work; the cold q06 answers were still correct in those generations.

DEMO.md `cluster2`-equivalent smoke (FE + 2 CNs on GPU 0, `--gpu-memory-limit 8GiB --host-memory-limit 12GiB`, `SIRIUS_EXCHANGE_STAGING_BYTES=1280MiB`, `CUDA_VISIBLE_DEVICES=0`): first attempt died at exec with `libnixl.so: cannot open shared object file` because my launcher (`demo-step3.sh`) did not `source scripts/cn-env.sh` the way `cluster8.sh` does; a harness mistake, not a branch defect. Rerun (`demo-smoke2.sh`, log `scratchpad/demo-smoke2.log`): 2 alive after 24 s, two CN pids on GPU-4243ea2a at 10182 MiB each; DEMO Q6 shape `revenue 61567694.9502`, `count(*) 6001215`; canary `peer=127.0.0.1:8060 gbps="339.1"`, `peer=127.0.0.1:8062 gbps="626.4"`; `transmitted batches via nixl stream_id=2 sender_id=1 dest=127.0.0.1:8060 batches=4 bytes=4000768` and `stream_id=3 … bytes=64`; negative markers 0. `SIRIUS_CN_DUMP_FRAGMENTS` on both CNs: the single 197,921,624-byte lineitem file arrives as two byte ranges, `.cn2` `start_offset: 0 size: 98960812` and `.cn1` `start_offset: 98960812 size: 98960812` (dumps in `scratchpad/step3-demo/dump-cn{1,2}/`).

CI-equivalent checks on HEAD `1e163623` (`scratchpad/ci-demo-final.log`): CN trio as `experimental.yml` runs it, `cargo fmt --check` OK, `cargo clippy --all-targets --no-default-features -- -D warnings` exit 0, `cargo test --workspace --no-default-features` 149 + 7 + 19 + 167 passed, 0 failed; rust bindings job as `check.yml` runs it, fmt OK, clippy `-D warnings` exit 0, `cargo test --no-run` built both crates (with `RUSTFLAGS="-C link-arg=-Wl,--allow-shlib-undefined"`, the box-specific link workaround noted in Step 1).

Not done: SF100 (out of scope by the Q2 answer), decimal datasets (fail q01 for the `dev`-side reason in Step 0), any second host.

## Step 4 — publish: DONE

- `git push aocsa demo/q1q6-integration` (new branch, HEAD `1e163623`); `git push aocsa 98fe1a84:refs/heads/demo/q1q6-base` because the fork's `dev` (a57441bd) is six commits behind `origin/dev` and a PR against it would show unrelated diff. Nothing pushed to `origin`, no `stacked/*` branch pushed.
- Draft PR on the fork: https://github.com/aocsa/sirius/pull/3, title "Do not merge: demo/q1q6-integration, the carved Q1/Q6 multi-CN stack end to end", body from `~/.claude/plans/pr-bodies/demo-q1q6-integration.md` (merged PRs, carved commits with SHAs and future layers, measured table, deviations, testing note, not-handled list).
- Untouched: the 19 open Drafts on `sirius-db/sirius` (no Ready flips, no merges), #1644.

## Harness notes (for whoever reruns this)

- `scripts/cn-env.sh` derives `TOOLS_DIR` as `<repo>/../tools`; export `TOOLS_DIR=/home/prestouser/aocsa/tools` in worktrees. Any ad-hoc CN launch outside `cluster8.sh` must `source scripts/cn-env.sh` first or the CN dies at exec on `libnixl.so`.
- `pkill -f <CN path>` from a wrapper whose own command line mentions that path kills the wrapper (exit 144); kill the launcher's process group instead (`kill -TERM -- -$pid`).
- The harness `performance_test.py` q1/q6 are not the bench-kit q01/q06 (different literals); compare like with like.
- Quent per-CN sessions lose the once-per-process declarations at CN exit; `cn-distribution.py` reads the newest session only unless `--all-runs`.

## Drift from the source branch

Every deliberate difference is named in the commit body of the piece that carries it; the per-piece list is in `scratchpad/carve-step2-final.md` ("Drift per piece"). The ones that change behavior or that a reviewer of the future layers will meet:

- **F4**: `substrait_scan_ranges.cpp` sits at the end of the planner block in `CMakeLists.txt` (SOT: out of order); the Rust helper `write_multi_row_group_parquet` is F2's, shared, not re-added. No CTE-comment reword (that SOT hunk does not exist; the appendix skeptic was right).
- **F5**: `stream_input_binding` brace-init keeps K's field order (`estimated_rows` after `built`); stale "bump head" wording replaced by the address-ordered free-list wording everywhere; the SOT reword of the `key_cast_types` comment is not taken (multi-CN partition-key prose); `streaming-fragments.md` gained a 39-line export_packed/push_packed section SOT never had. Rust tests call `result_to_arrow()` (F2's verb; the tree has no `into_arrow`).
- **T6**: `BTreeMap` instead of `HashMap` so the node named in an error is deterministic; a file whose coalesced ranges are all empty is refused per path (SOT silently dropped it when the node also owned real bytes of another file); #1232's tests and error strings are kept and re-pointed at the new semantics instead of deleted; `error.rs` doc example updated (outside the piece's file list).
- **C2a/C2b**: one `run_fragment` (SOT's inner/outer split returns with C8's lease release); `FragmentInstanceId` made `pub` (private-interfaces error under `-D warnings`); every destination routes locally and a Remote destination errors at routing time until C6b; `with_executor(executor, identity)` is the production constructor; DEMO.md ships SOT lines 1–104 only; `cancelPlanFragment` answers OK.
- **C5**: the seven `cfg_attr(not(feature = "nixl-transport"), allow(dead_code))` sites are plain `allow(dead_code)` until C6a adds the feature (C6a then narrows them back).
- **C6a**: `NixlTransport::start(executor, agent_name)` without the `fe` argument and with a `Result<(), String>` ready channel (C7 widens both); blocking `send_fragment` instead of `start_fragment -> DrainTicket` (C8); the nixl smoke test builds its arena from `SiriusContext::new().staging_arena()` behind a test-only `ArenaExecutor`; new pure `check_single_visible_device` with CI-run unit tests; `Cargo.lock` regenerated and `nixl-sys` pinned at 1.3.2 to match SOT and the installed libnixl 1.3.
- **C6b**: remote destinations drained in the FE's order with a blocking loop (`FragmentOutcome::from_ready`), no `SenderDrains`/`dispatch_then_join`; `SHUTDOWN_GRACE` not taken; DEMO.md says 1280 MiB (matches `cluster2`); `(PLAN-PATH-B B5)` internal plan references scrubbed from three docs.
- **C7**: `SIRIUS_CN_NIXL_WARMUP` and `SIRIUS_CN_NIXL_WARMUP_PEERS` are validated `tunable.rs` knobs (`Switch`, `PeerList`), so a garbage value or a malformed peer fails bring-up where SOT read it as on or dropped it with a warning; `Tunables` lost `Copy`; `retry_backoff` uses `clamp(1, 4)` (same results as SOT's `min(4)`, no underflow at 0).
- **bench**: `cluster8.sh` unsets `CUDA_VISIBLE_DEVICES` and defaults `SIRIUS_QUERY_WATCHDOG_SECS=0`; `bench.sh` takes `ORACLE_DIR` and `exec`s `compare.py`, which exits 1 on any mismatch, no-oracle or empty row; `oracle.py` uses `tempfile.gettempdir()` instead of `/opt/dlami/nvme/duckdb-tmp`; `cn-distribution.py`'s hint says `rm -rf .cn*/telemetry/*` because `clean-telemetry.sh` (D2a) is not in the tree; README rewritten to what ships.
- **Not carried at all** (and therefore absent from this demo): the canonical float-sum sort of `441f05b2` (this tree's `dev`-side aggregate has no such sort; see `starrocks-sirius-perf-plan.md` for why that is the right default), `SHUTDOWN_GRACE`, `configure_duckdb_extensions`/`ensure_parquet_extension_env`, `admin_command.rs` (C9), `wire_type_parity.rs` (C10), `DrainTicket` (C8), the nixl bench/echo/two-node harnesses, `stream_lifecycle.*`, `configs/gb200-*`, `notes/`, the other 20 TPC-H queries.

## Cherry-pick plan for the carved commits

Each commit is one future layer and cherry-picks (`git cherry-pick -x <sha>`) onto the head of its base branch once the base exists; verify in the layer's own tree (the commits were built on a tree that already holds all twelve merged PRs, so a layer whose base lacks a sibling stack will not compile until that stack merges into `dev`). Order and gates:

| order | piece | commit | future branch | base | merge gate |
|---|---|---|---|---|---|
| 1 | F4 | `92e91adf` | `stacked/ffi-substrait-byte-ranges` | F2 `stacked/ffi-fragment-rust` (#1702) | S2 #1700 merged to `dev` (uses `resolved_file_ranges`) |
| 2 | F5 | `b7452f67` | `stacked/ffi-exchange-staging` | F4 | X #1693 and K #1694 merged |
| 3 | T6 | `d88265aa` | `translator-byte-range-splits` (fork PR on `dev`) | `dev` | HOLD until F4 merged (ranges into an engine that ignores them duplicate rows N times, silently) |
| 4 | C2a | `85da8f6f` | `stacked/cn-fragment-park-relay` | C2p `stacked/cn-result-store-failure-propagation` (#1715) | F2 #1702 merged |
| 5 | C2b | `37b48a46` | `stacked/cn-exchange-dispatch` | C2a | T1 #1708 merged; drops C2a's `mod local_exchange` dead-code allow and the `execute()` shim (already done in the commit) |
| 6 | C5 | `86b3f9a6` | `stacked/cn-prpc-client` | C2b | C4 #1706 merged |
| 7 | C6a | `36072ef8` | `stacked/cn-nixl-agent-tier` | C5 | C3 #1707 merged (proto patch); regenerate `Cargo.lock` if upstream bumps syn |
| 8 | C6b | `9e547c89` | `stacked/cn-nixl-exchange` | C6a | F5 merged |
| 9 | C7 | `49d23e8a` | `stacked/cn-nixl-session-warmup` | C6b | none beyond C6b |
| 10 | bench | `1e163623` | split into D2a `stacked/docs-cn-runbooks` (`cluster8.sh`, the `cn-distribution.py` hint), D2b `stacked/bench-tpch-harness-oracle` (`bench.sh`, `compare.py`, `oracle.py`, `README.md`, `q01.sql`, `q06.sql`, `gen-tpch.sh`), D2c `stacked/bench-cn-distribution` (`scripts/cn-distribution.py`) | `dev` → D2a → D2b → D2c | docs-only for CI; MD057 needs the README's `cn-distribution.py` link to land with or after D2c, and the `compare.py` exit-code change must travel with `bench.sh`'s `exec` tail |

Stack mechanics per `CONTRIBUTING.md` and the plan: `gh stack add` from the base layer's branch, push `stacked/*` only with same-repo write access (this session never pushes a `stacked/*` branch), wait ~20 s after `gh stack submit` before `gh pr edit` (the `clean-pr-body` job race), bodies from `~/.claude/plans/pr-bodies/`. Where a cherry-pick conflicts, the fix stages' notes in `carve-step2-final.md` say which side is intended (e.g. C5's `allow(dead_code)` form, C6a's start signature, C6b's blocking loop).
