# 04 — Throttle host/GPU memory bandwidth by scheduling memcpys between plan fragments

Design only. Nothing here is implemented. Every `file:line` is against
`/home/prestouser/aocsa/sirius-stacks-wt/sot` (git `aocsa/feat/pin-table-cn` @ `d24f02c4`);
paths are relative to that checkout. `~/.claude/plans/starrocks-sirius-perf/01-trace-schema.md`
(agent 1) did not exist when this was written, so no trace-event names are cited from it.

Two existing plans own adjacent ground and are referenced, not re-proposed:
PLAN-01 copy-out on arrival (`notes/OPEN.md:22`, `notes/2026-08-20-rtx-sf500/PLAN-01-copy-out-on-arrival.md`)
and PLAN-09 exchange backpressure (`notes/OPEN.md:30`, `notes/2026-08-20-rtx-sf500/PLAN-09-exchange-backpressure.md`).
PLAN-09 §4.1 already establishes the fact this design leans on: the send side holds exactly one
export lease at a time per CN.

## 1. Copy inventory

### 1.1 One CN (1 GPU): sender fragment → receiver fragment → FE

| # | Hop | Copy? | Where |
|---|---|---|---|
| A1 | Sender's streaming sink with ONE destination pushes the batch handle into the output repo | no — `shared_ptr` handoff | `src/op/sirius_physical_streaming_sink.cpp:130-135` |
| A2 | Receiver `relay_from`: `pull` from the parked sink, `push` into the input stream | no — pointer handoff; `batch_stream::push` does `_repo->add_data_batch(std::move(batch))` | `src/sirius_ffi.cpp:747-757`; `src/exec/stream_session.cpp:105-108,120-124`; `src/op/sirius_physical_streaming_source.cpp:71-74`; `src/exec/batch_stream.cpp:41-53`. The engine comment says it outright: "Nothing is converted, written, or copied" (`experimental/starrocks/src/engine.rs:588-589`) |
| A3 | Result fragment: GPU batch → host (`clone_to<host_data_representation>`) | **D2H**, one per result batch | `src/op/sirius_physical_result_collector.cpp:147-198` |
| A4 | Host table → DuckDB `DataChunk` → `ColumnDataCollection::Append` | host→host | `src/op/sirius_physical_result_collector.cpp:223-242` |
| A5 | `result_to_arrow`: DuckDB `ResultArrowArrayStreamWrapper` converts chunks to Arrow C arrays | host→host | `src/sirius_ffi.cpp:971-983`; `duckdb/src/common/arrow/arrow_converter.cpp:19` |
| A6 | arrow-rs FFI import of the stream | no — wraps the C buffers | `rust/crates/sirius/src/lib.rs:213-220,344-356` |
| A7 | MySQL text encode of every cell into `Vec<u8>` rows | host→host | `experimental/starrocks/src/result_encoder.rs:26-52`, called from `experimental/starrocks/src/compute_node_service.rs:951-952` |
| A8 | `fetch_data`: thrift `to_binary()` then brpc attachment | host→host, twice | `experimental/starrocks/src/compute_node_service.rs:454-462` |

Bytes for the two shapes: q06 result = 1 row × 1 DOUBLE (8 B payload); q01 result = 4 groups ×
~10 columns (~400 B). A3–A8 each move at most a few hundred bytes. There is **no device memcpy
between fragments on one GPU**; the only GPU copy on the 1-GPU path is A3, and for q01/q06 it is
one 8–400 B D2H plus its stream sync.

### 1.2 N CNs: sender on CN-A → receiver on CN-B (per destination, per batch)

