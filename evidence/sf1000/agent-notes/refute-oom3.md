# OOM-3 source check (refuter) — verdict: NOT refuted; two corrections

Measured (cn1/engine-cn0.log, q05 window 13:03:43-13:05:25): 2503 "downgrade request not satisfied ... disk", 2503 "after downgrade (0 bytes freed)",
2500 "reschedule (retry", 2 "exceeded"; whole log 17917 downgrade warnings, 0 with non-zero bytes freed; 0 "un-consumed data batch" leak warnings.
[gpu_pool] window 78 query 219 (= q05 lineitem sender, 13:03:43.434 -> 13:05:24.624) QueryEnd allocated=104297693440 peak=107374182400 (100 GiB cap).
Quent q05 fragment ...5774: STREAMING_SINK n=40 in=104.30GB, GPU_SCAN n=2543 (cn1-quent.txt:353-355).

Code: streaming_fragment.cpp:103-113 (repos created outside the manager, shared_ptr); data_repository_manager.hpp:108-118 (manager owns unique_ptr repos,
only caller of add_new_repository is pipeline/repository_wiring_materializer.cpp:68 = inter-pipeline ports); downgrade_executor.cpp:223-232 (TIER 1 = registry
managers only), 294-300 (TIER 2 = task queue), 357-363 (disk warning independent of candidate count); sirius_ffi.cpp:1025-1026 (lifecycle finish right after
run) -> sirius_context.cpp:591-603 -> run_mandatory_cleanup:436 erase(query_id). batch_stream.cpp:41-52 push = add_data_batch only (idle, unsubscribed).
CN: engine.rs:564 output_row_count(...).ok() -> 582-585 "row count unknown; planning without a declared cardinality" (throw is swallowed, not fatal);
sirius_ffi.cpp:822-825 export_packed throws on non-GPU batch (4-CN path would fail). sirius_ffi.cpp:785-797 relay_from moves shared_ptr handles only;
receiver upgrades HOST->GPU in pipelineable_operator_data::prepare_for_processing -> lock_or_prepare_batch (sirius_physical_operator.cpp:98,
batch_lock_utils.hpp "HOST/DISK -> GPU upgrade/readback").

Corrections: (1) in the measured failures the bytes that fill the pool are the RUNNING sender's own sink output (query not ended; its manager exists but has
zero repositories) — the escape from the manager is the whole mechanism, "query already ended" only covers the small parked-after-run outputs.
(2) "relay_from accept HOST-tier batches / re-materialise on push" is already covered for the local relay; only output_row_count (degrades planning) and
export_packed (fails) need HOST-tier support. The proposed "oldest parked query first" order conflicts with the newest-first policy comment at
downgrade_executor.cpp:210-216 and would not pick the candidate that matters here (the newest, running fragment's sink output).
