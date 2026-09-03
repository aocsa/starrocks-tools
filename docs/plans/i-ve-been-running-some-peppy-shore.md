# Strong Scaling Shuffle Benchmark — NVL72

## Context

The NVL4 experiments showed ~1.6 TiB/s peak throughput (4 GPUs, 20 GiB/rank, `-p 1`), but that ceiling required a single monolithic 20 GiB partition per rank to amortize per-partition overhead. The next experiment is a **weak-scaling benchmark** across the NVL72 (1, 2, 4, 8 nodes = 4, 8, 16, 32 GPUs), designed to show:

1. **Linear throughput scaling** at 1→2→4 nodes (no spill, NVLink-only path)
2. **Spill onset** at 8 nodes, where accumulated shuffle-in-flight buffers exceed a device memory limit

The NVL72 uses Slurm + Pyxis (Enroot containers), with `srun --mpi=pmix_v4` for multi-rank launches.

---

## Scaling Design

**Type:** Weak scaling — each rank handles the same per-rank data; total data grows with node count.

| Nodes | GPUs (ranks) | Per-rank data | Total shuffle data |
|-------|-------------|--------------|-------------------|
| 1     | 4           | 20 GiB       | 80 GiB            |
| 2     | 8           | 20 GiB       | 160 GiB           |
| 4     | 16          | 20 GiB       | 320 GiB           |
| 8     | 32          | 20 GiB       | 640 GiB           |

**Per-rank config (same as NVL4 best):** `-p 1 -c 10 -n 536870912 -m pool -o 4 -g -s -x -w 3 -r 10`

**Metric:** Global throughput (TiB/s across all ranks combined). Perfect weak scaling = flat GiB/s/rank, linear global TiB/s.

---

## Spill Story

From NVL4 logs: 20 GiB/rank input with 4 ranks → device memory peak **~60 GiB** out of 184 GiB (GB200). Natural spill won't occur at any node count without a limit.

**Strategy:** Set an explicit device memory limit `-l 56g` (just below the 60 GiB no-limit peak). This is the sweet spot:
- 4–16 ranks: benchmark operates just at the limit boundary — some light spill expected
- 32 ranks: more simultaneous in-flight UCXX rendezvous buffers (outside RMM tracking) push total device usage over the limit → heavier spill onset

**Two-phase sweep:**
- **Phase 1:** No limit (`-l` omitted) — establish clean linear scaling baseline
- **Phase 2:** Fixed limit at `56g` — show spill onset; combined with Phase 1 gives the full story

If 56g is too aggressive (spills at all node counts), adjust upward to 80g for the "onset at 8 nodes" narrative.

---

## Launch Mechanism

The NVL4 single-node path used `rrun -n 4`. For multi-node NVL72, we use the same Slurm + Pyxis pattern as the TPC-H scripts, but replace the Ray head/worker bootstrap with a direct all-rank `srun`:

```bash
srun \
    --ntasks=$((SLURM_JOB_NUM_NODES * 4)) \
    --ntasks-per-node=4 \
    --gpus-per-task=1 \
    --mpi=pmix_v4 \
    --container-image=${LOCAL_CONTAINER_PATH} \
    --container-mounts=${CONTAINER_MOUNTS} \
    --container-remap-root \
    --container-writable \
    bash /path/to/per_rank_runner.sh
```

UCXX bootstraps via PMIx (PMIx v4 is confirmed on this cluster; used in `ucx-perftest-2node-nperera.sh`). No Ray, no head/worker coordination needed — all N×4 ranks start simultaneously.

**Key risk:** If `libcudf_streaming_bench_shuffle -C ucxx` doesn't bootstrap via PMIx, fall back to using `rrun` with a Slurm-provided hostfile. Plan flags this; validate with a quick 2-rank, 2-node smoke test before the full sweep.

---

## UCX Environment

Merge NVL4 env vars with the TPC-H multi-node env vars:

```bash
# NVL4 single-node (proven)
export UCX_MAX_RNDV_RAILS=1
export UCX_PROTO_ENABLE=y
export UCX_WARN_UNUSED_ENV_VARS=n

# NVL72 multi-node additions (from tpch-run-3k.sh)
export UCX_TLS=^ib,ud:aux       # use TCP/cuda_ipc; exclude IB
export UCX_NET_DEVICES=bond0    # control-plane Ethernet
export UCX_RNDV_PIPELINE_ERROR_HANDLING=y
export UCX_TCP_CM_REUSEADDR=y
export UCX_RNDV_MTYPE_WORKER_MAX_MEM=1G
export UCX_RNDV_MTYPE_WORKER_FC_ENABLE=y
export UCX_RNDV_FRAG_MEM_TYPES=cuda
```

**Note:** Verify from job logs that UCX selects `cuda_ipc` (NVLink) for data transport, not TCP. On NVL72, all GPUs share an NVLink fabric so cross-node `cuda_ipc` should be available. If UCX falls back to TCP, inter-node bandwidth will be dramatically lower than intra-node.

