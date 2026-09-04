#!/usr/bin/env python3
"""Compare two profile_diff.py JSONs (baseline, candidate) on the warm runs (r1, r2 mean) of the queries both ran.
Prints per query: wall, engine span, Computing total, Queued, GPU_SCAN Computing, non-scan Computing, reserve requested, peak alloc,
nixl GB, and per-operator-type Computing deltas; flags |delta| > 10% (and > 50 ms absolute for time columns)."""
import json, sys, collections
base, cand = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
def warm(d, q):
    rs = [d[k] for k in (f"{q}.r1", f"{q}.r2") if k in d and d[k].get("status") == "pass" and "state_s" in d[k]]
    return rs
def mean(xs): return sum(xs) / len(xs) if xs else 0
def agg(rs):
    o = {"wall_ms": mean([r["ms"] for r in rs]), "span_s": mean([r["span_s"] or 0 for r in rs]),
         "comp_s": mean([r["state_s"].get("Computing", 0) for r in rs]), "queued_s": mean([r["state_s"].get("Queued", 0) for r in rs]),
         "prep_s": mean([r["state_s"].get("Preparing", 0) for r in rs]), "rsv_s": mean([r["state_s"].get("Reserving", 0) for r in rs]),
         "req_GB": mean([r["reserve_req_GB"] for r in rs]), "alloc_GB": mean([r["peak_alloc_GB"] for r in rs]),
         "tx_GB": mean([r["tx_GB"] for r in rs]), "tx_n": mean([r["tx"] for r in rs]), "tasks": mean([r["tasks"] for r in rs]),
         "frags": mean([r["fragments"] for r in rs]), "batches": mean([r["batches"] for r in rs]), "batch_GB": mean([r["batch_GB"] for r in rs])}
    ops = collections.defaultdict(list)
    for r in rs:
        for op, (n, s, b) in r["ops_type"].items(): ops[op].append(s)
    o["ops"] = {op: sum(v) / len(rs) for op, v in ops.items()}
    o["scan_s"] = o["ops"].get("GPU_SCAN", 0); o["nonscan_s"] = o["comp_s"] - o["scan_s"]
    return o
qs = sorted({k.split(".")[0] for k in base} & {k.split(".")[0] for k in cand})
def pct(a, b): return (b - a) / a * 100 if a else float("nan")
cols = ["wall_ms", "span_s", "comp_s", "scan_s", "nonscan_s", "queued_s", "req_GB", "alloc_GB", "tx_GB", "batches"]
print(f"{'query':6s} " + " ".join(f"{c:>26s}" for c in cols))
print(f"{'':6s} " + " ".join(f"{'base -> cand (delta%)':>26s}" for c in cols))
flags = []
for q in qs:
    rb, rc = warm(base, q), warm(cand, q)
    if not rb or not rc: print(f"{q}: base n={len(rb)} cand n={len(rc)} (skipped)"); continue
    b, c = agg(rb), agg(rc)
    cells = []
    for col in cols:
        p = pct(b[col], c[col]); mark = "*" if abs(p) > 10 and (not col.endswith("_s") or abs(c[col] - b[col]) > 0.05) and (col != "wall_ms" or abs(c[col] - b[col]) > 50) else " "
        cells.append(f"{b[col]:9.2f}->{c[col]:9.2f} ({p:+5.0f}%){mark}")
        if mark == "*": flags.append((q, col, b[col], c[col], p))
    print(f"{q:6s} " + " ".join(cells))
    ops = sorted(set(b["ops"]) | set(c["ops"]), key=lambda o: -max(b["ops"].get(o, 0), c["ops"].get(o, 0)))
    for op in ops:
        bo, co = b["ops"].get(op, 0), c["ops"].get(op, 0)
        if max(bo, co) < 0.05: continue
        p = pct(bo, co); mark = "*" if abs(p) > 10 and abs(co - bo) > 0.05 else " "
        print(f"        {op:<20} {bo:8.3f}s -> {co:8.3f}s ({p:+5.0f}%){mark}")
        if mark == "*": flags.append((q, "op:" + op, bo, co, p))
print("\nFLAGS (|delta|>10%, and >50 ms / >0.05 s absolute):")
for q, col, b, c, p in flags: print(f"  {q} {col}: {b:.3f} -> {c:.3f} ({p:+.0f}%)")
