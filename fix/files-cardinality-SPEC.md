# Spec: real FILES() cardinalities in the StarRocks FE (fix 3 of the SF1000 top-4 plan)

Written 2026-09-03 for an implementer working alone in
`/home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality` (branch off `perf/profile-sf1000` = `45dab3be`;
the StarRocks submodule there already has the two existing patches applied: `git -C experimental/starrocks/starrocks
status` shows `Config.java`, `FileScanNode.java`, `internal_service.proto` modified). Every `file:line` below was
re-read in that tree today. Paths: `FE` = `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks`,
`FET` = `experimental/starrocks/starrocks/fe/fe-core/src/test/java/com/starrocks`, `CN` = `experimental/starrocks/src`,
proto = `experimental/starrocks/starrocks/gensrc/proto/internal_service.proto`. Evidence root =
`/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000`.

"Measured" = read from a named evidence file or from source. "Inference" = hand computation of FE cost code that has
never been executed with statistics (report section 5 item 2); the EXPLAIN gate in section 7 is what settles it.

## 0. Decision record

Both judges scored the three designs (surgical 19/23, upstream 19/21, robust 14/14). Judge 1 chose upstream, judge 2
chose surgical "in the upstream design's shape". Those are the same thing: PR-A of upstream *is* the surgical design
with the estimate living on `TableFunctionTable` instead of inside `StatisticsCalculator`. This spec implements:

- **PR-A** (FE patch, no wire change): bytes / row width estimate (the Hive no-statistics rule), one mutable Config
  knob, FE unit tests, harness and docs. Deliverable of issue 3 on its own; identical to the surgical design.
- **PR-B** (stacked on PR-A): one `optional int64 num_rows = 3` on `PGetFileSchemaResult`, filled by the CN from the
  parquet footers it already opens, consumed by the FE as the preferred source. Makes the leaf counts exact and the
  EXPLAIN goldens independent of the dataset copy. ~40 lines of Rust, +3 proto lines, one CI step change.

Merged in from the other designs as the judges asked: explicit `isListFilesOnly()` guard; `getFileColumns()` that
excludes `columns_from_path`; `tableRowCountMayInaccurate=true` only on the fallback path; one FE INFO line per
statement and CN span fields; a `PlanTestBase` test with injected statistics asserting join side and distribution;
the `build.rs` guard extended for the new proto token; `.github/workflows/experimental.yml` switched to
`apply-starrocks-patches.sh`; `SET GLOBAL cbo_cte_reuse = false` in the bench bring-up with read-back; the mutable-knob
A/B procedure on one FE; goldens worded as **post-predicate** FileScanNode cardinalities; string equality on an
UNKNOWN column is **1.0** (not 0.5), `colA < colB` is **0.5**.

**Not** in this spec (decided at the EXPLAIN gate, see section 11): the robust design's Part B cost levers
(`exec_mem_limit`, `enable_local_shuffle_agg=false`, `broadcast_row_limit`, a probe-penalty cap in
`HashJoinCostModel`) = the plan's fix 4(d), and per-file / per-column footer statistics (proto surface without a
measured payoff: no decision examined changes sign between the 0.25 default and the footer range).

## 1. Goal and non-goals

**Goal.** Every FILES() scan the FE plans carries its real row count (exact from parquet footers when the CN
reports it, else bytes / typed row width), so join sides, BROADCAST/SHUFFLE choice, the broadcast guard, semi/anti
commutation and CTE costing stop being arithmetic on column widths. Observable: `EXPLAIN COSTS` prints
`cardinality: 5999989709` on the lineitem `FileScanNode` instead of `cardinality: 1`; `card_all_one=False` for all
22 TPC-H plans.

**Non-goals (stated so nobody scores this fix against them).**

1. It does not make q05 q08 q09 q17 q18 q21 pass on 1 CN. Inference from the FE cost code, agreed by all three
   designs and both judges (section 8.1): with real counts and stock settings the FE still hash-partitions
   lineitem at 1 CN whenever the other side is above ~5M estimated rows. Fix 4 (fusion or spill) is what makes the
   six pass; fix 1 makes the still-wrong plans die in seconds.
2. No column statistics (min/max/NDV). Every column stays `ColumnStatistic.unknown()`.
3. No engine (`src/`) change, no translator change, no change to scan split assignment (`FileScanNode.java:545-549`
   is bytes-based and untouched), no change to the receiver-first exchange mechanism (B1).
4. No cost-model change. Session-variable experiments belong to the gate, not to this patch.

## 2. Measured facts this spec relies on

| fact | where |
|---|---|
| Every FE estimate is 1 row for all 22 plans; actuals reach 6.0e9 | `survey/plan-summary.txt` (`card_all_one=True` x22); `card-compare-cn1.txt` (126 exchanges, FE est 1; e.g. `q04 4 BROADCAST 1 3,793,363,900`, `q22 10 BROADCAST 1 1,500,000,000`) |
| Source of the 1 | `FE/sql/optimizer/statistics/StatisticsCalculator.java:664-676` (`builder.setOutputRowCount(1)`, every column `unknown()`), reached from `visitLogicalTableFunctionTableScan` `:374-376`. The physical FILES() operator falls through to `visitOperator` (`FE/sql/optimizer/operator/OperatorVisitor.java:454-456`) and is never re-derived on the main path (`PlanFragmentBuilder.java:4294` uses `optExpression.getStatistics()`), so the logical override is what decides today |
| Predicates and limit are applied after the leaf estimate | `StatisticsCalculator.visitOperator` `:245-300` (`estimateStatistics(ImmutableList.of(predicate), statistics)`, then limit); `PlanNode.computeStatistics` `FE/planner/PlanNode.java:578-587` copies `Math.round(getOutputRowCount())` into the `cardinality:` line printed at `:398/:445`. So `EXPLAIN COSTS` shows the **post-predicate** count on a FileScanNode (`survey/explain/q05.costs.txt` shows the `o_orderdate` range already applied on FileScanNode 2) |
| File sizes already on the FE | `FE/catalog/TableFunctionTable.java:606-613` (`brokerFileStatus.setSize(fileStatus.getLen())` at `:611`), list at `:188`, exposed by `loadFileList()` `:300-302`; `fake://` fixture lists 1024 + 2048 B (`:589-604`) with schema `col_int INT, col_string VARCHAR` (`:259-262`); `columns_from_path` columns are appended after the file schema (`:268-275`, `getSchemaFromPath` `:732-738`) |
| Schema RPC | `TableFunctionTable.getFileSchema()` `:688-730`: picks a BE or (shared-data) CN, `BackendServiceClient.getInstance().getFileSchema(address, request)` (`FE/rpc/BackendServiceClient.java:293-302`), unbounded `future.get()` at `:713`, consumes `result.status.statusCode` and `result.schema` (`:721-727`). `run_mode = shared_data` (`experimental/starrocks/conf/fe.conf:88`) so CNs are eligible |
| Same rule exists upstream for Hive | `FE/connector/hive/HiveStatisticsProvider.java:138-163` (`totalBytes / sum(getTypeSize)`, returns 1 when `totalBytes <= 0`) |
| Type widths | `experimental/starrocks/starrocks/fe/fe-type/src/main/java/com/starrocks/type/PrimitiveType.java:343-385`: INT/DECIMAL32/DATE 4, BIGINT/DECIMAL64/DOUBLE 8, DECIMAL128 16, CHAR/VARCHAR 16 |
| `getOutputSize` prices UNKNOWN columns by `getTypeSize()` | `FE/sql/optimizer/statistics/Statistics.java:88-106` (so the estimator is self-consistent with the cost model) |
| Proto reply has only status + schema | proto `:647-650` |
| CN already parses every footer | `CN/file_schema.rs:14-50` (`ParquetRecordBatchStreamBuilder::new(file).await`, `builder.metadata().file_metadata()`), `parquet_files_schema` `:62-99` reads every file (deliberately, `:59-61`); handler `CN/compute_node_service.rs:681-699`; attachment decode `:1683-1707`; parquet 59.2.0 (`experimental/starrocks/Cargo.lock:1648-1649`), `FileMetaData::num_rows()` |
| Patch delivery | `experimental/starrocks/patches/{files-query-whole-file-ranges,nixl-exchange-proto}.patch`; `experimental/starrocks/scripts/apply-starrocks-patches.sh` loops `patches/*.patch` in glob order, `git apply --check` then apply, `--reverse --check` = already applied, exit 1 otherwise; `pixi.toml:94` task, `fe-check`/`cn-build`/`cn-test` depend on it (`:96-104`, `:131-140`); `fe-build` at `pixi.toml:197-215` (`./build.sh --fe`, copies `conf/fe.conf`); `fe/fe-core/pom.xml:1125-1155` regenerates Java classes from `internal_service.proto` with jprotobuf at `generate-sources` |
| jprotobuf field naming | snake_case proto fields become camelCase public Java fields: `pResult.packetSeq` for `optional int64 packet_seq` (`FE/qe/ResultReceiver.java:116`, proto `:495`); `result.status.statusCode` (`TableFunctionTable.java:721`) |
| CI applies only the nixl patch by name | `.github/workflows/experimental.yml:52-57`; CI then runs `cargo fmt --check`, `cargo clippy --all-targets --no-default-features -D warnings`, `cargo test --workspace --no-default-features` (`:66-77`) |
| `build.rs` guard | `experimental/starrocks/build.rs:72-108` `require_exchange_proto_patch` matches `message PExchangeNixlMd`, prints the `git apply` remedy |
| CN refuses RIGHT_SEMI and MULTI_CAST by name | `experimental/starrocks/crates/starrocks-plan-translator/src/node_translator.rs:1743-1762` (no `RIGHT_SEMI_JOIN` arm; `_ => "hash join type is unsupported"`); `CN/compute_node_service.rs:1064-1070` (non-DATA_STREAM_SINK refused, test `:3565-3590`) |
| FE commutes semi/anti when cost says so | `FE/sql/optimizer/rule/transformation/JoinCommutativityRule.java:34-44` (LEFT_SEMI <-> RIGHT_SEMI, LEFT_ANTI <-> RIGHT_ANTI) |
| CTE inline is cost-driven once statistics exist | `FE/sql/optimizer/CTEContext.java:157-190` (`!enableCTE` -> inline); `cbo_cte_reuse` session variable (`FE/qe/SessionVariable.java:499, 1506`) |
| The survey "1 CN" FE had four CN ids registered | `survey/cluster.log:345-363` (ids 10001, 11001, ...); `cluster8.sh` neither wipes `output/fe/meta` nor drops nodes (grep: no `rm -rf`, no `DROP COMPUTE NODE`), CNs self-register with `ALTER SYSTEM ADD COMPUTE NODE` (`CN/lib.rs:210-212`); `SystemInfoService.isSingleBackendAndComputeNode` counts **registered** nodes (`FE/system/SystemInfoService.java:246-248`) |
| Dataset inputs (SF1000 decimal copy, `/scratch/sirius/datasets/tpch_sf1000`) | `du -sb` and `metadata.json` (surgical design appendix B): lineitem 60 files / 177,214,004,134 B / 5,999,989,709 rows; orders 15 / 52,374,826,801 / 1,500,000,000; partsupp 8 / 37,878,661,069 / 800,000,000; customer 2 / 10,761,809,562 / 150,000,000; part 2 / 5,084,217,527 / 200,000,000; supplier 1 / 686,828,646 / 10,000,000; nation 1 / 2,250 / 25; region 1 / 1,103 / 5 |

