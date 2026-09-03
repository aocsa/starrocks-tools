# 01 — Quent trace schema: what the sim can extract today, what is missing

Agent 1 report, 2026-09-03 (rewrite; the earlier nsys/log-oriented version is condensed into
Appendix A). Read-only survey of `aocsa/feat/pin-table-cn` @ d24f02c4 and the Quent sessions on
disk. No repo edits, no builds, no GPU runs. All ndjson lines below are copied verbatim from the
files named; all field names are as they appear on disk.

Path shorthands (absolute on this box):

- `$SOT` = `/home/prestouser/aocsa/sirius-stacks-wt/sot` (baseline tree, read-only)
- `$SR`  = `$SOT/experimental/starrocks`
- `$Q`   = `/home/prestouser/aocsa/sirius-stacks-wt/quent` (instrumentation chain; same commit, so
  `file:line` cites are identical in both trees)
- `$CN0` = `$SR/.cn0/telemetry/01a065d5-616d-7481-8cce-0656f57765ac` — newest cn0 session
  (engine Init 05:54:38.083 UTC, Exit 05:55:53.644 UTC, 12 queries, 539 KiB, the lead's decimal
  SF100 run)
- `$SA`  = `/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/telemetry-standalone/01a065c7-9451-7f32-a29b-f8a385caa68a`
  — newest standalone session (8 queries, 6.7 MiB)

## 0. Sources and how they join

| Source | Where | Notes |
|---|---|---|
| Quent per CN | `$SR/.cn<i>/telemetry/<session-uuid>/<type>/<uuid>.ndjson` + `model.qmi` (cn0 has 40 sessions, cn1..3 15+ each; newest 05:37–05:54 UTC) | One session per engine process lifetime. The session dir, the per-type dirs and `model.qmi` are created at engine init (dir mtime 05:54:38.65 = `engine.Init` timestamp); the ndjson files are flushed at engine exit (`query/*.ndjson` mtime 05:55:54.22, `engine.Exit` 05:55:53.64). One file per type holds every entity of that type. |
| Quent standalone | `$SA` and 4 siblings (05:33–05:39 UTC) | Same 19 record types, same schema; only `query.Init.instance_name` differs (`q1_iter0`.. via `sirius_set_query_label`, else `unnamed_query`). |
| Engine log per CN | `$SR/.cn<i>/log/sirius_2026-09-03.log` | `[info] [sirius_engine.cpp:250] query N telemetry_query=<32 hex>` is the join to the Quent `query` entity id (dashes removed). Verified on `$CN0`: all 12 query ids match a log line, e.g. `[2026-09-03 05:54:48.387] [info] [sirius_engine.cpp:250] query 1 telemetry_query=01a065d589c37cd1a8e0ec54124206a5` ↔ `"id":"01a065d5-89c3-7cd1-a8e0-ec54124206a5"`. Also `[sirius_context.cpp:389] QueryBegin: sirius_ffi`, `[window] begin/end`, the `Query Plan:` block. |
| CN cluster log | scratchpad `step0`/`step3` cluster logs (see Appendix A) | Rust `tracing` at info; the only source with nixl bytes and relay batch counts today. |

Attribution chain inside a session (all uuid-valued fields, measured 100 % in
`$SR/scripts/cn-distribution.py`):
`plan.Declaration.parent.query_id → operator.Declaration.plan_id → port.Declaration.operator_id`,
`task.Created.pipeline_uuid → operator id`, `data_batch.Constructed.producer_pipeline_uuid → operator id`,
`batch_placement.BatchRegistered.{pipeline_uuid,port_uuid}`, `batch_placement.BatchPackaged.task_uuid → task id`,
`task.*.executor_thread.resource_id → executor_thread id → parent_group_id → thread_group → gpu_device`.

Analyzer example: `print_resource_tree` (`$Q/rust/crates/telemetry/analyzer/examples/print_resource_tree.rs`)
prints the engine → gpu-N → thread-type → thread tree. No built binary exists
(`$SOT/rust/target` and `$Q/rust/target` absent; `/home/prestouser/aocsa/sirius-stacks/rust/target/debug/examples`
has no `print_resource_tree`), so it was not run; fields below are from the files.

## 1. On-disk schema (19 record types)

Envelope on every line: `{"id":<entity-uuid>,"timestamp":<unix-ns>,"data":{...}}`. Two payload shapes:
declaration entities `"data":{"Declaration":{...}}` (engine/worker: `{"Init":...}` / `{"Exit":null}`);
FSM entities `"data":{"seq":<n>,"state":{"<State>":{...}}}` with the terminal transition as the bare
string `"state":"Exit"`. Resource usages are `{"resource_id":<uuid>,"capacity":{"capacity_bytes"|"capacity_entries":<n>}|null}`.

Model source: `$Q/rust/crates/telemetry/model/src/lib.rs` (`model! { name: Sirius, root: Engine, entities: {...} }`),
`task.rs`, `data_batch.rs`, `batch.rs`, `gpu_device.rs`, `thread_group.rs`; upstream quent
`domains/query_engine/model/src/{query,operator,plan,port,engine,worker,query_group}.rs` and
`crates/stdlib/src/{channel,memory}.rs` at `~/.cargo/git/checkouts/quent-515d44f958e14372/2a5ca83/`.