| # | Hop | Copy | Where |
|---|---|---|---|
| B0 | Hash fan-out at the sink: `cudf::hash_partition` gathers the whole output into a reordered table, then each partition slice is copied again into its own `cudf::table` | **2 × D2D over the full sender output**, on every CN, for local and remote destinations alike | `src/op/sirius_physical_streaming_sink.cpp:160-182`; `src/op/partition/gpu_partition_impl.cpp:73-79` (gather 1), `:98-106` (gather 2); cost model `sirius_physical_streaming_sink.cpp:193-194` |
| B0' | Broadcast fan-out: outputs 1..N-1 each get a deep `clone` | (N-1) × D2D | `src/op/sirius_physical_streaming_sink.cpp:137-158` |
| B1 | `export_packed`: lease `total + 8 MiB` in the local arena, `cudf::chunked_pack` gathers into the lease, `stream.synchronize()` | D2D (the pack IS the staging copy — `src/include/sirius_ffi.hpp` says "no extra copy"); legacy default stream | `src/sirius_ffi.cpp:763` (`kPackChunkBytes = 8 MiB`), `:807-809`, `:819`, `:824-826`, `:836` |
| B2 | `rpc_request_lease(peer, batch.len)` → peer leases exactly `align256(len)` | no data; 1 brpc RTT | `experimental/starrocks/src/nixl_transport.rs:712,887-904`; `compute_node_service.rs:513-542,1203-1207`; `src/exec/exchange_staging_arena.cpp:212-240` |
| B3 | nixl WRITE lease→lease, polled to DONE with `yield_now` | **D2D over NVLink / cuda_ipc** | `nixl_transport.rs:713-719,796-835` |
| B4 | `rpc_transmit` (pack metadata in the attachment) + one more frame for eos | host; 2 brpc RTTs per destination + 1 per batch | `nixl_transport.rs:724-741,765-780,907-916`; receiver `compute_node_service.rs:643-712` |
| B5 | Receiver `push_packed`: `cudf::unpack` (view, no alloc) then `cudf::table(unpacked, …)` deep copy into the RMM pool, `stream.synchronize()`, release lease | **D2D**, on the engine thread, before the receiver's `run()` | `src/sirius_ffi.cpp:870,911-912`; `engine.rs:626-654` (release at `:646`) |
| B6 | Sender releases its local lease after the frame is acknowledged | no data | `nixl_transport.rs:745-758` |

Engine-thread round trips per destination: `batches + 1` `ExportNext` (the last returns `None`,
`nixl_transport.rs:706`; served at `engine.rs:318-321`) plus one `DropParked` (`:781`; `engine.rs:322-325`).

**Bytes per hop, q06 shape** (1 partial row per CN; measured `bytes=64` in the nixl log,
`notes/2026-08-05-multi-cn-nixl/TWO-CN-NIXL-DEMO.md:170`): B0 = 2 × 64 B (skipped when the
destination count is 1, `streaming_sink.cpp:130`); B1 lease = 8 MiB + 256 B for 64 B of payload;
B2 peer lease = 256 B; B3 WRITE = 64 B; B5 copy = 64 B. Fixed cost dominates: 3 brpc RTTs, 2 engine
round trips, 1 nixl post/poll, 2 `stream.synchronize()`.

**Bytes per hop, q01 shape** (4 groups hash-partitioned over N CNs): estimated 1–4 KiB packed per
destination (4 rows × ~12 buffers incl. two VARCHAR key columns, each buffer padded by cudf pack);
not measured in the notes — the nearest logged datum is a 19008 B small-table broadcast
(`notes/2026-08-06-tpch-features/PARTITIONED-OUTPUT-PLAN.md:139`). Destinations whose partition is
empty get no batch frame, only eos (`streaming_sink.cpp:179`). Same fixed costs as q06 plus B0.

For a large intermediate (q14 SF500: 948 MB/destination, `compute_node_service.rs:1113`) the
device-bandwidth bill per sender byte is B0 (2×) + B1 (1×) + B3 (1×) + B5 (1×) ≈ 5 passes.

## 2. Scheduling unit and where the counter lives

**Unit:** one `StagedBatch` (`experimental/starrocks/src/fragment_executor.rs:57-70`) travelling to
one destination, i.e. one sender-side lease alive from `arena.lease` (`sirius_ffi.cpp:819`) to
`staging_release` (`nixl_transport.rs:746`). Its size is `len` (payload) — known only after
`export_packed` returns, because `chunked_pack::create` computes it (`sirius_ffi.cpp:809`).

