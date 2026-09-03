# Draft reply to morningman on #1590 (Doris `push_arrow` proposal)

Post as a comment on https://github.com/sirius-db/sirius/issues/1590. Facts checked on 2026-09-03 against `dev` 01613070,
cudf 26.06.1 (`include/cudf/interop.hpp`), `src/include/sirius_ffi.hpp`, `src/include/exec/{batch_stream,stream_session}.hpp`
and the `push_packed` body on `aocsa/feat/pin-table-cn`. Edit the two bracketed spots before posting.

---

Thanks for writing this up so concretely. The shape you propose is the one we had pencilled in, and your two questions moved
decisions we had left open. Here is how we plan to land it, and our answer on both.

## Two PRs, both additive

Arrow out already exists: `result_to_arrow` is in #1702, the Rust bindings layer of the ffi stack. Arrow in becomes two PRs. Neither
changes an existing `Fragment` or `Context` method, signature or lifecycle rule.

**1. `helper-arrow-host-import`, a fork PR against `dev`, engine helper only.** `src/helper/arrow_host_import.{hpp,cpp}` takes an
`ArrowSchema*` and an `ArrowArray*` (Arrow C Data Interface, host memory), imports them with
`cudf::from_arrow(ArrowSchema const*, ArrowArray const*, stream, mr)` and checks the result against a declared stream schema
before handing back a `cudf::table`. The check is the one `push_packed` already does, `sirius::get_cudf_type(declared.types[i])`
column by column, plus the normalizations you listed: decimal width picked by precision (32/64/128, cudf's negated scale), bool
bitmap to BOOL8, string offsets widened to INT64 where the reader needs it, and dictionary, large_list, timezone-aware timestamps and
128-bit integer shapes refused by name. A Catch2 round trip builds the input with `cudf::to_arrow_host`, imports it, and compares.
This PR has no FFI dependency, so it can start now, and you could test the import rules against it standalone.

We picked `cudf::from_arrow` over `from_arrow_host`. Both copy host buffers to the device, and cudf 26.06 ships the host-array
overload directly (`interop.hpp:684`). Using it means we never construct an `ArrowDeviceArray{ARROW_DEVICE_CPU}` ourselves, so
no nanoarrow struct definitions enter our tree and the one-definition-rule hazard that wrapper would carry goes away.

**2. `stacked/ffi-push-arrow`, the top layer of the ffi stack.** `Fragment::push_arrow(stream_id, sender_id, array_addr,
schema_addr)`, in the `uintptr_t` style of `result_to_arrow` so the header still needs no Arrow headers. The body mirrors
`push_packed` step for step: require `build()`, pick the GPU memory space, `acquire_stream()`, `make_reservation_or_null()`, import
through the helper, `stream.synchronize()`, `sirius::make_data_batch`, `session().push()`, and throw when `push` returns `false`
so a push after EOS never disappears silently. The H2D copy is mandatory for the reason you gave: the HOST tier is addressed by
offsets inside cuCascade-owned blocks and spill assumes it owns the memory, so pinning caller memory does not fit. Synchronize
before returning, the caller frees right after, and no Sirius thread ever calls back into host memory. The Rust half is
`Fragment::push_arrow(&mut self, &RecordBatch)` through `arrow_array::ffi::to_ffi`. Tests: to_arrow_host, push, run, compare via
`result_to_arrow` against the same rows read from a file, plus the threaded test described below. Roughly your 150 plus 230 lines.

`sender_id` stays explicit, as in your signature: several producers can feed one stream, and `close_input(stream_id, sender_id)`
stays the per-sender end-of-stream, idempotent as today.

## 1. Threading contract

Yes. We will document `push_arrow` as legal during `run()` from other threads, and we will ship that in the first cut rather than
store-and-forward. Your reading of the code matches ours. `batch_stream`'s producer side is marked "any thread" and is
mutex-protected, `stream_session::push` forwards to it and needs no DuckDB transaction, and the S1 admission-ordering contract
exists so that a consumer waking on `HAS_DATA` finds the batch that was pushed from elsewhere. The `Context` stays single-threaded
for everything else. The contract we will write into the header:

- `push_arrow` and `close_input` may be called from any thread once `build()` has returned, including while `run()` is blocking on
  another thread. They touch only the stream session and immutable post-`build()` state (the declared schemas). They never touch
  the DuckDB connection or the query lifecycle.
- Every other `Fragment` and `Context` method keeps today's single-threaded rule.
- The `Fragment` must outlive its producers. Destroying it while a producer is inside `push_arrow` is undefined, exactly as for any
  other object.
- A push after the stream ended throws. There is no backpressure yet: the queue is unbounded and a producer that outruns the query
  grows the GPU and host tiers. We will state that in the PR and add a bounded or blocking push together with the `start()/join()`
  split, where it belongs.

The proof is a test that starts `run()` on one thread, pushes the first batch only after execution has begun, pushes the rest with
pauses, closes the sender, and checks the result equals the pre-materialized run. If that test exposes a hole in the multi-shot
source (#836), we fix the engine rather than narrow the contract, because that source was designed to be fed while running. If
such a fix could not land in time, we would fall back to the store-and-forward first cut you offered, with the same signature, so
nothing changes on your side when the contract relaxes. The `start()/join()` split is a separate PR in this issue's scope and does
not change `push_arrow`: once it lands, the same call works between `start()` and `join()` from your producer thread.

## 2. Sequencing

T5b is #1644, our own umbrella draft. We are re-cutting it into small PRs against `dev` rather than landing it as one branch, so
nothing should race it; the pieces are what to stack on. The ffi stack that replaces the `stream/*` branches is open as
#1697 (transaction scope) and #1702 (Rust `Fragment` bindings), with the next layers being byte ranges on the Substrait plan, then
the staging arena FFI with `push_packed` and `declare_input_cardinality`, then `pin_table`. `push_arrow` goes on top of the
`push_packed` layer by construction: it reuses that layer's schema guard, memory-space and reservation code, `make_data_batch` and
the push-or-throw rule, and the two files it touches (`src/sirius_ffi.{hpp,cpp}`) are the ones that stack owns. #1644 is closed
with pointers once every piece it carried has a replacement.

Concretely: the helper PR can start today against `dev`. The `push_arrow` layer is drafted against the current tip of the stack and
rebased as layers merge; if you want to start on the Doris side before it lands, the signature above is final unless you object
to it here.

## What would help from your side

- The Arrow types you need first. We would start with the TPC-H set: BIGINT, DOUBLE, DECIMAL(15,2), DATE, VARCHAR.
- Whether one producer thread per input stream is the common case for you, or several senders per stream. It decides how much of
  the `sender_id` bookkeeping the first tests need to cover.
- [Optional: name who on the Sirius side is writing the two PRs and roughly when the helper PR opens.]

[Optional closing line.]