| Type (dir) | Kind | States / fields (as on disk) | Count in `$CN0` | Real line |
|---|---|---|---|---|
| `engine` | Init/Exit | `Init.implementation.{name,version,custom_attributes}`, `Init.instance_name` | 2 | `$CN0/engine/01a065d5-617b-76f2-86fe-bad2944f3e24.ndjson`: `{"id":"01a065d5-616d-7481-8cce-0618dfd7bba8","timestamp":1788414878083181406,"data":{"Init":{"implementation":{"name":"siriusDB","version":null,"custom_attributes":[]},"instance_name":"siriusDB"}}}` |
| `worker` | Init/Exit | `Init.parent_engine_id`, `Init.instance_name` (= `worker-<pid>`) | 2 | `{"id":"01a065d5-616d-7481-8cce-06212021aa35","timestamp":1788414878083193566,"data":{"Init":{"parent_engine_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","instance_name":"worker-2634334"}}}` |
| `query_group` | Declaration | `instance_name` (= `siriusDB-session-<pid>`), `engine_id` | 1 | `{"id":"01a065d5-616d-7481-8cce-063dfb3346ae","timestamp":1788414878083231294,"data":{"Declaration":{"instance_name":"siriusDB-session-2634334","engine_id":"01a065d5-616d-7481-8cce-0618dfd7bba8"}}}` |
| `query` | FSM Init→Planning→Executing→Exit | `Init.instance_name` (the query label), `Init.query_group_id` | 12 queries / 48 lines | `$CN0/query/01a065d5-617f-7033-9f02-836a45e99be1.ndjson`: `{"id":"01a065d5-89c3-7cd1-a8e0-ec54124206a5","timestamp":1788414888387548560,"data":{"seq":0,"state":{"Init":{"instance_name":"sirius_streaming_fragment","query_group_id":"01a065d5-616d-7481-8cce-063dfb3346ae"}}}}` … `{"id":"01a065d5-89c3-7cd1-a8e0-ec54124206a5","timestamp":1788414901716114804,"data":{"seq":3,"state":"Exit"}}` |
| `plan` | Declaration | `parent.{query_id,plan_id}`, `instance_name` (`pipeline_plan`), `edges[{source,target}]` (port uuids), `worker_id` | 12 | `$CN0/plan/01a065d5-617c-7023-8010-0da2467456a4.ndjson`: `{"id":"01a065d5-89c3-7cd1-a8e0-ecee03fcdd66","timestamp":1788414888387774576,"data":{"Declaration":{"parent":{"query_id":"01a065d5-89c3-7cd1-a8e0-ec54124206a5","plan_id":null},"instance_name":"pipeline_plan","edges":[{"source":"01a065d5-89c3-7cd1-a8e0-ecbd5a6e57ff","target":"01a065d5-89c3-7cd1-a8e0-ecafcfee81b9"},{"source":"01a065d5-89c3-7cd1-a8e0-ecda1dce10e4","target":"01a065d5-89c3-7cd1-a8e0-ecca24f735eb"}],"worker_id":"01a065d5-616d-7481-8cce-06212021aa35"}}}` |
| `operator` | Declaration (one per Sirius **pipeline**) | `plan_id`, `parent_operator_ids`, `instance_name` (operator chain), `type_name` (`Pipeline Id N`), `custom_attributes` (always `[]`) | 48 | `$CN0/operator/01a065d5-617d-7071-8557-392e1066e247.ndjson`: `{"id":"01a065d5-89c3-7cd1-a8e0-ec90cfa37ca2","timestamp":1788414888387758352,"data":{"Declaration":{"plan_id":"01a065d5-89c3-7cd1-a8e0-ecee03fcdd66","parent_operator_ids":[],"instance_name":"GPU_SCAN(0) -> PROJECTION(1) -> PROJECTION(2) -> HASH_GROUP_BY(3)","type_name":"Pipeline Id 0","custom_attributes":[]}}}` |
| `port` | Declaration | `operator_id`, `instance_name` (`default_sender` / `default_receiver`) | 72 | `$CN0/port/01a065d5-617d-7071-8557-3939b5d88c15.ndjson`: `{"id":"01a065d5-89c3-7cd1-a8e0-ecbd5a6e57ff","timestamp":1788414888387764816,"data":{"Declaration":{"operator_id":"01a065d5-89c3-7cd1-a8e0-ec90cfa37ca2","instance_name":"default_sender"}}}` |
| `gpu_device` | Declaration | `instance_name` (`gpu-0`), `parent_group_id`, `ordinal` | 1 | `$CN0/gpu_device/01a065d5-617e-75e1-94ca-9f1c37abd8a3.ndjson`: `{"id":"01a065d5-6183-7d21-be7f-98cf6ea9f8eb","timestamp":1788414878083237982,"data":{"Declaration":{"instance_name":"gpu-0","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","ordinal":0}}}` |
| `thread_group` | Declaration | `instance_name` (`shared`, `executor_thread`, `task_manager_loop_thread`), `parent_group_id` | 3 | `{"id":"01a065d5-616d-7481-8cce-0641ecb1fc43","timestamp":1788414878083234558,"data":{"Declaration":{"instance_name":"shared","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8"}}}` |
| `executor_thread` | resource FSM Initializing→Operating→Finalizing→Exit | `ExecutorThreadInitializing.{instance_name,parent_group_id,resource_type_name}` | 4 (`gpu_pipeline-gpu0-exec-0..3`) | `$CN0/executor_thread/01a065d5-6182-73a2-924d-af3852b4a8c2.ndjson`: `{"id":"01a065d5-6196-7f01-9155-b6ba57f95aef","timestamp":1788414878102618805,"data":{"seq":0,"state":{"ExecutorThreadInitializing":{"instance_name":"gpu_pipeline-gpu0-exec-0","parent_group_id":"01a065d5-6183-7d21-be7f-98d1fd570ecb","resource_type_name":"executor_thread"}}}}` |
| `task_manager_loop_thread` | resource FSM | same shape; names `gpu-0-exec-manager` (13 — one per manager loop incarnation), `task-scheduler-thread` | 14 | `{"id":"01a065d5-6197-7322-a31c-3bb1d2f73ffd","timestamp":1788414878103036950,"data":{"seq":0,"state":{"TaskManagerLoopThreadInitializing":{"instance_name":"gpu-0-exec-manager","parent_group_id":"01a065d5-6183-7d21-be7f-98ea3efe1b55","resource_type_name":"task_manager_loop_thread"}}}}` |
| `task_queue` | resource FSM | names `task-scheduler-gpu-queue` (under `shared`), `gpu_pipeline-task-queue` (under `gpu-0`); `TaskQueueOperating.capacity_entries` = 18446744073709551615 (unbounded) | 2 | `$CN0/task_queue/01a065d5-6181-77e1-ab88-a83258eb96bd.ndjson`: `{"id":"01a065d5-618c-7553-aa44-5386fb182f83","timestamp":1788414878092555320,"data":{"seq":0,"state":{"TaskQueueInitializing":{"instance_name":"task-scheduler-gpu-queue","parent_group_id":"01a065d5-616d-7481-8cce-0641ecb1fc43","resource_type_name":"task_queue"}}}}` |
| `memory` | resource FSM | `memory_space(tier=GPU, device_id=0, limit=107374182400)`, `memory_space(tier=HOST, device_id=0, limit=171798691840)`; `MemoryOperating.capacity_bytes` | 2 | `{"id":"01a065d5-6183-7d21-be7f-9883058480fb","timestamp":1788414878083214430,"data":{"seq":0,"state":{"MemoryInitializing":{"instance_name":"memory_space(tier=GPU, device_id=0, limit=107374182400)","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","resource_type_name":"memory"}}}}` |
| `memory_tier` | resource FSM | `GPU-0`, `HOST`, `DISK` (batch_placement tiers and task reservations) | 3 | `{"id":"01a065d5-6183-7d21-be7f-98f95ce4e149","timestamp":1788414878083273662,"data":{"seq":0,"state":{"MemoryTierInitializing":{"instance_name":"GPU-0","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","resource_type_name":"memory_tier"}}}}` |
| `channel` | resource FSM | `host-0->gpu-0`, `gpu-0->host-0`; `ChannelInitializing.{source_id,target_id}` = memory ids; `ChannelOperating.capacity_bytes` | 2 | `$CN0/channel/01a065d5-6180-7302-805a-48c92d4879c2.ndjson`: `{"id":"01a065d5-6183-7d21-be7f-98aa9bab304c","timestamp":1788414878083223550,"data":{"seq":0,"state":{"ChannelInitializing":{"instance_name":"host-0->gpu-0","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","resource_type_name":"channel","source_id":"01a065d5-6183-7d21-be7f-9899d4a3a7b5","target_id":"01a065d5-6183-7d21-be7f-9883058480fb"}}}}` |
| `task` | FSM (see `TASK_FSM.md`) Created→Queued→Routing→Queued→Reserving→Preparing→Computing…→Finalizing→Exit | `Created.{instance_name,pipeline_uuid}`; `Queued.queue`; `Routing.{preferred_device_id,manager_thread}`; `Reserving.{requested_bytes,input_basis,peak_estimate,bytes_to_materialize,manager_thread}`; `Preparing.{origin_tier,target_tier,input_bytes,executor_thread,reservation}`; `Computing.{instance_name=<OP(id)>,current_operator_id,input_bytes,peak_allocated_bytes,executor_thread,reservation}`; `Finalizing.success` | 600 tasks / 6335 lines (1200 Queued, 1535 Computing) | `$CN0/task/01a065d5-6181-77e1-ab88-a82fd9b3cfe0.ndjson`: `{"id":"01a065d5-89e2-7b02-9102-6eb541c37c91","timestamp":1788414888418881896,"data":{"seq":6,"state":{"Computing":{"instance_name":"GPU_SCAN(0)","current_operator_id":0,"input_bytes":2410431042,"peak_allocated_bytes":0,"executor_thread":{"resource_id":"01a065d5-6196-7f01-9155-b6ba57f95aef","capacity":null},"reservation":{"resource_id":"01a065d5-6183-7d21-be7f-98f95ce4e149","capacity":{"capacity_bytes":19516033778}}}}}}` |
| `data_batch` | FSM Constructed→Stationary(→InTransit→Stationary)*→Destructed→Exit | `Constructed.{instance_name="batch",data_batch_id,producer_pipeline_uuid}`; `Stationary.memory.{resource_id,capacity.capacity_bytes}` (= the batch's own size); `InTransit.{source_memory,dest_memory,channel}` (model only — **0 InTransit lines in any session on disk**) | 1295 batches / 5180 lines | `$CN0/data_batch/01a065d5-617f-7033-9f02-83594513721e.ndjson`: `{"id":"01a065d5-8ab4-7433-a340-e84b79da49d5","timestamp":1788414888628817587,"data":{"seq":0,"state":{"Constructed":{"instance_name":"batch","data_batch_id":0,"producer_pipeline_uuid":"01a065d5-89c3-7cd1-a8e0-ec90cfa37ca2"}}}}` then `{"id":"01a065d5-8ab4-7433-a340-e84b79da49d5","timestamp":1788414888628839187,"data":{"seq":1,"state":{"Stationary":{"memory":{"resource_id":"01a065d5-6183-7d21-be7f-9883058480fb","capacity":{"capacity_bytes":2376609302}}}}}}` |
| `batch_placement` | FSM BatchRegistered→BatchQueued→BatchPackaged→BatchProcessing→BatchConsumed→Exit | `BatchRegistered.{instance_name="batch-<id>",batch_id,pipeline_uuid,port_uuid,origin∈{operator_output,partition_output,reschedule_intermediate},tier}`; `BatchPackaged/BatchProcessing.{task_uuid,tier}`; `BatchConsumed.reason∈{processed,task_failed,query_end}` | 595 placements / 3564 lines (origins 367/222/6) | `$CN0/batch_placement/01a065d5-617c-7023-8010-0d990ca33ab3.ndjson`: `{"id":"01a065d5-8e4f-7301-b129-c42e884ac59d","timestamp":1788414889551688235,"data":{"seq":0,"state":{"BatchRegistered":{"instance_name":"batch-12","batch_id":12,"pipeline_uuid":"01a065d5-89c3-7cd1-a8e0-ec8658aeca70","port_uuid":"01a065d5-89c3-7cd1-a8e0-ecafcfee81b9","origin":"operator_output","tier":{"resource_id":"01a065d5-6183-7d21-be7f-98f95ce4e149","capacity":{"capacity_bytes":464}}}}}}` |
| `model.qmi` | JSON | quent `version 0.1.0`, `commit 2a5ca83442953cbea1c9c53560808104f6b59127`, model `Sirius` | — | — |

C++ emitters: `$Q/src/telemetry/telemetry_context.cpp` (engine/worker/query_group/gpu_device/thread_group
declarations; `emit_plan_telemetry` declares one `operator` per pipeline with `custom_attributes = {}`,
ports `<port>_receiver`/`<port>_sender`, plan edges), `$Q/src/sirius_engine.cpp:180-260` (query FSM,
label = `sirius_iface.query_label.value_or("unnamed_query")`, group = `query_group_id_for(session_label)`),
`$Q/src/include/telemetry/data_batch_probe.hpp` (`quent_data_batch_probe`: `created→stationary`,
`conversion_started→in_transit`, `conversion_completed/data_replaced→stationary`; `create()` returns the
no-op base probe when `telemetry_info.context == nullptr`), `$Q/src/telemetry/batch_telemetry.cpp`
(`batch_telemetry_registry`, bytes = `ro.get_data()->get_size_in_bytes()` at `:76`),
`$Q/src/include/telemetry/memory_context.hpp` (Memory per memory space, Channel per (source,dest) space pair).
Probe call sites under `$SOT/src/op`: `sirius_physical_table_scan.cpp:148`, `sirius_physical_partition.cpp:315`,
`sirius_physical_streaming_sink.cpp:152`, `sirius_physical_result_collector.cpp:187,193`,
`sirius_physical_grouped_aggregate_merge.cpp:225`, `sirius_physical_top_n.cpp:224,335`,
`sirius_physical_ungrouped_aggregate.cpp:439,551`, `sirius_physical_limit.cpp:120`; plus
`$SOT/src/scan_manager/sirius_scan_manager.cpp:290,393,415` (pinned host/device chunks) and the
`make_data_batch` helpers in `src/include/data/data_batch_utils.hpp`. `on_published` callers:
`sirius_physical_operator.cpp:294` (`operator_output`), `sirius_physical_partition_consumer_operator.cpp:33`
(`partition_output`); lazy registration at claim yields `reschedule_intermediate` (`batch_telemetry.cpp:349`).

## 2. EXTRACT TABLE — sim event → Quent record

| Sim event | Quent record (file, field) | Real line / derivation |
|---|---|---|
| Query label | `query/*.ndjson`, `Init.instance_name` | CN: `"Init":{"instance_name":"sirius_streaming_fragment",...}` (181 of 250 on cn0) or `"instance_name":"sirius_ffi"` (69); standalone: `$SA/query/01a065c7-9453-7282-8a97-fd6d42397faf.ndjson`: `{"id":"01a065c7-949d-76d3-809e-b81144460fee","timestamp":1788413973661726558,"data":{"seq":0,"state":{"Init":{"instance_name":"unnamed_query","query_group_id":"01a065c7-9451-7f32-a29b-f88d6a7faff4"}}}}` and `q1_iter0..q6_iter4` when the run script labelled them |
| Query (fragment) start / end | `query`: `Executing` timestamp → `"state":"Exit"` timestamp | q1 of `$CN0`: Executing 1788414888387735376 → Exit 1788414901716114804 = 13 328 ms; the intermediate receiver fragments (`STREAMING_SOURCE(0) -> HASH_GROUP_BY(1)` … `STREAMING_SINK(9)`, also labelled `sirius_streaming_fragment`) 0.7–21 ms — `$CN0` holds no `sirius_ffi` result fragment |
| Pipeline (Sirius operator chain) identity | `operator/*.ndjson` `Declaration.{instance_name,type_name,plan_id}`; `plan.Declaration.parent.query_id` | 12 distinct chains on cn0, e.g. `"instance_name":"STREAMING_SOURCE(0) -> HASH_GROUP_BY(1)","type_name":"Pipeline Id 0"`, `"MERGE_GROUP_BY(5) -> PROJECTION(6) -> STREAMING_SINK(7)"` |
| Pipeline start / end | `task`: min `Created.timestamp` / max `Exit.timestamp` over tasks with `Created.pipeline_uuid` = operator id | `{"id":"01a065d5-89e2-7b02-9102-6eb541c37c91","timestamp":1788414888418716135,"data":{"seq":0,"state":{"Created":{"instance_name":"task-0","pipeline_uuid":"01a065d5-89c3-7cd1-a8e0-ec90cfa37ca2"}}}}` |
| Operator start / end (per task) | `task`: `Computing.instance_name` / `current_operator_id`; duration = next `Computing`/`Finalizing` timestamp − this timestamp | scan task above: `Computing GPU_SCAN(0)` @…418881896 → next Computing; `Finalizing.success` closes it. Computing counts on `$CN0` by operator type (all ids): GPU_SCAN 348, PROJECTION 571, HASH_GROUP_BY 222, PARTITION 222, UNGROUPED_AGGREGATE 132, STREAMING_SINK 11, MERGE_GROUP_BY 7, STREAMING_SOURCE 6, MERGE_AGGREGATE 4, ORDER_BY 3, SORT_SAMPLE 3, SORT_PARTITION 3, MERGE_SORT 3 (= 1535) |
| Task scheduling phases | `task`: `Queued`→`Routing`→`Queued`→`Reserving`→`Preparing` timestamps and attrs | `{"id":"01a065d5-89e2-7b02-9102-6eb541c37c91","timestamp":1788414888418804295,"data":{"seq":4,"state":{"Reserving":{"instance_name":"","requested_bytes":19516033778,"input_basis":2410431042,"peak_estimate":19516033778,"bytes_to_materialize":0,"manager_thread":{"resource_id":"01a065d5-6197-7322-a31c-3bb1d2f73ffd","capacity":null}}}}}` |
| GPU | `gpu_device.Declaration.{instance_name,ordinal}`; task→`executor_thread.resource_id`→`executor_thread.*Initializing.parent_group_id`→`thread_group.parent_group_id`→gpu_device; `task.Routing.preferred_device_id` | `"Declaration":{"instance_name":"gpu-0","parent_group_id":"01a065d5-616d-7481-8cce-0618dfd7bba8","ordinal":0}`; `"Routing":{"instance_name":"","preferred_device_id":0,...}` |
| Executor thread | `task.Preparing/Computing.executor_thread.resource_id` → `executor_thread` entity | `gpu_pipeline-gpu0-exec-0..3` (4 per CN) |
| Manager thread | `task.Routing/Reserving.manager_thread.resource_id` → `task_manager_loop_thread` | `gpu-0-exec-manager`, `task-scheduler-thread` |
| Task queue | `task.Queued.queue.resource_id` → `task_queue` entity | `{"id":"01a065d5-89e2-7b02-9102-6eb541c37c91","timestamp":1788414888418746695,"data":{"seq":1,"state":{"Queued":{"queue":{"resource_id":"01a065d5-618c-7553-aa44-5386fb182f83","capacity":{"capacity_entries":1}}}}}}` → `task-scheduler-gpu-queue` |
| Task-queue occupancy | derived: +1 at `Queued`, −1 at the task's next state, per `queue.resource_id` | computed on `$CN0`: peak 50 in `task-scheduler-gpu-queue`, 11 in `gpu_pipeline-task-queue` (2 Queued per task: scheduler queue, then executor queue) |
| Edges / ports | `plan.Declaration.edges[{source,target}]` (port uuids) + `port.Declaration.{operator_id,instance_name}` | `"edges":[{"source":"01a065d5-89c3-7cd1-a8e0-ecbd5a6e57ff","target":"01a065d5-89c3-7cd1-a8e0-ecafcfee81b9"},...]`; `"instance_name":"default_sender"` / `"default_receiver"` |
| Batch produced (bytes, producer, tier) | `data_batch`: `Constructed.{data_batch_id,producer_pipeline_uuid}` + `Stationary.memory.{resource_id,capacity.capacity_bytes}`; lifetime to `Destructed` | scan output `"capacity_bytes":2376609302` on GPU memory `…9883058480fb`; 1295 batches on cn0, 2516 in `$SA` |
| Batch queued / claimed / processed per consumer port | `batch_placement`: `BatchRegistered.{batch_id,pipeline_uuid,port_uuid,origin,tier}` → `BatchQueued` → `BatchPackaged.task_uuid` → `BatchProcessing` → `BatchConsumed.reason` | `"BatchPackaged":{"instance_name":"","task_uuid":"01a065d5-bd0a-7f41-8068-dc98c77b77d8","tier":{...,"capacity":{"capacity_bytes":464}}}`; queue wait = Packaged − Queued (12 s for batch-12 above: a 464-byte `HASH_GROUP_BY(3)` output of pipeline 0 registered on the `default_receiver` port of the `PARTITION(4)` pipeline (`01a065d5-89c3-7cd1-a8e0-ec8658aeca70`), packaged by `task-54` of that pipeline once the scan pipeline finished) |
| Memory tier moves (spill/upgrade) | `data_batch.InTransit.{source_memory,dest_memory,channel}` and `task.Preparing.{origin_tier,target_tier,input_bytes}`, `Reserving.bytes_to_materialize`, `batch_placement` tier self-transitions | model present; zero occurrences in all sessions (no downgrade at SF10/SF100 with 100 GiB pools); `Preparing` shows `"origin_tier":"SOURCE","target_tier":"GPU"` only |
| CN identity | only indirectly: session path `.cn<i>/telemetry/…`, `worker.Init.instance_name` = `worker-<pid>`, `query_group` = `siriusDB-session-<pid>` | `"instance_name":"worker-2634334"` |

## 3. MISSING FOR THE SIM

Marking: **in Quent** (extractable today), **in CN log** (only in the Rust/engine logs), **nowhere**.

| Sim event | Where | Detail |
|---|---|---|
| Fragment identity (StarRocks `fragment_instance_id`/`query_id` ↔ Sirius `query` entity ↔ pipelines) | **nowhere** | Quent `query.Init.instance_name` is the constant `sirius_streaming_fragment` (`$SOT/src/exec/streaming_fragment.cpp:34,182`) or `sirius_ffi` (`$SOT/src/sirius_ffi.cpp:77,276-284`); `query_group` is per process (`session_label` never set on the CN path). The engine log joins Quent query ↔ engine window id (`sirius_engine.cpp:250`) but no source carries the FE ids next to either: the CN log prints `fragment_instance_id`/`query_id` only on `cancel_plan_fragment` acks and errors (`compute_node_service.rs:389,790-814`); `exec_plan_fragment` is `#[instrument(skip_all)]` (`:321`). Sirius pipeline ↔ StarRocks fragment is recoverable only by timestamp order within one CN. |
| Fragment kind (sender / receiver / result) | in Quent (weak) | `sirius_ffi` = result fragment, `sirius_streaming_fragment` = sender or intermediate; `STREAMING_SOURCE`/`STREAMING_SINK` in `operator.instance_name` distinguish receiver vs sender chains. |
| Scan bytes actually read, and file byte ranges per fragment | **nowhere** (ranges: env dump only) | Quent has the planner estimate (`task.Reserving.input_basis`, `Preparing/Computing.input_bytes` = 2 410 431 042 for the SF100 lineitem split) and the decoded output size (`data_batch.Stationary.capacity_bytes` of the scan pipeline's batches), not compressed bytes read nor which file ranges. Ranges: `SIRIUS_CN_DUMP_FRAGMENTS=<dir>` dumps `TExecPlanFragmentParams` (`compute_node_service.rs:899-905`) and the Substrait plan (`:1364-1370`); the engine logs `[parquet_gpu_ingestible] Byte range [{}, +{}) of {} owns {} row group(s)` only at debug (`parquet_gpu_ingestible.cpp:828`). `operator.Declaration.custom_attributes` is always `[]`. |
| Park (sender output parked) | **nowhere** | `parked.insert(park_id, ParkedOutput{...})` at `engine.rs:683` emits nothing; the sink's `data_batch` entities stay `Stationary` on GPU until the parked fragment drops, so the park interval is only implicit (batch `Stationary`→`Destructed` gap). |
| Relay (same-CN hop) | in CN log (count only) | `info!(stream_id, sender_id, batches = moved, "relayed native batches across a fragment boundary")` (`engine.rs:614-618`); `Fragment::relay_from` (`sirius_ffi.cpp:694-758`) moves the same `data_batch` pointer (`stream_session::push` → `batch_stream::push` → `_repo->add_data_batch`, `batch_stream.cpp:41-53`) without `on_published`, so the relayed batch has **no `batch_placement` until lazy registration at claim** (`origin":"reschedule_intermediate"`, 6 on cn0, all on `STREAMING_SOURCE(0) -> HASH_GROUP_BY(1)`), and no bytes/rows/duration anywhere. |
| nixl bytes / duration | in CN log (bytes, per stream+dest totals) | `info!(stream_id, sender_id, dest, batches, bytes, "transmitted batches via nixl")` (`nixl_transport.rs:782-788`); `write_and_wait` returns `Duration` (`:796-802`) but the caller discards it (`:713-719`); receiver frame bytes at debug only (`compute_node_service.rs:693-699`). Quent: the receiver's `push_packed` builds the batch with `telemetry::batch_telemetry_info{}` (`sirius_ffi.cpp:914-916`) → **remote batches have no `data_batch` entity at all** (verified: 3 of the 6 `STREAMING_SOURCE` input batch ids on cn0 — 220, 666, 892 — appear in `batch_placement` but not in `data_batch`). No channel other than `host-0->gpu-0`/`gpu-0->host-0` exists. |
| H2D / D2H / D2D | in Quent (model), **nowhere** (data) | Tier conversions would emit `data_batch.InTransit` over the two declared channels — 0 occurrences on disk. Non-conversion copies are invisible: hash-partition gather (`sirius_physical_streaming_sink.cpp:160-182`, `gpu_partition_impl.cpp`), result-collector `clone_to<host_data_representation>` D2H (`sirius_physical_result_collector.cpp:147-198`; the clone appears as a new `data_batch` on HOST memory but without duration), `export_packed` `chunked_pack` into the arena and `push_packed` deep copy (`sirius_ffi.cpp:807-836,911-912`). |
| Inflight I/O (uring ring depth) | **nowhere** | `int inflight` local in the reactor loop (`uring_reactor.cpp:901,960,973,1034`), `NUM_CHUNKS = 64` (`:53`); only warnings reach the log. Quent proxy: overlap count of `Computing GPU_SCAN(0)` intervals per CN (≤ 4 executor threads), which is task-level, not request-level. |
| Inflight packed exports (staging leases) | in CN log (peak at teardown only) | `exchange_staging_arena` tracks `leases_`, `live_bytes_`, `peak_live_bytes_` (`exchange_staging_arena.cpp:212-275`) and logs `peak live {} of {} bytes ({} leases outstanding ...)` at destruction (`:168`); per-lease timing (lease → release) is in no trace. |
| CN id / physical GPU | in CN log (indirect) | Quent: `gpu_device.ordinal` is 0 on every CN (each CN pins one device via `CUDA_VISIBLE_DEVICES`), `engine.Init.instance_name` = `siriusDB` everywhere; only the pid (`worker-<pid>`) and the directory differ. Engine log has `GPU 0: NVIDIA GB200 (numa=0, pci=00000008:01:00.0)` (`sirius_context.cpp:656`); the interleaved cluster log has no CN tag (Appendix A). |
| Rows per batch / per operator | **nowhere** in Quent | `data_batch` carries bytes only (`get_size_in_bytes()`); rows exist on the wire (`PTransmitPackedParams.rows`, `nixl_transport.rs:738`) and in `declared input stream cardinality stream_id=… rows=…` (`engine.rs:555`), not in telemetry. |

## 4. Does the CN emit Quent today? How are queries labelled?

**Yes.** `telemetry_config::enable_quent` defaults to `true` in C++ (`$SOT/src/include/sirius_config.hpp:208`,
read by `sirius_config.cpp:319`; `docs/super-sirius/configuration.md:443` documents the default), and
`telemetry_context` only picks the no-op exporter when it is `false` (`telemetry_context.cpp:56`). The CN's
derived YAML (`$SR/.cn0/derived-sirius-config.yaml`, written by `engine_settings.rs:105-108` at
`main.rs:281`) sets only `telemetry.output_directory: ".cn0/telemetry"`, so the default applies; the
same holds for the full YAMLs in `$SR/benchmarks/pinned/generated/cn{0..3}.yaml` (only `output_directory`).
Confirmed on disk: `$SR/.cn0/log/sirius_2026-09-03.log:3` `[telemetry_context.cpp:137] Telemetry context
initialized (engine=siriusDB, 1 GPU device group(s))` and `:4` `[batch_telemetry.cpp:227] Batch telemetry
installed (3 tier resources).`; `.cn0/telemetry/` holds 40 sessions whose timestamps track every CN
(re)start including the lead's 05:37–05:54 UTC runs, and `$CN0` contains 12 `query` entities whose
`Init` timestamps (05:54:48.387 …) match the engine log. `enable_batch_events` (default `true`,
`sirius_config.hpp:211`) is also on, hence the `batch_placement` files. Consequence for the lead's timing
runs: every CN and every standalone run is already paying the exporter cost (ndjson buffered in memory,
flushed at exit), and the performance YAMLs do not set `enable_quent: false`.

**Labels today.** Standalone: `sirius_set_query_label('<label>')` (table function, `sirius_extension.cpp:1682-1699,2447`)
or `gpu_execution(..., query_label => ...)` (`:158,605-619`), consumed once by the next query
(`sirius_context.hpp:149-153`, `physical_sirius_execution.cpp:132-136`); unlabelled →
`unnamed_query` (`sirius_engine.cpp:186`). The `$SA` sessions carry `q1_iter0..q6_iter4` (labelled by
`standalone_run.py`) plus 24 `unnamed_query` (warm-up / probe statements). CN: two compile-time
constants — `kQueryLabel = "sirius_ffi"` (`sirius_ffi.cpp:77`, result fragments via
`StandaloneQueryScope window(..., kQueryLabel)` and `sirius_interface iface(client, kQueryLabel)` at
`:276-284`) and `kFragmentQueryLabel = "sirius_streaming_fragment"` (`streaming_fragment.cpp:34`, passed at
`:182`). Tally over all CN sessions: cn0 181 `sirius_streaming_fragment` + 69 `sirius_ffi`, cn1 98+20,
cn2 85+25, cn3 59+9. `session_label` (→ `query_group_id_for`, `telemetry_context.cpp:141-157`) is never set
on the CN path, so all fragments land in `siriusDB-session-<pid>`.

## 5. Smallest additions to close each gap (file / function level; nothing implemented here)

Ordering rule from the brief: extend existing FSMs/attributes first; a new entity only where no existing
FSM can carry the event truthfully (called out explicitly below). Every probe must stay a no-op when the
telemetry context is null (`quent_data_batch_probe::create` already does this; new call sites must go
through the same `batch_telemetry_info` / registry guards).

1. **Fragment identity → Quent `query` label + group (no model change).**
   - FFI: add `Fragment::declare_label(std::string label, std::string group)` (before `build()`) in
     `$Q/src/include/sirius_ffi.hpp` / `src/sirius_ffi.cpp`; `streaming_fragment` takes the label instead of
     `kFragmentQueryLabel` (`streaming_fragment.cpp:182` → `sirius_interface(_context, label, group)`), and the
     result path replaces `kQueryLabel` at `sirius_ffi.cpp:276-284` (`StandaloneQueryScope` + `sirius_interface`)
     — this also fixes the engine-log `QueryBegin: sirius_ffi` line.
   - CN: `FragmentRun` (`fragment_executor.rs:110-129`) and `ExecuteRequest` (`engine.rs:53-77`) gain a
     `label: String` built in `compute_node_service.rs` from `Self::fragment_instance_id(params)` /
     `Self::query_id(params)` (`:1449-1463`) plus kind (`sender|receiver|result`), e.g.
     `01a06570-3543-77ae-a66c-cd5cb275951b/finst-…:sender`; `session_label` = the FE `query_id` so one FE
     query becomes one `query_group` (`{engine}-{query_id}`) spanning its fragments.
   - Result: `query.Init.instance_name` carries the FE ids; `query_group.Declaration.instance_name` groups them;
     the existing `plan → operator → task → data_batch` chain then attributes every pipeline to a fragment.
   - Zero-code interim: none for Quent; the CN log needs one `info!(fragment_instance_id, query_id, kind,
     elapsed_ms)` at `run_ready_fragment` (`compute_node_service.rs:773`) / `execute_fragment_with_inputs` (`:929`).
2. **CN id (no model change).** Emit `engine_name: "<engine_dir basename>"` next to `output_directory` in
   `derive_sirius_config_yaml` (`engine_settings.rs:105-108`) → `engine.Init.instance_name = ".cn0"`,
   `query_group` = `.cn0-session-<pid>`. Physical GPU: `gpu_device.Declaration` has only `ordinal`; the PCI id
   already known at `sirius_context.cpp:656` would need a `pci_bus_id: String` attribute in
   `$Q/rust/crates/telemetry/model/src/gpu_device.rs` (model change → bridge regen via
   `crates/telemetry/bridge/build.rs`, `quent_codegen::emit_cxx`).
3. **Scan byte ranges and estimated bytes per pipeline (no model change).** Fill
   `operator.Declaration.custom_attributes` (a `DynamicAttributes`, today `{}` at
   `telemetry_context.cpp:emit_plan_telemetry`) for pipelines whose source is `GPU_SCAN` with
   `files`, `byte_ranges` (`start`,`length`,`file_size` from `extract_scan_byte_ranges`, `sirius_ffi.cpp:114`
   → planner `sirius_physical_plan_generator.cpp`), and `estimated_compressed_bytes`. Actual compressed bytes
   read per task: the honest carrier is the scan task's `Computing` state — add `io_bytes: Option<u64>` to
   `task.rs` `Computing` (model change) and set it in `gpu_pipeline_task.cpp` from the split's
   `rg_slices[i].reserved_compressed_bytes` (`row_group_metadata.hpp:69`); output bytes are already there
   via `data_batch.Stationary`.
4. **Relay / park (no model change).** Relay: in `Fragment::relay_from`'s pull/push loop
   (`sirius_ffi.cpp:748-757`) call `batch_telemetry_registry::on_published(batch, repo, batch_origin::relay_input)`
   (one new `batch_origin` string, `batch_telemetry.hpp`) so the batch gets a `BatchRegistered→BatchQueued`
   placement on the `STREAMING_SOURCE` receiver port at relay time — that is the same FSM the operator path
   uses, and the relay is a real publish into that port's repository. Park: no new event needed once the
   relay is visible — park interval = sender `data_batch.Stationary` start … receiver `BatchRegistered`;
   bytes = `capacity_bytes`. Optional: a `data_batch.stationary => stationary` self-transition at push (same
   memory, same bytes) marks the exact move instant.
5. **nixl transfers (DataBatch FSM + one Memory and one Channel resource per peer).**
   - Receiver: `push_packed` (`sirius_ffi.cpp:914-916`) must pass a real `batch_telemetry_info{context,
     producer_pipeline_uuid}` — use the receiving `STREAMING_SOURCE` port's uuid (or a per-stream
     "remote sender" pseudo-operator declared in `emit_plan_telemetry`) — so remote batches exist as
     `data_batch` entities (today they are absent).
   - Transfer: model the packed frame as a `data_batch` (`Constructed{data_batch_id = source batch id,
     producer_pipeline_uuid = sink pipeline}` in `export_packed` after `stream.synchronize()`,
     `Stationary` on a new Memory resource `exchange_staging_arena` declared by `memory_context` when the arena
     exists; `InTransit{source=local arena, dest=peer arena, channel="staging(cn_a)->staging(cn_b)"}` around
     `write_and_wait` (`nixl_transport.rs:713-719`, which already returns the `Duration`); `Stationary` on the
     peer arena at DONE; `Destructed` at `staging_release`). The Channel resource (`quent_stdlib::channel::Channel
     {source_id,target_id, capacity{rate,bytes}}`) fits nixl exactly and can carry the canary rate as
     `capacity.rate`. This needs two FFI entry points on `Context` (`transfer_started(batch_id, peer, bytes)`,
     `transfer_completed(batch_id)`) because the WRITE is driven from Rust; the peer arena Memory has unknown
     capacity (`Option<u64>` = null) — declared per peer on first `ensure_session`. No new entity type: the
     packed frame is a batch of data and DataBatch's `in_transit` already means "a copy is crossing a channel".
   - Inflight packed exports = count of packed `data_batch` entities between `Constructed` and `Destructed`
     per arena Memory; the arena's `peak_live_bytes_` then becomes derivable and the `:168` log line redundant.
6. **H2D/D2H/D2D copies (DataBatch FSM, existing channels).** Result-collector D2H
   (`sirius_physical_result_collector.cpp:147-198`): construct the probe before the `clone_to<host>` copy and emit
   `in_transit{source=GPU memory, dest=HOST memory, channel="gpu-0->host-0"}` → `stationary(HOST)` after it
   (the channel is already declared by `memory_context`); same pattern for `push_packed`'s deep copy (needs a
   `staging->gpu-0` channel) and `chunked_pack` (`gpu-0->staging`). The partition gather
   (`sirius_physical_streaming_sink.cpp:160-182`) is a D2D within one memory space: no channel exists and
   inventing one would be a lie; keep it as the `Computing PARTITION(n)` / `STREAMING_SINK(n)` interval.
