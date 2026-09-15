# Notes: 2-CN NIXL GROUP BY on this box

Box: AWS g7e.4xlarge, one RTX PRO 6000 in MIG, two `2g.48gb` instances as CUDA ordinals **0 and 1**.

## Three layers

```
StarRocks FE     owns SQL → fragments → destinations
Rust CN          park-then-send; NIXL WRITE of packed GPU bytes
Sirius engine    one fragment; packs/unpacks through a staging arena
```

Query:

```sql
SELECT region, SUM(amount)
FROM FILES("path"="file:///opt/dlami/nvme/tmp/sirius-2cn-e2e/sales/sales_*.parquet","format"="parquet")
GROUP BY region
```

The e2e **writes** those parquet files each run (same bytes as [`data/`](data/)). Tiny FILES() shards may all land on one CN; the other still runs the merge. A receive-only CN never WRITEs and does not log the bandwidth canary.

## Box facts that shape the hop

- Staging arena is `cudaMalloc`, never RMM / `cudaMallocAsync`. UCX `cuda_ipc` cannot export pool allocations and silently host-bounces (~200x).
- `UCX_TLS=cuda_copy,cuda_ipc,tcp,self`. Without `cuda_copy`, VRAM pointer detection fails.
- **CUDA IPC across MIG instances is not supported.** CN0 (MIG 0) → CN1 (MIG 1) takes UCX's host-bounce path. Correct, slower. The canary logs GiB/s and is **not a gate**.
- Default arena: `SIRIUS_EXCHANGE_STAGING_BYTES=1GiB`. Unset = no arena = remote routes refused at CN bring-up.
- Pin with `CUDA_VISIBLE_DEVICES=0|1` and `GPU_DEVICES=0,1`. Never `CUDA_VISIBLE_DEVICES=MIG-<uuid>` (cucascade NVML count fails).
- One CN per visible GPU: the NIXL agent registers the arena as CUDA device 0 of **that process**.
- `TOOLS_DIR=/home/ubuntu/sirius-wt/tools` must be exported. `cn-env.sh` otherwise looks for `tools/` next to the Sirius repo root, and this worktree is not under `sirius-wt/`.
- `NIXL_NO_STUBS_FALLBACK=1`. Without it a broken nixl link silently becomes a dlopen stub.
- `LD_LIBRARY_PATH` order: engine `.so`, then nixl, then UCX, then pixi `lib`. Pixi ahead of UCX shadows `libplugin_UCX.so`.

## Build gotchas

- Do **not** set `RUSTFLAGS`. It busts cargo's fingerprint cache and re-runs `nixl-sys` bindgen against the conda sysroot (`bits/timesize.h` missing).
- Do **not** `pixi run cargo` from `experimental/starrocks` (broken `.pixi` symlink on the worktree). Use the demo pixi `cn` env on `PATH` plus system `/usr/bin` first.
- Do **not** commit `.pixi`.
- After `pixi run make`, `ln -sfn sirius.duckdb_extension build/release/extension/sirius/libsirius.so`. The CN `DT_NEEDED`s `libsirius.so`.
- System `gcc`/`g++` and system `ld` (`PATH=/usr/bin:$PATH`) compile `nixl-sys`. Conda `ld` + system libc = GLIBC_PRIVATE undefineds.
- Cargo `THRIFT` binary: `/home/ubuntu/sirius-wt/demo/experimental/starrocks/.pixi/envs/cn/bin/thrift`.
- Packed hop control uses unpatched `PInternalService.transmit_chunk`. Do **not** apply `patches/nixl-exchange-proto.patch` for this path.

## Cluster layout the e2e starts

Isolated FE (query port 9030) + two CNs. Ports for CN *i* with `PORT_BASE=9100`, `PORT_STRIDE=10`:

| | CN0 | CN1 |
|---|---|---|
| GPU | 0 | 1 |
| heartbeat | 9100 | 9110 |
| thrift (BE) | 9101 | 9111 |
| brpc | 9102 | 9112 |
| http | 9103 | 9113 |
| starlet | 9104 | 9114 |

Never run two FEs (9030). Wrap GPU work with `gpu-lock.sh` so nothing else holds a MIG. Do not re-exec gpu-lock from inside the e2e script (deadlocks on the same flock).

## Pass criteria (from the e2e)

- Rows match DuckDB, relative 1e-6.
- `shipping packed exchange hop` with `bytes=N>0` on at least one CN.
- `received remote packed batches` with `batches=N>0` on the peer (or both CNs ship).
- `transmit_chunk` in CN logs; no `POST /exchange` / packed HTTP server.
- `nixl bandwidth canary` on every CN that shipped.

## Prebuilt pieces this box already has

| Piece | Path |
|---|---|
| NIXL | `$TOOLS_DIR/nvda_nixl` |
| UCX | `$TOOLS_DIR/ucx-install` |
| Packaged FE | `/home/ubuntu/sirius-wt/demo/experimental/starrocks/starrocks/output/fe` |
| mysql client | `/home/ubuntu/sirius-wt/demo/experimental/starrocks/.pixi/envs/client/bin/mysql` |
| Python (pyarrow, duckdb) | `/home/ubuntu/sirius-wt/base/.pixi/envs/default/bin/python` |
| Java 21 | `/usr/lib/jvm/java-21-amazon-corretto` |
| gpu-lock | `/home/ubuntu/sirius-wt/all22/gpu-lock.sh` (copy in this folder) |
