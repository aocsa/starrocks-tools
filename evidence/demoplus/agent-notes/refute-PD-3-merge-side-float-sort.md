# PD-3 check: q20/q15 MERGE_GROUP_BY speedup = baseline's ungated merge-side float-sum sort

Verdict: NOT refuted. Every measured number and every source citation in PD-3 checks out; controls not cited by the
finding point the same way. Two corrections of emphasis (units of the "1.7 s win"; the q15 "defence" has no measured
effect) and one provenance detail. Written 2026-09-04, read-only.

Trees: perf = /home/prestouser/aocsa/sirius-stacks-wt/perf @ 45dab3be = d24f02c4 (feat/pin-table-cn) + 85658f09 (gate)
+ 9a5a4da6 (async dispatch) + 45dab3be (Quent labels) [git log there]. demo-plus = .../sirius-stacks-wt/demo-plus @
281b13bc, merge-base with dev = 01613070.

## Measured (prof-perf-cn4.json vs prof-dp-cn4.json, ops_type = [tasks, Computing s, input bytes])

| run | op | base s | dp s | ratio | tasks | GB |
|---|---|---:|---:|---:|---:|---:|
| q20.r1/r2 | MERGE_GROUP_BY | 2.084 / 2.087 | 0.408 / 0.406 | 5.1x | 12 / 12 | 24.35 / 24.34 |
| q20 per instance | MERGE_GROUP_BY(4) sender side | 1.500 / 1.497 | 0.315 / 0.314 | 4.8x | 8 | 14.11 |
| q20 per instance | MERGE_GROUP_BY(6) receiver side | 0.584 / 0.590 | 0.093 / 0.092 | 6.3x | 4 | 10.24 |
| q15.r1/r2 | MERGE_GROUP_BY | 0.346 / 0.346 | 0.054 / 0.054 | 6.4x | 16 | 5.40 |
| q15 per instance | MERGE_GROUP_BY(4) revenue | 0.287 | 0.045 | 6.4x | 8 | 4.45 |
| q10 | MERGE_GROUP_BY | 0.095 | 0.063 | 1.5x | 4 | 7.91 |
| q11 | MERGE_GROUP_BY | 0.041 | 0.011 | 3.8x | 8 | 0.73 |
| q03 | MERGE_GROUP_BY | 0.013 | 0.005 | 2.6x | 4 | 0.27 |
| **q13 (count only)** | MERGE_GROUP_BY(10) | 0.021 | 0.021 | **1.0x** | 4 | 1.80 |
| **q04 (count only)** | MERGE_GROUP_BY | 0.005 | 0.004 | 1.1x | 7 | ~0 |
| **q02 (MIN)** | MERGE_GROUP_BY | 0.000 | 0.000 | 1.0x | 5 / 7 | 2.83 |
| q01 / q20 / q10 | HASH_GROUP_BY (local) | 4.59 / 0.76,0.65 / 0.329 | 4.48 / 0.64,0.61 / 0.320 | ~1.0x | same | same |

Merges with no FP64 SUM (q13, q04, q02) are identical across arms; merges with an FP64 SUM are 2.6-6.4x slower in the
baseline, and the local HASH_GROUP_BY is equal in both arms. That is exactly the footprint of a merge-only sort keyed on
`is_order_sensitive_sum` (SUM over FLOAT32/FLOAT64, perf/src/op/aggregate/aggregate_op_util.cpp:237-241).

Types on the CN path (both arms lower decimal SUM to FP64): baseline cluster.log:47065
`Aggregate[$0, $1 => $0, $1, sum(($2)::!fp64?):fp64?]` over `Read[... l_partkey:i32?, l_suppkey:i32?, l_quantity:decimal?<15, 2>]`
with the 1994-01-01/1995-01-01 filter (q20 sender), :47703 `Aggregate[$0, $1 => $0, $1, sum($2):fp64?]` over
`Read[sirius_stream_9 => col_28:i32?, col_29:i32?, col_43:fp64?]` (q20 receiver); dp-cn4/cluster.log:50396 / :50991 identical.
q15: baseline :40115 and dp :40646 `Aggregate[$1 => $1, sum(($0)::!fp64?):fp64?]`.

Wall (runs/runs.csv): q20 base r1 1978 / r2 2022 -> 2000 ms; dp 1836 / 1786 -> 1811 ms (-9%). q15 base 2028 / 2041 ->
2035; dp 2076 / 2070 -> 2073. q15 rows base 1/0/0, dp 1/1/0. Non-scan Computing q20 3.09 -> 1.33 s, q15 GPU_SCAN
23.7 -> 24.8 s (compare-warm-perf-vs-dp.txt via profile-diff.md section 2).

