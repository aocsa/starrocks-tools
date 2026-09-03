# oom-failures family: q05 q08 q09 q17 q18 q21 (SF1000, 1 CN, 100 GiB pool)

Evidence root: scratchpad/perf/sf1000. Source: /home/prestouser/aocsa/sirius-stacks-wt/perf.
All six fail on 1 CN with `GPU pipeline task exceeded maximum retry limit (100) ... OOM at operator GPU_SCAN (index 0)`
(cn1/runs/qNN.r0.err). cn4 and standalone arms skipped them; standalone-failed arm ran them all: PASS.

## 1. Arms

| query | cn1 (ms, status) | standalone-failed cold/warm ms (rows) | reschedule warnings (cn1-cnlog.txt) |
|---|---|---|---|
| q05 | 101460 fail | 5191 / 2786 (5) | 2500 = 25 tasks x 100 |
| q08 | 168285 fail | 3835 / 3674 (2) | 3600 = 36 x 100 |
| q09 | 232533 fail | 6818 / 5427 (175) | 5600 = 56 x 100 |
| q17 | 103894 fail | 4386 / 3786 (1) | 2100 = 21 x 100 |
| q18 | 54202 fail | 2764 / 2691 (100) | 2300 = 23 x 100 |
| q21 | 114485 fail | 6534 / 6401 (100) | 1800 = 18 x 100 |

Same GPU, same 100 GiB pool (sirius-standalone-quent-failed.yaml: usage_limit 100GiB, host 160GiB), same SQL, same parquet.

## 2. What is being parked: the whole lineitem projection crosses an exchange

FE plans (survey/plan-summary.txt, 1 CN; identical shape on 4 CNs in plan-summary-4cn.txt):
- q05 `10:INNER JOIN (PARTITIONED) build=9:EXCHANGE<-lineitem probe=7:EXCHANGE`
- q08 `5:INNER JOIN (PARTITIONED) build=4:EXCHANGE<-lineitem probe=2:EXCHANGE<-part`
- q09 `5:INNER JOIN (PARTITIONED) build=4:EXCHANGE<-lineitem probe=2:EXCHANGE<-part`
- q17 `5:INNER JOIN (PARTITIONED) build=4:EXCHANGE<-part probe=1:EXCHANGE<-lineitem`
- q18 `11:INNER JOIN (BUCKET_SHUFFLE(S)) build=10:EXCHANGE<-lineitem`
- q21 `10:RIGHT ANTI JOIN (PARTITIONED) build=9:EXCHANGE<-lineitem probe=2:EXCHANGE<-lineitem; 14:LEFT SEMI JOIN build=13:EXCHANGE<-lineitem`
Every FE node has cardinality 1 (card-compare-cn1.txt; StatisticsCalculator.java:664-676 `builder.setOutputRowCount(1)`).
q05 lineitem sender dump: cn1/dump/fragment-0141.txt (instance ...5774, 1 FILE_SCAN node, 32 scan ranges,
`dest_node_id: 9, output_partition type_: TPartitionType(2)` = HASH_PARTITIONED, Partitions.thrift:41-49).

Size of that projection = the standalone GPU_SCAN of the same query (agent-notes/standalone-failed-quent.txt, run via
`python3 quent_bp.py standalone-failed/quent/*/`):

| query | standalone lineitem GPU_SCAN tasks / bytes | cn1 lineitem sender: parked batches (STREAMING_SINK in) + stuck tasks |
|---|---|---|
| q05 | 65 / 171.00 GB | 40 batches 104.30 GB + 25 stuck (= 65) |
| q08 | 74 / 195.75 GB | 38 / 99.54 GB + 36 (= 74) |
| q09 | 93 / 244.50 GB | 37 / 96.42 GB + 56 (= 93) |
| q17 | 46 / 122.25 GB | 25 / 66.26 GB + 21 (= 46) |
| q18 | 37 / 97.50 GB | 14 / 35.87 GB + 23 (= 37) |
| q21 (2nd lineitem scan) | 46 / 123.00 GB | 10 / 25.37 GB + 18 (= 28; other 18 tasks were the 1st scan? see below) |
(cn1-quent.txt sections q05.r0 ... q21.r0; the stuck count is the number of distinct original tasks retried 100x.)

Standalone DuckDB plan (standalone-failed/runs/q05.explain.txt:50-61): READ_PARQUET lineitem ~6.11B rows is the PROBE
of the top HASH_JOIN, build side ~61.6M rows (orders x customer x nation x region); lineitem streams, never materialises.
q18 standalone (q18.explain.txt:37-56, 92-117): lineitem is the probe of `l_orderkey = o_orderkey`, the HAVING subquery is a
HASH_GROUP_BY over lineitem feeding a SEMI join on orders.

## 3. Pool arithmetic per failure (engine-cn0.log `[gpu_pool] GPU:0 QueryBegin/End allocated=`)

Fragments run ONE AT A TIME on the CN engine thread (engine.rs:278-280 "One request at a time"; cluster.log order).
Receivers only build after all their senders ran (local_exchange.rs:248-283 take_ready; engine.rs:610-644 relay_from after
build, `run()` then park at engine.rs:697-716; sirius_ffi.cpp:739-745 relay_from requires source.ran).

