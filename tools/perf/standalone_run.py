#!/usr/bin/env python3
"""Standalone Sirius (DuckDB + sirius extension, one process, one GPU) over the SAME SQL files the
StarRocks bench kit uses (benchmarks/tpch/queries/qNN.sql with FILES() -> read_parquet), mirroring
performance_test.py's GPU path: allow_unsigned_extensions, LOAD extension, SET gpu_execution=true,
time con.execute().fetchall() with perf_counter. Writes <out>/runtimes.csv and <out>/qNN.r<i>.out
(mysql --batch format) so tools/compare.py can diff against the oracle.
usage: standalone_run.py <ext_path> <queries_dir> <tpch_data> <out_dir> <iterations> q01 q06 ..."""
import decimal, os, re, sys, time
import duckdb
ext, qdir, data, out, iters = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
names = sys.argv[6:]
os.makedirs(out, exist_ok=True)
FILES = re.compile(r'FILES\(\s*"path"\s*=\s*"file://([^"]+)"\s*,\s*"format"\s*=\s*"parquet"\s*\)', re.I)
def fmt(v):
    if v is None: return "NULL"
    if isinstance(v, decimal.Decimal): return format(v.normalize(), "f")
    if isinstance(v, float): return repr(v)
    return str(v)
con = duckdb.connect(":memory:", config={"allow_unsigned_extensions": "true"})
con.execute(f"LOAD '{ext}'")
con.execute("SET gpu_execution = true;")
with open(os.path.join(out, "runtimes.csv"), "w") as csv:
    csv.write("engine,query,iteration,runtime_s,rows\n")
    for n in names:
        sql = open(os.path.join(qdir, f"{n}.sql")).read().replace("__TPCH_DATA__", data)
        sql = FILES.sub(lambda m: f"read_parquet('{m.group(1)}')", sql).strip().rstrip(";")
        for i in range(iters + 1):   # i=0 is first contact (cold), i>=1 warm
            t0 = time.perf_counter()
            cur = con.execute(sql)
            rows = cur.fetchall()
            dt = time.perf_counter() - t0
            cols = [d[0] for d in cur.description]
            with open(os.path.join(out, f"{n}.r{i}.out"), "w") as f:
                f.write("\t".join(cols) + "\n")
                for r in rows: f.write("\t".join(fmt(v) for v in r) + "\n")
            csv.write(f"sirius,{n},{i},{dt:.6f},{len(rows)}\n"); csv.flush()
            print(f"{n} r{i} {'cold' if i == 0 else 'warm'} {dt*1000:.0f}ms rows={len(rows)}", flush=True)