Gate state in both runs: neither perf/sf1000/capture-cn.sh nor fix/capture-arm.sh sets SIRIUS_CANONICAL_FLOAT_SUMS
(grep), so the local-aggregate sort was off in both arms; only the merge side differs.

## Source (verified)

- perf/src/op/merge/gpu_merge_impl.cpp:125-134 `cudf::sort` of partials before an FP ungrouped SUM; :206-220
  `canonical_sort_cols` + `canonicalize_row_order` when any aggregate `is_order_sensitive_sum`; :284-292 groupby with
  `cudf::sorted::YES` on that path; :239 dict-encode skipped on that path. No `canonical_float_sums_enabled()` call in
  the file (grep over perf/src hits only aggregate_op_util.{hpp,cpp} and gpu_aggregate_impl.cpp:164-167).
- 85658f09 (`git show` in sirius-stacks): hunk gpu_aggregate_impl.cpp @@ -161,7 +161,10 (gate at new lines 164-167),
  plus the helper in aggregate_op_util.{hpp,cpp} and docs/super-sirius/configuration.md. Its message: "the merge-side
  canonicalization of partial states stays on". Merge file untouched.
- `throw_if_int64_sum_could_overflow` returns immediately for non-INT64/UINT64 (aggregate_op_util.cpp:188-189), so the
  guard costs nothing on FP64 merges; the delta is the sort + sort-based groupby.
- Lineage: `git log dev..feat/pin-table-cn -- src/op/merge/gpu_merge_impl.cpp` = 441f05b2 only.
  `git log --all -S'canonicalize_row_order' -- src/op/merge/gpu_merge_impl.cpp` = 441f05b2, 0bc03cca (squash siblings),
  5d149277 (2026-08-07 "fix(op): bit-stable float grouped sums via the sort-based groupby path", only on
  aocsa/demo-multi-cn, demo-multi-cn-2b). dev's copy (01613070, 340 lines) has 0 matches for canonical/cudf::sort; the
  perf-dev merge-base 84ea4ab5 copy has 0 matches; 84ea4ab5..dev = 4 commits, none under src. In the main repo dev
  (de719006) and origin/dev (a57441bd) are ancestors of feat/pin-table-cn. demo-plus's merge file is byte-identical to
  dev's (`git diff dev demo/q1q6-integration-plus-fixes -- src/op/merge/gpu_merge_impl.cpp` empty).
- `diff -u demo-plus/... perf/...` merge file: 69 changed lines, all of them the canonical path, the sorted-groupby
  arguments, the overflow guard, and its include. demo-plus src/op/aggregate has no canonical/is_order_sensitive_sum,
  and no throw_if_int64_sum_could_overflow anywhere in src.

## Corrections / nuances (do not change the verdict)

1. Units: the impact's "1.7 s q20 win" is summed per-task Computing across 12 merge tasks on 4 GPUs. The client-visible
   win is 189 ms (2000 -> 1811 ms), engine span 1.70 -> 1.54 s (profile-diff.md section 2). The merge overlaps other
   work, so the wall gain is ~1/9 of the operator-time gain.
2. The "defence" has no measured effect on q15. 1-CN A/B (perf/sf1000/cn1-canon-q15/runs/runs.csv, merge-side sort
   always on AND SIRIUS_CANONICAL_FLOAT_SUMS=1): rows 0,1,0,0,1,0 (4 of 6 runs empty); cn1-nocanon-q15: 0,0,1,0,0,0
   (5 of 6). 4 CNs: baseline 2 of 3 empty, dp 1 of 3. The operator-level statement (perf's merged sum depends only on
   the multiset of partials) is true by construction, but WORKFLOW-PLAN.md fact 21 already showed the two independent
   revenue computations feed different partial multisets, so q15's equality join is not rescued by any merge-order
   canonicalisation. PD-3's hedge ("one fewer defence", "both arms already non-deterministic") is right; a reader should
   not expect porting the merge-side sort to fix q15.
3. Provenance: the sort was authored in 5d149277 on aocsa/demo-multi-cn and reached feat/pin-table-cn through the squash
   441f05b2; "entered with 441f05b2" is correct for feat/pin-table-cn's history.
4. The step "feat/pin-table-cn..dev -- src is empty, so dev never had it" also needs the merge-base copy to lack the sort
   (it does: 84ea4ab5, 0 matches). With that, the conclusion holds.
