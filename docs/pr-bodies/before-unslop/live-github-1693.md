## Description

**Motivation.** Sirius can already split a query into fragments and hand batches from one to the
next, but only in process (`Fragment::relay_from`, #1481). Running one compute node per GPU means a
fragment boundary has to cross a process and a device, so a transport must be handed a stable
device address to write into and read from. That address cannot live in the RMM pool: UCX's
`cuda_ipc` path cannot export `cudaMallocAsync` (stream-ordered pool) allocations and silently
degrades ~220x to staged host copies — the bytes stay correct, no error is raised, and nothing but
a bandwidth measurement notices. That failure mode is why this is a second, dedicated device
allocator *outside* every RMM/cudf pool rather than a pool tag.

**What changed** (one reviewable unit: a pure allocator and its tests).

- `sirius::exec::exchange_staging_arena` (`src/include/exec/exchange_staging_arena.hpp`,
  `src/exec/exchange_staging_arena.cpp`): one plain-`cudaMalloc` device region, sized from
  `SIRIUS_EXCHANGE_STAGING_BYTES` via `from_env()` (unset = no arena; `require()` turns every
  staging call into a loud, named error instead of a slow path). Leases come from an
  **address-ordered, coalescing free list** under a mutex: `release` merges forward and backward,
  so released space is reusable regardless of release order, there is no bump head and no drift,
  and **capacity bounds concurrently live bytes, not lifetime totals**. Every lease is 256-byte
  aligned and contiguous, so it is a valid RDMA target and a valid `cudf::unpack` source.
  Exhaustion reports total free vs. largest free block (the gap is external fragmentation) and
  names the env var; oversize requests are refused separately, and `align_up` overflow on a
  wire-supplied length is guarded. Construction and teardown log the granted size and the peak
  live bytes (plus leaked leases), the only sizing feedback for a slab no pool accounting sees.
- Opt-in fabric path: `SIRIUS_EXCHANGE_STAGING_ARENA=fabric` swaps `cudaMalloc` for a CUDA
  **driver-API** VMM allocation (`cuMemCreate` with `CU_MEM_HANDLE_TYPE_FABRIC`, then
  `cuMemAddressReserve`/`cuMemMap`/`cuMemSetAccess`; first use of the driver API in Super Sirius
  proper — it links through the `CUDA::cuda_driver` dependency `simpatico` already exports, no
  CMake change). It is only needed when peers live on **different hosts**: `cudaMalloc`'s IPC
  handle is node-local, and a cross-host exchange over it measured 0.32-0.43 GB/s (host bounce)
  versus 765 GB/s for a fabric handle over MNNVL on a two-host GB200 pair (standalone harness).
  Needs a live IMEX domain and `/dev/nvidia-caps-imex-channels`; `cuMemCreate` fails loudly at
  bring-up otherwise. **Not exercised by the tests** (no MNNVL/IMEX CI runner); the default
  `cudamalloc` path is the one every single-host deployment uses.
- `CMakeLists.txt`: the source in `EXTENSION_SOURCES`, the test in `TEST_SOURCES`.
- `docs/super-sirius/configuration.md`: an "Exchange Staging Arena" subsection documenting both
  environment variables next to the other `SIRIUS_*` env-var docs, plus a Key Files row.

**Configuration changes.** Two new process-environment knobs, read once at bring-up:
`SIRIUS_EXCHANGE_STAGING_BYTES` (byte suffixes per `sirius::yaml::parse_bytes`; unset = no arena,
`0`/unparsable rejected) and `SIRIUS_EXCHANGE_STAGING_ARENA` (`cudamalloc` default | `fabric`).
Neither is a `sirius_config` YAML key or a `SET` variable.

**Tests.** 13 Catch2 cases tagged `[staging_arena]` (`test/cpp/exec/test_exchange_staging_arena.cpp`),
all of which need a GPU (`cudaMalloc` in the constructor, `cudaPointerGetAttributes`,
`cudaMemset`/`cudaMemcpy`): ARENA-8 asserts `free + live == capacity` after every operation;
ARENA-10 runs 10,000 out-of-order lease/release cycles with 8 leases live and asserts no drift
(a bump allocator exhausts the same 1 MiB arena within a few hundred cycles); ARENA-7 pins that a
never-released lease blocks only its own block; ARENA-9 pins forward and backward coalescing;
ARENA-11 stamps and reads back device bytes to prove live leases are disjoint; ARENA-12 runs 8
threads x 2000 iterations; ARENA-13 pins the `align_up` overflow guard; ARENA-3/4/5 pin the three
distinct error paths (oversize, exhausted, misuse) and the unconfigured message; ARENA-6 covers
`from_env` (it sets/unsets the env var process-wide; an RAII guard restores the unset state even
on assertion failure). Run on the GB200 box (`presto-gb200-gcn-18`).

Verified: GB200 box (aarch64, CUDA 13.0): baseline dev release build exit 0; `pixi run make` incremental build at 1e61c16c exit 0 (386/386 steps, 0 errors); `./build/release/extension/sirius/test/cpp/sirius_unittest "[staging_arena]"` -> All tests passed (20291 assertions in 13 test cases), 0 failed/0 skipped; `pixi run --locked rumdl check docs/super-sirius/configuration.md` -> no issues.

**Intentionally not handled.** No in-tree consumer yet: nothing on `dev` constructs the arena or
takes a lease. The Fragment FFI layer that packs a batch into a lease (`cudf::chunked_pack`
straight into the lease) and unpacks on arrival, the `StagingArena` handle for embedders, and the
Rust bindings follow in the `ffi` stack (`stacked/ffi-exchange-staging`, gated on this PR); the
transport and compute-node wiring follow that. Making the two env vars YAML keys, and exposing the
arena's bytes to pool accounting, are open design questions left to the follow-ups. Supersedes the
arena portion of the old draft #1644 (its remaining pieces are re-cut as separate PRs).

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
Supersedes the exchange-staging-arena part of draft #1644 (being closed and re-cut).
Refs #1481 (in-process `Fragment::relay_from`, the hop this arena lets cross a process boundary).