7. **Inflight I/O (the one place an existing FSM cannot carry it).** A uring request is neither a Task nor a
   DataBatch, so an honest per-request trace needs a new resource (`IoRing{capacity: entries = NUM_CHUNKS}`)
   and an FSM that uses it — declared explicitly as new. Cheapest honest alternative without a new entity:
   record ring occupancy as an attribute on the scan task — `Computing.io_inflight_peak: Option<u32>` sampled
   at `--inflight` (`uring_reactor.cpp:973`) — and let the sim use the GPU_SCAN Computing overlap as depth.
8. **Rows.** Add `rows: Option<u64>` to `data_batch.rs` `Constructed` (model change; `num_rows()` is at hand
   at every `make_data_batch` site) — batch_placement and the sink/relay/nixl paths inherit it for free.
9. **Flags.** Keep `enable_quent: false` in the performance YAMLs (`$SR/benchmarks/pinned/generated/cn*.yaml`
   today rely on the C++ default = on); derived YAML should emit `enable_quent` explicitly from a CLI flag
   (`--telemetry`), on for sim captures, off for timing. Keep `enable_batch_events` paired with it. Regenerate the
   bridge only for the model changes in 2/3/7/8 (`cargo build -p telemetry-bridge` runs `build.rs`).

Tests to size like `test/cpp/telemetry/test_telemetry_context.cpp` (tag `[telemetry_context]`, one
`TEST_CASE`, writes to a temp dir, greps ndjson lines): one case per new event family — label/group on the
query FSM, relay placement origin, remote-batch `data_batch` presence, transfer `in_transit` pair.

