---
name: tpch-decimal-datasets-cn-truncation
description: The decimal-typed TPC-H parquet under /scratch/sirius/datasets makes q01 fail the oracle on the StarRocks CN path (1 - 0.07 truncates to 0.92); use f64-typed data for oracle-exact runs
metadata: 
  node_type: memory
  type: project
  originSessionId: 25f3e903-c551-4a98-b7fc-a6e0ed53ade6
  modified: 2026-09-03T03:55:10.213Z
---

On the GB200 box, `/scratch/sirius/datasets/tpch_sf{1,10,...}` store monetary columns as `DECIMAL(15,2)`.
Through the Sirius StarRocks CN (source branch `aocsa/feat/pin-table-cn` @ d24f02c4, 4 CNs, 2026-09-03),
TPC-H q01's `sum_disc_price` and `sum_charge` come out 9.6e-4 relative below DuckDB. Only the
`l_discount = 0.07` group is wrong: `1 - 0.07` lowered to FP64 is `0.9299999999999999`, and the
FE-declared `cast(... as DECIMAL128(16,2))` truncates it to `0.92` instead of rounding. q06 and every
other q01 column match exactly. The lowering (#1236) and the cast live on `dev`, so this is not a
multi-CN or carve defect.

f64-typed copies (every DECIMAL column cast to DOUBLE, ~1M-row row groups) live at
`/scratch/prestouser/aocsa/demo-q1q6/tpch_sf{1,10}_f64_{1file,multi}` (multi = lineitem in 8/16 files);
the generator script is `make-f64-datasets.py` in that session's scratchpad. The campaign's own data
(`tpch_parquet_sf1000_f64`, gen-tpch.sh default) is f64-typed too.

**Why:** the oracle compare (`compare.py`, rel tol 1e-6) is the milestone gate; running it on decimal
data reports a real engine bug, not a carve regression, and wastes a triage cycle.

**How to apply:** for CN-path oracle comparisons use the f64 datasets; file or point at the
FP64→DECIMAL cast rounding bug separately (see [[sirius-multicn-pr-carveup-plan-2026-09-02]]).
