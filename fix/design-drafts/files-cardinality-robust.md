# Fix 3, robust design: real FILES() statistics in the StarRocks FE

Angle: what a senior engineer ships for the long run. Written 2026-09-03 from the report
`starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md` (C1, P1, P3, P4, section 4),
the evidence bundle `scratchpad/perf/sf1000/`, and the source worktree
`/home/prestouser/aocsa/sirius-stacks-wt/perf` (FE = `experimental/starrocks/starrocks/fe/fe-core/src/main/java/com/starrocks`).
Every number is **measured** (named evidence file) or marked **inference**. Code excerpts relied on are in the appendix.

## 0. Summary

Today the FE plans every `FILES()` scan at 1 row (`StatisticsCalculator.java:664-676`), so every distribution
decision is arithmetic on column widths and node count (P3). This design has three parts:

- **Part A, statistics (the fix).** The CN already opens every parquet footer for the schema RPC
  (`file_schema.rs:14-27`, 60 footers for lineitem). Extend `PGetFileSchemaResult` with per-file
  `num_rows`, `file_size` and per-column `null_count`/`min`/`max` (proto patch + CN + FE). The FE stores them on
  the per-statement `TableFunctionTable` and `computeFileScanNode` emits the exact row count plus finite column
  ranges. Fallback when the CN sends nothing: rows = bytes / row width from `TBrokerFileStatus.size`
  (`TableFunctionTable.java:611`), flagged `tableRowCountMayInaccurate`. Column NDV is a separate, flag-gated mode.
- **Part B, GPU-aware cost inputs (required for the six 1-CN failures).** Hand-computation of the FE cost code
  (section 4) shows that with real statistics the stock model *still* shuffles lineitem at 1 CN: the single-node
  shuffle has zero network cost (`CostModel.java:437-440`), a broadcast whose build exceeds `exec_mem_limit`
  (2 GiB) is multiplied by 1000 (`CostModel.java:426-429`), and the broadcast probe carries a CPU-cache penalty
  of up to 12x vs 3x for shuffle (`HashJoinCostModel.java:141-156`). Three existing session variables
  (`exec_mem_limit`, `enable_local_shuffle_agg`, `broadcast_row_limit`) plus one flag-gated 4-line cost change
  (`enable_sirius_gpu_join_cost`: cap the broadcast probe penalty at the shuffle cap) make the FE choose
  "broadcast the small side, stream lineitem", which is the standalone plan that passes all six.
- **Part C, rollout.** Mutable FE config `files_query_statistics_mode = off | rows | rows_ranges | rows_ranges_ndv`
  (kill switch without restart), `SET GLOBAL cbo_cte_reuse = false` until the CN implements
  `MULTI_CAST_DATA_STREAM_SINK`, and translator `RIGHT_SEMI` (plan item 5) **before** enabling in the sweep,
  because real statistics make the FE commute LEFT SEMI joins (q04/q18/q20/q21) into a join type the translator
  refuses today.

Predicted (inference, section 7): q05 q08 q09 q17 q18 pass on 1 CN at roughly standalone time (2.8-5.4 s + ~0.2 s
head); q21 additionally needs item 5. At 4 CNs q03 moves ~7 GB instead of 58 GB, q12 ~1.5 GB instead of 23 GB,
q22 stops broadcasting 1.5B orders rows; q04 stops parking 30.8 GB of lineitem keys. Effort ~4 engineer-days plus
one multi-hour FE rebuild.

## 1. The problem, measured

- `card_all_one=True` for all 22 plans (survey/plan-summary.txt); actuals up to 6.0e9 rows per exchange
  (card-compare-cn1.txt, card-compare-cn4.txt). Root cause `StatisticsCalculator.computeFileScanNode`
  (`StatisticsCalculator.java:664-676`): `builder.setOutputRowCount(1)`, every column `ColumnStatistic.unknown()`.
- Consequences confirmed in source (C1): `checkBroadcastRowCountLimit` inert (needs right rows > 15M,
  `EnforceAndCostTask.java:324-325`), CTE inlining always wins, `JoinCommutativityRule` never commutes semi/anti,
  the fragment cut is decided by widths and node count (P3 mechanism, `HashJoinCostModel.java:115-127`).
- All six 1-CN failures are one shape: unfiltered lineitem leaf -> `HASH_PARTITIONED` sink -> parked in full
  (P1 table: 171-245 GB projections vs a 107 GB pool). Standalone Sirius with DuckDB's parquet statistics streams
  lineitem as the probe of a hash join built on the filtered dimension chain and passes all six
  (standalone-failed/runs/q05.explain.txt:50-61, `~6,110,827,680 rows` probe vs `~61,617,442 rows` build).
- Row counts already exist in two places: `TBrokerFileStatus.size` per file on the FE
  (`TableFunctionTable.java:606-613`) and the parquet footer the CN already parses
  (`file_schema.rs:19-27`, `ParquetRecordBatchStreamBuilder::metadata()`; `FileMetaData::num_rows`,
  `ColumnChunkMetaData::statistics()` in parquet-rs 59.2.0 `file/metadata/mod.rs:548,1072`).
- The schema RPC is per statement and per FILES() reference with no cache (P4: 8 RPCs per query, 73-114 ms);
  the lineitem call is busy 28 ms (cn1/cluster.log:923). Reading statistics off the same footers adds no I/O.

## 2. Design overview

```
FE analyzer                      CN (Rust)                         FE optimizer
QueryAnalyzer:2192 ---> get_file_schema RPC ----> file_schema.rs      StatisticsCalculator
 new TableFunctionTable           parquet footers (already read)      .computeFileScanNode
   .getFileSchema():688   <---- PGetFileSchemaResult                    rows = sum(num_rows)
   parses schema + NEW              schema[]                             ranges = min/max per column
   file_statistics[]      NEW:      file_statistics[] {path,num_rows,    nulls  = null_count/rows
   -> FilesStatistics                file_size, columns[]{col_name,      NDV    = mode-dependent
      (immutable, per stmt)          null_count,min,max,uncompressed}}  -> Statistics (exact) or
                                                                         bytes/width fallback (inaccurate)
```

Then the CBO does what it does for OLAP tables: real `getOutputSize`, real range selectivities, armed broadcast
guard, join commutation, CTE cost. Part B keeps its cost constants from steering a GPU cluster with CPU heuristics.

## 3. Mechanism

### 3.1 Wire: `gensrc/proto/internal_service.proto` (patched, Sirius-only)

