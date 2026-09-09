# SF500 on one RTX PRO 6000 split into two MIG 2g.48gb instances (g7e.4xlarge)

- `arms-S500-mig.sh` — the arm driver (smoke q06, then the 22 queries, compare against the SF500 oracle in
  `../../oracle`, evidence). Run from `/home/ubuntu/sirius-wt` under `all22/gpu-lock.sh` with `setsid nohup`.
- `sirius-patches/` — the two commits of the `bench/sf500-2-mig-gpus` branch of aocsa/sirius (on all22/integration
  95bec853), `git am`-able: `GPU_DEVICES` (place CN i on a listed CUDA ordinal, so N CNs can share one GPU or take
  one MIG instance each) and `MIG_DEVICES` (place CN i on a MIG UUID through CUDA_VISIBLE_DEVICES, no --gpu-device).
- `cluster8.sh` — the patched launcher as it ran (`experimental/starrocks/benchmarks/cluster8.sh`).
- `capture-arm.sh` — the box-local harness copy: accepts `GPU_DEVICES`/`MIG_DEVICES` past the GPU-count check and
  matches running CNs by binary path rather than by `--gpu-device`.

What worked: `GPU_DEVICES=0,1`. CUDA 13 / driver 580.126.09 enumerates the two MIG instances as ordinals 0 and 1.
What did not: `MIG_DEVICES=MIG-<uuid>,...` — cucascade's GPU count (`reservation_manager_configurator.cpp`,
"Requested number of GPUs exceeds available GPUs") cannot resolve a MIG UUID in CUDA_VISIBLE_DEVICES
("does not map to an NVML device").

Result 2026-09-09 (`../../evidence/sf500-2mig`): 16/22 MATCH, q05 q08 q09 q17 q18 q21 GPU out_of_memory at a
40 GiB pool + 4 GiB staging + 40 GiB host per CN; timings within 2% of the same two CNs sharing the un-split GPU.
