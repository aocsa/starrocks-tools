# Fix 2 implementer notes: per-query parked-output bookkeeping (CN layer)

Worktree `/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping`, branch `fix/parked-bookkeeping`
off `perf/profile-sf1000` = 45dab3be. GPU 1 only. Spec: `fix/designs/parked-bookkeeping-SPEC.md`;
decisions: `fix/designs/DECISIONS.md` (packaging 1 branch / 2 commits; cancel scope = spec default;
gate 2 ON; arm D deferred to the integration sweep).

## Reading done before touching code

- Spec sections 0-12, INTEGRATION.md (fix-2 rows, 2.3 fusion contract: non-exhaustive `SenderSource`
  matches in `release_staged`/`release_sources`), DECISIONS.md.
- `engine.rs`, `local_exchange.rs`, `result_store.rs`, `fragment_executor.rs` whole;
  `compute_node_service.rs` whole (4131 lines); `lib.rs` module list and re-exports; `nixl_transport.rs`
  anchors 405-416 and 785-796; `docs/super-sirius/README.md` reading order (no engine C++ is touched).
- Generated proto: `PCancelPlanFragmentRequest.cancel_reason: Option<i32>` (prost enumeration),
  `PPlanFragmentCancelReason { LimitReach=1, UserCancel=2, InternalError=3, Timeout=4, QueryFinished=5 }`.

## Deviations from the spec (why)

1. `RetireTrigger` is `pub`, not `pub(crate)`: `FragmentExecutor` is re-exported from `lib.rs:73`
   (`pub use fragment_executor::{FragmentExecutor, ...}`), so a `pub(crate)` type in a trait method
   signature trips `private_interfaces` under `-D warnings`.
2. Gate-2 test `output_parked_after_a_retire_is_dropped_not_parked`: the spec says "retire_query(D) first,
   then run a sender labelled D" but gate 1 (dequeue refusal) fires before gate 2 whenever the mark
   precedes the run. Gate 2 is only reachable when the mark lands *while* the fragment runs, so the test
   uses a `#[cfg(test)]` `after_run` hook on `ExecuteRequest` (precedent: `EngineRequest::Sleep`) that
   marks D between `fragment.run()` and the park.
3. `export_next` stays as a small free function in `engine.rs`: the `export_packed -> StagedBatch`
   conversion is `sirius::Fragment`-specific and cannot live in the generic registry; only the
   slot/claim bookkeeping moved.

## Log of runs (red -> green)

(filled in as I go)

### Commit 1

- `parked_registry.rs` tests written first (9 tests, spec 6.1): red = 22 unresolved-name compile errors
  (`cargo test --no-default-features parked_registry`, 30 s). Implementation added: 9/9 green.
- `result_store::failure_of` + test `failure_of_reports_the_first_recorded_failure`: green on first run
  (written together with the accessor; the `cancel_query` half comes in commit 2).
- Five service tests (spec 6.4, commit-1 rows) written before the gates: red for behavioural reasons
  (straggler ran -> `wait_until` never saw exactly 3 runs; late sender RPC returned OK 0 instead of
  INTERNAL_ERROR 6; `retired` empty; `released` empty instead of `[4096]`). After gates 3/4,
  `fail_fragment`, `release_staged`/`release_sources`, `StagedLeases`: 5/5 green.
- Full no-engine suite: 167 passed, 0 failed (was 158 + the 9 registry tests).
- `cargo clippy --all-targets --no-default-features -- -D warnings`: clean. `cargo fmt --check`: five
  reflows in the new test code, applied.
- Engine-linked suite (`cargo test --release -p sirius-starrocks-cn`, CUDA_VISIBLE_DEVICES=1): log in
  `parked-bookkeeping-gpu-run1.log` (see below).
- Engine-linked suite run 1 (release, GPU 1): 179 passed, 0 failed, 4 ignored (the pre-existing
  debug harnesses). The four new engine tests and the two guard tests (`engine_executes_local_files_and_sequential_exchange`,
  `engine_pushes_staged_remote_batches` with its double-drop assertion) pass. Three dead-code warnings in
  the engine build (`slots`, `len`, `is_empty` are test-only there) fixed with `#[cfg(test)]`; `is_empty`
  dropped. `cargo check --release --all-targets` clean afterwards.
- Pre-commit: every hook reports "no files to check" because `.pre-commit-config.yaml` excludes
  `^experimental/.*`. Run anyway, exit 0.