```proto
// Sirius extension: per-file statistics read from parquet footers by the compute node.
message PFileColumnStatistics {
    optional string col_name = 1;
    optional int64  null_count = 2;         // summed over row groups; absent if any row group lacks it
    optional string min = 3;                // SQL literal per logical type (ints/decimals as decimal text,
    optional string max = 4;                //   DATE 'YYYY-MM-DD', DATETIME 'YYYY-MM-DD HH:MM:SS[.ffffff]');
                                            //   absent for strings/binary, INT96, NaN, deprecated/inexact stats
    optional int64  uncompressed_bytes = 5; // for average row size
    optional int64  distinct_count = 6;     // only when the writer recorded it (rare); FE ignores in phase A
}
message PFileStatistics {
    optional string path = 1;
    optional int64  num_rows = 2;
    optional int64  file_size = 3;
    repeated PFileColumnStatistics columns = 4;
}
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
    repeated PFileStatistics file_statistics = 3;   // Sirius extension; absent from stock BEs
};
```

Why per file, not totals: the FE merge is 20 lines, the payload is 60 files x 16 columns, and per-file rows are
what a later per-split pruning or a partial-footer failure needs. All fields optional: an old FE ignores field 3,
an old CN (or a stock BE) leaves it empty and the FE falls back (3.3).

The patch lives beside the two existing ones (`experimental/starrocks/patches/*.patch`,
`scripts/apply-starrocks-patches.sh` applies them in glob order and treats "already applied" as success). The
new hunk at `internal_service.proto:647-650` does not overlap the nixl patch's hunk at `:786`, so the two apply
independently. FE Java classes are regenerated from the proto at build time
(`fe/fe-core/pom.xml:1136-1149`, jprotobuf `--java_out`), so the FE must be rebuilt (`pixi run fe-build`).
The CN regenerates via prost in `build.rs:26-56`; extend `require_exchange_proto_patch` (`build.rs:70-105`) to
also check for `message PFileStatistics`, so a stale submodule fails with the remedy instead of a type error.

### 3.2 CN: `experimental/starrocks/src/file_schema.rs`, `compute_node_service.rs`

- `parquet_file_schema(path)` (`file_schema.rs:14-50`) already has the footer in hand. Return a
  `FileFooter { slots: Vec<PSlotDescriptor>, stats: PFileStatistics }`. Row count: `metadata.file_metadata().num_rows()`.
  Per column (top-level primitive fields only, same iteration as today): sum `ColumnChunkMetaData::num_values`
  and `statistics().null_count_opt()` over row groups; min/max: take `min_bytes_opt/max_bytes_opt` only when
  `!is_min_max_deprecated() && min_is_exact() && max_is_exact()` for every row group, decode by the column's
  physical+logical type (INT32/INT64 with DATE/DECIMAL/plain annotations, DOUBLE/FLOAT excluding NaN,
  INT64 TIMESTAMP) and render as the SQL literal the FE parses with `StatisticUtils.convertStatisticsToDouble`
  (`statistic/StatisticUtils.java:440-468`: DATE/DATETIME via the FE date parsers, everything else `Double.parseDouble`).
  Strings/binary/INT96 get no min/max (the FE treats string ranges as infinite anyway,
  `StatisticRangeValues.java:77-88`). `uncompressed_bytes` = sum of `uncompressed_size`.
- `parquet_files_schema(paths)` (`file_schema.rs:62-99`) keeps the fail-closed schema-agreement contract and
  additionally returns `Vec<PFileStatistics>` in request order. No new I/O: the footer was already parsed.
- `compute_node_service.rs::get_file_schema` (`:682-697`) fills `file_statistics`; `file_schema_from_attachment`
  (`:1680-1707`) passes the paths through unchanged. The `#[instrument]` span gains `files`, `rows`, `with_stats`
  fields and one `info!` line per request so cn logs show what the FE was told.
- Optional, same function, P4 synergy: a bounded-concurrency footer read (`buffer_unordered(8)`) and a
  process-wide memo `(path, len, mtime) -> Arc<FileFooter>`; entries are immutable, the key changes when the file
  does, so no invalidation logic. Not required for correctness; ship separately if the reviewer prefers.

### 3.3 FE: statistics into the optimizer

`TableFunctionTable` (`catalog/TableFunctionTable.java`):

- `getFileSchema()` (`:688-731`) already receives the result; after the `schema` loop, build
  `FilesStatistics.fromRpc(result.file_statistics, columns, fileStatuses)` and store it in a new final field.
  If `file_statistics` is empty, `FilesStatistics.estimateFromBytes(fileStatuses, columns)`:
  rows = sum(size) / sum(type width of the inferred columns), `exact=false`.
  One `LOG.info` per statement: path, files, rows, bytes, source (`footer|estimated`), columns with ranges,
  RPC ms (3.6).
- New immutable holder `catalog/FilesStatistics.java`: `totalRows`, `totalBytes`, `exact`, per-column
  `{nullCount, min, max (as double via convertStatisticsToDouble, merged min/max across files), avgSize}`; a
  `columnStatistic(Column, Type, Mode)` factory.

`StatisticsCalculator.computeFileScanNode` (`:664-676`), reached from `visitLogicalTableFunctionTableScan` (`:374-376`),
`visitLogicalFileScan`/`visitPhysicalFileScan` (`:655-662`):

```java
Table table = (node instanceof LogicalScanOperator) ? ((LogicalScanOperator) node).getTable()
                                                     : ((PhysicalScanOperator) node).getTable();
FilesStatistics fs = (table instanceof TableFunctionTable && mode != OFF)
        ? ((TableFunctionTable) table).getFilesStatistics() : null;
Statistics.Builder builder = Statistics.builder();
for (ColumnRefOperator ref : colRefToColumnMetaMap.keySet()) {
    builder.addColumnStatistic(ref, fs == null ? ColumnStatistic.unknown()
                                               : fs.columnStatistic(colRefToColumnMetaMap.get(ref), ref.getType(), mode));
}
builder.setOutputRowCount(fs == null ? 1 : fs.getTotalRows());
builder.setTableRowCountMayInaccurate(fs != null && !fs.isExact());
context.setStatistics(builder.build());
return visitOperator(node, context);   // applies the scan's own conjuncts and limit, :245-300
```

Every required column must get an entry (`Statistics.getColumnStatistic` throws on a missing one, `Statistics.java:118-125`).
`FileTable` scans (not `TableFunctionTable`) keep today's behaviour. Add `visitPhysicalTableFunctionTableScan`
symmetric to `:374` so physical re-derivation cannot fall back to the default.