## Appendix A — CN-log facts kept from the previous version

- Cluster logs: `$SP/step0/sot-cluster-sf1.log`, `$SP/step3/sot-sf10-1file/cluster.log`,
  `$SP/step3/sot-sf10-multi/cluster.log` (`$SP` = the scratchpad root). Rust `tracing` fmt at
  `RUST_LOG=sirius_starrocks_cn=info,info`, span-close events on, FE log4j lines interleaved; **no CN tag on
  CN lines** (`benchmarks/cluster8.sh:76` launches all CNs into one stdout; brpc `91i2` ↔ cn *i* is inferred).
- Per-fragment RPC wall time only as `handle_connection{peer=…}:exec_plan_fragment: … close time.busy=7.42µs time.idle=302ms`
  (`compute_node_service.rs:321-345`, `spawn_blocking` → `time.idle` ≈ fragment wall time), no ids.
- nixl: `transmitted batches via nixl stream_id=2 sender_id=1 dest=127.0.0.1:9112 batches=1 bytes=4000704`
  (`nixl_transport.rs:788`); `nixl bandwidth canary peer=127.0.0.1:9112 gbps="401.7" bytes=16777216 floor_gbps=2.0`
  (`:673`; the `:9102` peer measured 85–103 GB/s vs 302–404 GB/s to the others); receiver `received remote batches
  stream_id=3 sender_id=0 batches=1` (`engine.rs:662`), frame bytes at debug (`compute_node_service.rs:699`).
