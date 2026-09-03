<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

Layer 1 of the cn stack; base dev.

Two failures measured on the source branch (`feat/pin-table-cn`) drove this. First, dev's CN cannot pick a GPU or size its memory. `SiriusEngine::start` takes only a config path and the default config primes about 0.95x of device memory, so N CNs launched on one box all primed the same default GPU pool. Second, dev builds the engine before it binds any port. Engine bring-up takes about 7 s on a GB200 and the FE is up in about 4 s, so the FE's first probe found a closed port and auto-blacklisted the node. The FE lifts a blacklist entry only when every advertised port accepts a TCP connection, and dev never bound `http_port`. On the 4x GB200 box 2 of 4 CNs were auto-blacklisted about 2 s after every cluster start, and q14 at SF100 ran 48.9/0/0/51.1 percent across the four CNs.

What changed, as one reviewable unit:

- `engine_settings.rs` (new). `EngineSettings` and `derive_sirius_config_yaml`, which turns the memory flags into the YAML `sirius_config.cpp` reads. `reservation_limit_fraction` is pinned to 1.0 so the carve-out is the whole budget: the engine may reserve all of its limit, not all of the device. `resolve_cuda_visible_devices` holds the device rule described below.
- `gpu_affinity.rs` (new). Reads the GPU's NUMA socket from sysfs and pins the engine's `scan_manager`, `task_creator` and `downgrade` pools to it through the derived YAML. `SIRIUS_CN_CPU_AFFINITY` overrides it (`off`, or a cpulist such as `0-71`). Unresolvable means unpinned, as before.
- `engine.rs`. `SiriusEngine::start(EngineSettings)` replaces `start(Option<PathBuf>)`. `configure_engine_environment` sets `SIRIUS_LOG_DIR=<engine_dir>/log` (an exported value wins) and exports `CUDA_VISIBLE_DEVICES` from `--gpu-device`. Nothing else from the source branch's engine.rs is in this layer.
- `main.rs`. The flags, `EngineConfig::resolve` (writes `<engine_dir>/derived-sirius-config.yaml`), `ensure_gpu_unclaimed`, listeners before engine, and 7 CLI tests.
- `lib.rs`. `EngineReadiness`, the heartbeat NOT READY interlock, `start_http_server`, `GPU_ENGINE_TEST_LOCK`, 4 tests.
- `.gitignore`. Engine directories (`.cn*/`, `sirius-cn-*/`).

Configuration changes. `--gpu-device <ordinal>` exports `CUDA_VISIBLE_DEVICES` before bring-up; default unset, so the engine sees whatever the environment exposes. `--gpu-memory-limit <size>` (for example `8GiB`, passed verbatim to the engine's `parse_bytes`) and `--gpu-memory-fraction <f>` (0 < f <= 1 of total device memory) are mutually exclusive GPU carve-outs. `--host-memory-limit <size>` sets `sirius.memory.host.capacity_bytes`. All three conflict with `--sirius-config`, since a full config already decides memory. `--engine-dir <path>` holds the derived config, logs and telemetry; default `sirius-cn-<brpc_port>` under the working directory. `SIRIUS_CN_CPU_AFFINITY` overrides socket discovery. `SIRIUS_CN_USE_SIRIUS_DATASOURCE=false` selects the cudf datasource in the derived YAML.

The readiness interlock. `run` binds heartbeat, backend and HTTP before the engine starts. Until `readiness.mark_ready()` fires, after the engine and BRPC are up, the heartbeat answers with an ERROR status. The FE then holds the node as not alive, schedules nothing onto it, and does not add a blacklist entry (only a failed fragment RPC does that). The HTTP listener answers everything with a fixed 200 and closes; the FE only needs the port to accept.

`ensure_gpu_unclaimed` runs only for a default-config bring-up (no `--sirius-config`, no GPU carve-out) and refuses to start when `nvidia-smi` shows another compute process on the device, with the remedy in the message. With a carve-out, sharing a GPU is intended, so the check is skipped.

The `CUDA_VISIBLE_DEVICES` rule. The source branch let an exported `CUDA_VISIBLE_DEVICES` win over `--gpu-device` with a warning. That is how a launcher that forgot to unset it put all N CNs on one GPU while the cluster kept answering queries. I made it an error unless the exported value is exactly the requested ordinal. A device list or a UUID is refused too, because it cannot be checked here. Covered by `engine_settings::tests::disagreeing_export_is_refused`.

Verified 2026-09-03 on the GB200 (aarch64, pixi cn env, rustc 1.96.0), commit 9738b25: cargo fmt --check clean; cargo clippy --all-targets --no-default-features -D warnings clean; cargo test --workspace --no-default-features: 205 passed, 0 failed (sirius-starrocks-cn lib 76, sirius-starrocks-cn bin 7, starrocks-plan-translator lib 6, starrocks-plan-translator tests/translate 116, starrocks-thrift 0; all 34 new tests ran); engine-feature cargo check -p sirius-starrocks-cn --all-targets with the default sirius-engine feature against the main clone's build/release compiles clean (engine tests not run).

Not handled here: the launcher scripts and BUILDING.md (docs/bench PR); the TUNABLES.md engine rows (after #1706 creates the file); the multi-fragment runtime (next layers of this stack); `SHUTDOWN_GRACE`, because the HTTP listener shuts down through the same wake-and-join path as the thrift servers and does not need it, so it belongs with the engine-teardown layer. #1706 and this PR both edit main.rs's `sirius_starrocks_cn` import line and the top of `run`, a mechanical conflict for whichever lands second.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Source branch: `aocsa/feat/pin-table-cn`
- #1706 (cn-transport-tunables) shares main.rs's import line and `run`
