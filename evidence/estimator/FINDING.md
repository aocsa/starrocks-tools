# q13's reservation: traced to a hard-coded 16x heuristic in DENSE_COUNT_JOIN

Date 2026-09-09. Box: g7e.4xlarge, 2 CNs on 2 MIG 2g.48gb, SF500, 36 GiB pool per CN.

## The number, and where it comes from

Task telemetry (quent `Reserving` record) for the failing q13 task:

```
requested_bytes      73,611,595,136   (73.61 GB)
peak_estimate        73,611,595,136
input_basis           4,600,659,160   ( 4.60 GB)
bytes_to_materialize              0
operator             DENSE_COUNT_JOIN(4)
```

`src/op/sirius_physical_dense_count_join.cpp`, `no_history_peak_memory_estimate`:

```cpp
// Sparse execution: 16 is a heuristic expansion factor
auto const sparse_peak = saturating_add(allocation_floor, saturating_mul(16, stats.bytes));
return std::max({dense_peak, sparse_peak, minmax_peak});
```

16 x 4,600,659,160 + 1 MiB = 73,611,595,136 — an exact match to the telemetry. The estimate is
`sparse_peak`, and it is **1.90x the entire GPU pool**.

## Why it is inflated, not real

The estimator returns the **max over three mutually exclusive execution modes** (dense, sparse,
min/max). It cannot pick one because, in its own words, "the estimator has no key domain, so this
bound -- not the layout -- is what the two have in common". So it must assume the sparse path is
possible and reserve for it.

The task then **completed successfully on a 34.05 GB partial grant** (`Finalizing: success = true`),
i.e. under half the reservation it asked for. For this query the sparse path's worst case never
materialized.

## Consequence

The request is clamped to the pool maximum, so a single task books ~88-100% of the GPU. Downgrade
frees little or nothing (0 bytes on the first attempt), the pool reaches its limit, and the next
allocation to arrive fails. That allocation is typically small and belongs to an unrelated
component -- the exchange packer's 76 MB -- which is then blamed for pressure it did not create.

This also explains why two earlier attempts failed to fix q13:
- `BATCH_BYTES=512MiB` (WP1 P0 config-only): frames were never near the cap (max pack 76 MB), so
  frame size was never the cause.
- Wait-or-requeue at the reservation gate: the gate correctly declined to wait, logging "nothing in
  flight to free memory" -- the pool is held by the query's own data, not by a rival task that will
  finish and release.

## Options

1. **Decide the mode before reserving.** The dense-vs-sparse choice needs the key domain, which is a
   min/max reduction over the input. Computing it before the reservation would let the task reserve
   for the path it will actually take, instead of the worst of three.
2. **Reserve for dense, recover via the existing OOM reschedule path** if the sparse layout turns out
   to be needed. Cheaper than (1); relies on machinery that already exists.
3. **Revisit the 16.** It is documented as "a heuristic expansion factor" — an acknowledged guess,
   not a derived bound. Measuring the sparse path's real expansion on representative data would
   either justify it or replace it with something defensible.

Option 1 is the principled fix; option 2 is the small one. Both are strictly better than capping the
request, which would leave the estimate wrong and merely hide it.