## 3. Mechanism

### 3.1 PR-A, FE: `TableFunctionTable` owns the estimate

`FE/catalog/TableFunctionTable.java`. Add after the `fileStatuses` field (`:188`):

```java
// Row total the compute node read from the parquet footers with the schema (PGetFileSchemaResult.num_rows);
// -1 when it reported none. Set only by getFileSchema(); PR-A never sets it.
private long footerRowCount = -1;
```

Add public accessors (place after `loadFileList()` `:300-302`):

```java
/** Bytes of the listed files (directories excluded); the numerator of the row estimate. */
public long getTotalFileBytes() {
    long bytes = 0;
    for (TBrokerFileStatus f : fileStatuses) {
        if (!f.isDir) {
            bytes += Math.max(0L, f.size);
        }
    }
    return bytes;
}

/** The inferred file schema without the trailing columns_from_path entries, which occupy no file bytes. */
public List<Column> getFileColumns() {
    List<Column> all = getFullSchema();
    int fileColumns = all.size() - columnsFromPath.size();
    return all.subList(0, Math.max(0, fileColumns));
}

/** True when estimateRowCount() returns a footer total rather than the bytes / width estimate. */
public boolean hasExactRowCount() {
    return footerRowCount > 0;
}

/**
 * Row count for the CBO. The footer total the compute node returned with the schema when present, else
 * HiveStatisticsProvider.getEstimatedRowCount's rule (total bytes / sum of type widths), else 1. Each width is
 * floored at 1 so a zero-width type cannot zero the denominator (the one deviation from the Hive rule).
 */
public long estimateRowCount() {
    if (!Config.files_scan_estimate_row_count || listFilesOnly || fileStatuses.isEmpty()) {
        return 1;
    }
    if (footerRowCount > 0) {
        return footerRowCount;
    }
    long bytes = getTotalFileBytes();
    long width = 0;
    for (Column c : getFileColumns()) {
        width += Math.max(1, c.getType().getTypeSize());
    }
    if (bytes <= 0 || width <= 0) {
        return 1;
    }
    return Math.max(1L, bytes / width);
}
```

`listFilesOnly` is the existing field behind `isListFilesOnly()` (`:432-434`); `LIST_FILES` tables get their own
one-row-per-entry schema (`setSchemaForListFiles` `:278-280`) and must keep 1. UNLOAD tables
(`TableFunctionTable(List<Column>, Map, SessionVariable)` `:245`) never populate `fileStatuses` and return 1.

One INFO line per statement at the end of `setSchemaForLoadAndQuery()` (`:255-275`, after `setNewFullSchema`):

```java
LOG.info("FILES() statistics path={} files={} bytes={} rows={} source={}",
        path, fileStatuses.size(), getTotalFileBytes(), estimateRowCount(),
        hasExactRowCount() ? "footer" : "estimated");
```

(`LOG` already exists in the class, cf. `:617`.) This line is how the SF1000 arm proves which path each table took.

### 3.2 PR-A, FE: `StatisticsCalculator` takes the table

`FE/sql/optimizer/statistics/StatisticsCalculator.java`:

```java
@Override
public Void visitLogicalTableFunctionTableScan(LogicalTableFunctionTableScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

// New. Mirrors the visitLogicalFileScan / visitPhysicalFileScan pair at :655-662; today the physical FILES()
// operator falls through to OperatorVisitor.visitOperator (:454-456). Defensive: nothing re-derives it on the
// main path, but a future physical re-derivation must not silently return to 1 row.
@Override
public Void visitPhysicalTableFunctionTableScan(PhysicalTableFunctionTableScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

@Override
public Void visitLogicalFileScan(LogicalFileScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

@Override
public Void visitPhysicalFileScan(PhysicalFileScanOperator node, ExpressionContext context) {
    return computeFileScanNode(node, context, node.getTable(), node.getColRefToColumnMetaMap());
}

private Void computeFileScanNode(Operator node, ExpressionContext context, Table table,
                                 Map<ColumnRefOperator, Column> columnRefOperatorColumnMap) {
    Statistics.Builder builder = Statistics.builder();
    for (ColumnRefOperator columnRefOperator : columnRefOperatorColumnMap.keySet()) {
        builder.addColumnStatistic(columnRefOperator, ColumnStatistic.unknown());
    }
    // Column statistics stay unknown (no footer min/max/NDV). A FILES() scan gets the row total the compute node
    // read from the parquet footers, else total bytes / row width, else 1. The external FILE table keeps 1.
    long rows = 1;
    boolean exact = false;
    if (table instanceof TableFunctionTable) {
        TableFunctionTable files = (TableFunctionTable) table;
        rows = files.estimateRowCount();
        exact = files.hasExactRowCount();
    }
    builder.setOutputRowCount(rows);
    // Honest only on the bytes / width path. Consumers of the flag (DeriveStatsTask:72-77 MV only, DataSkew:161/194,
    // StatisticsEstimateUtils:117, CostModel:371) are already short-circuited by the unknown column statistics.
    builder.setTableRowCountMayInaccurate(rows != 1 && !exact);
    context.setStatistics(builder.build());
    return visitOperator(node, context);
}
```