Column statistic per mode (`FilesStatistics.columnStatistic`):

| mode | row count | column statistic | join-key estimate path |
|---|---|---|---|
| `off` | 1 | `unknown()` | today |
| `rows` | exact / estimated | `unknown()` | `max(L,R)` (`StatisticsCalculator.java:1222-1242`) |
| `rows_ranges` (default) | exact / estimated | type **UNKNOWN**, finite `min/max`, real `nullsFraction`, `averageRowSize` = type width (or `uncompressed/rows`), `distinctValuesCount = 1` | `max(L,R)`; range predicates use `overlap/length` (`StatisticRangeValues.java:102-107`) instead of 0.5 |
| `rows_ranges_ndv` | exact / estimated | type **ESTIMATE** for integral/date/decimal columns with `ndv = min(nonNullRows, max-min+1)`; strings/floats stay as in `rows_ranges` | `L*R/max(ndv)` (`estimateInnerJoinStatistics`, `:1711-1719`) |

Why an UNKNOWN-typed statistic with a finite range is native, not a hack: the estimator itself produces that
state today (`estimatePredicateRange` rebuilds the column with the intersect range and `setType(columnStatistic.getType())`,
`BinaryPredicateStatisticCalculator.java:201-225`; the printed `o_orderdate-->[7.414848E8, 7.494336E8, ...] UNKNOWN`
in survey/explain/q04.costs.txt:102 is exactly it). `StatisticRangeValues.from` reads min/max/ndv regardless of type
(`:52-54`); `distinctValues == 1` is the marker the point-predicate branch uses for "unknown" (`:90-96`), so
equality selectivity stays 0.5 (no regression) while ranges become real. No FE code compares against the
`ColumnStatistic.unknown()` singleton by identity (grep, none). The `rows_ranges_ndv` heuristic is right for
dense surrogate keys (TPC-H `*_key`, nationkey, dates, quantities) and degrades to `ndv = rows` for sparse keys
(the usual PK-FK assumption); it is a mode, not the default, and the EXPLAIN gate picks between the two (6.2).

### 3.4 FE cost inputs for a GPU cluster (Part B)

Measured cost code (appendix A.4-A.6): `realCost = 0.5 cpu + 2 mem + 1.5 net` (`CostModel.java:129-135`).
Exchange BROADCAST: `cpu = S * aliveBackends` (0 here: the Sirius CNs register as COMPUTE NODEs, P3),
`mem = S`, `net = S`, all x1000 when `S > exec_mem_limit` (`:419-429`, default 2 GiB `SessionVariable.java:1305`).
Exchange SHUFFLE: `cpu = S*f`, `net = 0` when `enable_local_shuffle_agg && pipeline && single node` else `S*f`
(`:432-441`, `SystemInfoService.java:246-248`). Hash join: BROADCAST `cpu = R + L*pB, mem = R*N`;
SHUFFLE `cpu = R/p + L*pS, mem = R` with `pB = clamp(ln(rowsR/1e5), 1, 12)`, `pS = clamp(ln(rowsR/1e5) - log2(2p), 1, 3)`
(`HashJoinCostModel.java:87-156`, `BOTTOM_NUMBER = 100000` `:58`). Guard: broadcast rejected when
`L_bytes < R_bytes * 10 && rowsR > broadcast_row_limit(15M)` (`EnforceAndCostTask.java:312-327`).

Three of the four levers are existing session variables; set once per cluster (`SET GLOBAL`, as the bench kit already
does for `enable_pipeline_engine`, `run-abc.sh:799`), shipped as `experimental/starrocks/conf/sirius-cbo-defaults.sql`
and applied by the launchers after the FE is up:

| setting | value | why (source) |
|---|---|---|
| `exec_mem_limit` | 64 GiB (below the GPU pool; the CN does not enforce `TQueryOptions.mem_limit`: no `mem_limit` in `experimental/starrocks/src`) | removes the x1000 penalty on multi-GB broadcast builds (`CostModel.java:426-429`); builds of 1-10 GB are normal on a 100 GiB GPU pool |
| `enable_local_shuffle_agg` | `false` | on a Sirius CN a single-node shuffle parks the whole stream (B1); its network cost must be the bytes, not 0 (`CostModel.java:437-440`) |
| `broadcast_row_limit` | 2,000,000,000 | the guard protects CPU BE memory; without it q03's 7.2e8-row (est.) build is rejected although it is 2.3 GB real (section 4, case B) |
| `cbo_cte_reuse` | `false` | real costs can make CTE reuse win, which emits `MULTI_CAST_DATA_STREAM_SINK`; the CN refuses it (`compute_node_service.rs:1519`, P5); revisit when plan item 9 lands |
| `enable_sirius_gpu_join_cost` (new, patch) | `true` | see below |

The one code change (patch, `HashJoinCostModel.getAvgProbeCost`, `:141-156`): when the new session variable is set,
cap the BROADCAST cache penalty at `SHUFFLE_MAX_RATIO` (3) like the shuffle branch. The 12x-vs-3x split models
CPU cache misses of a large hash table; a GPU hash join keeps both builds in HBM. Four lines plus the variable.
Everything else in the model (bytes moved, memory per node, N-way replication) stays as upstream wrote it.

### 3.5 Concurrency and cancellation

- FE: statistics live on the per-statement `TableFunctionTable` created in
  `QueryAnalyzer.resolveTableFunctionTable` (`:2192-2196`); no shared mutable state, no cache, no invalidation.
  `Config` is read once per `computeFileScanNode`; a mode flip mid-statement cannot mix modes inside one plan
  because the mode is captured in `FilesStatistics` at RPC time.
- FE cancellation: the RPC wait is the existing `future.get()` (`TableFunctionTable.java:713`); an interrupt is
  already converted to `DdlException` (`:714-716`). Statistics add no new blocking. Pre-existing gap worth a
  separate line item: the wait is unbounded; a bounded `future.get(timeout)` is a one-line hardening.
- CN: the handler is async per request (`compute_node_service.rs:682-697`); statistics are a pure function of
  metadata already loaded; a cancelled FE statement simply discards the reply. The optional memo (3.2) stores
  immutable `Arc` entries keyed by `(path, len, mtime)`, so concurrent requests never observe a partial entry.
- Wire compatibility: all new fields optional; mixed FE/CN versions degrade to the bytes/width estimate, never
  to a failure.

### 3.6 Observability

