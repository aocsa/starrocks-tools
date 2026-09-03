# Design: real FILES() cardinalities in the StarRocks FE (issue 3), "what upstream would accept" angle

Written 2026-09-03 against worktree `/home/prestouser/aocsa/sirius-stacks-wt/perf` at `45dab3be`
(FE = `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks`, CN = `experimental/starrocks/src`,
proto = `experimental/starrocks/starrocks/gensrc/proto`). Evidence root = `scratchpad/perf/sf1000`.
Every number is **measured** (named evidence file or a command run for this design) or marked **inference**.

## 0. Decision in one paragraph

Give every FILES() scan the row count the system already knows, through the abstractions StarRocks already has for
stats-less tables, in two layers that share one FE code path:

1. **Exact:** the CN already opens every parquet footer for the `get_file_schema` RPC (`file_schema.rs:19-25`); it
   returns the summed `FileMetaData::num_rows` in a new optional field of `PGetFileSchemaResult`
   (`internal_service.proto:647-650`). The FE stores it on `TableFunctionTable`.
2. **Fallback, FE-only:** when the field is absent (upstream BE, `fake://` in unit tests, non-parquet), the FE uses
   `total file bytes / sum(getTypeSize)` from the `TBrokerFileStatus.size` it already collected
   (`TableFunctionTable.java:611`), which is byte-for-byte the rule `HiveStatisticsProvider.getEstimatedRowCount`
   uses for Hive tables without statistics (`connector/hive/HiveStatisticsProvider.java:138-163`).

`StatisticsCalculator.computeFileScanNode` (`sql/optimizer/statistics/StatisticsCalculator.java:664-676`) replaces
`setOutputRowCount(1)` with that estimate for `TableFunctionTable` scans. Column statistics stay `UNKNOWN` (no
min/max/NDV yet; that is a later layer). One mutable FE `Config` boolean, mirroring the existing Sirius FE knob
`files_query_whole_file_ranges` (`patches/files-query-whole-file-ranges.patch`), lets a reviewer flip old/new plans
with `ADMIN SET FRONTEND CONFIG` and `EXPLAIN COSTS` in minutes instead of a 40-minute FE rebuild.

