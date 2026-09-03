# Fix 3, surgical variant: real FILES() cardinalities from file bytes, FE-only

Design for issue 3 of `~/.claude/plans/sf1000-top4-fixes-plan.md` ("The FE plans every FILES() scan at 1 row").
Angle: the smallest change that removes the measured defect, with the least blast radius, an FE unit test,
and an acceptance test that runs in minutes. Written 2026-09-03 from the report
`starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md` (C1, P1, P3, P4) and the evidence bundle
`scratchpad/perf/sf1000/`. Source paths are relative to `/home/prestouser/aocsa/sirius-stacks-wt/perf`;
`FE` = `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks`. Every number is marked
**measured** (copied from a named file or computed from measured inputs) or **inference** (hand computation of
FE code that was not executed).

## 0. Summary

Replace the hard-coded `setOutputRowCount(1)` in `StatisticsCalculator.computeFileScanNode` (FE
`sql/optimizer/statistics/StatisticsCalculator.java:664-676`) with
`rows = sum(file bytes) / sum(getTypeSize of the inferred schema)` for `TableFunctionTable` scans. Both inputs
already exist on the FE at analysis time: the file list with byte sizes (`catalog/TableFunctionTable.java:587-624`,
`:611 setSize`) and the schema from the CN's schema RPC (`:688-730`). Column statistics stay `UNKNOWN`. No
proto or CN change. Two FE files (`StatisticsCalculator.java`, `common/Config.java` for a mutable on/off knob),
delivered as one patch under `experimental/starrocks/patches/`, rebuilt with `pixi run fe-build`.

On the SF1000 dataset this estimator lands within 0.2x-6x of the footer row counts for all eight tables
(measured, section 2.3) and preserves the ordering that matters (lineitem > partsupp/orders > customer/part >
supplier > nation > region). It fixes C1 (`card_all_one=True` for all 22 plans) and the class of P3 decisions
where a tiny dimension is the shuffled or broadcast side. It does **not** by itself un-break the six 1-CN
failures: hand computation of the FE cost model (section 3) says that with real row counts the FE shuffles any
join whose build side is above ~1e7 estimated rows, because `HashJoinCostModel` charges a broadcast probe a
cache penalty of up to 12x versus 3x for shuffle. The same term predicts that a few 1-CN plans that stream
lineitem today (q03, q12, and to a lesser extent q07, q10) would start hash-partitioning it. The fix is
therefore gated by a mutable FE config so it can be A/B'd on one running FE, and its acceptance test is an
`EXPLAIN COSTS` survey of all 22 queries at 1 and 4 CNs (minutes, no query execution) before any SF1000 sweep.

## 1. The measured failure

- **Measured.** `card_all_one=True` for all 22 plans (`survey/plan-summary.txt`); every `FileScanNode`,
  join and exchange prints `cardinality: 1` (`survey/explain/q05.costs.txt` lines 13, 27, ... 352). Actual
  rows per exchange reach 3,793,363,900 (q04 exch 4) and 6.0e9 summed (q22 exch 10 at 4 CNs)
  (`card-compare-cn1.txt`, `card-compare-cn4.txt`).
- **Measured source.** `StatisticsCalculator.java:664-676`:

  ```java
  private Void computeFileScanNode(Operator node, ExpressionContext context,
                                   Map<ColumnRefOperator, Column> columnRefOperatorColumnMap) {
      // Use default statistics for now.
      Statistics.Builder builder = Statistics.builder();
      for (ColumnRefOperator columnRefOperator : columnRefOperatorColumnMap.keySet()) {
          builder.addColumnStatistic(columnRefOperator, ColumnStatistic.unknown());
      }
      // cause we don't know the real schema in file，just use the default Row Count now
      builder.setOutputRowCount(1);
      context.setStatistics(builder.build());
      return visitOperator(node, context);
  }
  ```

  Reached for FILES() through `visitLogicalTableFunctionTableScan` (`:374-376`), and for the unrelated
  external `FileTable` through `visitLogicalFileScan`/`visitPhysicalFileScan` (`:655-662`). The physical
  FILES() operator has no override (`sql/optimizer/operator/OperatorVisitor.java:454-456` falls through to
  `visitOperator`), so the row count of a FILES() group is set exactly once, from the logical operator.
- **Measured consequences** (report P1/P3/P5): with every child at 1 row, `Statistics.getOutputSize`
  (`sql/optimizer/statistics/Statistics.java:88-106`) is the sum of the projected type widths, so the
  BROADCAST/SHUFFLE choice is width arithmetic plus the CN count; `checkBroadcastRowCountLimit`
  (`sql/optimizer/task/EnforceAndCostTask.java:281-329`) never fires because it needs
  `rightChildStats.getOutputRowCount() > 15,000,000`; `JoinCommutativityRule` never pays to commute
  semi/anti joins; CTE inlining always wins.

## 2. Mechanism

### 2.1 What changes

`FE/sql/optimizer/statistics/StatisticsCalculator.java`, function `computeFileScanNode` only:

```java
private Void computeFileScanNode(Operator node, ExpressionContext context,
                                 Map<ColumnRefOperator, Column> columnRefOperatorColumnMap) {
    Statistics.Builder builder = Statistics.builder();
    for (ColumnRefOperator columnRefOperator : columnRefOperatorColumnMap.keySet()) {
        builder.addColumnStatistic(columnRefOperator, ColumnStatistic.unknown());
    }
    // Column statistics stay UNKNOWN (no min/max/NDV in the schema RPC). The row count is estimated
    // from the listed file bytes so that join sides and exchange types are no longer decided at 1 row.
    double rowCount = estimateFilesRowCount(node);
    builder.setOutputRowCount(rowCount);
    builder.setTableRowCountMayInaccurate(rowCount != 1);
    context.setStatistics(builder.build());
    return visitOperator(node, context);
}

// bytes / typed row width. Only for FILES() (TableFunctionTable); the external FILE table keeps 1.
private static double estimateFilesRowCount(Operator node) {
    if (!Config.files_query_estimate_row_count) {
        return 1;
    }
    Table table = node instanceof LogicalScanOperator ? ((LogicalScanOperator) node).getTable()
            : node instanceof PhysicalScanOperator ? ((PhysicalScanOperator) node).getTable() : null;
    if (!(table instanceof TableFunctionTable)) {
        return 1;
    }
    TableFunctionTable files = (TableFunctionTable) table;
    if (files.isListFilesOnly()) {                   // list_files_only: one row per listed entry is a different scan
        return 1;
    }
    long bytes = 0;
    for (TBrokerFileStatus f : files.loadFileList()) {
        if (!f.isDir) {
            bytes += Math.max(0, f.size);
        }
    }
    long width = 0;
    for (Column c : files.getFullSchema()) {
        width += Math.max(1, c.getType().getTypeSize());
    }
    if (bytes <= 0 || width <= 0) {
        return 1;
    }
    return Math.max(1, (double) bytes / width);
}
```

