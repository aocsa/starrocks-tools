#!/usr/bin/env python3
"""Per-query, per-pipeline, per-operator summary of one Quent session dir (ndjson exporter).
usage: quent_summary.py <session_dir> [label]"""
import json, sys, glob, collections
s = sys.argv[1]; label = sys.argv[2] if len(sys.argv) > 2 else s
def rows(kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            j = json.loads(l); st = j["data"].get("state", j["data"]); k = st if isinstance(st, str) else next(iter(st))
            yield j["id"], j["timestamp"], k, (st if isinstance(st, dict) else {})
# queries
q = collections.defaultdict(dict)
for qid, ts, k, st in rows("query"):
    q[qid].setdefault("t0", ts); q[qid]["t1"] = ts
    if k == "Init": q[qid]["name"] = st["Init"].get("instance_name")
    if k == "Executing": q[qid]["exec"] = ts
# pipelines (operator Declaration) -> query via plan
plan_q = {}
for pid, ts, k, st in rows("plan"):
    plan_q[pid] = st.get("Declaration", {}).get("parent", {}).get("query_id")
pipe = {}
for oid, ts, k, st in rows("operator"):
    d = st.get("Declaration", {}); pipe[oid] = (plan_q.get(d.get("plan_id")), d.get("instance_name"), d.get("type_name"))
# tasks
tasks = collections.defaultdict(list)
for tid, ts, k, st in rows("task"): tasks[tid].append((ts, k, st))
agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0]))  # (qid,pipe) -> op -> [n, secs, bytes]
pipe_tasks = collections.Counter(); queue_wait = collections.defaultdict(float); span = {}
for tid, ev in tasks.items():
    ev.sort(); pu = next((st["Created"]["pipeline_uuid"] for _, k, st in ev if k == "Created"), None)
    if pu is None: continue
    qid = pipe.get(pu, (None,))[0]; key = (qid, pu); pipe_tasks[key] += 1
    for i, (ts, k, st) in enumerate(ev):
        nxt = ev[i + 1][0] if i + 1 < len(ev) else ts
        if k == "Computing":
            c = st["Computing"]; a = agg[key][c.get("instance_name")]; a[0] += 1; a[1] += (nxt - ts) / 1e9; a[2] += c.get("input_bytes") or 0
        elif k in ("Queued",): queue_wait[key] += (nxt - ts) / 1e9
        elif k in ("Reserving", "Preparing", "Routing", "Finalizing"): agg[key]["_" + k][1] += (nxt - ts) / 1e9
    t0 = ev[0][0]; t1 = ev[-1][0]; sp = span.setdefault(key, [t0, t1]); sp[0] = min(sp[0], t0); sp[1] = max(sp[1], t1)
# data batches per producer pipeline
batches = collections.defaultdict(lambda: [0, 0])
for bid, ts, k, st in rows("data_batch"):
    if k == "Constructed": batches[st["Constructed"]["producer_pipeline_uuid"]][0] += 1
    if k == "Stationary":
        m = st["Stationary"].get("memory", {}).get("capacity", {}) or {}; 
print(f"##### {label}")
for qid, info in sorted(q.items(), key=lambda x: x[1]["t0"]):
    wall = (info["t1"] - info["t0"]) / 1e9
    print(f"query {qid[-12:]} {info.get('name')} wall={wall:.3f}s")
    for (qq, pu), ops in agg.items():
        if qq != qid: continue
        name = pipe[pu][1]; sp = span[(qq, pu)]
        print(f"  pipeline {pipe[pu][2]} [{name}] tasks={pipe_tasks[(qq,pu)]} span={(sp[1]-sp[0])/1e9:.3f}s queue_wait_sum={queue_wait[(qq,pu)]:.3f}s batches_out={batches[pu][0]}")
        for op, (n, secs, b) in sorted(ops.items(), key=lambda x: -x[1][1]):
            if op.startswith("_"): print(f"      {op[1:]:<16} sum={secs:8.3f}s"); continue
            print(f"      {op:<20} n={n:4d} sum={secs:8.3f}s avg={secs/max(n,1)*1000:8.1f}ms input_GB={b/1e9:7.2f}")