Today the drain loop is serial on the single `nixl-transport` thread (`nixl_transport.rs:352,
395-419`; one drain at a time, `:215-217`), so the outstanding-export count is already exactly 1
per CN. An inflight cap therefore adds nothing until drains become concurrent; the **rate/bandwidth
slice is the knob that changes behaviour now**.

**Struct:** a new `CopyBudget { inflight: Semaphore-like, bucket: Mutex<TokenBucket>, cv }` held
as `Arc<CopyBudget>` in `ServiceCore` (`compute_node_service.rs:178-199`) and handed to
`NixlTransport::start` next to the executor (`nixl_transport.rs:341-345`) so `TransportState`
(`:697`) can admit. Sharing through `ServiceCore` matters because the receive-side copy (B5) is
accounted from a different thread (engine thread today; the `transmit_packed` worker after PLAN-01
§3.3).

**Admit / release points:**

| Copy | Admit | Release / debit |
|---|---|---|
| B1+B3 (sender) | `TransportState::send_fragment`, immediately before `export_packed_next` (`nixl_transport.rs:706`): take one inflight slot; wait until the bucket is non-negative | after the local `staging_release` (`:746-758`): return the slot; debit `batch.len` (debit-after, see §3) |
| B5 (receiver, today) | none — the engine thread must never wait (§3 H1) | debit `batch.len` in the push loop (`engine.rs:645-653`), or by the dispatch worker before `executor.run` (`compute_node_service.rs:1082`) summing `remote_inputs[*].len` |
| B5 (receiver, after PLAN-01) | in `handle_transmit_packed` before the copy-out (`compute_node_service.rs:701`) — safe: the worker holds only a lease in its own arena and no engine call | after the copy returns |
| B0/B0' (sink fan-out) | not schedulable from the CN — it runs inside `fragment.run()` on pipeline task threads (`streaming_sink.cpp:125-183`); model it as a fixed 2× (hash) or (N-1)× (broadcast) D2D per sender output | — |
| A3 (result D2H) | not gated (bytes are trivial for q01/q06) | debit for the trace only |

The bucket is per process (per CN = per GPU). A host-copy bucket would attach to A7
(`compute_node_service.rs:951`) and to H2D reads of host-pinned tables (`PinTier::Host`,
`fragment_executor.rs:72-79`); neither is inter-fragment, and on q01/q06 both are ≤ 1 KiB, so this
plan defines the host knob but expects it to stay at "unlimited".

## 3. Delay, never drop: ordering, threads, deadlocks

**Why a blocked export drops nothing.** Waiting before `export_packed_next` leaves the batch parked
in the sender's `ParkedOutput` on the GPU (`engine.rs:43-46,683-689`); nothing is consumed until
`export_packed` pulls it (`sirius_ffi.cpp:779`). The receiver cannot start early: `take_ready`
requires every remote sender's eos (`local_exchange.rs:248-313`, completeness `:269-279`), and eos
is the last frame of the drain (`nixl_transport.rs:765-780`). So the receiver's `run()` slides
later by exactly the accumulated wait — the "next fragment delayed" semantics are structural.

**Per-(exchange, sender) ordering.** `seq` is assigned after admission, inside the same loop
(`nixl_transport.rs:702,731,760`), and all frames of one destination are issued by one thread in
order (`:221-224`), so the receiver's gap check (`local_exchange.rs:182-199`) is unaffected. If
drains ever become concurrent across destinations, the budget must hand out slots FIFO per
destination; cross-destination order is not required (the check is keyed per (exchange, sender),
`local_exchange.rs:94`).

**DrainTicket order.** Tickets are posted FIFO and joined after local receivers are dispatched
(`compute_node_service.rs:1121-1142,276-298`); `SenderDrains::join` waits for all of them
(`:111-120`). A throttle lengthens the sender's `exec_plan_fragment` RPC, which stays open until
the join (`:268-270`). The wait holds no lease and no RPC (admission is before `rpc_request_lease`,
`:712`), so the peer's `SIRIUS_CN_RPC_TIMEOUT_SECS` (`tunable.rs:37-42`) is not at risk; the FE's
query timeout is (§5 R2).