- Commit 1 = 63e7e0c1 `fix(cn): retire a failed query's parked output instead of wiping every query's`.

### Commit 2

- Tests first (spec 6.2 `cancel_query`, 6.3 both exchange tests, 6.4 the five commit-2 rows): red = 23
  compile errors naming exactly the missing surface (`is_retired`, `retire_receiver`, `RETIRED_CAPACITY`,
  `cancel_query`, `RetireTrigger::Cancel`, the `PPlanFragmentCancelReason` import) plus one test bug of
  mine (`let key = key(10, 7)` shadowed the helper before `key(11, 7)`; fixed by binding `other` first).
- Implementation: `RetireTrigger::Cancel(String)` (+ `cancel:<NAME>` Display), `ResultStore::cancel_query`,
  `LocalExchange::{retire_receiver, is_retired}` with the 1024-entry HashSet+FIFO, `cancel_plan_fragment`
  teardown exactly as spec 4.4g (QUERY_FINISHED/LIMIT_REACH skip `cancel_query`; dummy `(0,0)` skips
  `retire_receiver`), late-frame refusal in `handle_transmit_packed` placed after the batch is built and
  before the debug/eos log lines, `process_inline` shared by `exec_single_attachment` and the batch loop
  (records `fail_fragment` on any inline `Err` carrying both ids). One compile fix: `as_str_name` takes
  `&self`, so `map_or(.., |code| code.as_str_name())`.
- No-engine suite: 175 passed, 0 failed (167 + 8 new).
- Docs per spec 4.7: DEMO.md bullet, tpch/README.md caveat, QUERY-DEVIATIONS.md scope line, runbooks
  reworded to "cannot abort a running fragment" (RESTART_CMD advice kept); 2NODE-REPLICATE's lease-leak
  paragraph moved to past tense.
- Not added (scope): a `push_sender` refusal for a retired receiver. A local sender of a cancelled query
  that parked *before* the cancel but whose CN-side `push_sender` lands *after* `retire_receiver` leaves
  one small `sources` entry in the rendezvous (no GPU memory, no lease: the parked output itself is
  dropped by the fire-and-forget `RetireQuery`). Worth a 3-line follow-up if the leftover ever shows.
- Engine-linked suite run 2 (release, GPU 1, `parked-bookkeeping-gpu-run2.log`): 187 passed, 0 failed,
  4 ignored, no warnings. fmt --check and clippy (`--all-targets --no-default-features -D warnings`) clean.
  Pre-commit exit 0 (all hooks skipped by the experimental/ exclusion).
- Commit 2 = 0f4b1c19 `feat(cn): cancel_plan_fragment tears down the cancelled query on this CN`.

## Result

Branch `fix/parked-bookkeeping` = 45dab3be + 63e7e0c1 + 0f4b1c19. Working tree clean; nothing pushed.
Diff: commit 1 = 7 files, +1667/-206 (of which ~530 is `parked_registry.rs` incl. its 9 tests and ~600 is
tests in `compute_node_service.rs`/`engine.rs`); commit 2 = 10 files, +724/-48 (6 doc files, ~450 test lines).

Tests run (all green):
- CI trio: `cargo fmt --check`, `cargo clippy --all-targets --no-default-features -- -D warnings`,
  `cargo test -p sirius-starrocks-cn --no-default-features` (175 lib tests + 9 translator + 0 bin).
- Engine-linked: `cargo test --release -p sirius-starrocks-cn` with CUDA_VISIBLE_DEVICES=1 (187 passed, 4 ignored
  = the pre-existing debug harnesses); includes the four new `engine::tests` and the two guard tests.
- `cargo check --release -p sirius-starrocks-cn --all-targets` (engine features) warning-free.

What is left (not this role's):
- SF1000 arms A/B/C (spec 8) on the orchestrator's cluster; arm D on the integration sweep (DECISIONS.md 4).
- `cnlog_extract.py` counters `skips=`/`retired=` (spec 8 tooling, scratchpad-side, orchestrator).
- Fix 4a rebases onto this branch (INTEGRATION.md 2.3): `release_sources` already matches `SenderSource`
  non-exhaustively (`_ => 0`), `retire_receiver` removes every `sources` entry of the cancelled instance.
- Follow-up candidate (not in spec): refuse `push_sender` for a retired receiver (see the scope note above).