Delivered as two fork PRs on `experimental/starrocks` (the carve plan's layer for FE patches and CN code): PR-A = FE
patch + FE unit tests + doc rows + CI patch-apply generalisation; PR-B = proto field + CN + FE consumption + CN tests.
No engine (`src/`) change, no translator change.

What this fix does **not** do, shown with the FE's own cost arithmetic in section 4: it does not by itself make
q05/q08/q09/q17/q18/q21 pass on 1 CN. The FE's cost model prefers shuffling a 6e9-row probe against any build above
~5M rows at 1 CN regardless of cardinalities, so lineitem still crosses an exchange until fix 4 (fusion or spill)
lands. Its value is that every downstream decision (build side, semi/anti commutation, broadcast guard, CTE reuse,
`broadcast_row_limit`, `exec_mem_limit`) stops being arithmetic on column widths (P3) and becomes tunable and
testable.

## 1. The defect and its consequences (measured)

| fact | evidence |
|---|---|
| Every FE estimate is 1 row for all 22 plans; actuals reach 6.0e9 | `survey/plan-summary.txt` (`card_all_one=True` x22); `card-compare-cn1.txt` (126 exchanges, FE est 1 everywhere; e.g. `q04 4 BROADCAST 1 3,793,363,900`, `q22 10 BROADCAST 1 1,500,000,000`, `q21 6 BROADCAST 1 730,806,711`) |
| Source of the 1 | `StatisticsCalculator.java:664-676` (`// cause we don't know the real schema in file, just use the default Row Count now`, `builder.setOutputRowCount(1)`, every column `ColumnStatistic.unknown()`), reached from `visitLogicalTableFunctionTableScan` (:374-375) |
| With every side at 1 row, BROADCAST vs SHUFFLE reduces to a column-width rule | P3: `HashJoinCostModel.getMemCost` (:115-130) `memCost = rightOutput * beNum`, `Statistics.getOutputSize` (:88-107) uses `getTypeSize()` for UNKNOWN columns; reproduces all 27 observed decisions |
| The six 1-CN failures are one plan shape: unfiltered lineitem leaf under a HASH_PARTITIONED sink parked whole (104 GB pool at death, 17,917 OOM lines) | P1 table; `cn1/engine-cn0.log`; `cn1-quent.txt` |
| `checkBroadcastRowCountLimit`, `JoinCommutativityRule` (semi/anti), and CTE reuse never fire at cardinality 1 | `EnforceAndCostTask.java:281-325` needs `rightChildStats.getOutputRowCount() > 15,000,000`; `JoinCommutativityRule.java:34-44`; `CostModel.visitPhysicalCTEAnchor` :563-570 (measured effect: q15 scans lineitem twice, `cn1-quent.txt:990-1000`; q04 parks 30.82 GB of `l_orderkey`, q22 broadcasts 1.5e9 orders rows x 4 CNs = 18.56 GB nixl, `cn4-transmits.txt`) |
| Row counts already exist on both sides of the RPC | FE: `TableFunctionTable.java:587-623` collects `TBrokerFileStatus.size` (`:611 brokerFileStatus.setSize(fileStatus.getLen())`; thrift `FileBrokerService.thrift:48-58`); CN: `file_schema.rs:19-25` reads the footer (`ParquetRecordBatchStreamBuilder::new(file).await`, `builder.metadata().file_metadata()`), and `parquet::file::metadata::FileMetaData::num_rows()` is one accessor away (parquet-59.2.0 `src/file/metadata/mod.rs:548`) |
| The translator does not read the FE cardinality; the engine's own statistics propagation is off | `node_translator.rs:1922-1929` (comment only; `grep cardinality` finds no consumer in the translator or CN besides the receiver-side `declare_input_cardinality`, `engine.rs:577`); P5 PC-6 `src/sirius_ffi.cpp:233-240` |

Consequence for blast radius: this change alters FE plan shape only (fragment cut, join sides, exchange type). Scan
split assignment is bytes-based (`FileScanNode.java:545-549`) and unchanged; in-fragment engine plans are unchanged.

## 2. Mechanism

### 2.1 FE: `TableFunctionTable` owns the estimate

`TableFunctionTable` (`catalog/TableFunctionTable.java`) gains:

```java
// total rows over fileStatuses as reported by get_file_schema; -1 when the node did not report one
private long footerRowCount = -1;

public long getTotalFileBytes() {                     // TBrokerFileStatus.size, files only
    return fileStatuses.stream().filter(f -> !f.isDir).mapToLong(f -> f.size).sum();
}

/** Row-count estimate for the CBO: footer total, else bytes / row width (Hive's rule), else 1. */
public long estimateRowCount() {
    if (!Config.files_scan_estimate_row_count) { return 1; }
    if (footerRowCount >= 0) { return Math.max(1, footerRowCount); }
    long bytes = getTotalFileBytes();
    int width = getFileColumns().stream().mapToInt(c -> c.getType().getTypeSize()).sum(); // excludes columns_from_path
    return (bytes <= 0 || width <= 0) ? 1 : Math.max(1, bytes / width);
}
```

`getFileSchema()` (:688-728) reads the new field right after the status check (:721-723):

```java
if (result.numRows != null && result.numRows >= 0) { footerRowCount = result.numRows; }
```

(jprotobuf-starrocks generates public camelCase fields from the proto, cf. `result.status.statusCode`,
`result.schema` at :721/:726; `fe-core/pom.xml:1128-1150` runs it over `internal_service.proto` at
`generate-sources`, so no hand-written Java mirrors the message.)

`getFileColumns()` is the schema minus the trailing `columnsFromPath` entries that `setSchemaForLoadAndQuery` appends
(:255-275), because path columns occupy no file bytes. `list_files_only` tables never populate `fileStatuses`, so
they fall through to 1 as today.

### 2.2 FE: `StatisticsCalculator` uses it

`computeFileScanNode` keeps its shape and takes the table:

```java
@Override
public Void visitLogicalTableFunctionTableScan(LogicalTableFunctionTableScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

@Override   // new: mirrors the visitLogicalFileScan/visitPhysicalFileScan pair at :655-662
public Void visitPhysicalTableFunctionTableScan(PhysicalTableFunctionTableScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

private Void computeFileScanNode(Operator node, ExpressionContext context, Table table,
                                 Map<ColumnRefOperator, Column> columnRefOperatorColumnMap) {
    Statistics.Builder builder = Statistics.builder();
    for (ColumnRefOperator columnRefOperator : columnRefOperatorColumnMap.keySet()) {
        builder.addColumnStatistic(columnRefOperator, ColumnStatistic.unknown());
    }
    // Column statistics stay UNKNOWN (no footer min/max/NDV yet). The row count is the footer total the compute
    // node reported with the schema, else total file bytes / row width, else 1 (FileTable keeps the old default).
    double rows = table instanceof TableFunctionTable ? ((TableFunctionTable) table).estimateRowCount() : 1;
    builder.setOutputRowCount(rows);
    context.setStatistics(builder.build());
    return visitOperator(node, context);
}
```

`visitOperator` (StatisticsCalculator, the block starting `public Void visitOperator(Operator node, ...)`) then applies
the scan's predicates and limit exactly as for every other scan (`estimateStatistics(ImmutableList.of(predicate),
statistics)`), so `o_orderdate` ranges, `r_name = 'ASIA'` etc. are estimated with the UNKNOWN-column rules
(section 4.1). `PlanNode.computeStatistics` (`planner/PlanNode.java:577-583`) copies `getOutputRowCount()` into the
`cardinality:` line of `EXPLAIN COSTS` (:398/:445), which is the observable the tests assert on.

`FileTable` (external file table, `visitLogicalFileScan`/`visitPhysicalFileScan` :655-662) keeps 1: out of scope,
noted as a follow-up in the patch comment (it has its own `RemoteFileDesc` lengths).

### 2.3 FE: one mutable Config switch (documented)

```java
/**
 * When true, FILES() scans are planned with a real row-count estimate: the parquet footer total returned by the
 * compute node with the file schema, else total file bytes / row width (the Hive no-statistics rule). Column
 * statistics stay unknown. Upstream plans every FILES() scan at 1 row, which makes every join side, broadcast and
 * CTE decision data-blind. Mutable so EXPLAIN COSTS can compare both plans without a rebuild.
 */
