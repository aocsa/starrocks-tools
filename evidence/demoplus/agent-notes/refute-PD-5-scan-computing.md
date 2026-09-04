# PD-5 check: lower GPU_SCAN Computing on demo-plus (q03 -12%, q02 -32%)

Verdict: not refuted as a whole. The numbers, "same bytes and task counts", "no wall benefit" and "q02 is
baseline run-to-run variance" all hold. Two supporting statements are wrong and are corrected below:
(a) the scan code does not differ between the trees except for the perf tree's telemetry, and the byte-range
path is byte-identical in both; (b) q03 is not unattributed: 37-46% of its GPU_SCAN delta is the orders scan
(GPU_SCAN(3)) running staggered under the carve's inline RPC dispatch (PD-1), the same mechanism the note used
for q06. Working script: `refute-PD-5-work/scan_tasks.py <arm> <run>...` (per-task GPU_SCAN spans by op, CN,
start offset).

## Numbers verified (prof-*.json, `scan_tasks.py`)
- q03 GPU_SCAN: base 11.447 / 11.368 s (r1/r2), dp 9.985 / 10.147 s; 82 spans (70 GPU_SCAN(0) + 12 GPU_SCAN(3));
  total input 177,618,488,778 bytes in both arms and both runs (per-task sizes agree to <1 MB on 2.28 GB tasks).
- per-CN dp scan 2.35-2.62 s is r1 only; r2 is 2.43-2.72 s (base r2 2.75-2.88).
- q02 GPU_SCAN: base r1 3.582 s (partsupp tasks 319-355 ms on all 4 CNs) vs r2 1.708 s (76-108 ms); dp 1.731 /
  1.876 s (72-107 ms) = base r2. Variance/warming in the baseline, as the finding says.
- q01 GPU_SCAN: dp 15.926 / 23.877 s, base 18.509 / 20.641 s; wall 1846/2824 vs 2022/2298 ms.

## (a) Scan code: telemetry-only difference; byte-range path identical
`diff -u` perf vs demo-plus (both worktrees under sirius-stacks-wt, read-only):
- `src/op/scan/sirius_gpu_scan_operator.cpp`: only the perf tree's `scan_split_read` probe (`read_started`/
  `read_completed`, `summarize_sources`, `telemetry::emit_scan_split_read`, commit 45dab3be). No other hunk.
- `src/op/scan/parquet_gpu_ingestible.cpp`, `src/op/scan/parquet_byte_range.cpp`, `src/include/op/scan/
  parquet_byte_range.hpp`, `src/scan_manager/sirius_scan_manager.cpp`: 0 diff lines. a22235e1 / 94a77836 are not
  ancestors of the perf HEAD, but their content is (feat/pin-table-cn carries the same code).
- `parquet_gpu_ingestible.hpp`, `gpu_ingestible_types.hpp`: removal of the `source_reads()` telemetry hook plus a
  doc comment.
- Translator `scan_paths.rs`: both emit `[start, start+length)` byte-range splits (lines 24-28 in both); 67 diff
  lines = HashMap->BTreeMap and error text (d88265aa's "instead of refusing" is about earlier behaviour, not the
  perf tree).
Cost of the perf-only telemetry inside Computing, from the baseline's own events: q03.r1 GPU_SCAN Computing
11.447 s = materialize_ns 11.404 + finish_ns 0.015 + residual 0.029 s (0.35 ms/split); q03.r2 residual 0.029 s;
q01.r1 0.023 s; q06.r1 0.020 s. So >99.7% of every Computing span is `materialize_table` (I/O + decode), which
is identical code in both trees; the telemetry cannot produce a 1.2-1.5 s gap.

## (b) q03 decomposition (per task, both warm runs)
GPU_SCAN(3) = orders scan in the join fragment (`inputs=4 outputs=4`), 12 tasks, 30.75 GB:
- base r1 2.197 s: all 12 start at 1227-1235 ms on 4 CNs (4-way concurrent), 128-223 ms each;
  r2 2.039 s, start 1215-1222 ms, 167-174 ms each.
- dp r1 1.526 s: cn1 starts alone at 1208 ms (104-107 ms each), cn0/cn2/cn3 at 1371-1387 ms (119-146 ms);
  r2 1.583 s: cn3 alone at 1248 ms (109-111 ms), the other three at 1416-1430 ms (114-153 ms).
- Cause (cluster.log, `percn_timeline.py`): the join fragment on one CN starts at 1185.6 ms (r1) / 1226.0 (r2)
  right after its customer scan, the other three start at 1350.7-1361.0 / 1393.5-1405.0 ms, i.e. after the first
  one FINISHED (1350.2 / 1393.1); that CN's exec_plan_fragment RPC closed with idle=217 / 224 ms (customer scan
  39 ms + join fragment 164-167 ms run inline). In the baseline all four join fragments start within 3 ms
  (1206.2-1209.0 ms). Same PD-1 mechanism as q06 (dp q06.r1: the late lone cn0 scans 1.415 s vs 1.99-2.06 s on the
  three concurrent CNs; base 2.29-2.40 s on four).
- Share: -0.67 s of -1.46 s (r1, 46%) and -0.46 s of -1.22 s (r2, 37%).
GPU_SCAN(0) = lineitem (+ customer) scans, 70 tasks, 146.87 GB: base 9.250 / 9.329 s vs dp 8.460 / 8.565 s
(-8.5%), lower on all 4 CNs in both runs; all four lineitem senders start within 1 ms in both arms (4-way
concurrent), so this part is not the dispatch effect and stays unattributed. It is smaller than the within-arm
spread of the same scans on q01 (dp 15.9 vs 23.9 s, base 18.5 vs 20.6 s) and than the cold-run swing of q03
itself (dp r0 24.84 s vs base r0 16.64 s), so the variance reading is consistent with it.

## Impact correction
"Same CN binary, engine swapped" is the wrong A/B axis for this delta: the engine scan path is identical, and
the measured difference in scan concurrency comes from the CN dispatch. Swap the CN binary (or restore the
async sender dispatch) with the same engine; n >= 5 warm runs because of the lineitem part's variance.
