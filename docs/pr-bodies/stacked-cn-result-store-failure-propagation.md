<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 2 of the cn stack; base `stacked/cn-cluster-bringup`.

The failure this removes. On the source branch (`feat/pin-table-cn`) a multi-fragment query runs its result fragment on one CN and its intermediate fragments elsewhere. When an intermediate fragment fails (a relay guard fires, a sender never delivers), nothing tells the result instance the FE is polling. The `ResultStore` only knows `Pending` and `Drained`, so `fetch_data` keeps answering not-ready and the FE waits out its 600 s query timeout, then reports a timeout with no cause.

What changed, all in `experimental/starrocks/src/result_store.rs`:

- One `StoreState` behind the store's single mutex. It holds the fragment states plus two per-query maps, the result instances reserved for each query and the first failure recorded for each query.
- `reserve(id, query_id)` marks a result fragment `Waiting` and ties it to its query. If that query has already failed, the reservation lands as `Failed` at once, so the very first poll reports the cause.
- `fail(id, cause)` marks one fragment failed. `fail_query(query_id, failed_id, cause)` also marks every reserved result instance of that query with `fragment instance <failed_id> failed: <cause>` and records the failure at query level. First failure wins; later ones are usually downstream echoes.
- `cancel(id, reason)` turns a still `Waiting` entry into a failure so a long-poll returns. Delivered, drained, failed and unknown entries are left alone.
- `wait_ready(id, timeout)` blocks on a Condvar until the entry leaves `Waiting`, then polls. A timeout is a loud `Failed`, not an empty reply. The FE's ResultReceiver counts every packet, so a not-ready reply burns a sequence number and the rows that follow arrive stale ("expect=1, receive=0").
- `poll(id)` is the non-blocking state machine and returns `FetchOutcome::{Rows, Failed}`. `insert` now refuses to overwrite a `Failed` entry, so late rows cannot mask a recorded failure.

Why failure is attributed per query. The FE dispatches the result fragment and the intermediate fragments independently and in no fixed order. Keying failures by fragment instance alone loses the case where the intermediate fails before the result fragment has reserved. Keying by query lets a late reservation find the failure waiting for it.

What is additive. Dev's `insert` keeps its signature and `compute_node_service.rs` is untouched. `take_next` keeps its call shape (the handler never names the return type) but now returns the rows-only `FetchProgress` struct, the renamed version of dev's old `FetchOutcome`, as a wrapper over `poll`. A `Failed` entry reads as `None` through it, so dev's handler still answers its "no buffered result" error rather than hanging. Nothing on this base records a failure, so that path is reachable only from tests until the dispatch layer moves `fetch_data` onto `wait_ready`. The source branch changed `FetchOutcome` from a struct to an enum in place, which would have broken dev's handler; the wrapper is the one deliberate departure from the source file. The methods only the dispatch layer calls carry a targeted `#[allow(dead_code)]` naming that PR.

**How I tested it.** On a GB200 box (aarch64) I ran the CI trio: cargo fmt, clippy with warnings as errors, and the workspace test suite without the engine feature. All 214 tests pass.

Not handled here: the dispatch worker that calls `reserve`, `fail_query` and `wait_ready` and removes the `take_next` wrapper (`stacked/cn-exchange-dispatch`); cancellation beyond `cancel()` marking a waiting entry; eviction of drained entries and the per-query maps (the existing TODO); `FragmentInstanceId::as_halves` and its `pub` visibility, which belong to the nixl agent tier layer.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Source branch: `aocsa/feat/pin-table-cn`
- Base: `stacked/cn-cluster-bringup` (layer 1 of the cn stack)
- Next layer: `stacked/cn-exchange-dispatch` wires these methods into the dispatch worker and `fetch_data`