`FE/common/Config.java`, one mutable knob next to the other CBO/statistics configs (not adjacent to the
`files_query_whole_file_ranges` hunk that the existing patch adds at `:1212-1223`, so the two patches apply in
either order):

```java
/**
 * When true, FILES() scans are planned with an estimated row count (listed file bytes divided by the
 * typed row width of the inferred schema) instead of the default 1 row. Column statistics stay unknown.
 * Flip at runtime with ADMIN SET FRONTEND CONFIG to A/B plans on one FE.
 */
@ConfField(mutable = true)
public static boolean files_query_estimate_row_count = true;
```

Inputs, all present before the optimizer runs (measured in source):

- `TableFunctionTable.parseFilesForLoadAndQuery` (`catalog/TableFunctionTable.java:587-624`) fills
  `fileStatuses` (`:188`) from the FE's own `FileSystem.globList(piece, true)` (`fs/FileSystem.java:73`,
  `skipDir=true`) with `brokerFileStatus.setSize(fileStatus.getLen())` (`:611`); `loadFileList()` (`:300-302`)
  exposes it. No CN involvement, no extra RPC.
- `setSchemaForLoadAndQuery` (`:255-275`) calls `getFileSchema()` (`:688-730`), which sends one
  `get_file_schema` RPC and builds `Column`s from the returned `PSlotDescriptor`s (`:722-726`); for the CN,
  `experimental/starrocks/src/file_schema.rs:14-49` maps parquet types (INT32 -> INT, INT64 -> BIGINT,
  decimal(15,2) -> DECIMAL64 at `:180-197`, strings -> VARCHAR(1048576) at `:11`, date -> DATE).
- Type widths: `fe/fe-type/src/main/java/com/starrocks/type/PrimitiveType.java:343-403`
  (INT/DECIMAL32/DATE 4, BIGINT/DECIMAL64 8, DECIMAL128 16, CHAR/VARCHAR 16 "as char type estimate size");
  `ScalarType.getTypeSize` delegates (`type/ScalarType.java:294-296`).
- `Statistics.getOutputSize` already uses exactly these widths for unknown columns
  (`Statistics.java:97-99`: `isUnknown() ? getTypeSize()`), so the estimator is self-consistent: the CBO's
  estimated bytes for a full-row scan equal the table's on-disk bytes.

`setTableRowCountMayInaccurate(true)` is honest and inert here: every consumer that reads the flag
(`EnforceAndCostTask.java:411-415`, `CostModel.java:371`, `StatisticsEstimateUtils.java:117`,
`DataSkew.java:161,194`) is already short-circuited by the unknown column statistics, and
`StatisticsCalculator.visitOperator:274-276` only propagates it upward.

### 2.2 How the estimate flows through the rest of the estimator (measured in source)

With row counts present and column statistics still `UNKNOWN`:

| construct | selectivity / result | source |
|---|---|---|
| `col < const`, `col >= const` (dates) | 0.5 per conjunct (`predicateRange` NDV = NaN -> `OVERLAP_INFINITE_RANGE_FILTER_COEFFICIENT`) | `BinaryPredicateStatisticCalculator.java:239-259`, `StatisticRangeValues.java:73-89`, `StatisticsEstimateCoefficient.java:35` |
| `col = numeric const` | 0.5 (`lengthOfIntersect == 0 && distinctValues == 1`) | `StatisticRangeValues.java:90-95` |
| `col = string const` (`r_name = 'ASIA'`, `c_mktsegment = 'BUILDING'`) | 1.0 (both ranges infinite, NDV 1/1) | `StatisticRangeValues.java:84-87` |
| `col IN (...)` | 0.5 | `PredicateStatisticsCalculator.java:213-221`, coefficient `:27` |
| `colA < colB` (`l_commitdate < l_receiptdate`) | 0.5 | `BinaryPredicateStatisticCalculator.java:369-374` |
| anything else | 0.25 (`PREDICATE_UNKNOWN_FILTER_COEFFICIENT`) | `PredicateStatisticsCalculator.java:104-105`, `:31` |
| equi-join with an unknown key | `max(L, R)`; `max(L,R) * 0.25^k` if `max/min >= 1e7`; anti join `min(L, R)` | `StatisticsCalculator.java:1219-1240` |

### 2.3 What the estimator produces on the SF1000 dataset (measured inputs, arithmetic)

File bytes: `du -sb /scratch/sirius/datasets/tpch_sf1000/*` (this session). Rows: parquet footers via
`metadata.json` `row_count` (matches `agent-notes/planning-contrast.md`). Widths: the CN's inferred types
(`metadata.json` `table_schema` through `file_schema.rs`) priced by `PrimitiveType.getTypeSize`.

| table | files | on-disk bytes | rows (footer) | FE row width (B) | estimate = bytes/width | est / actual |
|---|---:|---:|---:|---:|---:|---:|
| lineitem | 60 | 177,214,004,134 | 5,999,989,709 | 144 (1 BIGINT, 3 INT, 4 DECIMAL64, 3 DATE, 6 VARCHAR) | 1,230,652,806 | 0.205 |
| orders | 15 | 52,374,826,801 | 1,500,000,000 | 92 | 569,291,596 | 0.380 |
| partsupp | 8 | 37,878,661,069 | 800,000,000 | 36 | 1,052,185,030 | 1.315 |
| customer | 2 | 10,761,809,562 | 150,000,000 | 96 | 112,102,183 | 0.747 |
| part | 2 | 5,084,217,527 | 200,000,000 | 112 | 45,394,799 | 0.227 |
| supplier | 1 | 686,828,646 | 10,000,000 | 80 | 8,585,358 | 0.859 |
| nation | 1 | 2,250 | 25 | 40 | 56 | 2.25 |
| region | 1 | 1,103 | 5 | 36 | 30 | 6.1 |