**Engine thread.** `ExportNext` and `Run` are serialised on one thread (`engine.rs:277-355`); the
drain calls in via `engine_call` (`:723-738,785-787`). A throttled transport thread simply asks
later, which frees the engine thread for other fragments — the overlap the demo in §4 looks for.
But the receiver's own B5 copies run on that thread inside `run_fragment_inner` before `run()`
(`engine.rs:626-654`), so today a receiver already pays its copy-out serially; sender throttling
adds to that, it does not overlap it.

**Deadlock hazards.**
- H1 (real): if the engine thread *waited* for a token while the transport thread held one and was
  blocked in `export_packed_next` (which needs the engine thread), both stall forever. Rule: the
  engine thread only debits, never waits. Same rule PLAN-01 §3.3 applies to the copy itself.
- H2: waiting after `rpc_request_lease` would pin a peer lease for the wait and, with PLAN-09's
  throw-on-exhaustion arena (`exchange_staging_arena.cpp:245-256`), could fail other queries. Gate
  before export, hold nothing while waiting.
- H3: a burst smaller than one packed batch stalls forever under classic token semantics. Use
  debit-after: admit when balance ≥ 0, subtract `len` on release, let the balance go negative;
  refill at the rate. A batch larger than the burst then costs `len / rate` of wait *after* it, not
  an infinite wait before it.
- H4: a bounded wait (`SIRIUS_CN_MEMCPY_MAX_WAIT_MS`) that fails the drain loudly, not a silent
  bypass — mirrors PLAN-01 §4.2's argument for `SIRIUS_CN_EXCHANGE_COPY_WAIT_MS`.
- H5: `write_and_wait` spins with `yield_now` (`nixl_transport.rs:831`); the throttle wait must be
  a condvar/timed sleep so a throttled CN does not burn a core while "idle".

## 4. Seeing it work locally

Cluster: two CNs on one host (`experimental/starrocks/benchmarks/cn-2host.sh:176-188` writes
`/tmp/cn-<host>-<i>.log`; the 8-GPU variant is `cluster8.sh:67-68`). Run q06 and q01 at SF1
against the DuckDB oracle so a dropped row is caught (`bench/gb200-8gpu/sweep.sh`).

Knobs for the demo: `SIRIUS_CN_MEMCPY_INFLIGHT=1` (equal to today's serial drain — the check is
"no timing change, counter never above 1") and `SIRIUS_CN_MEMCPY_RATE_BYTES_PER_SEC=64
SIRIUS_CN_MEMCPY_BURST_BYTES=64`, which makes every q06 64-B export wait ~1 s after the previous
one and every q01 ~2 KiB export ~30 s (use 1024 B/s for q01). `RUST_LOG=sirius_starrocks_cn=debug`
(default filter `main.rs:726-733`; spans log busy/idle on close, `:732`).

Log lines, in the order they should appear on the sending CN:
1. `translated StarRocks plan fragment` (`compute_node_service.rs:1355-1359`) — sender start.
2. new `memcpy budget admit slot=.. len=.. waited_ms=.. inflight=..` / `... release` — the wait.
3. `transmitted batches via nixl stream_id= sender_id= dest= batches= bytes=`
   (`nixl_transport.rs:782-789`) — its delay from (1) grows by the summed waits; `bytes` unchanged.
On the receiving CN: `received remote exchange frame … batch_bytes=` (debug,
`compute_node_service.rs:693-700`) spaced by the wait; `received remote batches`
(`engine.rs:658-663`) and `declared input stream cardinality` (`:555`) with the same row count as
the unthrottled run; result equal to the oracle. Overlap: the receiving CN's own local sender
logs `relayed native batches across a fragment boundary` (`engine.rs:614-619`) while the peer's
(3) has not yet appeared — the engine thread is running a fragment while the peer is throttled.