---

## Files to Create

All files land in `~/bzaitlen/shuffle-experiments/`:

### 1. `strong_scaling_nvl72.slurm`
sbatch script. Key directives:
- `#SBATCH --nodes=1` (overridden at submission: `sbatch --nodes=N strong_scaling_nvl72.slurm`)
- `#SBATCH --ntasks-per-node=1` (outer: one task per node to set up env; inner srun launches all ranks)
- `#SBATCH --gres=gpu:4`
- `#SBATCH --exclusive`
- `#SBATCH --nodelist=presto-gb200-gcn-[01-16]`
- `#SBATCH --output=logs/strong-scaling-nvl72-%j.log`

Sets `LOCAL_CONTAINER_PATH`, `CONTAINER_MOUNTS` (with `/dev/nvidia-fs*` loop), `NVIDIA_*` env vars, `REAL_HOME`, then calls `srun ... bash strong_scaling_runner.sh`.

### 2. `strong_scaling_runner.sh`
Per-node inner script (run by srun with `--ntasks-per-node=1`). Sets all UCX, RMM, conda env vars, then launches the actual benchmark with a *second* `srun` call for all N×4 ranks:

```bash
source /opt/conda/etc/profile.d/conda.sh
conda activate rapidsmpf

NRANKS=$((SLURM_NNODES * 4))
DEVICE_LIMIT_ARG=${DEVICE_LIMIT_GIB:+"-l ${DEVICE_LIMIT_GIB}g"}

srun \
    --ntasks=$NRANKS --ntasks-per-node=4 --gpus-per-task=1 --mpi=pmix_v4 \
    libcudf_streaming_bench_shuffle \
    -C ucxx -w 3 -r 10 -m pool -g -s -x \
    -p 1 -o 4 -c 10 -n 536870912 \
    $DEVICE_LIMIT_ARG
```

Only the head node needs to do this; workers block. Use `SLURM_PROCID=0` to gate.

**Note:** If PMIx bootstrap fails, replace the inner srun with:
```bash
rrun -n $NRANKS --bind-to cpu --bind-to memory \
    -x UCX_MAX_RNDV_RAILS=1 -x UCX_PROTO_ENABLE=y ... \
    libcudf_streaming_bench_shuffle -C ucxx ...
```

### 3. `submit_strong_scaling.sh`
Wrapper that submits the 4 node-count jobs (Phase 1 = no limit, Phase 2 = with limit):

```bash
#!/bin/bash
CONTAINER_IMAGE=${1:-/scratch/prestouser/images/mg-polars-tpch-nvl72-2026-08-25-arm64-cuda-13-1.sqsh}
LIMIT=${DEVICE_LIMIT_GIB:-}   # empty = no limit

for N in 1 2 4 8; do
    LABEL="nvl72-${N}node${LIMIT:+-spill${LIMIT}g}"
    sbatch --nodes=$N \
           --job-name="shuffle-scale-${N}n" \
           --export=ALL,LOCAL_CONTAINER_PATH=${CONTAINER_IMAGE},DEVICE_LIMIT_GIB=${LIMIT},RUN_LABEL=${LABEL} \
           strong_scaling_nvl72.slurm
    sleep 2
done
```

Run twice: once with `DEVICE_LIMIT_GIB=` (unset) and once with `DEVICE_LIMIT_GIB=56`.

### 4. `summarize_strong_scaling.py`
Log parser that reads `logs/strong-scaling-nvl72-*.log`, extracts:
- Node count (from `SLURM_NNODES` printed at start)
- Phase (no-limit vs. spill limit value)
- Per-rank `means:` lines: elapsed, local/global throughput, peak device memory
- Spill stats (`copy-device-to-pinned_host`, `copy-pinned_host-to-device`) from `-x` output

Output: Markdown table with columns: nodes, GPUs, global throughput (TiB/s), local throughput (GiB/s/GPU), peak device mem (GiB), spill evicted (GiB), spill reload (GiB), vs-1node-baseline ratio.

Reuse the regex patterns from the existing `summarize_spill_sweep.py`.

---

## Verification

1. **Smoke test:** `sbatch --nodes=2 strong_scaling_nvl72.slurm` — confirm two nodes launch, UCXX bootstrap succeeds, all 8 ranks report throughput. Check UCX transport in logs.
2. **Full no-limit sweep:** submit all 4 node counts; verify global throughput scales linearly.
3. **Spill sweep:** rerun with `DEVICE_LIMIT_GIB=56`; verify throughput drops at 8 nodes but not 1-4.
4. **Recovery check:** if any run crashes with UCX OOM or overflow error, `pkill -9 -f libcudf_streaming_bench_shuffle` across all nodes (add to slurm epilog or submit as a cleanup job).