- FE `INFO` per statement: `FILES() statistics path=... files=60 rows=5999989709 bytes=... source=footer
  columns=16 ranges=13 exact=true rpc_ms=73`. `source=estimated` names the fallback.
- `EXPLAIN COSTS` shows the new inputs at every `FileScanNode` (`PlanNode.computeStatistics`, `PlanNode.java:578-587`
  copies `outputRowCount` into `cardinality` and prints the column statistics; today's format at
  survey/explain/q04.costs.txt:96-104). `explain_summary.py` (`scratchpad/perf/sf1000/`) gains a per-scan
  cardinality column so `card_all_one` becomes a golden check.
- CN: `get_file_schema` span fields `files`, `rows`, `with_stats`, `elapsed_ms` (`tracing`), one `info!` per request.
- Kill switch: `ADMIN SET FRONTEND CONFIG ("files_query_statistics_mode" = "off")`, mutable, no restart
  (same pattern as `files_query_whole_file_ranges`, `Config.java:1212-1223`).

### 3.7 Configuration

- `Config.files_query_statistics_mode` (mutable string, default `rows_ranges`; `off` restores today's plans).
- Session variable `enable_sirius_gpu_join_cost` (default `false`; `true` in `sirius-cbo-defaults.sql`).
- The four existing session variables above, applied by the launcher; documented in `docs/TUNABLES.md`.

## 4. Behaviour under the measured SF1000 case (inference: hand computation of the FE cost code)

Widths are FE type sizes as in P3 (lineitem 4-column projection 32 B, orders-side join output 16 B); a 2x width
error does not change any sign below except the two rows marked "coin flip". `f` = `setExchangeCostFactor`
(1-1.5, `CostModel.java:398`); `p` = parallel factor (>= N CNs).

**Case A, q05 at 1 CN: lineitem ⋈ (orders ⋈ customer ⋈ nation ⋈ region).**
`rows_ranges`: orders `o_orderdate` in [1994-01-01, 1995-01-01) over the footer range [1992-01-01, 1998-08-02] =
365/2405 = 0.152 -> 2.28e8 rows (today 0.5 x 0.5); `⋈ customer` = `max(2.28e8, 1.5e8)` = 2.28e8 rows;
R = 2.28e8 x 16 = 3.65e9 B; L = 6.0e9 x 32 = 1.92e11 B; `pB = ln(2280) = 7.7`, `pS = 3`.

| variant | BROADCAST R (lineitem stays in its scan fragment, streamed) | SHUFFLE both (lineitem parked) | choice |
|---|---:|---:|---|
| stock, default settings | 3.5·R·1000 + 0.5(R + 7.7L) + 2R = **1.35e13** | 0.5fL + 0.5fR + 0.5(R/p + 3L) + 2R = **3.95e11** | SHUFFLE (34x) -> q05 still dies |
| + `exec_mem_limit` 64 GiB | 7.6e11 | 3.95e11 | SHUFFLE (1.9x) |
| + `enable_local_shuffle_agg=false` | 7.6e11 | 2fL + 2fR + 2.97e11 = 6.9e11 (f=1) / 8.8e11 (f=1.5) | coin flip |
| + `enable_sirius_gpu_join_cost` (pB=3) | 1.28e10 + 0.5(R + 3L) + 2R = **3.1e11** | 6.9e11 | BROADCAST (2.2x) |

With BROADCAST the join sits in lineitem's fragment (StarRocks places a broadcast join in the probe child's
fragment; survey/explain/q04.costs.txt F00 is the same layout), the build is 61.6M x 12 B ≈ 0.7 GB parked
(standalone estimate; the FE thinks 3.65 GB), and lineitem streams through the join the way q21's post-failure
fragment streamed 123 GB through FILTER -> HASH_JOIN in 3.15 s (B1, cn1-quent.txt q21.r0). q08/q09 (build = filtered
part, est. 1e8 rows x 8 B = 8e8 B) and q17 (build = filtered part, 5e7 rows) follow the same arithmetic with
smaller R, so the margin is larger. q18: the semi build is the HAVING aggregate (57 rows real; FE est. 7.5e8 x 16 B =
1.2e10) and the outer lineitem becomes the probe of orders ⋈ customer ⋈ agg; passes if the FE commutes the LEFT
SEMI (needs plan item 5, see 4 case D).

**Case B, q03 at 4 CNs: lineitem(l_shipdate > 1995-03-15) ⋈ (orders(o_orderdate < 1995-03-15) ⋈ customer).**
L = 3.22e9 x 24 = 7.7e10 (range 1357/2525 = 0.537; actual 3.234e9, card-compare-cn4 exch 2);
R = `max(7.2e8, 7.5e7)` = 7.2e8 rows x 16 = 1.15e10 (actual 1.46e8 rows / 2.33 GB parked, P3 table).
Guard: `7.7e10 < 1.15e10 x 10` and `7.2e8 > 1.5e7` -> broadcast **rejected** with the default
`broadcast_row_limit`; with 2e9 it is allowed. `pB = ln(7200) = 8.9`, `pS = 3`.

