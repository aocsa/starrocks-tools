**Draft: review unfinished, no end-to-end run yet.** Base is `perf/profile-sf1000`. Ships as a checked-in StarRocks patch plus a CN change; the submodule pointer does not move.

## What
The FE planned every FILES() scan at 1 row (`StatisticsCalculator.computeFileScanNode`): all 22 TPC-H plans had every cardinality at 1, so join sides, BROADCAST vs SHUFFLE and CTE decisions were data-blind, and one CN broadcast 3.79 billion lineitem rows into q04's semi join.

Commit 1 (FE, `patches/files-scan-row-count.patch`): `StatisticsCalculator` asks `TableFunctionTable` for the count: the parquet footer total when the compute node reports one, else total file bytes divided by the typed row width of the inferred schema. Column statistics stay unknown. A mutable FE config `files_scan_estimate_row_count` (default true) restores the 1-row plans for an `EXPLAIN COSTS` A/B on one running FE. The bench scripts set `cbo_cte_reuse=false`, because real costs let the CBO keep a CTE materialised and the CN refuses `MULTI_CAST_DATA_STREAM_SINK`.

Commit 2 (CN + proto): the CN already opens every parquet footer for the schema RPC; it now sums `num_rows` and returns it in a new optional field `PGetFileSchemaResult.num_rows`, exact or absent. The FE prefers it, which makes leaf cardinalities exact (lineitem 5,999,989,709 instead of 1,230,652,806) and independent of the dataset copy.

## How I tested it
FE JUnit: `TableFunctionTableTest`, `StatisticsCalculatorTest`, the new `FilesScanStatisticsTest` (a 3072-byte fake table plans at 153 rows; with 1e9 vs 10 injected rows the small side builds and the FE commutes LEFT SEMI to RIGHT SEMI), `testFooterRowCountPreferred`; `fe-build` in 269 s. CN: 157 lib tests including the new schema tests, clippy with warnings denied, fmt.

## Left
The CN translator has no `RIGHT_SEMI_JOIN` arm yet, which the FE now produces, so the knob must stay off in a sweep until that lands. Gate A (`EXPLAIN COSTS` at 1 and 4 CNs) and the SF1000 arms (`fix/INTEGRATION.md` V4, V5) have not run. This does not by itself make q05 q08 q09 q17 q18 q21 pass on 1 CN. Spec: `fix/files-cardinality-SPEC.md` in aocsa/starrocks-tools.