The error is systematic with the number of string columns (16 B per VARCHAR in the FE versus a few
compressed bytes on disk), so string-heavy tables are under-estimated (lineitem 4.9x, part 4.4x) and the
one-string table partsupp is over-estimated 1.3x. Two orderings invert (partsupp above orders, customer above
part); neither pair is ever joined directly in TPC-H. Every decision examined in section 3 depends on
order-of-magnitude ratios (lineitem vs dimension, dimension vs 1e5-1e7 thresholds), not on the exact count.
The tiny tables (nation, region) are over-estimated by parquet footer overhead but remain two-digit.

### 2.4 Cost at plan time

Zero measurable: a loop over at most 60 `TBrokerFileStatus` and 16 `Column`s per FILES() reference. The fixed
120-270 ms head measured in P4 (one schema RPC per FILES() CTE, `TableFunctionTable.java:264 -> :688-712`) is
untouched by this design.

## 3. Behaviour under the FE cost model (inference; the acceptance survey in section 5 is what settles it)

### 3.1 The cost function as it applies to this deployment (measured in source)

- `CostModel.getRealCost` (`sql/optimizer/cost/CostModel.java:129-135`): `0.5*cpu + 2*mem + 1.5*net`.
- BROADCAST exchange (`CostModel.java:419-431`): `cpu = size*aliveBackendNumber`, `mem = size*beNum`,
  `net = max(size*beNum, 1)`, `beNum = max(1, aliveBackendNumber)`; multiplied by
  `BROADCAST_JOIN_MEM_EXCEED_PENALTY = 1000` (`StatisticsEstimateCoefficient.java:60`) when
  `size > exec_mem_limit` (2 GiB, `qe/SessionVariable.java:1305-1307`). The Sirius CNs are COMPUTE NODEs, so
  `ConnectContext.getAliveBackendNumber()` (`qe/ConnectContext.java:1497-1503`) is 0 and a broadcast costs
  `3.5 * S_B` regardless of CN count (report P3, confirmed here).
- SHUFFLE exchange (`CostModel.java:432-441`): `cpu = size`, `net = size`, or `net = 0` when the FE has exactly
  one registered node (`system/SystemInfoService.java:246-248 isSingleBackendAndComputeNode`, counts registered
  not alive) and `enable_local_shuffle_agg` (default true, `SessionVariable.java:1229`) -> `2.0 * S` per side,
  or `0.5 * S`.
- Hash join (`sql/optimizer/cost/HashJoinCostModel.java`): cpu `:86-111` = build + probe + output;
  BROADCAST build `S_B`, probe `S_L * P_b`; SHUFFLE build `S_B / pf`, probe `S_L * P_s`. `getAvgProbeCost`
  `:134-156`: `P_b = clamp(ln(R_B / 1e5), 1, 12)` (`BOTTOM_NUMBER :58`, `BROADCAST_MAT_RATIO :62`),
  `P_s = clamp(ln(R_B / 1e5) - log2(2*pf), 1, 3)` (`SHUFFLE_MAX_RATIO :60`),
  `pf = max(aliveBE + aliveCN, dop)`. mem `:115-129`: BROADCAST `S_B * (aliveBE + aliveCN)`, SHUFFLE `S_B`.
- `dop` = `pipeline_dop` if set, else `max(1, avgCores/2)` (`SessionVariable.java:3964-3976`,
  `system/BackendResourceStat.java:210-213`); the CN heartbeats `available_parallelism()` as its core count
  (`experimental/starrocks/src/lib.rs:410, :519`). On this box that is tens of cores, so `pf` is 36-72 and
  `log2(2*pf)` is 6-7; the conclusions below hold for any `pf >= 4`.
- Broadcast guard (`EnforceAndCostTask.java:281-329`): reject broadcast iff
  `S_L < S_B * max(1, aliveBackendNumber) * 10 && R_B > 15,000,000` (`SessionVariable.java:1833-1836`).

Sanity check against today's plans: with every side at 1 row, `P_b = P_s = 1`, and broadcast beats shuffle iff
`3.5*S_B < 2*(S_L + S_B)` at 1 CN (i.e. build width below ~1.3x probe width) and iff
`3.5*S_B + 2*4*S_B < 2*(S_L+S_B) + 2*S_B` at 4 CNs (build width below ~0.27x probe width). That reproduces
every observed 1-CN/4-CN flip in `plan-summary.txt` vs `plan-summary-4cn.txt` (q03 build 16 B vs probe 24 B:
BROADCAST at 1 CN, PARTITIONED at 4; customer 4 B vs orders 20 B: BROADCAST at both), so the model used below is
the one the FE actually ran.

### 3.2 With row counts: the probe-cache term decides, not the exchange bytes

For a build of `R_B` estimated rows the broadcast probe carries `S_L * (P_b - P_s) * 0.5` of extra cost
against the shuffle's `2*S_L` (or `0.5*S_L`) exchange. `P_b - P_s` reaches `log2(2*pf)` (about 6) as soon as
`R_B >= 1e5 * e^(1 + log2(2pf))`, i.e. a few million rows. Consequences:

- builds below ~3e5 rows: `P_b = P_s = 1`, broadcast wins as today (nation, region, filtered supplier,
  the 1-row region of q02, the single GERMANY nation of q11);
- builds between ~3e5 and ~1e7 rows: close call, depends on `pf` and on whether the FE counts network cost;
- builds above ~1e7 rows: shuffle wins at 1 CN and at 4 CNs, independent of the ×1000 memory penalty (which
  only starts at 2 GiB, i.e. >= ~1e8 rows of a narrow build).

Worked example, q05 exch 9 (the failing fragment of P1), 1 CN, network counted, `pf = 36`:

| term | BROADCAST orders_f x customer (build) | SHUFFLE both |
|---|---:|---:|
| build `R_B`, `S_B` | 1.42e8 rows (5.69e8 x 0.5 x 0.5, then `max` with customer 1.12e8), 12 B -> 1.70e9 B | same |
| probe `S_L` (lineitem, 28 B projected) | 1.23e9 x 28 = 34.5e9 B | same |
| exchange | 3.5 x 1.70e9 = 5.9e9 | 2.0 x (34.5e9 + 1.70e9) = 72.4e9 |
| join cpu x 0.5 | 0.5 x (1.70e9 + 34.5e9 x 7.26) = 126.1e9 (`P_b = ln(1420)`) | 0.5 x (1.70e9/36 + 34.5e9 x 1.09) = 18.8e9 |
| join mem x 2 | 3.4e9 | 3.4e9 |
| total | 135.4e9 | 94.6e9 (40.3e9 with network ignored) |