| variant | BROADCAST R | SHUFFLE both | choice |
|---|---:|---:|---|
| stock (guard off, exec_mem_limit up) | 3.5R + 0.5(R + 8.9L) + 2·4R = 4.8e11 | 2f(L+R) + 0.5(R/4 + 3L) + 2R = 3.2e11 | SHUFFLE (today's 4-CN plan, 58 GB moved) |
| + gpu join cost (pB=3) | 4e10 + 0.5(R + 3L) + 9.2e10 = 2.5e11 | 3.2e11 | BROADCAST (1.25x): 3 x 2.33 GB = 7 GB moved |
| `rows_ranges_ndv` (R = 1.46e8 rows, 2.3e9 B) | 1.4e11 | 2.8e11 | BROADCAST (2x) |

**Case C, q22 at 1 and 4 CNs.** LEFT ANTI: probe customer-filtered (est. 3.75e7 rows x 12 B), build orders
1.5e9 x 8 B = 1.2e10 today (exch 10; 18.6 GB nixl at 4 CNs). With real counts `JoinCommutativityRule`
(LEFT_ANTI <-> RIGHT_ANTI, P5) makes the customer side the build (27x smaller); anti-join rows with unknown key
stats = `min(L, R)` (`StatisticsCalculator.java:1229-1231`). The translator supports `RIGHT_ANTI_JOIN`
(planning-contrast.md Table 3), so no new dependency. Expected: per-CN `HASH_JOIN(15)` 0.516 s -> tens of ms
(build 6M rows instead of 1.5B), nixl 18.6 GB -> ~0.5 GB.

**Case D, q04 (and q18/q20/q21 semi joins).** LEFT SEMI: probe orders (est. 2.28e8 x 12 B = 2.7e9), build
lineitem (`l_commitdate < l_receiptdate` column-vs-column with unknown stats = x0.8,
`BinaryPredicateStatisticCalculator.java:615-616`, so 4.8e9 rows x 8 B = 3.8e10). The commuted RIGHT SEMI with
orders as build is 14x smaller, so the FE will emit `RIGHT_SEMI_JOIN`, which the translator refuses
(`node_translator.rs:1743-1762`, P5). **Without plan item 5, fix 3 turns q04 from pass to refused.** With it:
build orders (57M real, 0.7 GB), lineitem streams; 1 CN stops parking 30.8 GB, 4 CNs move ≤ 2 GB instead of 22.8 GB.

**The other passing queries (inference, same arithmetic):**

| query | today (1 CN / 4 CN) | with Part A+B | note |
|---|---|---|---|
| q01 q06 | scan + agg, no join | unchanged | aggregates stay 2-phase: one-stage agg requires no UNKNOWN column stats (`EnforceAndCostTask.java:411-415`) |
| q02 | partsupp 800M broadcast (1 CN) / four 800M shuffles (4 CN) | nation/region (25/5 rows, exact) broadcast; partsupp probes | removes exch 28/31 (800M rows on `n_regionkey`); DELIM shape still needs remote runtime filters (P2) |
| q07 | lineitem+orders shuffled at 4 CN; orders broadcast at 1 CN | unchanged at 4 CN (orders 3e10 B x 4 loses to shuffle); unchanged at 1 CN | no regression; shuffle skew on `c_nationkey` unchanged |
| q10 | 4 CN shuffles lineitem 26.7 GB + customer 19 GB | `rows_ranges`: FE estimates lineitem ⋈ orders at `max` = 3e9 rows and may broadcast the 25.5 GB customer projection x3 = 76 GB (**worse**); `rows_ranges_ndv`: lineitem ⋈ orders est. 2.85e7 rows -> broadcast the join result, ~7 GB | the case that decides between the two modes at the EXPLAIN gate |
| q11 | supplier shuffled on `s_nationkey` to 1 GERMANY row | nation (1 row) broadcast | fixes the P3 skew example |
| q12 | orders 1.5e9 shuffled (23 GB) | filtered lineitem (est. 3e8, real 3.1e7 rows / 0.5 GB) broadcast | ~1.5 GB moved |
| q13 | 1.48B orders parked through a local shuffle at 1 CN (18.2 GB) | customer (1.5e8 x 4 B = 6e8 B) broadcast; orders streams | inference; RIGHT OUTER keeps its side |
| q14 | part 200M broadcast build | filtered lineitem (1 month = 1/79 -> 7.6e7 rows) becomes the build | matches P3 "build on the 75M filtered lineitem" |
| q15 | two lineitem scans (CTE inlined) | unchanged with `cbo_cte_reuse=false`; with reuse the CN refuses multicast | correctness bug (q15 0 rows) is item 9, not this fix |
| q19 | lineitem 128M shuffled (2.7 GB) | filtered part broadcast (0.02 GB) | |
| q20 | agg over lineitem-1994 before the join (exch 9 304M rows) | LEFT SEMI joins may commute -> needs item 5; pre-aggregation placement is `PushDownAggregate` (uses `broadcast_row_limit`, `PushDownAggregateCollector.java:647`) | verify at the gate |

## 5. Files to touch

FE, delivered as `experimental/starrocks/patches/files-statistics.patch` (generated with
`git -C experimental/starrocks/starrocks diff -- <paths>`; applied by `scripts/apply-starrocks-patches.sh`;
rebuilt with `pixi run fe-build`):

- `gensrc/proto/internal_service.proto` (:647-650): the three messages in 3.1.
- `fe/fe-core/src/main/java/com/starrocks/catalog/TableFunctionTable.java` (:688-731): parse `file_statistics`,
  fallback estimate, log line, `getFilesStatistics()`.
- `fe/fe-core/src/main/java/com/starrocks/catalog/FilesStatistics.java` (new, ~150 lines).
- `fe/fe-core/src/main/java/com/starrocks/sql/optimizer/statistics/StatisticsCalculator.java` (:374-376, :664-676,
  new physical visitor).
- `fe/fe-core/src/main/java/com/starrocks/common/Config.java` (next to `files_query_whole_file_ranges`, :1212-1223).
- `fe/fe-core/src/main/java/com/starrocks/qe/SessionVariable.java`: `enable_sirius_gpu_join_cost`.
- `fe/fe-core/src/main/java/com/starrocks/sql/optimizer/cost/HashJoinCostModel.java` (:141-156).
- Tests: `fe/fe-core/src/test/java/com/starrocks/catalog/TableFunctionTableTest.java`,
  `.../sql/optimizer/statistics/StatisticsCalculatorTest.java`, new `.../sql/plan/FilesStatisticsPlanTest.java`.

CN (fork PR against `experimental/starrocks`):

- `src/file_schema.rs` (:14-99 plus tests), `src/compute_node_service.rs` (:682-697, :1680-1707, tests at :3624),
  `build.rs` (:70-105 patch-presence check), `src/wire_type_parity.rs` if it enumerates `PGetFileSchemaResult`.

Kit and docs:

- `experimental/starrocks/conf/sirius-cbo-defaults.sql` (new) and the launchers under `benchmarks/tpch/` and
  `benchmarks/cluster8*.sh` apply it after the FE answers; `docs/TUNABLES.md`, `DEMO.md:173` (patch list);
  `scratchpad/perf/sf1000/explain_summary.py` (per-scan cardinality).

## 6. Tests

### 6.1 Unit / component (minutes, no GPU)

CN (`pixi run cn-test-no-engine`, i.e. `cargo test -p sirius-starrocks-cn --no-default-features`):

- `file_schema.rs`: a fixture written with `parquet::arrow::ArrowWriter` (the existing `write_parquet` helper
  at `:266-283` writes schema-only files) holding int64/int32/date/decimal(15,2)/double/utf8 columns with nulls
  across two row groups: asserts `num_rows`, summed `null_count`, min/max literals (`'1994-01-01'` for DATE,
  `12.34` for the decimal), no min/max for utf8, NaN-bearing double omitted, and a deprecated-stats file yields
  no min/max. Multi-file: statistics returned in request order, one entry per file, schema-mismatch still
  fails closed (existing tests `:409-470` keep passing).
- `compute_node_service.rs`: `get_file_schema_attachment_reports_file_statistics` next to `:3624`: two ranges ->
  two `PFileStatistics` with the right paths and row counts.

FE (`mvn -pl fe-core test -Dtest=...` inside the FE build environment):

- `TableFunctionTableTest`: mocked `BackendServiceClient.getFileSchema` returning `file_statistics` -> totals,
  `exact=true`, merged min/max; empty `file_statistics` -> bytes/width estimate, `exact=false`; a file with
  `num_rows` missing -> whole table `exact=false`.
- `StatisticsCalculatorTest.testTableFunctionTableScanUsesFileStatistics`: `LogicalTableFunctionTableScanOperator`
  over a `fake://` table with injected `FilesStatistics` (package-private setter); `outputRowCount` equals the
  sum; a `date >= c` predicate yields `overlap/length` selectivity, not 0.5; mode `off` yields 1 row.
- `FilesStatisticsPlanTest` (PlanTestBase): a q03-shaped statement over three `fake://` FILES() tables with
  injected statistics; asserts `BROADCAST` of the orders ⋈ customer side at 1 and 4 registered nodes with the
  GPU flag on, `PARTITIONED` with it off; asserts no `RIGHT SEMI` is emitted for a q04-shaped statement when the
  translator gate flag is off (documenting the item-5 dependency).

### 6.2 EXPLAIN gate (tens of minutes, FE plus one translate-only CN, no GPU work)

`EXPLAIN COSTS` for all 22 queries at 1 CN and 4 CNs (as survey.sh did, `SIRIUS_CN_TRANSLATE_ONLY=1`; the schema
RPC needs a live CN), for `rows_ranges` and `rows_ranges_ndv`, with `sirius-cbo-defaults.sql` applied. Goldens
for q03 q04 q05 q22 (plan-summary lines) checked by `explain_summary.py`: `card_all_one=False`; every
`FileScanNode` cardinality equals the footer count (lineitem 5,999,989,709); join sides as in section 4; no
`MULTI_CAST_DATA_STREAM_SINK`; `RIGHT SEMI` present only if item 5 is in the build. The q10 plan decides the
default mode.

### 6.3 SF1000 acceptance (orchestrator, one arm at a time)

1 CN and 4 CN sweeps of the 15 passing queries plus the six, `compare.py` against the oracle, plan shapes
recorded (dumps), per-query nixl GB (cn4-transmits) and parked GB per fragment (Quent `STREAMING_SINK in=`)
against the campaign table. Pass criteria: the 15 still pass with warm medians within noise or better; the six
listed in section 7 pass on 1 CN with oracle MATCH; q03/q12/q22 nixl volume drops as predicted.

## 7. Predicted effect

Inference from the campaign numbers (results.md, card-compare-*.txt, cn4-transmits.txt, P3/B5 drain times):

| arm | query | today | predicted | basis |
|---|---|---|---|---|
| 1 CN | q05 q08 q09 q17 q18 | fail after 101/168/232/104/54 s | pass, ~3-7 s | standalone 2.8-5.4 s warm (results-failed.md) + ~0.2 s fixed head (P4) + build-side parking of ≤ 1-3 GB; lineitem streams (B1) |
| 1 CN | q21 | fail 114 s | pass only with item 5 (RIGHT SEMI); else refused or unchanged | P1 caveat, P5 |
| 1 CN | q04 | 1850 ms, 30.8 GB parked | ~1.7-1.8 s, ≤ 0.7 GB parked (with item 5) | build = 57M orders instead of 3.79B lineitem keys |
| 1 CN | q13 | 2198 ms, 18.2 GB parked | ~1.9-2.0 s | customer broadcast, orders streams |
| 4 CN | q03 | 1607 ms, 60.3 GB nixl, 14.1 GiB arena | ~1.35-1.4 s, ~7 GB, ~2.3 GiB arena | drain 0.28 s -> ~0.03 s (B5) |
| 4 CN | q12 | 1283 ms, 23.5 GB | ~1.15-1.2 s, ~1.5 GB | drain 0.12 s removed |
| 4 CN | q22 | 831 ms, 18.6 GB, HASH_JOIN 0.516 s/CN | ~0.5 s, < 1 GB | RIGHT ANTI on 6M customers |
| 4 CN | q10 | 1789 ms, 48 GB | ~1.55 s (`rows_ranges_ndv`) or worse than today (`rows_ranges`, 76 GB) | section 4 table |
| 4 CN | q19 q14 q11 q02 | 2.7 GB / 200M part build / skewed 10M shuffle / four 800M shuffles | small side broadcast in each | volumes tiny; time within noise except q02 (~0.1-0.2 s) |
| both | q01 q06 q07 q15 q20 | | unchanged (q20 needs item 5 verification) | |

Not predicted to change: the ~0.2 s fixed head (P4), the 1.3-1.6x 4-CN scan slowdown (B7), q15's wrong answers
(item 9), the decimal truncation diffs (#1236).

## 8. Risks and failure modes

Enumerated with the behaviour and the mitigation:

1. **New plan shapes the CN cannot execute.** `RIGHT_SEMI_JOIN` (translator `node_translator.rs:1743-1762`) for
   q04/q18/q20/q21; `MULTI_CAST_DATA_STREAM_SINK` if CTE reuse wins (`compute_node_service.rs:1519`). Both fail
   closed with a named error (as q16 does today), no wrong result. Mitigation: item 5 lands first;
   `cbo_cte_reuse=false` in the defaults; `files_query_statistics_mode=off` kill switch. The EXPLAIN gate
   (6.2) catches both before any GPU run.
2. **Under-estimated build broadcast to every CN.** A wrong small-side estimate parks a large build on N GPUs.
   Bounded by `exec_mem_limit` (x1000 above 64 GiB est.) and, at run time, by fix 1 (fail fast) instead of
   54-232 s storms. The `max(L,R)` join rule of `rows_ranges` errs toward over-estimating intermediates
   (never under), which is the safe direction for this failure; `rows_ranges_ndv` can under-estimate joins on
   sparse keys, which is why it is a mode.
3. **q10-type over-estimation** (`max(L,R)` says 3e9 for lineitem ⋈ orders, real 1.15e8) makes the FE move
   the wide customer projection instead. Decided at the gate; the engine's in-fragment re-plan (C2) still
   applies inside the receiver.
4. **CN returns no statistics** (stock BE, non-parquet, remote URI): bytes/width estimate with
   `tableRowCountMayInaccurate=true`, log `source=estimated`. Plans as good as byte totals allow; the flag
   disables skew rewrites and one-stage aggregation (`DataSkew.java:161,194`, `EnforceAndCostTask.java:413`).
5. **Unreliable footer statistics** (deprecated/inexact min/max, writer bugs): min/max omitted per column ->
   infinite range = today's 0.5 behaviour; row counts are always exact in parquet. Statistics never affect
   correctness, only the plan.
6. **Partial footer failure**: the schema RPC already fails closed on any unreadable file (`file_schema.rs:62-99`);
   no partial-statistics state exists.
7. **Files change between analysis and execution**: statistics stale by one statement; harmless.
8. **Version skew FE/CN**: optional protobuf fields; either direction degrades to (4).
9. **Very large file counts**: footer reads already dominate the RPC (P4); the optional memo/concurrency (3.2)
   is the fix; statistics add no I/O.
10. **Cost-model side effects of the settings**: `broadcast_row_limit=2e9` also relaxes `PushDownAggregate`
    (`PushDownAggregateCollector.java:647`) -> pre-aggregation may be pushed below more joins (the q20 pattern,
    P5 SAJ-1). Visible at the gate; if it hurts, `cbo_push_down_aggregate_mode` is the lever.
11. **Determinism**: the plan depends on file statistics, which are deterministic per dataset; the
    `Collections.shuffle(nodeIds)` choice of which CN answers the RPC (`TableFunctionTable.java:706`) does not
    matter because every CN reads the same footers.
12. **Upstream drift**: the patch touches five FE files; `computeFileScanNode` and `getFileSchema` are stable
    since 3.x. Keep the hunks small and re-apply on submodule bumps as with the nixl patch.

## 9. Effort

| piece | size | time |
|---|---|---|
| proto + CN (stats extraction, service, build.rs check, tests) | ~250 lines Rust + ~150 tests | 1 day |
| FE (FilesStatistics, TableFunctionTable, StatisticsCalculator, Config, cost flag, tests) | ~350 lines Java + ~200 tests | 1.5 days |
| patch generation, defaults SQL, launchers, TUNABLES/DEMO docs | ~80 lines | 0.5 day |
| FE rebuild (`pixi run fe-build`, multi-hour Maven) | | 1-2 builds, background |
| EXPLAIN gate (two modes x two node counts x 22) and golden capture | | 0.5 day |
| SF1000 sweeps (1 CN 15+6, 4 CN 15+6) | ~1 h each with fix 1; the six cost 775 s per 1-CN sweep without it | 0.5 day |

Total about 4 engineer-days plus build time. Upstream shape: the FE part is a checked-in patch (carve plan layer:
FE -> patch), the CN part a fork PR, the defaults/docs a small kit PR; three PRs, each reviewable alone.

## 10. Dependencies and ordering with the other fixes

- **Hard: plan item 5 (translator `RIGHT_SEMI_JOIN`, small, high confidence) before fix 3 is enabled in a sweep**;
  otherwise q04 (and possibly q18/q20/q21) go from pass to refused (section 4, case D). Item 5 is not one of the
  four fixes; it should be added to the build order as a prerequisite of fix 3.
- **Fix 1 (fail fast)**: independent code; strongly preferred first because every mis-plan during the gate/sweep
  otherwise costs 54-232 s and 17,917 log lines.
- **Fix 2 (parked-output bookkeeping)**: independent; makes sweep results order-independent (B4 leaks would
  otherwise confound the new plans' memory footprints).
- **Fix 4 (fragment fusion / spillable parked repositories)**: complementary, not required. With Part A+B the FE
  itself produces the streaming shape for five of the six; fusion remains valuable for the 1-CN exchanges that
  stay (broadcast builds are still parked, receiver-first) and for q21 until item 5 lands. Fix 4(d) in the plan
  ("real cardinalities may already move lineitem to the probe side") is exactly this design's Part B.
- **Item 9 (multicast sink)**: lets `cbo_cte_reuse` return to `true`.
- No engine (`src/`) change in this fix.

## Appendix: code excerpts relied on

A.1 `StatisticsCalculator.java:664-676` (root cause)
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
Reached from `:374-376 visitLogicalTableFunctionTableScan` and `:655-662 visitLogicalFileScan/visitPhysicalFileScan`.
`visitOperator` (`:245-300`) then applies the scan's conjuncts (`estimateStatistics`) and limit.

A.2 `TableFunctionTable.java:606-613` (file sizes exist on the FE) and `:688-731` (schema RPC, no cache, unbounded wait)
```java
brokerFileStatus.setPath(fileStatus.getPath().toString());
brokerFileStatus.setIsDir(fileStatus.isDirectory());
brokerFileStatus.setSize(fileStatus.getLen());
...
Future<PGetFileSchemaResult> future = BackendServiceClient.getInstance().getFileSchema(address, request);
result = future.get();
...
for (PSlotDescriptor slot : result.schema) {
    columns.add(new Column(slot.colName, TypeDeserializer.fromProtobuf(slot.slotType), true));
}
```
Created per statement at `QueryAnalyzer.java:2192-2196` (`new TableFunctionTable(properties, pushDownSchemaFunc)`).

A.3 `gensrc/proto/internal_service.proto:647-650` and `PSlotDescriptor` (`descriptors.proto:43-56`)
```proto
message PGetFileSchemaResult {
    required StatusPB status = 1;
    repeated PSlotDescriptor schema = 2;
};
```

A.4 `CostModel.java:129-135, 419-441` (real cost, exchange costs)
```java
double cpuCostWeight = 0.5; double memoryCostWeight = 2; double networkCostWeight = 1.5;
...
case BROADCAST:
    int aliveBackendNumber = ctx.getAliveBackendNumber();      // BEs only: 0 in this deployment (P3)
    int beNum = Math.max(1, aliveBackendNumber);
    result = CostEstimate.of(outputSize * aliveBackendNumber, outputSize * beNum, Math.max(outputSize * beNum, 1));
    if (outputSize > sessionVariable.getMaxExecMemByte()) {   // 2 GiB default, SessionVariable.java:1305
        result = result.multiplyBy(StatisticsEstimateCoefficient.BROADCAST_JOIN_MEM_EXCEED_PENALTY); // 1000
    }
case SHUFFLE:
    boolean ignoreNetworkCost = sessionVariable.isEnableLocalShuffleAgg()
            && sessionVariable.isEnablePipelineEngine()
            && GlobalStateMgr.getCurrentState().getNodeMgr().getClusterInfo().isSingleBackendAndComputeNode();
    double networkCost = ignoreNetworkCost ? 0 : Math.max(outputSize, 1);
    result = CostEstimate.of(outputSize * factor, 0, networkCost * factor);
```
`SystemInfoService.java:246-248 isSingleBackendAndComputeNode`: `idToBackendRef.size() + idToComputeNodeRef.size() == 1`.

A.5 `HashJoinCostModel.java:58-62, 87-156` (join cost, probe penalty)
```java
private static final int BOTTOM_NUMBER = 100000;
private static final double SHUFFLE_MAX_RATIO = 3;
private static final double BROADCAST_MAT_RATIO = 12;
...
case BROADCAST: buildCost = rightOutput; probeCost = leftOutput * getAvgProbeCost(); break;
case SHUFFLE:   buildCost = rightOutput / parallelFactor; probeCost = leftOutput * getAvgProbeCost(); break;
...
int beNum = Math.max(1, aliveBackendNumber + (RunMode.isSharedDataMode() ? aliveComputeNodeNumber : 0));
memCost = (JoinExecMode.BROADCAST == execMode) ? rightOutput * beNum : rightOutput;
...
if (JoinExecMode.BROADCAST == execMode) {
    cachePenaltyFactor = Math.max(1, Math.log(mapSize / BOTTOM_NUMBER));
    cachePenaltyFactor = Math.min(BROADCAST_MAT_RATIO, cachePenaltyFactor);
} else {
    cachePenaltyFactor = Math.max(1, (Math.log(mapSize / BOTTOM_NUMBER) - Math.log(parallelFactor) / Math.log(2)));
    cachePenaltyFactor = Math.min(SHUFFLE_MAX_RATIO, cachePenaltyFactor);
}
```

A.6 `EnforceAndCostTask.java:312-327` (broadcast guard)
```java
if (sv.getBroadcastRowCountLimit() <= 0) { return false; }
int beNum = Math.max(1, ctx.getAliveBackendNumber());
...
if (leftOutputSize < rightOutputSize * beNum * sv.getBroadcastRightTableScaleFactor()
        && rightChildStats.getOutputRowCount() > sv.getBroadcastRowCountLimit()) {
    return false;
}
```
`SessionVariable.java:1832-1836`: `broadcastRowCountLimit = 15000000`, `broadcastRightTableScaleFactor = 10.0`.

A.7 `StatisticsCalculator.java:1222-1242` (join rows with unknown key statistics)
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

A.8 `StatisticRangeValues.java:52-54, 73-111` (range selectivity; finite range on an UNKNOWN-typed column is honoured)
```java
public static StatisticRangeValues from(ColumnStatistic column) {
    return new StatisticRangeValues(column.getMinValue(), column.getMaxValue(), column.getDistinctValuesCount());
}
...
if (isInfinite(lengthOfIntersect)) { ... return OVERLAP_INFINITE_RANGE_FILTER_COEFFICIENT; }   // 0.5
if (lengthOfIntersect == 0) {
    // distinctValues equals 1 means the column statistics is unknown, requires special treatment
    if (this.distinctValues == 1 && length() > 1) { return 0.5; }
    return 1 / max(this.distinctValues, 1);
}
...
if (lengthOfIntersect > 0) { return lengthOfIntersect / length; }
```
`BinaryPredicateStatisticCalculator.java:201-225` rebuilds the column after a predicate with
`ColumnStatistic.buildFrom(columnStatistic).setNullsFraction(0)` keeping the type (UNKNOWN stays UNKNOWN with a
finite range: survey/explain/q04.costs.txt:102). `:615-616`: column-vs-column with an unknown side = x0.8.
`ColumnStatistic.java:32-33`: `UNKNOWN = (-inf, +inf, nulls 0, avgRowSize 1, ndv 1, UNKNOWN)`; `:144-149`
`unknown()` / `isUnknown()` by type. `Statistics.java:88-107 getOutputSize`: type width for unknown columns.

A.9 `PlanNode.java:578-587` (EXPLAIN shows the new inputs)
```java
public void computeStatistics(Statistics statistics) {
    cardinality = Math.round(statistics.getOutputRowCount());
    avgRowSize = (float) statistics.getColumnStatistics().values().stream()
            .mapToDouble(columnStatistic -> columnStatistic.getAverageRowSize()).sum();
    columnStatistics = statistics.getColumnStatistics();
```
called from `PlanFragmentBuilder.java:4290` for the FILES() `FileScanNode`.

A.10 CN: `file_schema.rs:14-27, 62-99`; `compute_node_service.rs:682-697, 1680-1707`; `build.rs:70-105`
```rust
pub(crate) async fn parquet_file_schema(path: &str) -> Result<Vec<PSlotDescriptor>, String> {
    let local = local_file_path(path)?;
    let file = tokio::fs::File::open(&local).await...;
    let builder = ParquetRecordBatchStreamBuilder::new(file).await...;
    let slots = builder.metadata().file_metadata().schema_descr().root_schema().get_fields()...
...
async fn get_file_schema(&self, _request: PGetFileSchemaRequest, attachment: Vec<u8>)
        -> Result<crate::prpc::Reply<PGetFileSchemaResult>, crate::prpc::Error> {
    let result = match Self::file_schema_from_attachment(&attachment).await {
        Ok(schema) => PGetFileSchemaResult { status: Self::ok_status(), schema },
        Err(err) => PGetFileSchemaResult { status: Self::internal_error(err), schema: Vec::new() },
    };
```
parquet-rs 59.2.0 (`Cargo.lock:1648-1649`): `FileMetaData::num_rows` (`file/metadata/mod.rs:548`),
`ColumnChunkMetaData::{num_values, statistics}` (`:1012, :1072`), `Statistics::{is_min_max_deprecated,
null_count_opt, min_is_exact, max_is_exact, min_bytes_opt, max_bytes_opt}` (`file/statistics.rs:406-467`).

A.11 Delivery: `scripts/apply-starrocks-patches.sh` (glob order, `git apply --check` / `--reverse --check`);
`pixi.toml:94` `apply-starrocks-patches`, `:197-215` `fe-build` (`./build.sh --fe`), `:136-148` `cn-test`,
`cn-test-no-engine`; `fe/fe-core/pom.xml:1136-1149` regenerates the proto Java classes; `Config.java:1212-1223`
is the pattern for a mutable FE config in an existing patch; `run-abc.sh:799` is the existing `SET GLOBAL` precedent.
