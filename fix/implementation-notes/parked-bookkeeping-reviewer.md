# Fix 2 adversarial review: per-query parked-output bookkeeping

Branch `fix/parked-bookkeeping` = 45dab3be + 63e7e0c1 + 0f4b1c19 in
`/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping`. Tree clean, nothing pushed. No code edited by me.

## What I re-ran (logs under `fix/review-pb/`)

- `cargo fmt -- --check`: clean.
- `cargo clippy --all-targets --no-default-features -- -D warnings`: clean.
- `cargo test -p sirius-starrocks-cn --no-default-features`: 175 lib + 9 bin passed, 0 failed (matches the implementer).
- `CUDA_VISIBLE_DEVICES=1 cargo test --release -p sirius-starrocks-cn` (engine-linked): 187 passed, 0 failed, 4 ignored
  (the pre-existing debug harnesses); all four new `engine::tests` and the two guard tests pass; no compiler warnings.

## Verified against the spec

- Gate 1 is the first statement of `run_fragment_inner` (engine.rs:496-505), so `run_fragment`'s lease sweep (:458-475)
  still runs on a refusal; the GPU test `a_failed_run_retires_only_its_own_query` proves `staging_lease(1024) == 0`
  after a refused run that carried a lease.
- Only three code paths mark/retire a query: the engine `Err` arm (engine.rs:376-378), `fail_fragment`
  (compute_node_service.rs:1091-1095, called from the worker `Err` arm :1068 and `process_inline` :789) and
  `cancel_plan_fragment` (:493, :503). Nothing else calls `mark`, `retire_query`, `cancel_query` or `retire`.
- FE semantics for retiring on QUERY_FINISHED/LIMIT_REACH: both are sent only after `resultBatch.isEos()` and only for
  multi-instance queries (`DefaultCoordinator.java:1022-1035`); a `cancel()` after all results are returned downgrades
  to QUERY_FINISHED (:1046-1050); a query that already failed is never cancelled twice (:1051-1053). So no legitimate
  run of a query can follow its retire, in either dispatch mode.
- `cnlog_extract.py` counts lines containing the substring `cancel_plan_fragment`; both new INFO lines still contain it,
  so acceptance check C4 (`cancels=` unchanged) stays measurable without tooling changes.
- Registry refactor is a move, not a change: `park`/`claim`/`peek`/`release` bodies mirror engine.rs:369-425, :559-566,
  :625-639, :697-717 at 45dab3be; `park` additionally refuses a duplicate inside one fan-out before inserting anything
  (the old code inserted the first copy before detecting the second).
- The `Retiring<E>` wrapper records instead of delegating `retire_query`; every production executor is `SiriusEngine`
  or `StubExecutor` directly (main.rs:209/214), no production wrapper swallows the new trait method.
- Both `.pre-commit-config.yaml` excludes `^experimental/.*`, so the hooks are vacuous here (implementer's note is right).

## Findings (none blocking)

1. minor, correctness window: `handle_transmit_packed` checks `is_retired` and then calls `push_remote_frame` under a
   separate lock (compute_node_service.rs:849, :886). A cancel that runs `retire_receiver` between the two re-creates the
   receiver's `sources` entry with the frame's lease, which nothing releases afterwards (later frames are refused, the
   receiver never becomes ready, the FE cancel already happened). Microsecond window against an FE RPC, but it is a
   process-lifetime arena lease on N CNs, the very class this fix removes. Fix: make `push_remote_frame` consult
   `retired` under its own lock and return a `Refused` outcome the caller turns into release + ack.
2. minor, redundancy: `process_inline` calls `fail_fragment` on every inline `Err`, including gate 4's own
   "query ... already failed on this CN" refusal (:789 after :1150-1163). Each refused arrival therefore re-runs
   `fail_query` (a new `Failed` entry under the straggler's id in the never-evicted `fragments` map) and queues another
   `RetireQuery` at the engine ("retire found nothing parked"). Also note `fail_query` overwrites Delivered result
   entries (pre-existing, result_store.rs:163-198), unlike `cancel_query`; reachable now via a straggler arriving after a
   USER_CANCEL/TIMEOUT `cancel_query`, harmless because the FE has stopped fetching. Fix: skip `fail_fragment` when
   `failure_of(query_id)` is already `Some`, or return a typed refusal from gate 4.
3. minor, test coverage: the `StagedLeases` RAII guard's `Drop` (:930-936) is never exercised with a lease in hand:
   `translation_failure_retires_the_slots_in_hand_and_releases_remote_leases` fails at the names check, which releases
   via the explicit `release_staged` arm (:1569-1576), not the guard; gate 3's `release_staged` is only exercised with
   empty inputs. A test that stages a remote frame and then fails translation (e.g. an untranslatable receiver plan)
   would pin the guard. The test's name says "translation failure" while its docstring admits it is the names check.
4. minor, known follow-up (implementer-noted): a local `push_sender` landing after `retire_receiver` leaves a small
   `sources` entry in the rendezvous (no GPU memory, no lease). Three-line fix in `push_sender`: refuse for a retired
   receiver.
5. minor, style/house rule: both commit bodies are 11 lines (rule: 3-10). Content is fine (what/why, tests, what is
   left, trailer exact).
6. minor, API hygiene: `RetireTrigger` is `pub` and appears in the public `FragmentExecutor` trait signature, but
   `lib.rs:74` does not re-export it, so an external implementor cannot name the type (only the default impl works).
   One-word fix in `lib.rs`.

## Not findings, considered and dismissed

- QUERY_FINISHED racing the transport's post-eos `drop_parked`: `AlreadyTornDown` returns Ok once; tested.
- Gate 2 dropping output of a fragment that finished after retire: intended; the `after_run` test hook is the right
  way to reach it (the spec's own test order would have hit gate 1 first).
- `retire(None)` on an unlabeled engine `Err` drops every unlabeled parked fragment: production runs are always
  labelled (`fragment_label`), test-only bucket.
- Cancel RPC never waits behind engine work: `retire_receiver` (exchange mutex), `staging_release` (arena handle),
  `retire_query` (mark + non-blocking send). Confirmed by reading; `engine_send` never `recv`s.
- Spec deviations 1-3 in the implementer notes are all justified; the extra INFO line for a cancel without a query id
  keeps the old log-count behaviour.

## Verdict

Approve. The measured SF1000 leftovers (q05/q08/q09/q17/q21 leftover runs, q16's 6.6 GB) are addressed by gates 1-4
and `fail_fragment`; the 15 passing queries take one hash lookup per fragment plus N cheap cancels at query end. The
findings are hygiene items that can land as a follow-up commit on the same branch before the PR is marked Ready.
