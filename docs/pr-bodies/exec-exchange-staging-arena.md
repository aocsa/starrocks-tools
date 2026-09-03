<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** Sirius can already split a query into fragments and hand batches from one to the
next, but only inside one process (`Fragment::relay_from`, #1481). With one compute node per GPU, a
fragment boundary has to cross a process and a device, so the transport needs a stable device
address to write into and read from. That address cannot live in the RMM pool. UCX's `cuda_ipc`
path cannot export `cudaMallocAsync` stream-ordered pool allocations and silently degrades ~220x
to staged host copies. The bytes stay correct, no error is raised, and nothing but a bandwidth
measurement notices. That failure mode is why I made this a second, dedicated device allocator
outside every RMM/cudf pool instead of a pool tag.

**What changed.** One reviewable unit, a pure allocator and its tests.

- `sirius::exec::exchange_staging_arena`, in `src/include/exec/exchange_staging_arena.hpp` and
  `src/exec/exchange_staging_arena.cpp`. It is one plain `cudaMalloc` device region, and
  `from_env()` sizes it from `SIRIUS_EXCHANGE_STAGING_BYTES`. Unset means no arena, and `require()`
  then turns every staging call into a loud error that names the variable instead of a slow path.
  Leases come from an address-ordered, coalescing free list under a mutex. Because `release` merges
  forward and backward, released space is reusable in any release order, there is no bump head and
  no drift, and capacity bounds concurrently live bytes rather than lifetime totals. Every lease is
  256-byte aligned and contiguous, which makes it a valid RDMA target and a valid `cudf::unpack`
  source. When the arena runs out, the error reports total free and largest free block, whose gap
  is the external fragmentation, and names the env var. A request larger than the whole arena is a
  different mistake than a full one, so it gets its own error rather than being folded into the
  exhaustion path. `lease` also guards `align_up` overflow, because a peer will supply that length
  over the wire once a transport sits on top. The constructor logs the granted size and the
  destructor logs peak live bytes plus any leaked leases. Those two lines are the only sizing
  feedback for a slab that no pool accounting sees.
- Opt-in fabric path. `SIRIUS_EXCHANGE_STAGING_ARENA=fabric` swaps `cudaMalloc` for a CUDA
  driver-API VMM allocation: `cuMemCreate` with `CU_MEM_HANDLE_TYPE_FABRIC`, then
  `cuMemAddressReserve`, `cuMemMap` and `cuMemSetAccess`. This is the first use of the driver API in
  Super Sirius proper. It links through the `CUDA::cuda_driver` dependency `simpatico` already
  exports, so no CMake change. You only need it when peers live on different hosts. `cudaMalloc`'s
  IPC handle is node-local, and a cross-host exchange over it measured 0.32 to 0.43 GB/s as a host
  bounce, against the 765 GB/s a standalone test program measured for a fabric handle over MNNVL on
  a two-host GB200 pair. The fabric path needs a live IMEX domain and
  `/dev/nvidia-caps-imex-channels`; without them `cuMemCreate` fails loudly at bring-up. The tests
  do not exercise this path, since there is no MNNVL/IMEX CI runner. The default `cudamalloc` path
  is the one every single-host deployment uses.
- `CMakeLists.txt` adds the source to `EXTENSION_SOURCES` and the test to `TEST_SOURCES`.
- `docs/super-sirius/configuration.md` gains an "Exchange Staging Arena" subsection that documents
  both environment variables next to the other `SIRIUS_*` env-var docs, plus a Key Files row.

**Configuration changes.** Two new process-environment variables, read once at bring-up.
`SIRIUS_EXCHANGE_STAGING_BYTES` takes the byte suffixes `sirius::yaml::parse_bytes` accepts. Unset
means no arena, and `0` or an unparsable value is rejected. `SIRIUS_EXCHANGE_STAGING_ARENA` is
`cudamalloc` by default, or `fabric`. Neither is a `sirius_config` YAML key or a `SET` variable.

**Tests.** 13 Catch2 cases tagged `[staging_arena]` in `test/cpp/exec/test_exchange_staging_arena.cpp`.
All of them need a GPU, since the constructor calls `cudaMalloc` and the cases use
`cudaPointerGetAttributes`, `cudaMemset` and `cudaMemcpy`. The cases that matter most:

- ARENA-8 asserts `free + live == capacity` after every operation.
- ARENA-10 runs 10,000 out-of-order lease/release cycles with 8 leases live and asserts no drift.
  A bump allocator exhausts the same 1 MiB arena within a few hundred cycles.
- ARENA-7 pins that a never-released lease blocks only its own block.
- ARENA-9 pins forward and backward coalescing.
- ARENA-11 stamps and reads back device bytes to prove live leases are disjoint.
- ARENA-12 runs 8 threads x 2000 iterations.
- ARENA-13 pins the `align_up` overflow guard.
- ARENA-3 pins that oversize and exhausted are two distinct errors, ARENA-4 pins misuse, and
  ARENA-5 pins the unconfigured message.
- ARENA-6 covers `from_env`. It sets and unsets the env var process-wide, and an RAII guard
  restores the unset state even on assertion failure.

**How I tested it.** On a GB200 box (aarch64) I did a full release build with pixi, ran the Catch2
tag `[staging_arena]` on a GPU, where all 13 cases pass, and checked the new configuration doc with
rumdl. Every one of those tests needs a GPU, so CI does not run them, and the fabric path has no test
at all since there is no MNNVL/IMEX runner.

**Intentionally not handled.** There is no in-tree consumer yet. Nothing on `dev` constructs the
arena or takes a lease. Three pieces follow in the `ffi` stack on `stacked/ffi-exchange-staging`,
gated on this PR: the Fragment FFI layer that packs a batch straight into a lease with
`cudf::chunked_pack` and unpacks on arrival, the `StagingArena` handle for embedders, and the Rust
bindings. The transport and compute-node wiring follow that. Two design questions stay open for
the follow-ups: whether the two env vars become YAML keys, and whether the arena's bytes get
exposed to pool accounting. Supersedes the arena portion of the old draft #1644; its remaining
pieces are re-cut as separate PRs.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
Supersedes the exchange-staging-arena part of draft #1644, which is being closed and re-cut.
Refs #1481, the in-process `Fragment::relay_from` hop that this arena lets cross a process boundary.
