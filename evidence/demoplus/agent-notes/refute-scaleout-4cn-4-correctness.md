# Refutation check: scaleout-4cn-4 (correctness vs oracle identical to baseline)

Verdict: the impact conclusion (no correctness regression) survives, but three specific claims are
contradicted by the files. All paths relative to the scratchpad root.

## 1. "cell-for-cell identical to the baseline" -- false (measured)
`cmp` of the 45 common (query,run) .out files, demoplus/arms/dp-cn4/runs vs perf/sf1000/cn4/runs:
26 identical, 19 differ. Differing: q01/q03/q07/q10/q22 on all three runs, q14.r1, q15.r0, q15.r1, q19.r2.
Differences are last-digit float-sum noise, e.g.
- q01.r1 sum_base_price 56616527268757.16 (dp) vs 56616527268757.19 (base)
- q22.r0 cntrycode 17 totacctbal 6820077715.450003 vs 6820077715.450006
- q14.r1 16.674223659369403 vs 16.6742236593694
The baseline is itself run-to-run nondeterministic (perf/sf1000/cn4/runs/q22.r0/r1/r2 all differ from
each other), so bitwise identity to it is not a meaningful bar.
What IS identical: verdict labels for all 15 common queries and badcells counts for the six VALUES-DIFFER
queries (compare.txt vs compare-cn4.txt). Not identical: q22 maxreldiff 6.992e-16 (dp) vs 5.593e-16
(base) -- omitted from the finding's "identical maxreldiff" list.

## 2. q10 "two-row reorder ... row 1" -- wrong rows, wrong count (measured)
paste of custkey columns, oracle/tpch_sf1000/q10.tsv vs demoplus/arms/dp-cn4/runs/q10.r1.out:
- rows 10-12 are a 3-cycle: oracle 44871742, 91538647, 90626302 -> Sirius 91538647, 90626302, 44871742
- rows 15-16 swapped: oracle 124473127, 95772536 -> Sirius 95772536, 124473127
Five rows misplaced. Row 1 (51718390) is in the same position in both; its revenue 734306.4329 vs
736354.4839 is a 2.78e-3 relative deviation (the decimal truncation), not a reorder.
The 12.29 maxreldiff is c_acctbal on row 15: Sirius 7925.69 vs oracle -701.99, |diff|/701.99 = 12.29.
Sirius's own revenue column is monotone decreasing, and the custkey order is identical across dp r0/r1/r2
and baseline r0/r1, so the reorder is deterministic and truncation-induced (mechanism as claimed).

## 3. q16 "no oracle" -- stale (measured)
oracle/tpch_sf1000/q16.tsv written 2026-09-04 22:08:21; demoplus/arms/dp-cn4/compare.txt written
22:03:45 (before it). demoplus/oracle-q16.log: "q16 MATCH rows=27840 maxreldiff=0.000e+00", "9/22 match".
tail -n +2 of q16.tsv is byte-identical to dp-cn4/runs/q16.r0.out (r0 == r1 too). So q16 is a MATCH and
the dp-cn4 tally is 9 MATCH, not 8.
"Newly passes" holds only against baseline cn1 (perf/sf1000/cn1/runs/q16.r0.err: translator rejects
multi_distinct_count partial state); baseline cn4 never attempted q16 (no q16 row in
perf/sf1000/cn4/runs/runs.csv).

## Not refuted
- q11: oracle/tpch_sf1000/q11.tsv is 1 line (header `ps_partkey value`); both arms return 0 rows x3.
- q15: dp r0 3351778.0989, r1 3351778.0988999996, r2 0 rows; baseline r0 one row, r1/r2 0 rows.
  q15.sql lines 34-36 filter `total_revenue = (select max(total_revenue) ...)` on a float sum that
  visibly drifts run to run, consistent with the equality-flake mechanism. Note: SIRIUS_CANONICAL_FLOAT_SUMS
  is not present in either source tree (demo-plus or feat/pin-table-cn), so it is not a factor here.
- Five of the six VALUES-DIFFER queries are at ~1e-3 (q01 9.570e-04, q03 1.766e-03, q07 9.558e-04,
  q15 1.126e-03, q19 9.552e-04); q10's 12.29 is the reorder above.
- compare.py (bench/rtxpro6000-2gpu/tools/compare.py) prefers the last non-empty run, which is why q15
  shows rows=1 in both compare files despite the 0-row warm runs.
