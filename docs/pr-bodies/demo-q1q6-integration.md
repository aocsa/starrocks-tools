<!-- NOTE: Draft, do not merge. Demo integration branch that puts the carved Q1/Q6 PRs together with the still-uncarved pieces to prove the carve reproduces the source branch end to end. -->

## Description

**Do not merge.** This branch exists to check that the carve of `aocsa/feat/pin-table-cn` (d24f02c4) into stack layers reproduces the working multi-CN system. It is `origin/dev` @ 98fe1a84 plus the twelve open Draft PRs merged in stack order plus one commit per still-uncarved piece, each carved from the source branch with the plan's corrections. Nothing here is meant to be reviewed as a unit; the review units are the individual PRs and the future stack layers named in each commit. The PR base is `demo/q1q6-base`, a branch at `origin/dev` 98fe1a84, because this fork's `dev` is six commits behind and would add unrelated diff.

**Merged PRs (in this order):** #1711 (`stacked/translator-avg-expansion`, carries #1708–#1710), #1700 (`stacked/scan-byte-range-ingestible`, carries #1696), #1702 (`stacked/ffi-fragment-rust`, carries #1697), #1693, #1694, #1699, #1704, #1705, #1706, #1707, #1713, #1715 (`stacked/cn-result-store-failure-propagation`, carries #1714), #1717 (`stacked/scan-pinned-file-subset`). Three conflict sets, all mechanical: FRAG-10/FRAG-11 appended at the same spot of `test_streaming_fragment.cpp`; the T0-vs-T2 `i64_type`/`cast_to`/`cast_parts` overlap; the C4-vs-C1 `main.rs` import line and first statement of `run`.

