"""Convert the decimal-typed TPC-H parquet under /scratch/sirius/datasets/tpch_sf<N> into f64-typed
copies (every DECIMAL column cast to DOUBLE, everything else unchanged), in two layouts:
  <out>/tpch_sf<N>_f64_1file/<table>/part.0.parquet         one file per table
  <out>/tpch_sf<N>_f64_multi/<table>/part.<i>.parquet       lineitem split into LINEITEM_PARTS files
Row groups stay ~1M rows so byte-range splits have several row groups per file."""
import duckdb, os, sys, time
sf = int(sys.argv[1]); out_root = sys.argv[2]; parts = int(sys.argv[3])
src = f"/scratch/sirius/datasets/tpch_sf{sf}"
tables = ["region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem"]
con = duckdb.connect(); con.execute("PRAGMA threads=64"); con.execute("SET preserve_insertion_order=true")
def select_list(table):
    cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}/{table}/*.parquet')").fetchall()
    return ", ".join(f'CAST("{c}" AS DOUBLE) AS "{c}"' if t.startswith("DECIMAL") else f'"{c}"' for c, t, *_ in cols)
for layout in ("1file", "multi"):
    root = f"{out_root}/tpch_sf{sf}_f64_{layout}"
    for t in tables:
        d = f"{root}/{t}"; os.makedirs(d, exist_ok=True)
        sel = select_list(t); n = con.execute(f"SELECT count(*) FROM read_parquet('{src}/{t}/*.parquet')").fetchone()[0]
        t0 = time.time()
        if layout == "multi" and t == "lineitem":
            k = (n + parts - 1) // parts
            for i in range(parts):
                con.execute(f"COPY (SELECT {sel} FROM read_parquet('{src}/{t}/*.parquet') ORDER BY l_orderkey, l_linenumber LIMIT {k} OFFSET {i*k}) TO '{d}/part.{i}.parquet' (FORMAT parquet, ROW_GROUP_SIZE 1000000)")
        else:
            con.execute(f"COPY (SELECT {sel} FROM read_parquet('{src}/{t}/*.parquet')) TO '{d}/part.0.parquet' (FORMAT parquet, ROW_GROUP_SIZE 1000000)")
        m = con.execute(f"SELECT count(*) FROM read_parquet('{d}/*.parquet')").fetchone()[0]
        assert m == n, (t, m, n)
        print(f"sf{sf} {layout} {t}: rows={n} files={len(os.listdir(d))} {time.time()-t0:.1f}s", flush=True)