@ConfField(mutable = true)
public static boolean files_scan_estimate_row_count = true;
```

Precedent: `Config.files_query_whole_file_ranges` in `patches/files-query-whole-file-ranges.patch` (same javadoc
style, same `@ConfField(mutable = true)`, toggled at runtime by `ADMIN SET FRONTEND CONFIG`,
`benchmarks/pinned/restart.sh:35`). Default on: the patch does what its name says; the switch exists for
measurement and for the 1-CN caveat in section 7, not as a permanent mode.

### 2.4 Wire and CN: the exact count rides on the RPC that already reads the footers

Proto (`internal_service.proto:647-650`, new patch file `patches/files-schema-row-count-proto.patch`):

```proto
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
    // Sirius extension: exact row total over every range in the request, from the parquet footers the node
    // opened for the schema. Absent when the node cannot count (upstream BE samples files and does not set it).
    optional int64 num_rows = 3;
};
```

proto2 `optional` on an existing message is wire-compatible in both directions: a stock FE ignores the field, a
stock BE never sets it, the FE falls back (2.1).

CN (`file_schema.rs`): `parquet_file_schema` returns `(Vec<PSlotDescriptor>, i64)` with
`builder.metadata().file_metadata().num_rows()`; `parquet_files_schema` (:62-95) sums the per-file counts while it
runs its existing name/type agreement loop and returns a small struct:

```rust
pub(crate) struct FilesSchema { pub slots: Vec<PSlotDescriptor>, pub num_rows: i64 }
```

Handler (`compute_node_service.rs:681-699`): `PGetFileSchemaResult { status, schema: s.slots, num_rows: Some(s.num_rows) }`
on success, `num_rows: None` on error. `file_schema_from_attachment` (:1683-1707) is unchanged apart from the return
type.

Cost: zero extra I/O. The CN deliberately reads every footer already (`file_schema.rs:59-61`: whole-set agreement is
the contract; P4 measured the lineitem call at 28.3 ms busy for 60 footers). Interaction with P4's proposed fix
(section 4 item 7 of the report): that fix must stay "concurrent + memoised", not "sample N files", or the total
becomes partial; the design defines `num_rows` as exact-or-absent, so a sampling CN must send `None`.

`build.rs` guard (:72-108, `require_exchange_proto_patch`): add a second `(token, patch)` pair,
`("num_rows = 3", "patches/files-schema-row-count-proto.patch")`, so a checkout missing the patch fails with the
one-line fix instead of `no field num_rows on PGetFileSchemaResult` 200 lines later (the guard's stated purpose).

### 2.5 Delivery

| step | mechanism | evidence |
|---|---|---|
| Patches | `experimental/starrocks/patches/files-scan-statistics.patch` (Java: `Config.java`, `TableFunctionTable.java`, `StatisticsCalculator.java`, two test files) and `files-schema-row-count-proto.patch` (proto). Separate hunks from `nixl-exchange-proto.patch` (:786+ vs :647-650), so `git apply` order is irrelevant | `scripts/apply-starrocks-patches.sh` loops over `patches/*.patch`, applies or reverse-checks, exits 1 on drift |
| Local build | `pixi run apply-starrocks-patches` (pixi.toml:94); `fe-check`, `cn-build`, `cn-test` already depend on it (:96-104, :135-145); FE: `pixi run -e fe fe-build` (Maven, ~40 min, copies `conf/fe.conf`, pixi.toml:197+) | `.claude/skills/build-sirius-starrocks/SKILL.md:23` |
| CI | `.github/workflows/experimental.yml:52-57` applies **only** `nixl-exchange-proto.patch` by name before `cargo` runs; PR-B must change that step to `bash experimental/starrocks/scripts/apply-starrocks-patches.sh` (idempotent by construction) or the CN build breaks on the new field. The carve plan already lists "the mandatory `.github/workflows/experimental.yml` patch-apply step" for proto patches (step 5, C3) | workflow file; carve plan |
| Submodule hygiene | `.gitmodules:29-31 ignore = dirty`; never `git add` the submodule after patching | tpch-bench skill note |
| FE unit tests | `starrocks/run-fe-ut.sh --test <class>` inside `pixi run -e fe` (no pixi task exists; document the invocation in the PR) | `run-fe-ut.sh` usage |

### 2.6 Docs (no knob without a doc row)

`experimental/starrocks/docs/TUNABLES.md` currently has no front-end section, and the existing FE knob
`files_query_whole_file_ranges` is documented only in `benchmarks/pinned/README.md:50-54`. Add:

```
## Front end (patched StarRocks)

FE `Config` values from `patches/*.patch`; set at runtime with `ADMIN SET FRONTEND CONFIG ("k" = "v")`.

| Knob | Role |
|---|---|
| `files_query_whole_file_ranges` | Whole-file scan assignment for FILES() queries so pinned tables can serve them (pinned README). |
| `files_scan_estimate_row_count` | Plan FILES() scans with the footer row total (or bytes / row width) instead of 1 row. On by default; off reproduces upstream's data-blind plans for A/B with `EXPLAIN COSTS`. |

Session variables that only start to matter once FILES() has cardinalities: `broadcast_row_limit` (15M rows; the
broadcast guard), `exec_mem_limit` (2 GiB; broadcast builds above it cost x1000), `cbo_cte_reuse` (keep `false`
until the CN implements MULTI_CAST_DATA_STREAM_SINK, see below).
```

## 3. Files to touch

FE (inside the submodule, shipped as `patches/files-scan-statistics.patch`):

- `fe/fe-core/src/main/java/com/starrocks/common/Config.java` (next to `files_query_whole_file_ranges`, ~1222): +1 field.
- `fe/fe-core/src/main/java/com/starrocks/catalog/TableFunctionTable.java`: field `footerRowCount`; `getTotalFileBytes()`, `getFileColumns()`, `estimateRowCount()`; one line in `getFileSchema()` after :723. ~35 lines.
- `fe/fe-core/src/main/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculator.java:374-375, 664-676`: table parameter, `visitPhysicalTableFunctionTableScan` override, one import. ~15 lines changed.
- `fe/fe-core/src/test/java/com/starrocks/catalog/TableFunctionTableTest.java`: 2 tests.
- `fe/fe-core/src/test/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculatorTest.java`: 1 test.

Proto (`patches/files-schema-row-count-proto.patch`): `gensrc/proto/internal_service.proto:647-650`, +3 lines.

CN (tracked files, ordinary PR diff):

- `experimental/starrocks/src/file_schema.rs:14-95`: return `num_rows`; tests.
- `experimental/starrocks/src/compute_node_service.rs:681-699, 1683-1707`: set the field; test at :3624 extended.
- `experimental/starrocks/build.rs:72-108`: second guard token.
- `.github/workflows/experimental.yml:52-57`: apply all patches.
- `experimental/starrocks/docs/TUNABLES.md`: section from 2.6.

Untouched: `src/` (engine), `crates/starrocks-plan-translator`, `FileScanNode.java`, `Deployer`, the bench kit.

## 4. Behaviour under the measured SF1000 case

### 4.1 What the FE will now compute (measured inputs, computed outputs)

Row-count inputs. Footer totals (measured, `metadata.json`, `agent-notes/planning-contrast.md:22`) and the FE-only
fallback from on-disk bytes (measured with `du -sb` on this box, 2026-09-03) over `getTypeSize` widths
(`fe-type/.../PrimitiveType.java`: INT/DATE 4, BIGINT/DECIMAL64 8, CHAR/VARCHAR 16; the inferred types are those in
`survey/explain/q05.costs.txt`, e.g. `l_orderkey BIGINT`, `l_extendedprice DECIMAL64(15,2)`, `n_name VARCHAR`):

| table | files | bytes on disk | width B | fallback est. rows | footer rows (exact path) | fallback / exact |
|---|---:|---:|---:|---:|---:|---:|
| lineitem | 60 | 177,214,004,134 | 144 | 1,230,652,806 | 5,999,989,709 | 0.21 |
| orders | 15 | 52,374,826,801 | 92 | 569,291,595 | 1,500,000,000 | 0.38 |
| partsupp | 8 | 37,878,661,069 | 36 | 1,052,185,029 | 800,000,000 | 1.32 |
| customer | 2 | 10,761,809,562 | 96 | 112,102,182 | 150,000,000 | 0.75 |
| part | 2 | 5,084,217,527 | 112 | 45,394,799 | 200,000,000 | 0.23 |
| supplier | 1 | 686,828,646 | 80 | 8,585,358 | 10,000,000 | 0.86 |
| nation | 1 | 2,250 | 40 | 56 | 25 | 2.24 |
| region | 1 | 1,103 | 36 | 30 | 5 | 6.00 |

The fallback is within 0.2-6x and preserves the big/small split (lineitem > orders/partsupp > customer/part >
supplier > nation/region) but inverts two pairs (partsupp vs orders; part vs customer) and puts supplier under the
15M broadcast guard while the exact count (10M) also does. This is why the exact path is primary and the fallback
is a fallback: the absolute thresholds in the cost model (15,000,000 rows; 2 GiB) are meaningful only with exact
counts.

Predicate handling (source reading, applies identically under both paths): for UNKNOWN columns
`estimateColumnEqualToConstant` (`BinaryPredicateStatisticCalculator.java:86-103`) and the inequality builders
(:240-262, :280-300, `new StatisticRangeValues(d, +inf, NaN)`) both end in `estimatePredicateRange` (:624-655) and
`StatisticRangeValues.overlapPercentWith`, which returns 0.5 for a point on an unknown column
(`lengthOfIntersect == 0 && distinctValues == 1 && length() > 1 -> 0.5`) and
`OVERLAP_INFINITE_RANGE_FILTER_COEFFICIENT = 0.5` for a half-open range. So `o_orderdate >= d1 AND < d2` -> 0.25,
`r_name = 'ASIA'` -> 0.5 (the report's "string equality 1.0" in C1 does not match this code path; flagged as a
correction, inference from source). Join outputs with unknown key statistics are `max(L, R)`
(`StatisticsCalculator.computeJoinNode` :1219-1240; x `0.25^n` when the sides differ by >= 1e7, which is the
lineitem x nation/region case).

Worked examples of what `EXPLAIN COSTS` should print after the change (inference, arithmetic from the above):
q05 exch 1 (customer, no predicate): 150,000,000 (actual 150,000,000); q05 exch 4 (customer x orders): max(1.5e8,
0.25 x 1.5e9) = 375,000,000 (actual 227,571,151); q04 lineitem leaf 5,999,989,709 x predicate factors; q21 exch 6
(orders `o_orderstatus = 'F'`): 750,000,000 (actual 730,806,711).

### 4.2 The six 1-CN failures: the FE will still shuffle lineitem (inference, computed from the cost code)

q05's lineitem join at 1 CN with exact counts. L = lineitem projection 6e9 rows x 28 B = 168 GB (measured parked
projection at death 171 GB, P1); R = customer x orders(0.25) = 3.75e8 rows x 12 B = 4.5 GB. `getRealCost`
weights 0.5/2/1.5 (`CostModel.java:129-135`):

| term | BROADCAST R | SHUFFLE both |
|---|---:|---:|
| exchange (`visitPhysicalDistribution` :400-460; CN-only so `aliveBackendNumber = 0`, beNum 1; single registered node so shuffle network cost 0, `SystemInfoService.isSingleBackendAndComputeNode` :246-248) | 2R + 1.5R = 15.8, x1000 because 4.5 GB > `exec_mem_limit` 2 GiB (:426-428, `SessionVariable.DEFAULT_EXEC_MEM_LIMIT` :1305) = 15,750 | 0.5(L+R) = 86 |
| hash join (`HashJoinCostModel.getCpuCost/getMemCost/getAvgProbeCost` :85-157) | probe penalty min(12, ln(3.75e8/1e5)) = 8.2 -> 0.5(R + 8.2L) + 2R = 703 | penalty min(3, 8.2 - log2(parallelFactor)) = 3 -> 0.5(R + 3L) + 2R = 263 |
| total | 16,453 (718 even with the memory penalty removed) | 350 |

SHUFFLE wins by 2x even with `exec_mem_limit` raised; broadcast wins at 1 CN only while ln(R_rows/1e5) < ~4, i.e.
build sides under ~5.5M rows (nation, region, supplier, filtered part). Each of the six has a lineitem join whose
other side is orders- or partsupp-derived (1e8-1e9 rows), so all six keep the P1 shape at 1 CN. With 4 registered
nodes (the survey condition, P3) shuffle network cost is nonzero and the margin narrows (345 vs 718 for the
exchanges) but the join penalty term still decides for shuffle. **Conclusion: fix 3 does not un-break the six; fix
4 does.** Fix 1 (fail fast) is what makes the still-wrong plans cheap to observe.

### 4.3 What does change (inference, ranked by measured stake)

| decision | before (measured) | after (predicted) | measured stake |
|---|---|---|---|
| Semi/anti build side via `JoinCommutativityRule` (:34-44) | q04 `5:LEFT SEMI JOIN (BROADCAST) build=lineitem` 3.79e9 rows; q22 `11:LEFT ANTI JOIN (BROADCAST) build=orders` 1.5e9; q21 `14:LEFT SEMI ... build=lineitem`; q20 `22:LEFT SEMI (BROADCAST)` | commutation becomes cost-driven: q04 -> RIGHT SEMI with orders(0.25 -> 3.75e8 est, 57M actual) as build; q22 -> RIGHT ANTI with filtered customers (est 3.75e7, actual ~6M) as build; q21/q20 similar | q04: 30.82 GB parked at 1 CN, 22.76 GB nixl at 4 CNs (P5); q22: 18.56 GB nixl, join 0.516 s x4 CNs, scaling 1.13x (P3/P5); q21 exch 6: 731M rows broadcast |
| Broadcast guard (`EnforceAndCostTask.java:312-325`) | inert (all rows = 1) | fires whenever R > 15M rows and L < 10R bytes. Computed: q03 orders x customer 7.5e8 est rows, L 72 GB < 10R 120 GB -> REJECT (PARTITIONED at 1 CN too); q12 lineitem-filtered est 1.9e8 rows (actual 31M), L 36 GB < 45 GB -> REJECT. P3's "q12/q19 flip to BROADCAST" does **not** hold under these numbers; both stay PARTITIONED until `broadcast_row_limit` is raised or column stats arrive | q03 1 CN: lineitem shuffle would park 3.23e9 x 24 B = 78 GB (cn4 measured 78.8 GB summed) in the 100 GiB pool: **regression risk**, section 7 |
| CTE inline vs reuse (`CTEContext.needInline` :157-190 defers to cost; `CostModel.visitPhysicalCTEAnchor` :563-570 = (0.25N + 2(1+1.15))S) | inline always (all costs ~0); q15 scans lineitem twice (2 x 122 GB, 2253 + 2163 ms serial), q11 twice | reuse wins by orders of magnitude (anchor 0.58 GB-units vs a second 336 GB scan) -> FE emits `MULTI_CAST_DATA_STREAM_SINK`, which the CN refuses (`compute_node_service.rs:1519`, test :3568-3588) | q15 and q11 would fail translation: **must be guarded** (section 7) |
| Fixed per-statement head (P4) | 8 `get_file_schema` RPCs, 104-114 ms median | unchanged: the footer is already parsed; summing 60 `i64`s is free | 0 ms |
| Join ordering (`ReorderJoinRule`, uses `StatisticsCalculator` at :173/:365/:381/:466) | arbitrary at 1 row | cost-driven with exact counts | q02's 800M-row partsupp chain (P2), q10's build on the 25.5 GB side (BJ-4) become addressable, not fixed here |

## 5. Tests

Unit / component (run without a cluster):

| layer | test | asserts |
|---|---|---|
| FE `TableFunctionTableTest` | `testEstimateRowCountFromFileSizes`: `fake://` path yields two files of 1024 + 2048 B and columns `col_int INT`, `col_string VARCHAR` (`TableFunctionTable.java:589-602, 259-262`) | `estimateRowCount() == 3072 / (4 + 16) = 153`; with `columns_from_path` set the width still excludes path columns; with `Config.files_scan_estimate_row_count = false` returns 1 |
| FE `TableFunctionTableTest` | `testFooterRowCountPreferred`: JMockit `MockUp<BackendServiceClient>` (the class's existing mocking idiom, :38-70) returns a `PGetFileSchemaResult` with `numRows = 1000` | `estimateRowCount() == 1000`; `numRows = null` falls back to 153 |
| FE `StatisticsCalculatorTest` | `testLogicalTableFunctionTableScan`: build `LogicalTableFunctionTableScanOperator(table, refToColumn, columnToRef, -1, null)` (ctor :32-45), `GroupExpression`/`ExpressionContext` as in `testLogicalOlapTableScan` (:246-292), `estimatorStats()` | `getOutputRowCount() == 153`; every column `isUnknown()`; `getComputeSize() == 153 x sum(getTypeSize)`; 1 when the Config is off; the physical operator path returns the same statistics |
| FE plan test (`PlanTestBase`) | `FilesScanStatisticsTest`: `getCostExplain("select * from files('path'='fake://a','format'='parquet') a join files('path'='fake://b','format'='parquet') b on a.col_int = b.col_int")` (fake FILES() statements are already planned in UTs, `PipeManagerTest:203-254`) | both `FileScanNode` blocks print `cardinality: 153` (contains-assert, not a full golden, to survive unrelated plan-text churn) |
| CN `file_schema.rs` | `multi_file_num_rows_sums_footers`: two fixtures written with one row group each (3 and 5 rows; `SerializedFileWriter` + a row-group writer, ~15 lines next to `write_parquet` :261-273) | `num_rows == 8`; the existing schema-only fixtures give 0 |
| CN `compute_node_service.rs` | extend `get_file_schema_attachment_infers_across_multiple_ranges` (:3624-3640) | `num_rows == Some(total)`; error path leaves it `None` |
| CN `build.rs` | none (guard behaviour is exercised by CI's patch-apply step) | |

Cluster, minutes (reviewer-rerunnable): `EXPLAIN COSTS` for q03 q04 q05 q22 (and all 22 via `survey.sh`'s loop,
`survey.sh:16-19`, no query execution needed) on a **clean 1-CN cluster** (one registered node; the survey had three
dead CNs registered, which changes the shuffle network term) and on 4 CNs, with
`ADMIN SET FRONTEND CONFIG ("files_scan_estimate_row_count" = "false")` then `"true"`. Golden: every `FileScanNode`
`cardinality:` equals the footer count (5,999,989,709 for lineitem, 1,500,000,000 orders, ...) before predicates;
diff of join ops/sides/distribution recorded as `survey-stats/plan-summary{,-4cn}.txt` with `explain_summary.py`.

SF1000 acceptance (orchestrator-run, one arm at a time, per the plan table): 1-CN and 4-CN sweeps of the 15 passing
queries plus the six with `run-queries.sh`, oracle compare (`compare-*.txt` procedure), timings vs `results.md`,
`[gpu_pool]` peaks from `engine-cn0.log`. Preconditions stated in section 8: translator RIGHT_SEMI landed,
`SET GLOBAL cbo_cte_reuse = false` set and read back (the `run-abc.sh:795-806` set-and-verify idiom), fix 1 landed
so a wrong shape costs seconds. Pass: the 15 still pass with the same oracle status; the six still fail (expected,
4.2) but quickly; plan shapes recorded.

## 6. Predicted effect (numbers derived from the evidence)

Measured facts the design changes directly:

- `card-compare-cn1.txt` FE-estimate column: 126 exchanges at 1 -> scan leaves exact (0% error on 8 tables); exchange
  estimates become predicate/join estimates instead of 1 (e.g. q05 exch 4: 3.75e8 vs actual 2.28e8 = 1.65x, from
  800,000,000x today).
- P4 head: +0 ms (footers already parsed).

Predicted plan consequences (inference, hand computation of the FE cost code, medium confidence; the `EXPLAIN COSTS`
step in section 5 is the arbiter):

- Semi/anti commutation to RIGHT SEMI / RIGHT ANTI where the non-lineitem side is smaller: q04, q22, q21, q20. Stake
  (measured today): q04 30.82 GB parked at 1 CN and 22.76 GB nixl at 4 CNs; q22 18.56 GB nixl at 4 CNs and a 1.13x
  4-CN speed-up; q21 exch 6 731M-row broadcast. How much of that is recovered depends on the guard (a RIGHT SEMI
  with a 3.75e8-row estimated build is still PARTITIONED, so at 4 CNs lineitem is still shuffled unless
  `broadcast_row_limit` is raised); the memory win at 1 CN is real only where the FE puts lineitem in the same
  fragment as the join.
- No change for the six at 1 CN (4.2). No change for q01/q06 (no joins). q14/q19 (filtered part builds, ~5e7-1e8
  est rows) sit near the 15M guard: PARTITIONED at 4 CNs as today.
- Wall-clock: no measured number can be promised from this fix alone; the plan-shape record is the deliverable, and
  the sweeps bound the regression risk.

## 7. Risks (ranked) and mitigations

1. **RIGHT SEMI plans hit a translator gap (high likelihood, high impact).** `JoinCommutativityRule` maps
   LEFT_SEMI <-> RIGHT_SEMI (:34-44); `node_translator.rs:1743-1762` has no `RIGHT_SEMI_JOIN` arm ("hash join type is
   unsupported"). q04, q18, q20, q21 carry LEFT SEMI joins whose other side is now estimated smaller. Mitigation:
   land plan item 5 (translator `RIGHT_SEMI_JOIN => (RightSemi, JoinOutput::Right)`; the Substrait consumer and
   engine already support it, P5) **before** enabling the Config by default in the bench; until then run with the
   Config off. RIGHT_ANTI is already translated (:1750), so q22's commutation is safe.
2. **CTE reuse turns on and the CN refuses `MULTI_CAST_DATA_STREAM_SINK` (high likelihood for q15/q11, high
   impact).** Cost arithmetic in 4.3. Mitigation: acceptance runs `SET GLOBAL cbo_cte_reuse = false` (existing
   session variable, `SessionVariable.java:499/1506`, wired at `OptimizerContext.java:98`), which reproduces today's
   inline plans exactly; documented in the TUNABLES row; lifted when plan item 9 lands. This is also the fix for the
   q15 double scan, so the two fixes are complementary, not competing.
3. **1-CN broadcast-to-shuffle flips park a large probe (medium likelihood, high impact).** With real counts the
   guard rejects q03's orders x customer broadcast (72 GB < 120 GB), so lineitem (78 GB measured on cn4, summed)
   would be parked at 1 CN in a 100 GiB pool with 58-80% cold-start reservations (C3): q03 goes from 3.1 s to
   marginal or OOM. Same mechanism, smaller volumes, for q12 (orders 36 GB) and q10. Mitigations, in order: (i)
   `EXPLAIN COSTS` at 1 CN before any sweep; (ii) GPU-sized `broadcast_row_limit` (session, no code) for the 1-CN
   arm and a documented row; (iii) fix 4a (fusion) removes the exchange at 1 CN entirely and makes this moot; (iv)
   fix 1 makes a bad case cost seconds.
4. **Fallback estimator error (0.2-6x) inverts partsupp/orders and part/customer** (measured table 4.1). Only in
   fallback mode (no CN `num_rows`); the exact path removes it. The FE UT covers the fallback with `fake://`.
5. **Patch drift on submodule bumps.** Three Java files plus the proto; `apply-starrocks-patches.sh` fails loudly
   (`exit 1`). The Java change is small and plausibly upstreamable to StarRocks (it is the Hive rule applied to
   FILES()), which would shrink the patch over time.
6. **Load path shares the operator.** `INSERT ... SELECT FROM FILES()` gets the same estimate; harmless for
   correctness, may change load plan text. Existing FE UTs that plan `fake://` FILES() (`PipeManagerTest`,
   `StatementPlannerTest`) assert structure, not `cardinality:` lines (checked by grep), so none should break.
7. **Determinism.** Statistics are a pure function of file sizes/footers; plans are stable across runs. The fallback
   depends on compression (a different copy of the same table gives different estimates); the exact path does not.

## 8. Effort and dependencies

Effort: PR-A (FE patch, three UTs, TUNABLES section, CI step) ~1 day including one 40-minute `fe-build` and
`run-fe-ut.sh --test` cycles; PR-B (proto, CN, guard, CN tests, FE consumption) ~0.5-1 day (CN rebuild is minutes;
the FE consumption line rides on PR-A's rebuild if the two are built together). `EXPLAIN COSTS` goldens ~30 min per
CN count; SF1000 sweeps ~30 min per arm (the 15 passing queries; the six now fail in seconds with fix 1).

Order relative to the other three fixes and the plan's items:

| dependency | direction | why |
|---|---|---|
| Plan item 5 (translator RIGHT_SEMI) | **before** default-on | risk 1 |
| Plan item 9 (CN multicast) or `cbo_cte_reuse=false` | guard until landed | risk 2 |
| Fix 1 (fail fast) | before the SF1000 arm | wrong shapes die in seconds, not minutes (54-232 s today) |
| Fix 2 (bookkeeping) | before the SF1000 arm | clean pool between queries; otherwise plan-shape timing is confounded by leaks (B4, report section 5 item 7) |
| Fix 4a (fusion) | after; complementary | fusion neutralises fix 3's 1-CN shuffle risk (risk 3) and is what makes the six pass; fix 3 gives the FE sane join order across fragments and makes `broadcast_row_limit`/`exec_mem_limit` meaningful for 4 CNs |
| Fix 4b (spill) | independent | |

## 9. Code excerpts relied upon

`StatisticsCalculator.java:664-676` (the defect):
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
`StatisticsCalculator.java:374-375`: `visitLogicalTableFunctionTableScan -> computeFileScanNode(node, context, node.getColRefToColumnMetaMap())`.
`OperatorVisitor.java:454-456`: `visitPhysicalTableFunctionTableScan` defaults to `visitOperator`.

`TableFunctionTable.java:604-613` (sizes already collected):
```java
                for (FileStatus fileStatus : fileStatusList) {
                    TBrokerFileStatus brokerFileStatus = new TBrokerFileStatus();
                    brokerFileStatus.setPath(fileStatus.getPath().toString());
                    brokerFileStatus.setIsDir(fileStatus.isDirectory());
                    brokerFileStatus.setSize(fileStatus.getLen());
                    brokerFileStatus.setIsSplitable(true);
                    fileStatuses.add(brokerFileStatus);
```
`TableFunctionTable.java:589-602`: `fake://` yields two files, sizes 1024 and 2048. `:259-262`: `fake://` schema is `col_int INT`, `col_string VARCHAR` without an RPC.
`TableFunctionTable.java:709-728`: the RPC and its consumption (`result.status.statusCode`, `result.schema`).

`HiveStatisticsProvider.java:157-162` (the rule reused):
```java
        if (totalBytes <= 0) {
            return 1;
        }
        long presentRowNums = totalBytes / dataColumns.stream().mapToInt(column -> column.getType().getTypeSize()).sum();
```

`internal_service.proto:647-650`:
```proto
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
};
```

`file_schema.rs:19-25` (footer already open):
```rust
    let builder = ParquetRecordBatchStreamBuilder::new(file)
        .await
        .map_err(|err| format!("failed to read parquet metadata from {local}: {err}"))?;
    // Iterate the top-level fields (not physical leaves) so column names and nesting are preserved.
    let slots = builder
        .metadata()
        .file_metadata()
```
parquet-59.2.0 `src/file/metadata/mod.rs:547-549`: `pub fn num_rows(&self) -> i64 { self.num_rows }` on `FileMetaData`.

`compute_node_service.rs:688-697` (handler):
```rust
        let result = match Self::file_schema_from_attachment(&attachment).await {
            Ok(schema) => PGetFileSchemaResult {
                status: Self::ok_status(),
                schema,
            },
            Err(err) => PGetFileSchemaResult {
                status: Self::internal_error(err),
                schema: Vec::new(),
            },
        };
```

`CostModel.java:419-428` (broadcast term and the exec-mem penalty):
```java
                case BROADCAST:
                    int aliveBackendNumber = ctx.getAliveBackendNumber();
                    int beNum = Math.max(1, aliveBackendNumber);
                    result = CostEstimate.of(outputSize * aliveBackendNumber,
                            outputSize * beNum,
                            Math.max(outputSize * beNum, 1));
                    if (outputSize > sessionVariable.getMaxExecMemByte()) {
                        result = result.multiplyBy(StatisticsEstimateCoefficient.BROADCAST_JOIN_MEM_EXCEED_PENALTY);
                    }
```
`CostModel.java:432-440`: SHUFFLE `networkCost = ignoreNetworkCost ? 0 : max(outputSize, 1)` with
`ignoreNetworkCost = isEnableLocalShuffleAgg && isEnablePipelineEngine && isSingleBackendAndComputeNode()`.
`CostModel.java:129-135`: weights cpu 0.5, memory 2, network 1.5.
`HashJoinCostModel.java:33-35`: `SHUFFLE_MAX_RATIO = 3`, `BROADCAST_MAT_RATIO = 12`; `:132-157` `getAvgProbeCost`
(`mapSize = min(1, keySize) * rightRows`, penalty `ln(mapSize / BOTTOM_NUMBER)` capped at 12 or 3 minus `log2(parallelFactor)`).
`EnforceAndCostTask.java:312-325`: `broadcast_row_limit <= 0 -> false`; `beNum = max(1, aliveBackendNumber)`;
reject when `leftOutputSize < rightOutputSize * beNum * 10 && rightRows > 15,000,000`.
`SessionVariable.java:1305, 1506, 1832-1836`: `DEFAULT_EXEC_MEM_LIMIT = 2147483648L`, `cboCteReuse = true`,
`broadcastRowCountLimit = 15000000`, `broadcastRightTableScaleFactor = 10.0`.
`CostModel.java:563-570`: CTE anchor `CostEstimate.of(produceSize * consumeNum * 0.5, produceSize * (1 + ratio), 0)`.
`CTEContext.java:157-190`: `needInline` returns false (memo decides) when reuse is enabled, N > 1 and ratio > 0.
`JoinCommutativityRule.java:34-44`: LEFT_SEMI <-> RIGHT_SEMI, LEFT_ANTI <-> RIGHT_ANTI.
`node_translator.rs:1743-1762`: supported ops INNER, LEFT/RIGHT/FULL OUTER, LEFT_SEMI, LEFT_ANTI, RIGHT_ANTI,
NULL_AWARE_LEFT_ANTI; `_ => "hash join type is unsupported"`.
`StatisticRangeValues.overlapPercentWith`: point on unknown -> 0.5; infinite intersect with NaN distinct -> 0.5.
`Statistics.java:88-107`: `getOutputSize` uses `getTypeSize()` for UNKNOWN columns.
`PlanNode.java:577-583`: `cardinality = Math.round(statistics.getOutputRowCount())`.
`FileScanNode.java:545-549`: `numInstances = totalBytes / min_bytes_per_broker_scanner` (bytes-based, unaffected).
`build.rs:72-108`: `require_exchange_proto_patch` matches `message PExchangeNixlMd`, prints the `git apply` fix.
`.github/workflows/experimental.yml:52-57`: applies `patches/nixl-exchange-proto.patch` only.
`apply-starrocks-patches.sh`: loops `patches/*.patch`, `git apply --check` / `--reverse --check`, `exit 1` on drift.
`pixi.toml:94, 96-104, 135-145, 197+`: `apply-starrocks-patches`, `fe-check`, `cn-build`/`cn-test` depend on it, `fe-build`.
`patches/files-query-whole-file-ranges.patch`: `Config.files_query_whole_file_ranges` javadoc + `@ConfField(mutable = true)` precedent.
`benchmarks/pinned/README.md:50-54`, `restart.sh:35`: how the existing FE knob is documented and set.

## 10. Open questions the acceptance step answers

1. Exact `EXPLAIN COSTS` at a clean 1 CN and at 4 CNs (never run with statistics; report section 5 item 2): which of
   the 27 broadcast joins flip, and whether q03 at 1 CN is the regression 4.3 predicts.
2. Whether the FE commutes q04/q21/q20 to RIGHT SEMI as predicted (decides the urgency of plan item 5).
3. What `broadcast_row_limit` value suits a GPU CN with a 100 GiB pool once counts are real (session experiment, no
   code).
4. Whether `exec_mem_limit` sent to the CN as `mem_limit` (`SessionVariable.java:6132`) is read anywhere in the CN
   (not checked); if not, raising it is FE-only and safe.