- Relay: `relayed native batches across a fragment boundary stream_id=3 sender_id=1 batches=1` (`engine.rs:618`);
  cardinality: `declared input stream cardinality stream_id=2 rows=6001215` (`engine.rs:555`).
- Engine logs are info-level (`SIRIUS_LOG_LEVEL` unset): `tools/log_analyzer/parse_logs.py` needs trace/debug
  lines and exits; per-operator rows/bytes (`gpu_pipeline_task.cpp:154`), byte ranges
  (`parquet_gpu_ingestible.cpp:828`) and downgrade summaries (`downgrade_executor.cpp:377`) are debug/trace.
- FE: `fe.audit.log` is empty; audit lines go to stdout (`|Time=1268|ScanBytes=0|ScanRows=0|ReturnRows=1|…|QueryId=01a06570-…|TransmittedBytes=0|`)
  — the CN never reports exec status, so `ScanBytes`/`TransmittedBytes` are always 0.
- Ids the CN does print: `cancel_plan_fragment … fragment_instance_id=01a06560-364a-7ff2-96a6-b12b820fbd85 query_id=Some(FragmentInstanceId(01a06560-364a-7ff2-96a6-b12b820fbd84))` after the query finished.
- Env-only knobs: `SIRIUS_CN_DUMP_FRAGMENTS=<dir>` (fragment params + Substrait per fragment),
  `SIRIUS_LOG_LEVEL=debug|trace` (honoured by `sirius_ffi.cpp:176-181`), `RUST_LOG=sirius_starrocks_cn=debug`.
- No nsys capture exists for any CN or standalone run; the old nsys table-schema notes were dropped from this
  document because the brief rules nsys out.