`LogicalScanOperator.getTable()` (`FE/sql/optimizer/operator/logical/LogicalScanOperator.java:110`) and
`PhysicalScanOperator.getTable()` (`.../physical/PhysicalScanOperator.java:144`) exist. Imports to add:
`com.starrocks.catalog.TableFunctionTable`, `com.starrocks.sql.optimizer.operator.physical.PhysicalTableFunctionTableScanOperator`
(the logical import is already at `:100`).

### 3.3 PR-A, FE: one mutable Config knob

`FE/common/Config.java`, placed next to `enable_statistic_collect` (`:2276`), **not** near `:1212-1223` where
`files-query-whole-file-ranges.patch` adds its hunk, so the two patches apply in either glob order:

```java
/**
 * When true, FILES() query scans are planned with a real row-count estimate: the parquet footer total the compute
 * node returns with the file schema, else total file bytes divided by the typed row width of the inferred schema
 * (the Hive no-statistics rule). Column statistics stay unknown. Upstream plans every FILES() scan at 1 row, which
 * makes every join side, broadcast and CTE decision data-blind. Mutable so EXPLAIN COSTS can compare both plans on
 * one running FE: ADMIN SET FRONTEND CONFIG ("files_scan_estimate_row_count" = "false").
 */
@ConfField(mutable = true)
public static boolean files_scan_estimate_row_count = true;
```

Default value is a user decision (section 11, item 1); the code shape does not change either way.

### 3.4 PR-B, wire: one optional field

