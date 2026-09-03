# Piece 10 — bench kit (D2a/D2b/D2c subset for the Q1/Q6 demo)
Commit title: `chore(bench): TPC-H q01/q06 harness, DuckDB oracle and one-CN-per-GPU launcher`

Plan references: golden-crane.md "The map" rows D2a, D2b, D2c and carve-sheet bullet **D2a/D2b** (every relative link must resolve inside the tree: rumdl MD057); "Verification" section (the launcher and harness commands the end-to-end run uses).

Why: the end-to-end proof (one CN per GPU, TPC-H q01/q06 against a DuckDB oracle) needs a launcher, a harness, an oracle, a comparator and a per-CN distribution check that live in the tree.

Take from SOT (paths relative to the repo root; `git show aocsa/feat/pin-table-cn:<path>`):
- `experimental/starrocks/benchmarks/cluster8.sh` (whole) with two fixes from the carve sheet: `unset CUDA_VISIBLE_DEVICES` before launching (an exported value wins over `--gpu-device` and collapses every CN onto GPU 0) and `export SIRIUS_QUERY_WATCHDOG_SECS=${SIRIUS_QUERY_WATCHDOG_SECS:-0}` so the knob reaches the CNs; keep `TOOLS_DIR` overridable as SOT has it.
- `experimental/starrocks/benchmarks/tpch/bench.sh`, `experimental/starrocks/benchmarks/tpch/queries/q01.sql`, `experimental/starrocks/benchmarks/tpch/queries/q06.sql`, `experimental/starrocks/benchmarks/tpch/README.md` (trim the README to what ships: q01/q06, bench.sh, the oracle/compare tools, cluster8.sh; remove or rewrite links to files not in this tree, e.g. QUERY-DEVIATIONS.md, analyze.py, run-comparison.sh, setup-engine-b.sh, the other 20 queries).
- `experimental/starrocks/tools/oracle.py` and `experimental/starrocks/tools/compare.py`, relocated from `bench/rtxpro6000-2gpu/tools/`. In oracle.py replace the `/opt/dlami/nvme/duckdb-tmp` default with a portable default (`$TMPDIR` or `tempfile.gettempdir()`), keep the `ORACLE_TMP`/`ORACLE_MEM`/`ORACLE_THREADS` overrides and drop the SF500 root-volume comment. bench.sh: if wiring compare.py in is more than a few lines, leave bench.sh as SOT and document the two-step (bench, then compare) in the README.
- `bench/common/gen-tpch.sh` (whole) and `bench/common/RETARGETING.md` only if gen-tpch.sh's header links to it (else skip it).
- `experimental/starrocks/scripts/cn-distribution.py` (whole).
- No configs/gb200-*, no run-abc.sh, no pinned kit, no other queries.

Verify: `bash -n` on every shell script; `shellcheck` if available in the pixi env (report if not); `python3 -m py_compile` on the .py files; `pixi run --manifest-path /home/prestouser/aocsa/sirius-stacks/pixi.toml pre-commit run --files <the new files>` (pre-commit skips `experimental/**`, so run rumdl on the markdown directly via `pixi run ... rumdl check <file>` if rumdl is present) and fix what it flags; `git diff HEAD aocsa/feat/pin-table-cn -- <each copied file>` shows only the fixes above.
