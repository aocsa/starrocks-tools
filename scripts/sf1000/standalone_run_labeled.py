#!/usr/bin/env python3
"""Standalone Sirius over the bench-kit SQL (FILES() -> read_parquet) with a Quent label per run and a per-run interrupt
timeout. usage: standalone_run_labeled.py <ext> <queries_dir> <tpch_data> <out_dir> <runs_after_warmup> <timeout_s> <tag> q01 ...
Writes <out>/runs.csv (query,run,phase,status,ms,rows,start_utc) and <out>/qNN.rK.out (mysql --batch format)."""
import decimal, os, re, sys, time, threading, datetime
import duckdb
ext, qdir, data, out, runs, to, tag = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), float(sys.argv[6]), sys.argv[7]
names = sys.argv[8:]
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
with open(os.path.join(out, "runs.csv"), "w") as csv:
    csv.write("query,run,phase,status,ms,rows,start_utc\n")
    for n in names:
        sql = open(os.path.join(qdir, f"{n}.sql")).read().replace("__TPCH_DATA__", data)
        sql = FILES.sub(lambda m: f"read_parquet('{m.group(1)}')", sql).strip().rstrip(";")
        try:   # DuckDB/Sirius plan for the planning comparison (once per query, before timing)
            plan = con.execute("EXPLAIN " + sql).fetchall()
            open(os.path.join(out, f"{n}.explain.txt"), "w").write("\n".join(str(r[1]) for r in plan))
        except Exception as e:
            open(os.path.join(out, f"{n}.explain.txt"), "w").write("EXPLAIN failed: " + str(e)[:300])
        for i in range(runs + 1):
            phase = "cold" if i == 0 else "warm"
            label = f"{tag}:{n}.r{i}"
            start = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            timer = threading.Timer(to, con.interrupt); status = "pass"; rows = []
            t0 = time.perf_counter()
            try:
                con.execute(f"CALL sirius_set_query_label('{label}')")
                timer.start()
                cur = con.execute(sql); rows = cur.fetchall(); cols = [d[0] for d in cur.description]
            except Exception as e:
                msg = str(e).replace("\n", " ")
                status = "timeout" if "INTERRUPT" in msg.upper() else "fail"
                cols = []; err = msg[:300]
            finally:
                timer.cancel()
            dt = time.perf_counter() - t0
            with open(os.path.join(out, f"{n}.r{i}.out"), "w") as f:
                if cols:
                    f.write("\t".join(cols) + "\n")
                    for r in rows: f.write("\t".join(fmt(v) for v in r) + "\n")
            csv.write(f"{n},{i},{phase},{status},{dt*1000:.0f},{len(rows)},{start}\n"); csv.flush()
            print(f"{n} r{i} {phase} {status} {dt*1000:.0f}ms rows={len(rows)}" + ("" if status == "pass" else f" err={err}"), flush=True)
            if status != "pass": break
