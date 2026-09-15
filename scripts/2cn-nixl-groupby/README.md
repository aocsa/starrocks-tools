# 2-CN FILES() GROUP BY over NIXL

Harness to **build** the Sirius engine and StarRocks compute node, **launch** an isolated FE + two MIG-pinned CNs, and **run** the packed-GPU shuffle e2e:

```bash
/home/ubuntu/sirius-wt/all22/gpu-lock.sh experimental/starrocks/tests/2cn_files_group_by.sh
```

That command is run from the Sirius worktree (see below). This folder is the notes, parquet fixture, and wrapper scripts so the same run can be reproduced on this box.

## Code

| | |
|---|---|
| Sirius repo | [github.com/aocsa/sirius](https://github.com/aocsa/sirius) |
| Branch | `feat/group-by-nixl-shuffle` (this box also has `claude/arrow-shuffle-nixl-gpu-8a2a50`, an earlier snapshot) |
| Worktree | `/home/ubuntu/sirius/.claude/worktrees/arrow-shuffle-nixl-gpu-8a2a50` |
| Query | [`query.sql`](query.sql) |
| Expected | east 18, west 27, north 14, south 9, midwest 4 ([`data/expected.tsv`](data/expected.tsv)) |
| Parquet | [`data/sales_0.parquet`](data/sales_0.parquet), [`data/sales_1.parquet`](data/sales_1.parquet) |

The live e2e script lives in Sirius. A snapshot is [`2cn_files_group_by.sh`](2cn_files_group_by.sh).

## One command (this box, already built)

```bash
cd /home/ubuntu/sirius/.claude/worktrees/arrow-shuffle-nixl-gpu-8a2a50
source /home/ubuntu/sirius-wt/env.sh
export TMPDIR=/opt/dlami/nvme/tmp
export TOOLS_DIR=/home/ubuntu/sirius-wt/tools
/home/ubuntu/sirius-wt/all22/gpu-lock.sh experimental/starrocks/tests/2cn_files_group_by.sh
```

Or from this folder:

```bash
./run-e2e.sh
```

Pass: DuckDB-equal rows, a non-empty packed hop (`bytes=N>0`) on one CN, matching `received remote packed batches` on the peer, `transmit_chunk` in the logs, and the bandwidth canary on every CN that shipped.

## Build from scratch

```bash
source ./env.sh
./build-engine.sh
./build-cn.sh
./run-e2e.sh
```

`env.sh` sets pixi, `TMPDIR`, `JAVA_HOME`, and `TOOLS_DIR` (NIXL + UCX under `/home/ubuntu/sirius-wt/tools`).

## What this is not

- Not TPC-H SF500/SF1000. Tiny two-file `sales_*.parquet` only.
- Does not rebuild the StarRocks FE. The e2e copies the packaged demo FE at
  `/home/ubuntu/sirius-wt/demo/experimental/starrocks/starrocks/output/fe`.
- Does not vendor NIXL/UCX. Those installs stay in `TOOLS_DIR`.