**Carved commits (one per piece, plan titles, future stack layer in each body):**
- `92e91adf` feat(ffi): byte ranges ride the Substrait plan into the parquet scan (F4, `stacked/ffi-substrait-byte-ranges` on #1702)
- `b7452f67` feat(ffi): staging-arena leases, packed batch export/push and declared stream cardinality (F5, `stacked/ffi-exchange-staging` on F4)
- `d88265aa` feat(starrocks): emit byte-range splits instead of refusing them (T6, fork PR `translator-byte-range-splits`, held until F4 merges)
- `85da8f6f` feat(cn): park a fragment's output on the GPU and relay it into the next fragment (C2a, `stacked/cn-fragment-park-relay` on #1715)
- `37b48a46` feat(cn): receiver-first exchange rendezvous and off-RPC fragment dispatch (C2b, `stacked/cn-exchange-dispatch` on C2a)
- `86b3f9a6` feat(cn): a blocking PRPC client for CN-to-CN calls (C5, `stacked/cn-prpc-client` on C2b)
- `36072ef8` feat(cn): nixl agent tier with arena registration and a bandwidth canary (C6a, `stacked/cn-nixl-agent-tier` on C5)
- `9e547c89` feat(cn): move exchange batches GPU-to-GPU over nixl (C6b, `stacked/cn-nixl-exchange` on C6a)
- `49d23e8a` fix(cn): pre-establish nixl peer sessions so a cold cluster cannot deadlock (C7, `stacked/cn-nixl-session-warmup` on C6b)
- `1e163623` chore(bench): TPC-H q01/q06 harness, DuckDB oracle and one-CN-per-GPU launcher (the q01/q06 subset of D2a/D2b/D2c)

**Measured result.** On the 4x GB200 box, one CN per GPU (64 GiB pool and 8 GiB staging each), TPC-H q01 and q06 match the DuckDB oracle in every arm, warm and after a cold restart: SF1 and SF10, lineitem as one file and as 8 or 16 files (largest relative difference 2.3e-12 on q01, 1.4e-15 on q06). Warm medians of three runs:

| dataset | q01 ms | q06 ms | cold-restart first run q01 / q06 ms |
|---|---|---|---|
| SF1, one file | 203 | 229 | 1168 / 1014 |
| SF1, 8 files | 182 | 199 | 1102 / 1116 |
| SF10, one file | 260 | 290 | 3287 / 1233 |
| SF10, 16 files | 251 | 294 | 1055 / 1115 |

The source branch on the same box gives the same shapes and numbers within noise (SF1 one file q01 245, SF10 q01 284 warm). EXPLAIN shows `AGGREGATE (update serialize)` → `EXCHANGE HASH_PARTITIONED: l_returnflag, l_linestatus` → `AGGREGATE (merge finalize)` → `SORT` → `MERGING-EXCHANGE` for q01 and `update serialize` → `EXCHANGE UNPARTITIONED` → `merge finalize` for q06. The nixl bandwidth canary ran between 78 and 432 GB/s against its 2 GB/s floor, the logs show `transmitted batches via nixl` for the merge streams, and there is no transport-tier refusal, no unknown stream row count, no stalled query and no `errorCode=62` in any arm. The DEMO.md two-CNs-on-one-GPU smoke reproduces the Q6 shape (`61567694.9502`, `6001215` rows), and its fragment dumps show the single lineitem file arriving as two byte-range halves (`start_offset` 0 and 98960812, 98960812 bytes each).

**Deviations from the source branch.** Each commit body names its own differences from d24f02c4; the report has the full list. The ones that change behavior: T6 refuses a parquet file whose byte ranges are all empty instead of silently dropping it, and keeps #1232's tests and error strings; C2b routes every destination locally and errors on a remote one until C6b, which drains remote destinations with a blocking loop (the overlapped drain is C8, not here); C7 puts the two warmup knobs in the validated tunables registry, so a garbage value fails bring-up instead of reading as on; `compare.py` exits non-zero on any mismatch and `bench.sh` execs it, so a one-row answer cannot pass by accident; `cluster8.sh` unsets `CUDA_VISIBLE_DEVICES` before launching. Not carried: the canonical float-sum sort (441f05b2), `SHUTDOWN_GRACE`, the admin channel, the wire-type parity gate, the drain overlap, the nixl bench/echo harnesses, `stream_lifecycle`.

**How I tested it.** Built the engine and the CN in a worktree of this branch, brought the cluster up with `benchmarks/cluster8.sh` (four CNs), ran `benchmarks/tpch/bench.sh` three warm runs and then `--cold-restart` per dataset with `ORACLE_DIR` pointing at DuckDB answers from `tools/oracle.py`, and grepped the cluster log for the canary, transmit and failure markers; `scripts/cn-distribution.py` on the CN telemetry confirms all four CNs do work at SF10 (at SF1 the FE leaves one CN idle in a two-query sample). The CN CI trio (fmt, clippy with `-D warnings`, 342 tests without the engine feature) and the rust bindings checks (fmt, clippy, `test --no-run`) pass on this HEAD. One caveat for anyone re-running this: the decimal-typed TPC-H datasets under `/scratch/sirius/datasets` fail q01 on the CN path by 9.6e-4 because `1 - 0.07` is lowered to FP64 and cast back to `DECIMAL(16,2)` with truncation. That comes from `dev` (#1236), not this branch, so the runs above use f64-typed copies of the data.

**Intentionally not handled.** `stream_lifecycle.*`, the nixl bench/echo harnesses, `notes/`, `configs/gb200-*`, generated YAML, the pin-table FFI and admin channel (F6/C9), the drain-overlap layer (C8), the wire-type parity gate (C10), the hash-join wedge fixes (L2), deterministic sums (A), the docs and bench PRs beyond the q01/q06 kit, TPC-H queries other than q01 and q06, and any second host.

## Checklist
- [ ] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist (not applicable: demo branch, do not merge)
- [x] Cover changes with new or existing tests (each carved commit carries the tests the plan names)
- [x] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md) (not applicable)

## References
- Plan: `~/.claude/plans/i-want-to-create-golden-crane.md` ("Q1/Q6 milestone", "Carve sheets"); report `~/.claude/plans/demo-q1q6-report.md`.
- Source branch `aocsa/feat/pin-table-cn` @ d24f02c4; old umbrella draft #1644 stays open until every piece has a replacement.