nsys: `nsys profile -t cuda,nvtx,osrt --cuda-memory-usage=true -o cn0 <CN_BIN> --gpu-device 0 …`
then `nsys stats --report cuda_gpu_trace`. Sender: the `chunked_pack` gather kernel (cudf
`contiguous_split` family) writing the arena on `sirius-engine` (`engine.rs:154`), followed after
the wait by the UCX D2D copy for the WRITE on the `nixl-transport` thread (`nixl_transport.rs:352`).
Receiver: per-buffer `cudaMemcpyAsync` D2D from the `cudf::table` copy ctor (`sirius_ffi.cpp:911`)
then `cudaStreamSynchronize` (`:912`) on `sirius-engine`. The throttle widens the gap between the
pack kernel and the WRITE copy; kernel and memcpy counts stay identical. On a 1-CN run the only
memcpy in the whole inter-fragment window is the result D2H (`result_collector.cpp:180-193`).

## 5. Knobs, defaults, risks

All in the `tunable.rs` registry (`Knob` at `tunable.rs:109-114`; rejected-not-clamped, logged by
`resolve` at `:259-284`), `SIRIUS_CN_` prefix like `SIRIUS_CN_NIXL_XFER_TIMEOUT_SECS` (`:49-54`).

| Knob | Default | Range | Meaning |
|---|---|---|---|
| `SIRIUS_CN_MEMCPY_INFLIGHT` | 1 | 1–64 | outstanding packed exports (sender leases) per CN; 1 = today |
| `SIRIUS_CN_MEMCPY_RATE_BYTES_PER_SEC` | 0 (unlimited) | 0–2^40 | device-copy bytes/s admitted through B1/B3 (+B5 debit) |
| `SIRIUS_CN_MEMCPY_BURST_BYTES` | 64 MiB | 4 KiB–2^40 | bucket capacity; debit-after semantics (§3 H3) |
| `SIRIUS_CN_MEMCPY_MAX_WAIT_MS` | 30000 | 1–600000 | fail the drain loudly past this; must stay under the FE query timeout |
| `SIRIUS_CN_HOST_MEMCPY_RATE_BYTES_PER_SEC` | 0 | 0–2^40 | reserved for A7 / host-pinned scans; unused by q01/q06 |

Risks: R1 engine-thread deadlock if any debit turns into a wait (H1). R2 the sender's RPC and the
FE's query timeout absorb the wait (`compute_node_service.rs:268-270`; PLAN-05 notes the FE
`query_timeout` is still 300 s). R3 arena interaction before PLAN-01: a slower sender means the
receiver's `take_ready` fires later, so *other* senders' leases into that receiver sit longer
(PLAN-09 §4.2 retention) — net arena effect is ambiguous until PLAN-01 lands. R4 the budget is
per CN; it cannot see NVLink contention from another CN pair, so a "reserved slice" is a per-GPU
egress cap, not a fabric reservation. R5 B0/B0' are not schedulable and must be modelled as fixed
multipliers. R6 the simulation must treat `INFLIGHT=1, RATE=0` as the measured baseline.

## 6. 1-GPU tax candidates vs scale-out only

- **1-GPU (q01/q06): nothing bandwidth-shaped.** A1/A2 are pointer handoffs; A3–A8 move 8–400 B.
  The 1-GPU inter-fragment tax is fixed latency — two serialized engine-thread `Run`s, DuckDB
  planning in `build` (`sirius_ffi.cpp:607`), the result collector's D2H + sync, four host
  re-encodes — and a memcpy throttle models none of it. Attach the throttle to A3 only for
  completeness; expect zero effect.
- **Scale-out only:** B0/B0' (2× or (N-1)× D2D of the full sender output on every CN, the largest
  device-bandwidth item and the only one that also hits the *local* destination), B1 pack, B3 WRITE,
  B5 copy-out, plus the two `stream.synchronize()` barriers on the legacy default stream
  (`sirius_ffi.cpp:836,912`; PLAN-01 §3.3 on PTDS being off). For q01/q06 these are latency
  (3 RTTs + 2 engine round trips + 8 MiB lease slack per 64 B), not bandwidth; for q14-class stages
  they are ~5 device passes per byte and the real throttle target.