Shuffle wins by 1.4x-3.4x, so lineitem still leaves its scan fragment through a `HASH_PARTITIONED` sink and is
parked in full; the six 1-CN failures are unchanged by this fix (agrees with the report's P1 caveat, for a
different reason than the memory penalty it named). Raising `exec_mem_limit` does not help: the probe term
dominates before the penalty is reached, so this design does not ask for it.

### 3.3 Predicted plan changes per query (inference)

Baselines are measured (`card-compare-cn1.txt`, `card-compare-cn4.txt`, `cn4-transmits.txt`, `cn1-quent.txt`).

| query | today | with the estimate | effect |
|---|---|---|---|
| q02 | 1 CN: exch 28 shuffles 800M partsupp rows on `n_regionkey` to meet 1 region row (13.1 GB parked); 4 CN: four 800M-row partsupp shuffles, 38.5 of 43.6-45.7 GB nixl | region (30 est rows) and nation (56) are broadcast (`P_b = P_s = 1`, `3.5*S_B` tiny); partsupp stays in its fragment | 4 CN: ~35 GB less nixl per run; 1 CN: 13 GB less parked. Wall effect small (q02 is wait-dominated, P2) |
| q11 | 4 CN: 10M supplier rows shuffled on `s_nationkey` vs 1 nation row (0.40 GB) | nation broadcast | removes the skewed shuffle (3.2M/1.2M/3.2M/2.4M per CN) |
| q22 | LEFT ANTI, build = orders (1.5e9 rows broadcast; 18.56 GB nixl at 4 CN; `HASH_JOIN(15)` 0.516 s per CN) | orders build est 5.69e8 x 4 B = 2.28e9 B > 2 GiB -> x1000 penalty -> shuffle, or commuted to RIGHT ANTI with the ~2.8e7-row customer side as build (`JoinCommutativityRule.java:34-44`; translator has `RIGHT_ANTI_JOIN`, `node_translator.rs:1750`) | 4 CN nixl 18.56 GB -> ~4.5 GB (shuffle) or <1 GB (right anti); build shrinks 1.5e9 -> 6e6 rows in the right-anti case |
| q03 | 1 CN: orders x customer broadcast (2.33 GB parked), lineitem streamed; 4 CN: lineitem shuffled (58.2 GB) | build est 2.85e8 rows / 4.6e9 B (`P_b = 7.95`, penalty too) -> PARTITIONED at both | 4 CN unchanged; **1 CN regression**: lineitem_f 3.23e9 rows x 24 B = 77.6 GB hash-partitioned and parked (today 0), ~1.2 s of `export_packed` at the measured 63 GB/s (B5), pool at ~80 of 100 GiB |
| q12 | 1 CN: lineitem_f (31M rows, 0.51 GB) broadcast, orders streamed; 4 CN: orders shuffled (23.09 GB) | build est 1.23e9 x 0.5 x 0.5 x 0.5 x 0.25 = 3.85e7 rows (`P_b = 5.95`) -> shuffle at both | 4 CN unchanged (contrary to the report's P3 hope); **1 CN regression**: orders 1.5e9 x 24 B = 36 GB parked |
| q10 | 1 CN: orders_f broadcast (0.70 GB), lineitem_f broadcast (2.29 GB) | orders_f est 1.42e8 -> shuffle; lineitem_f est 1.23e9 (string equality = 1.0) | 1 CN: customer x nation side (25.5 GB measured) may be hash-partitioned; risk, not a certainty |
| q07 | 1 CN: orders broadcast (18.38 GB parked), lineitem_f already shuffled | customer (1.12e8) and orders (5.69e8) builds -> shuffle | 1 CN: the 1.8e9-row lineitem x orders x supplier chain may cross a shuffle (~50 GB); risk |
| q14 | 1 CN: part broadcast (4 GB parked) | part est 4.5e7 > 15M and `S_L < 10*S_B` -> guard rejects broadcast -> PARTITIONED | 1 CN: +1.8 GB lineitem_f parked, neutral |
| q19 | 1 CN: lineitem_f (128.6M rows, ~3 GB) is the broadcast build | part_f est ~1e7 becomes the build; shuffle or broadcast depending on `pf` | neutral to slightly better |
| q04, q20, q21 | LEFT SEMI with lineitem or the aggregate as build | commutation to RIGHT SEMI becomes cost-relevant | **refusal risk**: `node_translator.rs:1743-1762` has no `RIGHT_SEMI_JOIN` arm -> "hash join type is unsupported" (dependency, section 8) |
| q13, q15, q01, q06, q11 (rest) | already shuffle-both or join-free | unchanged | none |
| q05 q08 q09 q17 q18 q21 (1 CN) | lineitem hash-partitioned, parked, OOM | unchanged shape (section 3.2) | still fail until fix 4; 4 CN not measured, per-CN share 31-61 GB (report P1) |

Net: at 4 CNs the change is neutral-to-positive (q02, q11, q22 move less; nothing moves more); at 1 CN it
trades the accidental "broadcast everything" plans for shuffles of the big side in q03/q12 (and possibly
q07/q10), each within the 100 GiB pool on a clean CN but not with the 0-38 GB B4 leaks present. This is why
the knob exists and why the survey (section 5.2) runs before any sweep.

## 4. Blast radius

Paths that change: only FILES() scans (`LogicalTableFunctionTableScanOperator`, whose constructor asserts
`table instanceof TableFunctionTable`, `sql/optimizer/operator/logical/LogicalTableFunctionTableScanOperator.java:44`).
Unchanged by construction: external `FileTable` scans (same function, `instanceof` guard fails -> 1),
`list_files_only` FILES() (`isListFilesOnly`, `TableFunctionTable.java:432`), OLAP/Iceberg/Hive/Delta/Paimon
statistics, every other estimator branch. `INSERT ... FROM FILES()` (load type) gets the same estimate for its
scan; its plan is scan -> sink and has no join to re-decide.

Second-order FE effects that need a companion setting or a landing order, all triggered by real row counts:

1. **CTE reuse.** The bench queries declare all eight tables as `WITH t AS (SELECT * FROM FILES(...))` and
   q15/q17/q18/q21/q11/q02 consume one of them 2-3 times. Reuse vs inline is costed in the memo
   (`CostModel.java:558-577`, ratio `cbo_cte_reuse_rate` 1.15, `SessionVariable.java:1511`); with 1-row
   statistics inline always won. With real statistics the CBO may materialise a FILES() CTE once through a
   `MULTI_CAST_DATA_STREAM_SINK`, which the CN refuses by name (`compute_node_service.rs:1519`, test
   `:3568-3588`). Companion: `SET GLOBAL cbo_cte_reuse = false` (`SessionVariable.java:1505-1506`;
   `CTEContext.needInline:157-190` returns true when `!enableCTE`). Cheap, reversible, and it is the behaviour
   the CN supports today.
2. **Semi/anti commutation.** `JoinCommutativityRule` (`rule/transformation/JoinCommutativityRule.java:34-44`)
   maps LEFT_SEMI <-> RIGHT_SEMI; the translator accepts LEFT_SEMI, LEFT_ANTI, RIGHT_ANTI,
   NULL_AWARE_LEFT_ANTI but not RIGHT_SEMI (`node_translator.rs:1743-1762`). Land the one-arm translator
   change (report item 5, `TJoinOp::RIGHT_SEMI_JOIN => (JoinType::RightSemi, JoinOutput::Right)`) first, or
   accept that q04/q20/q21 may be refused in the SF1000 arm and record it.
3. **Aggregation stages.** One-stage aggregation stays disabled because the column statistics are still
   unknown (`EnforceAndCostTask.java:411-415`); two-phase plans are unchanged.
4. **Runtime filters.** FE build-size thresholds see larger builds and may attach fewer RFs; the CN drops
   them anyway (report P2, `grep runtime_filter` over the translator finds only a fixture).

Operational: the FE must be rebuilt and redeployed (`pixi run fe-build`, then the packaged FE under
`starrocks/output/fe`); the CN binary, proto, translator and engine are untouched, so fixes 1, 2 and 4 can be
built and tested independently of this one. CI (`.github/workflows/experimental.yml:50-58`) applies only
`nixl-exchange-proto.patch` by name for the CN build; an FE-only patch needs no CI change.

## 5. Files to touch and delivery

| file | change |
|---|---|
| `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculator.java` | `computeFileScanNode` + `estimateFilesRowCount` (section 2.1); imports `TableFunctionTable`, `TBrokerFileStatus`, `LogicalScanOperator`, `PhysicalScanOperator` |
| `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks/common/Config.java` | `files_query_estimate_row_count` (mutable, default true) |
| `experimental/starrocks/starrocks/fe/fe-core/src/test/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculatorTest.java` | unit test (section 6.1) |
| `experimental/starrocks/patches/files-query-row-estimate.patch` | `git -C experimental/starrocks/starrocks diff` of the three files above; applied by `experimental/starrocks/scripts/apply-starrocks-patches.sh` (loops `patches/*.patch`, `git apply --check` / `--reverse --check`, so it is idempotent). Keep the `Config.java` hunk away from `:1212-1223` so it applies in either order relative to `files-query-whole-file-ranges.patch` |
| `experimental/starrocks/benchmarks/tpch/bench.sh` (or the cluster bring-up used by the sweep) | `SET GLOBAL cbo_cte_reuse = false;` after the FE is up (today the harness only defines `MYSQL`, `bench.sh:85`) |
| `experimental/starrocks/DEMO.md:168-176` | one line: the patch list now includes the FE estimate; `ADMIN SET FRONTEND CONFIG ("files_query_estimate_row_count" = "false")` turns it off without a rebuild |

Rebuild: `pixi run -e fe fe-build` in `experimental/starrocks` (`pixi.toml:197-215`, `./build.sh --fe`,
inputs include `starrocks/fe/*/src/**/*`; the project comments call it "a multi-hour Maven build", a warm
`~/.m2` shortens it). The sweep harness starts the packaged FE from `starrocks/output/fe`.

## 6. Tests

### 6.1 FE unit test (minutes, no cluster)

`StatisticsCalculatorTest.testTableFunctionTableScanRowCountFromFileBytes`, modelled on the Iceberg case at
`StatisticsCalculatorTest.java:295-345` (build operator -> `OptExpression` -> memo group ->
`ExpressionContext` -> `new StatisticsCalculator(...).estimatorStats()`):

- `new TableFunctionTable(props)` with `path=fake://some_bucket/some_path/*`, `format=parquet`
  (`TableFunctionTableTest.java:57` uses the same fixture). The fake path skips the schema RPC and lists two
  files of 1024 and 2048 bytes (`TableFunctionTable.java:589-604`) with schema `col_int INT, col_string VARCHAR`
  (`:258-263`): width 4 + 16 = 20, expected `outputRowCount = 3072 / 20 = 153`.
- `LogicalTableFunctionTableScanOperator` over both columns; assert 153 and `isTableRowCountMayInaccurate()`.
- `Config.files_query_estimate_row_count = false` -> assert 1 (the pre-change behaviour).
- A `LogicalFileScanOperator` over a `FileTable` -> still 1 (guard).

Optional planner-level check in `sql/plan/` (`PlanTestNoneDBBase.getCostExplain`, `:311-314`):
`EXPLAIN COSTS select count(*) from files("path"="fake://a","format"="parquet") a join files(...) b on a.col_int = b.col_int`
contains `cardinality: 153` under both `FileScanNode`s. Run with
`cd experimental/starrocks/starrocks && ./run-fe-ut.sh --test com.starrocks.sql.optimizer.statistics.StatisticsCalculatorTest`
inside `pixi run -e fe bash -lc ...` (the `fe` feature ships openjdk 17 and maven >= 3.9, `pixi.toml:170-185`).

### 6.2 Acceptance test A: EXPLAIN COSTS survey, 1 CN and 4 CNs (minutes, no query execution)

Reuse `scratchpad/perf/sf1000/survey.sh` (FE + CN with `SIRIUS_CN_TRANSLATE_ONLY=1`,
`compute_node_service.rs:317, :926-931`: fragments are translated and dumped, never executed; the CN only
answers the schema RPC). For `NUM_CNS` in {1, 4}, on the same FE:

1. `ADMIN SET FRONTEND CONFIG ("files_query_estimate_row_count" = "false")`; capture `EXPLAIN COSTS` for
   q01-q22 -> must equal `survey/explain*/` (today's plans: rollback path verified).
2. `... = "true"`; capture again; run `explain_summary.py` and diff against `plan-summary.txt` /
   `plan-summary-4cn.txt`.

Pass criteria (golden files checked in under `benchmarks/tpch/plans/`):

- `card_all_one=False` for all 22; each `FileScanNode` prints the section 2.3 estimate (lineitem
  1,230,652,806, orders 569,291,596, partsupp 1,052,185,030, customer 112,102,183, part 45,394,799, supplier
  8,585,358, nation 56, region 30) before predicates.
- q02 join 31 and q11 join 5 broadcast region/nation; q22 join 11 no longer broadcasts orders.
- The CN log has no `hash join type is unsupported` and no `MULTI_CAST_DATA_STREAM_SINK` refusal for the
  15 passing queries (with `cbo_cte_reuse=false` set); any refusal is recorded against section 4 items 1-2.
- The distribution table of section 3.3 is filled in from the real plans; every 1-CN join that flipped
  BROADCAST -> PARTITIONED is listed with the bytes it will park (`actual rows x projected width` from
  `card-compare-cn1.txt`).

Elapsed: about 22 x 2 x 2 EXPLAINs plus two cluster starts, well under 15 minutes total; translate-only runs
can be skipped if only plans are wanted.

### 6.3 Acceptance test B: SF1000 arms (orchestrator-run, one arm at a time)

Only after 6.2 is clean. 1 CN then 4 CNs with `run-queries.sh` over the 15 passing queries plus the six, same
configuration as the campaign (`capture-cn.sh:11-12`, 100 GiB pool), `cbo_cte_reuse=false`:

- record plan shapes (`explain_summary.py`) and per-exchange actual rows (`card_compare.py`) next to the
  campaign tables; warm medians against `results.md`;
- oracle compares unchanged for q02 q04 q06 q12 q13 q14 q20 q22 (MATCH) and the known decimal diffs for
  q01 q03 q07 q10 q19 (not touched by this fix);
- 4 CN: nixl GB per run for q02 (43.6-45.7 today), q11 (0.40), q22 (18.56) drop as predicted in 3.3; q03
  (60.34), q12 (23.47) unchanged;
- 1 CN: q03/q12 `[gpu_pool]` peak and `export_packed` time recorded; the six still fail (expected) but the
  time-to-fail is governed by fix 1, not this one.

## 7. Predicted effect

Measured today -> predicted (inference unless noted):

- C1: 22 of 22 plans `card_all_one=True` -> 0 of 22 (deterministic consequence of the code; verified by 6.2).
- 4 CN data movement per run: q02 43.6-45.7 GB -> ~8 GB; q22 18.56 GB -> 0.3-4.5 GB; q11 0.40 GB -> ~0;
  q03 60.34, q07 69.01, q10 48.04, q12 23.47 GB unchanged (their builds are >= 1e7 estimated rows).
- 4 CN wall: q22 `HASH_JOIN(15)` 0.516 s per CN builds on 1.5e9 rows today; a right-anti build on ~6e6
  customers or a 1/4 shuffle share plausibly saves 0.3-0.4 s of its 831 ms; q02 mostly wait-dominated,
  <= 0.3 s of Computing saved; others unchanged.
- 1 CN: no query gets faster except possibly q19/q22; q03 and q12 park 77.6 and 36 GB they stream today
  (+~1.2 s and +~0.6 s of drain at 63 GB/s, plus parking), q07/q10 at risk; the six still OOM.
- Not achieved by this fix and not claimed: moving lineitem to the probe side of a broadcast at 1 CN (the
  probe-cache term forbids it), q03/q10 broadcasts at 4 CNs (guard + probe term), the receiver-first
  materialisation (B1), NDV-driven decisions (q02 join 37, shuffle-key skew).

## 8. Risks, ranked

1. **1-CN plan regressions (q03, q12; possibly q07, q10)**, inference from section 3.2. Mitigations: the
   mutable knob (turn off on a 1-CN deployment without a rebuild); run fix 2 first so B4 leaks do not stack on
   the newly parked bytes; the follow-up that actually fixes this is a second, separate FE patch neutralising
   the CPU-cache probe penalty for this deployment (`HashJoinCostModel.getAvgProbeCost:134-156`, return 1.0
   under a config), which is out of scope here and belongs to the robust design.
2. **Translator refusals for RIGHT_SEMI** (q04, q20, q21), inference from `JoinCommutativityRule` becoming
   cost-relevant. Mitigation: land the translator arm first (report item 5, small), or measure and record.
3. **CTE reuse -> MULTI_CAST refused** (q15, q17, q18, q21, q11, q02), inference. Mitigation:
   `cbo_cte_reuse=false` in the harness; also protects the demo queries.
4. **Estimator bias** (0.2x-6x). Decisions in the studied set hinge on >= 10x ratios or on thresholds far
   from the estimates (q12 build 3.85e7 vs 1.5e7 limit); a table with many string columns and a single-row
   dimension is the failure mode to watch (the dimension is over-estimated, the fact under-estimated, both
   still separated by orders of magnitude).
5. **Determinism/correctness**: none; the change is planning-only and the CN executes whatever fragment shape
   it receives (oracle compares in 6.3 confirm). Cancellation paths untouched.
6. **FE rebuild cost** for every iteration on the estimator; the knob keeps iterations on the plan side to
   config flips.

## 9. Effort

- Code: ~40 lines in `StatisticsCalculator.java`, ~10 in `Config.java`, ~80 in the unit test. Half a day
  including the patch file and the DEMO note.
- FE rebuild: one `pixi run fe-build` (long; the pixi comment says multi-hour cold).
- Survey (6.2): under 15 minutes per CN count once the FE is packaged; golden files another hour to curate.
- SF1000 arms (6.3): two sweeps, ~20-30 minutes each of wall on the box, orchestrator-run.

## 10. Dependencies and ordering with the other three fixes

- **Fix 1 (fail fast)**: independent; makes any new 1-CN OOM caused by a flipped plan cost ~3 s instead of
  54-232 s. Recommended before the 1-CN arm of 6.3.
- **Fix 2 (parked-output bookkeeping)**: independent; required before the 1-CN arm so the 0-38 GB leaks do
  not turn q03's predicted 80 GB peak into a failure that is misattributed to this change.
- **Fix 4 (run the six on 1 CN)**: independent of this code; this fix does not deliver any of the six on 1 CN
  (section 3.2), so fix 4 is not optional. Fusion (4a) works on `TPlan` shape and is unaffected by
  cardinalities; spill (4b) likewise. Real cardinalities remain the prerequisite for the semi/anti flips that
  q21 needs after fusion (report P1 caveat).
- **Translator RIGHT_SEMI arm** (report item 5, part of the P5 follow-ups): should land before or with this
  fix's SF1000 arm.
- Bench harness: `cbo_cte_reuse=false` and the knob in the cluster bring-up. No proto change, no CN rebuild,
  no engine change.

## Appendix A. Code excerpts relied on (file:line, worktree `/home/prestouser/aocsa/sirius-stacks-wt/perf`)

`FE/sql/optimizer/statistics/StatisticsCalculator.java:374-376`
```java
public Void visitLogicalTableFunctionTableScan(LogicalTableFunctionTableScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getColRefToColumnMetaMap());
}
```

`FE/sql/optimizer/statistics/StatisticsCalculator.java:1219-1240` (join estimate with unknown keys)
```java
if (hasUnknownColumnStatistics) {
    if (joinType.isAntiJoin()) {
        innerRowCount = Math.max(0, Math.min(leftRowCount, rightRowCount));
    } else {
        double scaleFactor = Math.max(leftRowCount, rightRowCount) / Math.min(leftRowCount, rightRowCount);
        if (scaleFactor >= Math.pow(10, 7)) {
            innerRowCount = Math.max(1, Math.max(leftRowCount, rightRowCount)
                    * pow(PREDICATE_UNKNOWN_FILTER_COEFFICIENT, eqOnPredicates.size()));
        } else {
            innerRowCount = Math.max(1, Math.max(leftRowCount, rightRowCount));
        }
    }
}
```

`FE/sql/optimizer/statistics/Statistics.java:88-106`
```java
public double getOutputSize(ColumnRefSet outputColumns) {
    ...
            if (!entry.getValue().isUnknown()) {
                totalSize += entry.getValue().getAverageRowSize();
            } else {
                totalSize += entry.getKey().getType().getTypeSize();
            }
    ...
    return totalSize * outputRowCount;
}
```

`fe/fe-type/src/main/java/com/starrocks/type/PrimitiveType.java:359-385` (excerpt)
```java
case INT: case DECIMAL32: case DATE:            typeSize = 4;  break;
case BIGINT: case DECIMAL64: case DOUBLE: ...:  typeSize = 8;  break;
case LARGEINT: case DECIMALV2: case DECIMAL128: typeSize = 16; break;
case CHAR: case VARCHAR: case VARBINARY:        // use 16 as char type estimate size
                                                typeSize = 16; break;
```

`FE/catalog/TableFunctionTable.java:587-624` (file listing with sizes; `:611` is the size)
```java
List<FileStatus> fileStatusList = FileSystem.getFileSystem(piece, properties).globList(piece, true);
for (FileStatus fileStatus : fileStatusList) {
    TBrokerFileStatus brokerFileStatus = new TBrokerFileStatus();
    brokerFileStatus.setPath(fileStatus.getPath().toString());
    brokerFileStatus.setIsDir(fileStatus.isDirectory());
    brokerFileStatus.setSize(fileStatus.getLen());
    brokerFileStatus.setIsSplitable(true);
    fileStatuses.add(brokerFileStatus);
}
```
and the fake fixture at `:589-604` (`file1.size = 1024; file2.size = 2048`), `loadFileList()` at `:300-302`,
schema from the RPC at `:722-726`:
```java
for (PSlotDescriptor slot : result.schema) {
    columns.add(new Column(slot.colName, TypeDeserializer.fromProtobuf(slot.slotType), true));
}
```

`experimental/starrocks/starrocks/gensrc/proto/internal_service.proto:647-650` (why exact counts are not
FE-only; not changed by this design)
```proto
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
};
```
`experimental/starrocks/src/file_schema.rs:19-27` already holds `builder.metadata().file_metadata()` for
every file (`parquet_files_schema`, `:62-96`, reads all footers), so `num_rows` is one field away when a later
design wants exactness; `compute_node_service.rs:683-699` is the handler that would fill it.

`FE/sql/optimizer/cost/CostModel.java:129-135`
```java
public static double getRealCost(CostEstimate costEstimate) {
    double cpuCostWeight = 0.5; double memoryCostWeight = 2; double networkCostWeight = 1.5;
    ...
}
```
`FE/sql/optimizer/cost/CostModel.java:419-441`
```java
case BROADCAST:
    int aliveBackendNumber = ctx.getAliveBackendNumber();
    int beNum = Math.max(1, aliveBackendNumber);
    result = CostEstimate.of(outputSize * aliveBackendNumber, outputSize * beNum, Math.max(outputSize * beNum, 1));
    if (outputSize > sessionVariable.getMaxExecMemByte()) {
        result = result.multiplyBy(StatisticsEstimateCoefficient.BROADCAST_JOIN_MEM_EXCEED_PENALTY);
    }
    break;
case SHUFFLE:
    boolean ignoreNetworkCost = sessionVariable.isEnableLocalShuffleAgg()
            && sessionVariable.isEnablePipelineEngine()
            && GlobalStateMgr.getCurrentState().getNodeMgr().getClusterInfo().isSingleBackendAndComputeNode();
    double networkCost = ignoreNetworkCost ? 0 : Math.max(outputSize, 1);
    result = CostEstimate.of(outputSize * factor, 0, networkCost * factor);
    break;
```

`FE/sql/optimizer/cost/HashJoinCostModel.java:58-62, 86-111, 115-129, 134-156`
```java
private static final int BOTTOM_NUMBER = 100000;
private static final double SHUFFLE_MAX_RATIO = 3;
private static final double BROADCAST_MAT_RATIO = 12;
...
case BROADCAST: buildCost = rightOutput;                  probeCost = leftOutput * getAvgProbeCost(); break;
case SHUFFLE:   buildCost = rightOutput / parallelFactor; probeCost = leftOutput * getAvgProbeCost(); break;
...
int beNum = Math.max(1, ConnectContext.get().getAliveBackendNumber() +
        (RunMode.isSharedDataMode() ? ...getAliveComputeNodeNumber() : 0));
memCost = (JoinExecMode.BROADCAST == execMode) ? rightOutput * beNum : rightOutput;
...
double mapSize = Math.min(1, keySize) * rightStatistics.getOutputRowCount();
if (JoinExecMode.BROADCAST == execMode) {
    cachePenaltyFactor = Math.min(BROADCAST_MAT_RATIO, Math.max(1, Math.log(mapSize / BOTTOM_NUMBER)));
} else {
    cachePenaltyFactor = Math.min(SHUFFLE_MAX_RATIO, Math.max(1, Math.log(mapSize / BOTTOM_NUMBER)
            - Math.log(parallelFactor) / Math.log(2)));   // parallelFactor = max(nodes, dop) * 2
}
```

`FE/sql/optimizer/task/EnforceAndCostTask.java:318-328`
```java
int beNum = Math.max(1, ctx.getAliveBackendNumber());
...
if (leftOutputSize < rightOutputSize * beNum * sv.getBroadcastRightTableScaleFactor()
        && rightChildStats.getOutputRowCount() > sv.getBroadcastRowCountLimit()) {
    return false;
}
```

`FE/qe/SessionVariable.java:1305-1307` (`exec_mem_limit` 2 GiB), `:1505-1506` (`cbo_cte_reuse = true`),
`:1511` (`cboCTERuseRatio = 1.15`), `:1833` (`broadcastRowCountLimit = 15000000`), `:1836`
(`broadcastRightTableScaleFactor = 10.0`), `:3964-3976` (`getDegreeOfParallelism`);
`FE/system/BackendResourceStat.java:210-213` (`getDefaultDOP = max(1, avgCores/2)`);
`FE/qe/ConnectContext.java:1497-1503` (`getAliveBackendNumber`, BACKENDs only);
`FE/system/SystemInfoService.java:246-248` (`isSingleBackendAndComputeNode`, registered nodes).

`FE/sql/optimizer/statistics/BinaryPredicateStatisticCalculator.java:247-259` (range on unknown column ->
`predicateRange = new StatisticRangeValues(NEGATIVE_INFINITY, d.get(), NaN)`), `:369-374`
(`colA < colB` -> 0.5); `FE/sql/optimizer/statistics/StatisticRangeValues.java:73-95`
(`overlapPercentWith`: infinite intersect with a NaN NDV -> `OVERLAP_INFINITE_RANGE_FILTER_COEFFICIENT`,
point range on an unknown column -> 0.5, string equality -> 1.0).

`FE/sql/optimizer/CTEContext.java:157-190` (`needInline`: `!enableCTE` -> inline),
`FE/sql/optimizer/cost/CostModel.java:558-577` (CTE produce/anchor/consume costing with `cbo_cte_reuse_rate`);
`experimental/starrocks/src/compute_node_service.rs:1519` (`MULTI_CAST_DATA_STREAM_SINK` named in the
refusal), `:3568-3588` (test asserting the refusal).

`FE/sql/optimizer/rule/transformation/JoinCommutativityRule.java:34-44` (LEFT_SEMI <-> RIGHT_SEMI,
LEFT_ANTI <-> RIGHT_ANTI); `experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs:1743-1762`
(join arms: INNER, LEFT/RIGHT/FULL OUTER, LEFT_SEMI, LEFT_ANTI, RIGHT_ANTI, NULL_AWARE_LEFT_ANTI; `_ =>
UnsupportedPlanNode "hash join type is unsupported"`).

Delivery: `experimental/starrocks/scripts/apply-starrocks-patches.sh` (whole file, 40 lines: loops
`patches/*.patch`, `git apply --check` then apply, `--reverse --check` to detect already-applied);
`experimental/starrocks/pixi.toml:94` (`apply-starrocks-patches` task), `:96-104` (`fe-check` depends on it),
`:197-215` (`fe-build`: `./build.sh --fe --with-maven-batch-mode ON`); `.github/workflows/experimental.yml:50-58`
(CI applies only the nixl proto patch); existing FE patch `experimental/starrocks/patches/files-query-whole-file-ranges.patch`
(Config knob + `FileScanNode` hunk) as the pattern to follow.

## Appendix B. Measured inputs used for the numbers

- `du -sb /scratch/sirius/datasets/tpch_sf1000/*` (this session): customer 10,761,809,562 (2 files); lineitem
  177,214,004,134 (60); nation 2,250 (1); orders 52,374,826,801 (15); part 5,084,217,527 (2); partsupp
  37,878,661,069 (8); region 1,103 (1); supplier 686,828,646 (1).
- `/scratch/sirius/datasets/tpch_sf1000/metadata.json`: `row_count` per table (150,000,000 / 5,999,989,709 /
  25 / 1,500,000,000 / 200,000,000 / 800,000,000 / 5 / 10,000,000), `table_schema` types (int32/int64,
  decimal128(15,2), date32, string), `sum(total_compressed_size)` per table within 0.05% of `du`.
- `survey/explain/q05.costs.txt:143-357`: projected columns per `FileScanNode` (lineitem exch 9 projects
  `l_orderkey, l_suppkey, l_extendedprice, l_discount` = 8+4+8+8 = 28 B; orders `o_orderkey, o_custkey` = 12 B
  after the date range; customer `c_custkey, c_nationkey` = 8 B); the `o_orderdate-->[7.573824E8, 7.889184E8,
  0.0, 1.0, 1.0] UNKNOWN` line shows the range logic already runs on unknown columns.
- `card-compare-cn1.txt`, `card-compare-cn4.txt`: actual rows per exchange; `cn4-transmits.txt`: nixl GB per
  run (q02 45.14/43.60/45.66, q03 60.34, q07 69.01, q10 48.04, q12 23.47, q22 18.56 in the report);
  `results-ratios.md`: warm medians.
- `survey/plan-summary.txt`, `survey/plan-summary-4cn.txt`: join distribution per query at 1 and 4 CNs
  (reproduced by the cost arithmetic in 3.1).
