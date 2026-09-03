Snapshot of the fix 3 (real FILES() cardinalities) worktree at 2026-09-03 18:27 UTC, taken before the box shutdown while the
implementer was still working (no commit yet). `worktree.diff` = tracked changes in the sirius worktree (CN file_schema.rs,
compute_node_service.rs, build.rs, CI workflow, bench.sh, docs); `starrocks-submodule.diff` = the FE/proto changes inside the
StarRocks submodule (StatisticsCalculator, TableFunctionTable, Config, FileScanNode, proto, tests); the two `*.patch` files are
the same FE/proto changes exported for `experimental/starrocks/patches/` (applied by apply-starrocks-patches.sh);
`FilesScanStatisticsTest.java` is the new FE test (2/2 passing at 18:05 per the implementer notes). Apply on `perf/profile-sf1000`
(45dab3be). If the branch `fix/files-cardinality` on aocsa/sirius has a commit, prefer the branch.
