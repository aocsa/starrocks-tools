# refute check: scaleout-4cn-1 (demo-plus vs perf baseline at 4 CNs) — 2026-09-04

Verdict: NOT refuted on the core claim; two sub-claims are wrong and one attribution can be pinned to source.

## Verified (measured)
- Warm medians (mean of r1/r2) from `demoplus/arms/dp-cn4/runs/runs.csv` vs `perf/sf1000/cn4/runs/runs.csv`, 15 common
  queries: 21,699.5 ms vs 21,082 ms = 1.029x. Per-query dp-perf (ms): q01 +175, q02 -13, q03 +122.5, q04 +66, q06 +371.5,
  q07 -37.5, q10 -3.5, q11 +4.5, q12 +6.5, q13 +5, q14 +32, q15 +38.5, q19 +18.5, q20 -189, q22 +20.5.
- Plans identical: `_dispatch_skew.py` re-run on both cluster.logs gives the same frags per run (9/49/17/17/5/39/22/32/17/17/
  13/22/13/26/18), same declared-cardinality count/sum (q03 13/3,500,159,817; q10 21/1,802,745,091; q22 14/6,000,000,043) and
  same nixl GB (q03 60.34, q07 69.01, q10 48.04, q22 18.56, q12 23.47, q04 23.64). `plan-shapes-dp-cn4.txt` vs
  `plan-shapes-perf-cn4.txt` differ only by line order and the extra dp queries. `grep -c 'fused sender fragment'
  dp-cn4/cluster.log` = 0.
- q20 MERGE_GROUP_BY: `prof-perf-cn4.txt` 2.088/2.084/2.087 s vs `prof-dp-cn4.txt` 0.413/0.408/0.406 s. q01 GPU_SCAN r1/r2
  15.926/23.877 s for 1846/2824 ms (prof-dp-cn4.txt).
- Tooling: dp `transmitted batches via nixl` / `declared input stream cardinality` lines carry no `query_id=`; perf lines do;
  `perf/sf1000/cnlog_extract.py:28` keys on query_id.

## Contradicted
1. "The six fail on the cold run exactly as before." The baseline never ran the six at 4 CNs: `perf/sf1000/cn4/runs/runs.csv`
   has no q05/q08/q09/q16/q17/q18/q21 rows; `perf/sf1000/results.md` shows `-`; the report (lines 26, 37) says cn4 ran only the
   15 that passed on cn1. The only baseline failure is at 1 CN: engine-side `GPU pipeline task exceeded maximum retry limit
   (100) ... OOM at operator GPU_SCAN` after 54-232 s (`perf/sf1000/cn1/runs/q05,q08,q09,q17,q18,q21.r0.err`, runs.csv ms
   101460/168285/232533/103894/54202/114485). dp-cn4's six fail CN-side in 1.3-2.4 s with `exchange staging arena exhausted`
   (`dp-cn4/runs/*.r0.err`). And at 1 CN demo-plus passes all six (`dp-cn1-six/runs/runs.csv`, 18/18 pass). There is no
   "before" at 4 CNs, and where there is one the outcome and mechanism differ.
2. "everything else within +-0.02 s": q04 +0.066, q07 -0.038, q15 +0.038, q14 +0.032 (q07 is missing from the finding's own
   evidence list).
3. Impact "the 16 queries that already passed at 4 CNs": the baseline passed 15 at 4 CNs; q16 was never run there (cn1
   translator failure `multi_distinct_count ...`, `perf/sf1000/cn1/runs/q16.r0.err`).

## Attribution pinned to source (was "inference")
- q20 -0.19 s: `sirius-stacks` `git diff perf/profile-sf1000 demo/q1q6-integration-plus-fixes -- src/op/merge/gpu_merge_impl.cpp`
  removes the merge-side canonical float-sum sort (perf branch lines 211-220, 289: `canonicalize_row_order` + sort-based groupby
  whenever an aggregate is a FLOAT32/FLOAT64 SUM). Commit 85658f09's gate `SIRIUS_CANONICAL_FLOAT_SUMS` covers only the local
  aggregate ("The merge-side canonicalization is unaffected"), so the baseline always paid it (24 GB into MERGE_GROUP_BY on q20,
  5.4 GB on q15: 0.346 -> 0.054 s). Dev base has no such code. Direction of the finding's inference is right; mechanism now named.
- q06 +0.37 s (+44%): not noise. Every dp run holds `exec_plan_fragment` open for the sender's run (`_dispatch_skew.py`
  rpc_idle max 452-2620 ms in all 48 passing dp runs vs 1 ms in all 45 baseline runs). The perf branch has the opt-in
  `SIRIUS_CN_ASYNC_SENDER_DISPATCH` (perf/profile-sf1000 `experimental/starrocks/src/compute_node_service.rs:208,271,291`,
  commit 9a5a4da6); `git grep ASYNC_SENDER_DISPATCH demo/q1q6-integration-plus-fixes -- experimental/starrocks/src` = 0 hits,
  while `fix/capture-arm.sh:14` exported it. q06 is the only passing query where the FE's stage-2 deploy of the leaf on the
  result CN waits for the other three leaves (leaf skew 611-662 ms vs 1 ms baseline). Not one of the four fixes, but a
  branch-level difference the finding leaves unnamed.