| query | pool before the lineitem sender started | parked by it before 1st OOM | total at OOM |
|---|---|---|---|
| q05 | ~0 (nation+region only) | 39 x 2.67 GB = 104.06 GB in 1.316 s (Quent data_batch Stationary) | 104.3 GB (End 104.30 GB @13:05:24.624) |
| q08 | 4.70 GB (q05's post-failure leftovers 4.69 + part 0.01) | 99.54 GB | ~104 GB |
| q09 | 8.78 GB (q08 leftovers: cb1 7.46 + cb0 1.24 + cb2 0.08) | 96.42 GB | 105.24 GB (End @13:12:22.323) |
| q17 | 38.16 GB (q09 leftovers 31.56 + q16 leftover 6.6; Begin 38.16 GB @13:13:12.867) | 66.26 GB | 104.4 GB |
| q18 | 0.00 then own senders: customer 3.94 + agg ~24 + orders 36.75 = 64.7 GB | 35.87 GB | ~100.5 GB (End 104.42 @13:14:56.804) |
| q21 | 77.76 GB (own 1st lineitem scan, filtered, ...2acd; Begin 77.76 @13:16:15.274) | 25.37 GB | 103.1 GB |
Pool cap = 107,374,182,400 B; every OOM line reports `global usage 107374182400 bytes (102400.00 MB)`.

First OOM per window (engine log): q05 +1.61 s after query start, q08 +1.73 s, q17 +1.2 s, q21 +2.7 s; last reschedule at
+101.33 s / +168.15 s ... (script in this session: parse `reschedule (retry N/100) for task T (original task O)`).
Per original task: exactly 100 retries; distinct task ids = retries (each retry is a new task id).

## 4. Why nothing is freed (0 bytes) although HOST=160 GiB is configured

Every OOM cycle logs (engine log, 2503 times for q05):
- `[downgrade] [GPU:N] downgrade request not satisfied and disk memory space is not configured; data cannot be spilled`
- `after downgrade (0 bytes freed), reservation still partial (3318948864/5803790251 bytes) ... proceeding with partial reservation`
- `GPU_SCAN (id=0) threw during execute: std::bad_alloc: out_of_memory`
Mechanism: downgrade_executor.cpp TIER 1 iterates `_data_repo_registry.get_all()` (lines 222-227), TIER 2 the pipeline task
queue (293-300). The sender's output repositories are standalone `shared_data_repository`s that "escape
data_repository_manager_ cleanup so sender output outlives this fragment" (streaming_fragment.cpp:103-112) and the sender's
engine query has already ended (`data_repository_registry_.erase(query_id)` sirius_context.cpp:436). So parked batches
are invisible to the downgrade sweep; the warning text (357-361) blames the missing DISK tier although HOST targets exist
(165-190). sirius_ffi.cpp:1062-1066 `output_row_count` throws on a non-GPU-resident parked batch, so spilling parked
outputs also needs the relay/count path to accept HOST-tier batches.

## 5. Retry storm cost

gpu_pipeline_executor.cpp:348 `MAX_RETRIES = 100`, :410 `sleep_for(50ms)`, no progress/feasibility check; each retry
re-executes GPU_SCAN from operator index 0 (re-reads the split): q05 GPU_SCAN Computing sum 229.5 s over 2543 executions,
Queued sum 2228 s (cn1-quent.txt q05.r0 ...5774). 25 tasks x 100 retries / 4 slots ~= 100 s wall before the query dies.

## 6. Post-failure leak

cluster.log: `acknowledging cancel_plan_fragment (best-effort: no engine-side abort yet)`; after `fragment run failed`
the remaining queued senders of the dead query still start and park (q05: 5773, 5777, 5776, 5775 at 13:05:24.632+;
q09: 947/946/945/944 at 13:12:22.3+). engine.rs:286-300 wipes parked outputs only when a run returns Err.
Measured baseline carried into later queries: 4.69 GB (q06,q07), 8.78 GB (q09), 31.56 GB (q10..q16), 38.16 GB (q17);
q16's parked scan outputs dwelt 415.757 s (cn1-quent.txt q16.r0).

## 7. FE distribution choice at cardinality 1

RequiredPropertyDeriver.java:140-163 offers both broadcast and shuffle; CostModel.java:419-426 BROADCAST costs
outputSize x aliveBackendNumber, :432-441 SHUFFLE network cost is 0 when isSingleBackendAndComputeNode, so on 1 CN
shuffle wins ties and both join inputs become exchanges (PARTITIONED). node_translator.rs:1923-1929 already notes
the FE reports cardinality 1 for every FILES() scan and that "bounding it belongs to the executor".

## Commands run
- cat results.md, cn1-cnlog.txt, reservation-ratio.txt; awk sections of cn1-quent.txt, plan-summary*.txt, card-compare-cn1.txt
- python over cn1/engine-cn0.log: retry stats per run window; awk windows saved to agent-notes/raw/eng-*.log
- sed/grep on cn1/cluster.log for fragment run started/finished/failed + declared cardinalities per window
- python over cn1/quent/.cn0/*/data_batch/*.ndjson: Stationary capacity_bytes timeline per window
- python3 quent_bp.py standalone-failed/quent/*/ --json agent-notes/standalone-failed-quent.json > agent-notes/standalone-failed-quent.txt
- python over cn1/dump/fragment-013[4-9]/014[01].txt: instance id, node types, tables, scan ranges, partition type
- source: gpu_pipeline_executor.cpp 170-450, downgrade_executor.cpp 165-430, streaming_fragment.cpp 95-262, sirius_ffi.cpp 727-1085,
  sirius_context.cpp 385-445, engine.rs 255-720, local_exchange.rs 1-300, StatisticsCalculator.java 664-676,
  RequiredPropertyDeriver.java 140-163, CostModel.java 401-460, node_translator.rs 1912-1936