New patch file `experimental/starrocks/patches/files-schema-row-count-proto.patch`, hunk at proto `:647-650`
(does not overlap the nixl patch's hunk at `:786+`):

```proto
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
    // Sirius extension: exact row total over every file in the request, summed from the parquet footers the node
    // opened for the schema. Exact-or-absent: a node that samples files (schema_sample_file_count) must not set it.
    optional int64 num_rows = 3;
};
```

proto2 `optional` on an existing message is wire-compatible both ways: a stock FE ignores field 3, a stock BE never
sets it and the FE falls back to 3.1.

### 3.5 PR-B, CN: sum the footers already in hand

`CN/file_schema.rs`:

```rust
/// Schema and row total of one FILES() file set, from the footers `parquet_files_schema` already reads.
pub(crate) struct FilesSchema {
    pub(crate) slots: Vec<PSlotDescriptor>,
    /// Sum of `FileMetaData::num_rows` over every file in request order. Exact because every footer is read
    /// (the whole-set agreement contract at the top of `parquet_files_schema`); a future sampling mode must
    /// report `None` upstream instead of a partial sum.
    pub(crate) num_rows: i64,
}

pub(crate) async fn parquet_file_schema(path: &str) -> Result<FilesSchema, String>   // was Vec<PSlotDescriptor>
pub(crate) async fn parquet_files_schema(paths: &[String]) -> Result<FilesSchema, String>
```

`parquet_file_schema` reads `builder.metadata().file_metadata().num_rows()` next to the existing
`schema_descr()` call (`:24-25`). `parquet_files_schema` sums `num_rows` inside its existing per-file loop
(`:66-95`); the name/type agreement checks compare `.slots`. Existing tests index `.slots`.

`CN/compute_node_service.rs`:

- `file_schema_from_attachment` (`:1683-1707`) returns `FilesSchema` (only the return type changes).
- handler `get_file_schema` (`:681-699`):

```rust
#[instrument(skip_all, fields(files, rows))]
async fn get_file_schema(&self, _request: PGetFileSchemaRequest, attachment: Vec<u8>)
    -> Result<crate::prpc::Reply<PGetFileSchemaResult>, crate::prpc::Error>
{
    let started = std::time::Instant::now();
    let result = match Self::file_schema_from_attachment(&attachment).await {
        Ok(FilesSchema { slots, num_rows }) => {
            tracing::Span::current().record("files", slots.len()).record("rows", num_rows);
            tracing::info!(rows = num_rows, elapsed_ms = started.elapsed().as_millis() as u64,
                           "get_file_schema: schema and footer row total");
            PGetFileSchemaResult { status: Self::ok_status(), schema: slots, num_rows: Some(num_rows) }
        }
        Err(err) => PGetFileSchemaResult {
            status: Self::internal_error(err),
            schema: Vec::new(),
            num_rows: None,
        },
    };
    Ok(result.into())
}
```

(`files` should be the number of ranges, not slots; record `paths.len()` from inside `file_schema_from_attachment`
or return it in `FilesSchema` as `files: usize`. Either is fine; keep the span field.)

`CN/wire_type_parity.rs` does not enumerate `PGetFileSchemaResult` (grep), so no change there.

### 3.6 PR-B, `build.rs` guard and CI

`experimental/starrocks/build.rs:72-108`: generalise `require_exchange_proto_patch` into
`require_proto_patch(manifest_dir, proto_dir, contents_predicate, patch_relpath, what)` and call it twice from
`main()` (`:37`):

1. `contents.contains("message PExchangeNixlMd")` -> `patches/nixl-exchange-proto.patch` (unchanged behaviour).
2. the `message PGetFileSchemaResult { ... };` slice contains `num_rows` -> `patches/files-schema-row-count-proto.patch`.
   Scope the check to the message body (find `message PGetFileSchemaResult`, take up to the next `};`) because
   `num_rows` appears in other messages.

Error text stays one line (cargo prints build-script errors with `Debug`): `"the starrocks submodule is missing
patches/files-schema-row-count-proto.patch (see above)"`, with the `git apply` remedy on stderr as today.

`.github/workflows/experimental.yml:52-57`: replace the by-name step with

```yaml
      - name: Patch starrocks submodule
        working-directory: ${{ github.workspace }}
        run: bash experimental/starrocks/scripts/apply-starrocks-patches.sh
```

The script is idempotent (`--reverse --check` = already applied) and applies the FE Java patches too, which is
harmless for the CN build. Without this step PR-B's CN build fails in CI on `no field num_rows`.

### 3.7 PR-B, FE consumption (one line, regenerates the PR-A patch)

`TableFunctionTable.getFileSchema()` after the status check (`:721-723`):

```java
// Optional Sirius extension; a stock BE leaves it unset. Treat <= 0 as absent so the result is the same whether
// jprotobuf generated a boxed Long (null when absent) or a primitive long (0 when absent).
if (result.numRows != null && result.numRows > 0) {
    footerRowCount = result.numRows;
}
```

Confirm the generated type in `fe/fe-core/target/generated-sources/proto/com/starrocks/proto/PGetFileSchemaResult.java`
after the first `generate-sources`; if it is primitive `long`, drop the `!= null` (the `> 0` guard is what matters).

### 3.8 Bench harness and docs (PR-A)

- `experimental/starrocks/benchmarks/tpch/bench.sh` (after `MYSQL=` at `:85`) and `run-abc.sh` (next to
  `set_and_verify_pipeline` `:797-806`): `SET GLOBAL cbo_cte_reuse = false;` then read back with
  `SHOW VARIABLES LIKE 'cbo_cte_reuse'` and abort on mismatch. Reason: with real costs CTE reuse can win
  (`CostModel.visitPhysicalCTEAnchor`; `CTEContext.needInline:157-190` defers to the memo when `enableCTE`), the FE
  then emits `MULTI_CAST_DATA_STREAM_SINK`, which the CN refuses by name (`compute_node_service.rs:1064-1070`).
  `cbo_cte_reuse=false` reproduces today's inline plans exactly (q15/q11/q17/q18/q21/q02 consume a CTE 2-3 times).
  Also add the knob read-back: `ADMIN SHOW FRONTEND CONFIG LIKE 'files_scan_estimate_row_count'` logged once per run.
- `experimental/starrocks/docs/TUNABLES.md`: new section after "Engine-side":

```
## Front end (patched StarRocks)

FE `Config` values from `patches/*.patch`; set at runtime with `ADMIN SET FRONTEND CONFIG ("k" = "v")`.

| Knob | Role |
|---|---|
| `files_query_whole_file_ranges` | Whole-file scan assignment for FILES() queries so pinned tables can serve them (benchmarks/pinned/README.md). |
| `files_scan_estimate_row_count` | Plan FILES() scans with the parquet footer row total (or bytes / row width) instead of 1 row. `false` reproduces upstream's data-blind plans for an EXPLAIN COSTS A/B. |

Session variables that start to matter once FILES() has cardinalities: `cbo_cte_reuse` (keep `false` until the CN
implements MULTI_CAST_DATA_STREAM_SINK), `broadcast_row_limit` (15M rows, the broadcast guard), `exec_mem_limit`
(2 GiB; a broadcast build above it costs x1000). The bench sets `cbo_cte_reuse=false` at bring-up.
```

- `experimental/starrocks/DEMO.md:173` (patch list): add the new patch name(s) and the `ADMIN SET FRONTEND CONFIG`
  off switch.

### 3.9 What does not change

`src/` (engine), `src/legacy/`, `crates/starrocks-plan-translator`, `FileScanNode.java`, the deploy path, the
result/cancel paths on the CN. The RPC whose reply grows by one varint already has an unbounded wait
(`TableFunctionTable.java:713`); this spec does not touch it (pre-existing hardening item, out of scope).

## 4. Files to touch

| PR | file | change |
|---|---|---|
| A | `FE/catalog/TableFunctionTable.java` | field `footerRowCount`; `getTotalFileBytes()`, `getFileColumns()`, `hasExactRowCount()`, `estimateRowCount()`; INFO line (~45 lines) |
| A | `FE/sql/optimizer/statistics/StatisticsCalculator.java` `:374-376, 655-676` | table parameter; `visitPhysicalTableFunctionTableScan`; 2 imports (~20 lines changed) |
| A | `FE/common/Config.java` (near `:2276`) | `files_scan_estimate_row_count` |
| A | `FET/catalog/TableFunctionTableTest.java` | tests 5.1.1-5.1.2 |
| A | `FET/sql/optimizer/statistics/StatisticsCalculatorTest.java` | test 5.1.4 |
| A | `FET/sql/plan/FilesScanStatisticsTest.java` (new) | tests 5.1.5-5.1.6 |
| A | `experimental/starrocks/patches/files-scan-row-count.patch` (new) | `git diff` of the five FE files above (generation procedure in 6.1) |
| A | `experimental/starrocks/benchmarks/tpch/bench.sh`, `run-abc.sh` | `SET GLOBAL cbo_cte_reuse = false` with read-back; knob read-back |
| A | `experimental/starrocks/docs/TUNABLES.md`, `DEMO.md:173` | section 3.8 |
| B | `experimental/starrocks/patches/files-schema-row-count-proto.patch` (new) | proto `:647-650`, +3 lines |
| B | `CN/file_schema.rs` `:14-99` + tests | `FilesSchema`, `num_rows`; fixture helper 5.2 |
| B | `CN/compute_node_service.rs` `:681-699, 1683-1707` + tests `:3624-3640` | fill the field; span fields; INFO line |
| B | `experimental/starrocks/build.rs` `:37, 72-108` | second guard |
| B | `.github/workflows/experimental.yml` `:52-57` | apply all patches |
| B | `experimental/starrocks/patches/files-scan-row-count.patch` (regenerated) | the one-line consumption in `getFileSchema()` (3.7) and test 5.1.3 |

## 5. Tests to write first

No Catch2 tests: nothing under `src/` changes, so `test/cpp/` is untouched (say so in the PR).

### 5.1 FE JUnit (`FET/...`, run with `run-fe-ut.sh --test <class>`, section 6.2)

1. `TableFunctionTableTest.testEstimateRowCountFromFileBytes` (PR-A). `new TableFunctionTable(newProperties())`
   (the fixture at `:55-68`: `fake://` path, `columns_from_path = col_path1, col_path2, col_path3`). Assert
   `getTotalFileBytes() == 3072`, `getFileColumns().size() == 2` (INT + VARCHAR, path columns excluded),
   `estimateRowCount() == 153` (3072 / (4 + 16); with the three 16 B path columns wrongly included it would be
   3072 / 68 = 45, which is the regression this test pins), `hasExactRowCount() == false`. Then set
   `Config.files_scan_estimate_row_count = false` in a try/finally and assert `estimateRowCount() == 1`.
2. `TableFunctionTableTest.testEstimateRowCountListFilesOnly` (PR-A). Properties with `list_files_only = true`
   (see `setSchemaForListFiles` `:278`, `isListFilesOnly` `:432`) -> `estimateRowCount() == 1` even with the knob on.
3. `TableFunctionTableTest.testFooterRowCountPreferred` (PR-B). Path `hdfs://127.0.0.1:9000/dir/*`, `format =
   parquet`; `new MockUp<HdfsUtil>() { @Mock public static List<FileStatus> listFileMeta(String, Map<String,String>,
   boolean) }` returning two `FileStatus` of length 1024 and 2048 (idiom at `:218-224`); `MockUp<RunMode>` returning
   `SHARED_DATA` and the `SystemInfoService` `Expectations` as in `testGetFileSchema` (`:129-165`) so one compute node
   is chosen; `new MockUp<BackendServiceClient>() { @Mock public Future<PGetFileSchemaResult> getFileSchema(
   TNetworkAddress, PGetFileSchemaRequest) }` returning `CompletableFuture.completedFuture(result)` where
   `result.status.statusCode = TStatusCode.OK.getValue()`, `result.schema = [intSlot("col_int")]` (a helper building
   `PSlotDescriptor` -> `PTypeDesc` -> one `PTypeNode` of `TTypeNodeType.SCALAR` with `PScalarType.type =
   TPrimitiveType.INT.getValue()`, which `TypeDeserializer.fromProtobuf` accepts). Cases: `numRows = 1000` ->
   `estimateRowCount() == 1000`, `hasExactRowCount() == true`; `numRows = null` (or `0`) -> `768` (3072 / 4),
   `hasExactRowCount() == false`; `numRows = -5` -> `768`.
4. `StatisticsCalculatorTest.testTableFunctionTableScanRowCount` (PR-A). Model on `testLogicalOlapTableScan`
   (`:245-292`): `TableFunctionTable table = new TableFunctionTable(props)` with `path = fake://a/*`, `format =
   parquet` (no `columns_from_path`); one `ColumnRefOperator` per `getFullSchema()` column; `new
   LogicalTableFunctionTableScanOperator(table, refToColumn, columnToRef, -1, null)` (ctor `:32-45`);
   `GroupExpression` + `Group(0)` + `ExpressionContext`; `new StatisticsCalculator(ctx, columnRefFactory,
   optimizerContext).estimatorStats()`. Assert `getOutputRowCount() == 153`, every `getColumnStatistic(ref).isUnknown()`,
   `isTableRowCountMayInaccurate() == true`, `getComputeSize() == 153 * 20`. Repeat with limit `100` -> `100`
   (proves `visitOperator` still runs). Repeat with `new PhysicalTableFunctionTableScanOperator(table, refToColumn,
   new ScanOperatorPredicates(), -1, null, null)` (ctor `.../physical/PhysicalTableFunctionTableScanOperator.java:35-43`)
   -> `153`. Repeat with `Config.files_scan_estimate_row_count = false` -> `1` and flag `false`.
5. `FilesScanStatisticsTest extends PlanTestBase`, `testFilesScanCardinalityInExplainCosts` (PR-A).
   `getCostExplain("select a.col_int from files('path'='fake://a/*','format'='parquet') a join
   files('path'='fake://b/*','format'='parquet') b on a.col_int = b.col_int")` (fake FILES() statements are already
   planned in UTs: `FET/load/pipe/PipeManagerTest.java:203, 239, 254`, `FET/sql/StatementPlannerTest.java`). Assert
   the plan contains `cardinality: 153` at least twice (count occurrences) and not `cardinality: 1\n` on a
   FileScanNode. `getCostExplain` is at `FET/sql/plan/PlanTestNoneDBBase.java:311`.
6. `FilesScanStatisticsTest.testInjectedRowCountsDecideJoinSide` (PR-A; the judges' "stronger than a
   contains-assert" test). `new MockUp<TableFunctionTable>() { @Mock public long estimateRowCount(Invocation inv) {
   TableFunctionTable t = inv.getInvokedInstance(); return t.getPath().contains("big") ? 1_000_000_000L : 10L; } }`.
   Plan `select count(*) from files('path'='fake://big/*',...) b join files('path'='fake://small/*',...) s on
   b.col_int = s.col_int` and assert `INNER JOIN (BROADCAST)` with the `small` FileScanNode (cardinality 10) under
   the EXCHANGE on the build side (the min cluster has one backend: `UtFrameUtils.createMinStarRocksCluster()`).
   Second statement, documenting the translator dependency: `select count(*) from small s where s.col_int in
   (select col_int from big)` -> expect the FE to commute to `RIGHT SEMI JOIN` with `small` as build; if it does,
   assert it and reference plan item 5 in the test comment; if it does not on first run, record the plan text and
   drop the assertion (the point is to know, not to force).

### 5.2 CN Rust (`#[cfg(test)]` in the crate; `pixi run cn-test-no-engine`)

Fixture helper in `CN/file_schema.rs` `test_support` (next to `write_parquet` `:261-273`, which writes schema-only
files with zero row groups):

```rust
/// Writes `rows_per_group.len()` row groups of one `required int64 v` column, `rows_per_group[i]` values each.
pub(crate) fn write_parquet_rows(tag: &str, rows_per_group: &[usize]) -> std::path::PathBuf
```

(`SerializedFileWriter::next_row_group()`, `next_column()`, `ColumnWriter::Int64ColumnWriter(w).write_batch(..)`.)

1. `file_schema::tests::schema_only_file_reports_zero_rows`: `parquet_file_schema(write_parquet(..))` ->
   `num_rows == 0`, `slots` as before.
2. `file_schema::tests::multi_file_num_rows_sums_footers`: files with row groups `[3, 5]` and `[7]` ->
   `parquet_files_schema(&[a, b]).num_rows == 15`; a schema mismatch still fails closed (existing tests keep passing
   with `.slots`).
3. `compute_node_service::tests::get_file_schema_attachment_reports_num_rows`: extend
   `get_file_schema_attachment_infers_across_multiple_ranges` (`:3624-3640`) to use rows-bearing fixtures and assert
   `schema.num_rows == <sum>`; add a routed variant through `get_file_schema` (the `route(&service, methods::...)`
   harness used at `:3576`; if `GET_FILE_SCHEMA` is not routed there, call the handler directly) decoding
   `PGetFileSchemaResult` with `num_rows == Some(sum)`; error path (a range with `format_type != FORMAT_PARQUET`)
   -> `status` INTERNAL_ERROR and `num_rows == None`.

### 5.3 Lint gates that count as tests

`cargo fmt --check`, `cargo clippy --all-targets --no-default-features -- -D warnings` (what CI runs, `experimental.yml:66-77`),
`pixi run pre-commit run -a` at the repo root.

## 6. Build and test commands (this repository)

All from `/home/prestouser/aocsa/sirius-stacks-wt/fix-files-cardinality` unless stated. Fix 3 has no GPU assignment
(plan: "fix 3: none"); nothing here needs a GPU until the gate.

### 6.1 FE patch loop

Edit inside the submodule (`experimental/starrocks/starrocks`; `.gitmodules` has `ignore = dirty`, never `git add`
the submodule pointer). Generating the patch when the tree already carries the two existing patches:

```bash
cd experimental/starrocks/starrocks
git add -A && git commit -q -m "tmp: existing sirius patches"      # local only, never pushed
# ... edit the five FE files, run the FE unit tests ...
git diff -- fe/fe-core/src/main/java/com/starrocks/common/Config.java \
            fe/fe-core/src/main/java/com/starrocks/catalog/TableFunctionTable.java \
            fe/fe-core/src/main/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculator.java \
            fe/fe-core/src/test/java/com/starrocks/catalog/TableFunctionTableTest.java \
            fe/fe-core/src/test/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculatorTest.java \
            fe/fe-core/src/test/java/com/starrocks/sql/plan/FilesScanStatisticsTest.java \
            > ../patches/files-scan-row-count.patch
git reset -q --soft HEAD~1 && git reset -q                           # back to "patches applied, uncommitted"
# Prove the set applies from clean in glob order and from a tree that already has it:
git checkout -- . && bash ../scripts/apply-starrocks-patches.sh && bash ../scripts/apply-starrocks-patches.sh
```

Glob order is `files-query-whole-file-ranges`, `files-scan-row-count`, `files-schema-row-count-proto`,
`nixl-exchange-proto`; the `Config.java` hunks are ~1000 lines apart, so `git apply` tolerates the offset.

FE unit tests (upstream runner; the `fe` pixi feature ships JDK 17 and Maven >= 3.9, `pixi.toml:170-185`):

```bash
cd experimental/starrocks
pixi run -e fe bash -lc 'export STARROCKS_THIRDPARTY="$PIXI_PROJECT_ROOT/.pixi/starrocks-fe-thirdparty"; \
  cd starrocks && ./run-fe-ut.sh --test com.starrocks.catalog.TableFunctionTableTest'
pixi run -e fe bash -lc '... ./run-fe-ut.sh --test com.starrocks.sql.optimizer.statistics.StatisticsCalculatorTest'
pixi run -e fe bash -lc '... ./run-fe-ut.sh --test com.starrocks.sql.plan.FilesScanStatisticsTest'
```

(`run-fe-ut.sh` usage at `experimental/starrocks/starrocks/run-fe-ut.sh:29-40`; `fe-build` sets the same
`STARROCKS_THIRDPARTY` and touches a placeholder `libLLVMInstCombine.a`, `pixi.toml:209-214`; do the same if the
runner's `env.sh` asks for it.)

FE package (the wall-clock item; start it as soon as the unit tests pass; `pixi.toml:91` calls it multi-hour,
`~/.m2` is warm at 2.0 GB):

```bash
cd experimental/starrocks && pixi run -e fe fe-build          # produces starrocks/output/fe, copies conf/fe.conf
```

### 6.2 CN loop (PR-B)

```bash
cd experimental/starrocks
pixi run apply-starrocks-patches                              # pixi.toml:94
pixi run cn-test-no-engine                                    # cargo test -p sirius-starrocks-cn --no-default-features (CI path)
pixi run -e cn cargo fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check
pixi run -e cn cargo clippy --all-targets --no-default-features -- -D warnings
pixi run cn-build                                             # engine-linked release CN for the gate (depends on engine-build = `pixi run make` at the repo root; only when deploying)
```

Repo root: `pixi run pre-commit run -a`. `pixi run make` / `pixi run make test` are unaffected by this fix (no
`src/` change) and are only needed to link the CN for the gate.

## 7. Acceptance

### 7.1 Gate A: EXPLAIN COSTS survey (minutes, no query execution, reviewer-rerunnable)

Setup: FE from `fe-build`, one CN with `SIRIUS_CN_TRANSLATE_ONLY=1` (fragments translated and dumped, never
executed; `docs/TUNABLES.md` Debug section) so the schema RPC is answered. Reuse the loop of
`scratchpad/perf/sf1000/survey.sh:16-19` (`EXPLAIN COSTS` + `EXPLAIN VERBOSE` per query, `explain_summary.py`
over the results; extend `explain_summary.py` with a per-FileScanNode cardinality list in plan order).

**Clean-cluster precondition (measured cause of a wrong survey).** Before capturing, `SHOW COMPUTE NODES` must
return exactly the CNs of the arm (1 or 4). Stale ids persist in `starrocks/output/fe/meta` across restarts (the
survey had 10001/11001/12002/12003 registered, `survey/cluster.log:345-363`); either wipe `output/fe/meta` before
`start_fe.sh` or `ALTER SYSTEM DROP COMPUTE NODE "127.0.0.1:<hb_port>"` for each stale row. This matters because
`isSingleBackendAndComputeNode` counts registered nodes and zeroes the single-node shuffle network cost
(`CostModel.java:432-441`), so a dirty "1-CN" FE prices shuffles higher than a clean one.

Procedure, per arm (1 CN, then 4 CNs), on the same FE:

1. `ADMIN SET FRONTEND CONFIG ("files_scan_estimate_row_count" = "false")`; capture q01-q22. **Must equal**
   `survey/explain/` (1 CN) / `survey/explain4/` (4 CN) modulo the clean-cluster difference: this proves the
   rollback path.
2. `... = "true"`; `SET GLOBAL cbo_cte_reuse = false`; capture again.
3. Optional no-code arm (recommended by judge 2, cheap, answers fix 4(d) by measurement): repeat 2 with
   `SET GLOBAL exec_mem_limit = 68719476736; SET GLOBAL enable_local_shuffle_agg = false; SET GLOBAL
   broadcast_row_limit = 2000000000;` and record the diff. Reset after. Adopting any of these is a user decision.

Pass criteria for step 2 (numbers that must come out):

- `card_all_one=False` for all 22.
- Leaf `FileScanNode` cardinalities are **post-predicate**: `footer_rows x product(selectivity)` with the UNKNOWN-column
  coefficients below (source-verified): one-sided range `<`, `>`, `>=`, `<=` on a column = 0.5 each
  (`StatisticRangeValues.overlapPercentWith`, infinite intersect -> `OVERLAP_INFINITE_RANGE_FILTER_COEFFICIENT`);
  `col = numeric` = 0.5; `col = 'string'` = **1.0** (`StatisticRangeValues.java:84-89`, both NDV 1, infinite
  intersect; `StatisticUtils.convertStatisticsToDouble` is empty for CHAR/VARCHAR `:457-459`); `IN (...)` = 0.5;
  `colA < colB` = **0.5** (`BinaryPredicateStatisticCalculator.java:369-374`); anything else 0.25
  (`PREDICATE_UNKNOWN_FILTER_COEFFICIENT`). Join with an unknown key = `max(L, R)`, x`0.25^k` when the sides differ
  by >= 1e7 (`StatisticsCalculator.java:1219-1240`); anti join = `min(L, R)`.

  Exact path (PR-A + PR-B), predicate-free leaves print the footer count exactly:

  | table | footer rows (exact leaf) | PR-A fallback leaf (this dataset copy only) |
  |---|---:|---:|
  | lineitem | 5,999,989,709 | 1,230,652,806 |
  | orders | 1,500,000,000 | 569,291,595 |
  | partsupp | 800,000,000 | 1,052,185,029 |
  | customer | 150,000,000 | 112,102,182 |
  | part | 200,000,000 | 45,394,799 |
  | supplier | 10,000,000 | 8,585,358 |
  | nation | 25 | 56 |
  | region | 5 | 30 |

  Predicated leaves for the four golden queries (exact path; PR-A alone scales by the fallback ratio):
  q03 customer (`c_mktsegment = 'BUILDING'`) 150,000,000; orders (`o_orderdate <`) 750,000,000; lineitem
  (`l_shipdate >`) ~2,999,994,855. q04 orders (range, two bounds) 375,000,000; lineitem (`l_commitdate <
  l_receiptdate`) ~2,999,994,855. q05 customer 150,000,000; orders 375,000,000; lineitem 5,999,989,709; supplier
  10,000,000; nation 25; region (`r_name = 'ASIA'`) 5. q22 orders 1,500,000,000; customer = footer x the product of
  its `substring(...) IN` and `c_acctbal >` coefficients (capture and record on first run). Curate the first clean
  capture into goldens under `experimental/starrocks/benchmarks/tpch/plans/` (plan-summary lines, not full text).
- Plan shape checks (from the surgical design's checklist, corrected): q02 join 31 and q11 join 5 broadcast
  region/nation (no longer shuffle 800M partsupp / 10M supplier rows to meet 1 row); q22 join 11 no longer
  broadcasts orders (RIGHT ANTI on filtered customers, or shuffle); q12 stays PARTITIONED at 4 CNs (the guard fires
  at 1.9e8 est. rows; the report's P3 hope does not hold, inference); q14 part build rejected by the guard -> PARTITIONED.
- The translate-only CN log has no `hash join type is unsupported` and no `MULTI_CAST_DATA_STREAM_SINK` refusal
  for the 15 passing queries. Any `RIGHT SEMI JOIN` in a plan is listed (q04/q18/q20/q21 candidates) as the plan
  item 5 dependency.
- Every 1-CN join that flipped BROADCAST -> PARTITIONED is listed with the bytes it will park (`actual rows x
  projected width` from `card-compare-cn1.txt`). Predicted (inference): q03 lineitem ~78 GB, q12 orders ~36 GB;
  q07/q10 at risk. This list is the input to the section 11 decision.

Elapsed: 2 knob states x 22 queries x 2 EXPLAINs plus one cluster start per arm; the original survey ran 22
queries in 55 s (`survey.log`).

### 7.2 Gate B: SF1000 arms (orchestrator-run, one arm at a time, after Gate A)

Same configuration as the campaign (`capture-cn.sh`, 100 GiB pool, `/scratch/sirius/datasets/tpch_sf1000`),
`cbo_cte_reuse=false` set and read back, the knob in the state the user chose for that arm (section 11 item 1),
fixes 1, 2 **and 4a** landed (section 9; INTEGRATION.md V5). Fix 4a is required for the knob-on 1-CN arm because
Gate A is predicted to flip q03/q12 (and possibly q07/q10) to hash-partitioned lineitem/orders leaves (risk 8.1),
which would park 78/36 GB without fusion and are fused with it. Note that `capture-cn.sh` -> `start-cluster.sh` ->
`cluster8.sh` does not run `bench.sh`, so the arm script must issue `SET GLOBAL cbo_cte_reuse = false`, the
`ADMIN SET FRONTEND CONFIG` knob and both read-backs itself. Also record per query the `fused sender fragment into
its local receiver` count and the `fragment run started` count: these become fix 4a's new goldens under the new
plans (fusion spec section 8). 1 CN, then 4 CNs, over the 15 passing queries plus the six, with
`run-queries.sh`; record plan shapes (`explain_summary.py`), per-exchange actual rows (`card_compare.py`), warm
medians against `results.md`, `[gpu_pool]` peaks from `engine-cn0.log`, nixl GB per run (`cn4-transmits.txt`
procedure), and the new FE INFO lines (`source=footer` for all eight tables with PR-B; `source=estimated` with PR-A
alone).

Pass:

- The 15 still pass with the same oracle status (`compare-*.txt` procedure, rel tol 1e-6): MATCH for q02 q04 q06 q12
  q13 q14 q20 q22; the known decimal-lowering diffs for q01 q03 q07 q10 q19 unchanged; q15 flaky as before (item 9,
  not this fix); q11 EMPTY on both engines.
- No translator refusal on the 15 (or each refusal recorded against plan item 5 / item 9).
- 4 CNs: nixl GB per run drops for q02 (43.6-45.7 today), q11 (0.40), q22 (18.56); q03 (60.34) and q12 (23.47)
  unchanged (inference; record whatever comes out).
- 1 CN: q03/q12 `[gpu_pool]` peak and drain time recorded; the six still fail (expected) with time-to-fail
  governed by fix 1.
- No wall-clock number is promised by this fix; the plan-shape record is the deliverable.

## 8. Risks and how the reviewer should probe them

1. **1-CN BROADCAST -> PARTITIONED flips park a large probe (inference, medium likelihood, high impact).** With real
   counts the guard (`EnforceAndCostTask.java:312-327`: reject when `leftBytes < rightBytes x beNum x 10 && rightRows
   > 15,000,000`) rejects q03's orders x customer broadcast (est. 7.5e8 rows) and the probe-penalty term
   (`HashJoinCostModel.java:134-156`: broadcast penalty up to 12 vs shuffle cap 3) prefers shuffling lineitem, so
   q03 would park ~78 GB and q12 ~36 GB in the 100 GiB pool at 1 CN (both stream today). Probe: Gate A step 2 at a
   *clean* 1 CN, read the printed sizes on the two children of q03 join 9 and q12 join 4 and check the guard
   inequality by hand; confirm the flip list. Mitigation: knob state per arm (section 11), fix 1 (seconds, not
   minutes), fix 4a (fusion makes 1-CN exchanges moot), fix 3b levers at the gate.
2. **FE commutes LEFT SEMI to RIGHT SEMI and the translator refuses it (high likelihood for q04, high impact).**
   `JoinCommutativityRule.java:34-44`; `node_translator.rs:1743-1762` has no arm. Probe: Gate A plan text for
   q04/q18/q20/q21; the `FilesScanStatisticsTest` second statement. Mitigation: plan item 5 lands before the knob
   is on in any sweep (section 9).
3. **CTE reuse wins and the CN refuses MULTI_CAST (high likelihood for q15/q11, high impact).** Probe: with
   `cbo_cte_reuse` at its default, `EXPLAIN` q15 with the knob on and look for `MultiCastDataSinks`; then with
   `false` confirm it is gone. `CTEContext.needInline:157-190` returns inline when `!enableCTE`. Mitigation: the
   harness sets and reads back `cbo_cte_reuse=false` (3.8).
4. **Fallback bias (PR-A alone): 0.2x-6x, inverts partsupp/orders and customer/part; lineitem under-counted 4.9x,
   which enters every `S_L` term.** Probe: diff Gate A plan summaries between PR-A and PR-A+B; every studied
   decision hinges on >= 10x ratios so no sign should change (inference); if one does, record it, that is a reason
   to make PR-B mandatory rather than optional. The golden depends on the dataset copy under PR-A alone (f64 copies
   compress differently); exactness under PR-B removes that.
5. **`columns_from_path` width.** `getFullSchema()` includes path STRING columns (`:268-275`); `getFileColumns()`
   excludes them. Probe: test 5.1.1 (153, not 45).
6. **jprotobuf field type for `optional int64`.** If generated as primitive `long`, absent = 0. Probe: read the
   generated `PGetFileSchemaResult.java`; the `> 0` guard (3.7) makes both cases behave the same. Test 5.1.3 covers
   null, 0 and negative.
7. **Mixed versions.** New FE + old CN (no PR-B): `numRows` absent -> fallback, `source=estimated` in the INFO
   line. Old FE + new CN: field ignored. Probe: run the PR-A FE against the `perf` worktree CN for one EXPLAIN.
8. **A future sampling CN (report item 7) sends a partial sum.** Contract in the proto comment and the `FilesSchema`
   doc: exact-or-absent. Probe: reviewer reads both comments; nothing else can enforce it today.
9. **Patch drift / apply order.** Probe: `git -C experimental/starrocks/starrocks checkout -- . && bash
   scripts/apply-starrocks-patches.sh` twice (second run prints `already applied` for all); `git apply --check
   --reverse` for each patch individually. CI: `experimental.yml` runs the script, and the `build.rs` guard names
   the missing patch if a checkout skips it.
10. **Load path shares the operator.** `INSERT ... SELECT FROM FILES()` gets the same estimate; its plan is scan ->
    sink with no join to re-decide. Probe: `EXPLAIN` one such INSERT with the knob on and off; only `cardinality:`
    text differs. Existing FE UTs that plan `fake://` FILES() assert structure, not cardinality lines (grep
    `cardinality:` in `PipeManagerTest`, `StatementPlannerTest`: none).
11. **`tableRowCountMayInaccurate=true` on the fallback path.** Consumers: `DeriveStatsTask.java:72-77` (MV
    rewrite only), `DataSkew.java:161/194`, `StatisticsEstimateUtils.java:117`, `CostModel.java:371`,
    `EnforceAndCostTask.java:411-422` (one-stage agg already blocked by unknown column stats). Probe: grep
    `isTableRowCountMayInaccurate` and confirm each is inert with unknown column statistics; Gate A step 1 vs 2
    should show no aggregation-stage change.
12. **Determinism and cancellation.** Statistics are a pure function of footers/file sizes on per-statement
    `TableFunctionTable` objects (created in `QueryAnalyzer.resolveTableFunctionTable`); `Config` is read per
    call; no shared mutable state, no cache, no new blocking (the RPC wait at `:713` is pre-existing). No CN
    execution, cancel or result path changes; the CN runs whatever fragment shape arrives and fails closed by name.
    Probe: two consecutive `EXPLAIN COSTS` of the same query are byte-identical.
13. **P4 fixed head.** Summing 60 `i64`s adds nothing measurable to the 28 ms lineitem footer pass. Probe: the new
    CN INFO line's `elapsed_ms` before/after PR-B.

## 9. Landing order and dependencies

| step | what | why here |
|---|---|---|
| 0 | Plan item 5: translator `TJoinOp::RIGHT_SEMI_JOIN => (JoinType::RightSemi, JoinOutput::Right)` (+ `JoinOutput::Right` arm), `node_translator.rs:1743-1762`; validate q04 (5 rows) and q22 (7 rows) against the oracle | risk 8.2; must be in the CN build before the knob is on in any sweep. Ownership is a user decision (section 11 item 2) |
| 1 | PR-A (this worktree): tests 5.1.1-5.1.2, 5.1.4-5.1.6, code 3.1-3.3, harness 3.8, docs; start `fe-build` as soon as unit tests pass | the FE rebuild is the wall-clock item all designs share |
| 2 | PR-B stacked on PR-A: tests 5.2, code 3.4-3.7, CI step, regenerated FE patch; `cn-test-no-engine`, clippy, fmt; second `fe-build` only if PR-A's build finished before 3.7 was added (otherwise one build covers both) | exact goldens; ~40 lines of Rust, zero extra I/O |
| 3 | Gate A (7.1) at clean 1 CN and 4 CNs, knob off/on, optional no-code arm | the only place the inference in 8.1-8.3 gets measured |
| 4 | Fix 1 (fail fast), fix 2 (parked-output bookkeeping) and fix 4a (fragment fusion) before the 1-CN SF1000 arm (INTEGRATION.md section 3 puts this fix last) | wrong shapes die in seconds; leaks (0-38 GB, B4) would confound the new plans' memory footprints; the predicted 1-CN BROADCAST -> PARTITIONED flips (q03 ~78 GB, q12 ~36 GB, risk 8.1) are hash-partitioned single-destination leaves that fix 4a's `leaf` mode fuses, so without fix 4a the knob-on 1-CN arm risks regressing q03 |
| 5 | Gate B (7.2) | |
| 6 | Fix 3b (cost levers = plan fix 4(d)) only if Gate A shows the q03/q12 flips and the no-code arm shows they can be prevented; scheduled next to fix 4a | judges: not part of issue 3; changes every distribution decision cluster-wide |

Interactions with the other three fixes: none in mechanism; three textual ones (INTEGRATION.md 2.5, 2.6, 4).
Fix 1 is `src/pipeline/gpu_pipeline_executor.cpp`; fix 2 is `engine.rs` / `local_exchange.rs` /
`compute_node_service.rs` cancel and dispatch paths; fix 4a is `compute_node_service.rs process_fragment` /
`local_exchange.rs` at the `TPlan` level. PR-B touches `compute_node_service.rs` only in `get_file_schema` /
`file_schema_from_attachment` and their tests (the `mod tests` block is also extended by fixes 2 and 4a: offsets
only). Textual collisions: (1) `docs/TUNABLES.md`: this spec's "Front end (patched StarRocks)" section and fix 4a's
"Dispatch" table both insert after "Engine-side" (`:37`); order is "Dispatch" first, then "Front end", before
"Debug" (`:49`); (2) `DEMO.md`: this spec edits `:173`, fixes 2 and 4a edit `:37-42`; (3) plan item 5 edits
`node_translator.rs:1743-1762` in the translator crate whose `lib.rs` fix 4a extends with `pub mod fusion;`
(different files, no conflict). Fix 3 Gate A's `SIRIUS_CN_TRANSLATE_ONLY=1` survey is unaffected by fix 2's gate 4
and fix 4a's hook, both placed after the translate-only early return (`process_fragment :927-934`). Fix 4a is complementary: at 1 CN fusion removes the exchange this fix might newly introduce for
q03/q12; this fix gives the FE sane join order across fragments and makes `broadcast_row_limit` / `exec_mem_limit`
meaningful at 4 CNs.

## 10. Commit messages (Conventional Commits; trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`)

- PR-A: `feat(starrocks-fe): plan FILES() scans with real row counts instead of 1`
  Body (plain, per the user's unslop rule): what was measured (1 row on 22/22 plans, 126 exchanges), the rule used
  (Hive bytes / width), the knob, what it does not do (the six at 1 CN), the two landing-order dependencies (item 5,
  `cbo_cte_reuse=false`), one line on how it was tested (three FE UT classes; EXPLAIN A/B on one FE).
- PR-B: `feat(starrocks-cn): return the parquet footer row total with the FILES() schema`
  Body: optional proto2 field, exact-or-absent contract, zero extra I/O, CI step generalised to
  `apply-starrocks-patches.sh`, `build.rs` guard, FE consumption line.
- Harness/docs ride in PR-A (`bench.sh`, `run-abc.sh`, `TUNABLES.md`, `DEMO.md`).

## 11. Decisions that need the user

1. **Default of `files_scan_estimate_row_count` in the shipped patch: `true` or `false`.** `true` (upstream and
   surgical designs) means every FE started from this build plans with cardinalities, including a 1-CN SF1000 arm
   where Gate A is predicted (inference) to flip q03/q12 to parking 78/36 GB. `false` means the bench bring-up turns
   it on explicitly per arm. Recommendation: `true` in code, and the 1-CN arm of Gate B runs with the knob in
   whichever state Gate A justifies; both judges insist Gate A precedes any 1-CN sweep.
2. **Who lands plan item 5 (translator RIGHT_SEMI).** It is a small translator change outside this fix's files
   but a hard prerequisite for default-on. Options: this worktree as a preliminary commit, or a separate branch.
3. **PR-B in this worktree now, or PR-A alone first.** PR-A alone is the surgical design (0.2x-6x estimates, goldens
   tied to the dataset copy); PR-B adds exactness for +3 proto lines, ~40 lines of Rust, a CI step and a second
   patch to maintain across submodule bumps. Recommendation: both, stacked, one `fe-build`.
4. **Whether Gate A includes the no-code session-variable arm** (`exec_mem_limit` 64 GiB,
   `enable_local_shuffle_agg=false`, `broadcast_row_limit` 2e9) and whether any of it becomes fix 3b. Zero code, ten
   minutes of EXPLAINs; it answers the plan's fix 4(d) by measurement before anyone writes the probe-penalty cap.
5. **Scope of `SET GLOBAL cbo_cte_reuse = false`.** It is global on the FE (affects every session, including
   demo runs); it reproduces today's inline plans, so it is not a behaviour change, but it should be stated in the
   demo docs.

## Appendix A. Per-query prediction checklist for diffing Gate A plan summaries (inference unless marked)

Baselines are measured (`survey/plan-summary.txt`, `plan-summary-4cn.txt`, `card-compare-cn1.txt`,
`card-compare-cn4.txt`, `cn4-transmits.txt`).

| query | today (measured) | expected with real counts | what to record |
|---|---|---|---|
| q02 | 1 CN: exch 28 shuffles 800M partsupp on `n_regionkey` to meet 1 region row; 4 CN: four 800M shuffles, 38.5 of 43.6-45.7 GB nixl | region/nation broadcast (`P_b = P_s = 1`); partsupp stays in its fragment | join 31 distribution; 4-CN nixl GB |
| q03 | 1 CN: orders x customer broadcast (2.33 GB parked), lineitem streamed; 4 CN: lineitem shuffled 58.2 GB | 4 CN unchanged; **1 CN flips to PARTITIONED** (guard + probe term), ~78 GB parked | join 9 distribution at 1 CN; bytes |
| q04 | `5:LEFT SEMI JOIN (BROADCAST) build=lineitem` 3.79e9 rows; 30.82 GB parked | commutation to RIGHT SEMI with orders as build becomes cost-relevant | presence of `RIGHT SEMI` (item 5) |
| q05 q08 q09 q17 q18 q21 (1 CN) | lineitem hash-partitioned, parked, OOM | unchanged shape | confirm; the six still fail until fix 4 |
| q07 | 1 CN: orders broadcast (18.38 GB parked) | customer/orders builds -> shuffle; lineitem chain may cross a shuffle (~50 GB) | 1-CN distributions of joins 13/17 |
| q10 | 1 CN: orders_f, lineitem_f broadcast | orders_f est 1.42e8 -> shuffle; customer x nation side (25.5 GB) may be hash-partitioned | record |
| q11 | 4 CN: 10M supplier shuffled on `s_nationkey` vs 1 nation row | nation broadcast | join 5 distribution |
| q12 | 1 CN: lineitem_f broadcast (0.51 GB), orders streamed; 4 CN: orders shuffled 23.09 GB | build est 1.9e8 > 15M guard -> **PARTITIONED at both** (1-CN regression: orders 36 GB parked) | join 4 at 1 CN |
| q14 | 1 CN: part broadcast (4 GB) | part est > 15M and `S_L < 10 S_B` -> PARTITIONED (+1.8 GB lineitem_f parked, neutral) | |
| q15 | two lineitem scans (CTE inlined) | unchanged with `cbo_cte_reuse=false`; MultiCast refused otherwise | absence of MultiCast |
| q19 | 1 CN: lineitem_f (128.6M rows) is the build | part_f becomes the build | |
| q20, q21 | LEFT SEMI with lineitem / aggregate as build | commutation candidates | `RIGHT SEMI` occurrences |
| q22 | `11:LEFT ANTI JOIN (BROADCAST) build=orders` 1.5e9 rows; 18.56 GB nixl at 4 CN | RIGHT ANTI with filtered customers as build (translator has `RIGHT_ANTI_JOIN`, `node_translator.rs:1750`), or shuffle (orders build 2.28e9 B > 2 GiB -> x1000) | join 11 op and sides; 4-CN nixl GB |
| q01 q06 q13 (rest) | join-free or shuffle-both | unchanged | |

## Appendix B. Why the six still shuffle at 1 CN (inference; the reason this fix is not fix 4)

Cost = `0.5 cpu + 2 mem + 1.5 net` (`CostModel.java:129-135`). BROADCAST exchange: `mem = S_B`, `net = S_B`,
x1000 when `S_B > exec_mem_limit` 2 GiB (`:419-429`; `aliveBackendNumber = 0` here because Sirius CNs register as
COMPUTE NODEs). SHUFFLE exchange: `cpu = S`, `net = S` or 0 on a single registered node with
`enable_local_shuffle_agg` (`:432-441`). Hash join (`HashJoinCostModel.java:86-156`): broadcast probe penalty
`clamp(ln(R_rows / 1e5), 1, 12)`, shuffle penalty `clamp(ln(R_rows / 1e5) - log2(2 pf), 1, 3)`. For q05's
lineitem join (L = 6e9 x 28 B, R = orders x customer ~3.75e8 x 12 B): BROADCAST ~718 even with the x1000 penalty
removed vs SHUFFLE ~350 (both judges re-derived this). Broadcast wins at 1 CN only for builds under ~5M rows
(nation, region, supplier, filtered part). Every one of the six joins lineitem to an orders- or partsupp-derived
side of 1e8-1e9 rows, so all six keep the P1 shape. The robust design's Part B (raise `exec_mem_limit`, count
single-node shuffle bytes, cap the broadcast probe penalty at 3) flips the sign; that is fix 4(d), gate-decided.
