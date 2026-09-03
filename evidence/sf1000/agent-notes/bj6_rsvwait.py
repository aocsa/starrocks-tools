#!/usr/bin/env python3
"""For tasks that spent > thresh ms in Reserving: requested bytes, sum of other live reservations at that instant,
live data_batch capacity at that instant, which operator/pipeline. Also histogram of per-task requested_bytes."""
import sys, json, glob, collections, bisect
arm = sys.argv[1]; targets = set(sys.argv[2:]); thresh_ms = 20
base = "/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000"
runs = json.load(open(f"{base}/{arm}-cnlog.json")); run_of = {q[-12:]: r for r, o in runs.items() for q in o.get("query_ids", [])}
def rows(s, kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: yield json.loads(l)
            except Exception: pass
def state(j):
    st = j["data"].get("state", j["data"])
    if isinstance(st, str): return st, {}
    k = next(iter(st)); v = st[k]; return k, (v if isinstance(v, dict) else {})
for s in sorted(glob.glob(f"{base}/{arm}/quent/.cn*/*/")):
    cn = s.rstrip('/').split('/')[-2]
    q = {}
    for j in rows(s, "query"):
        k, v = state(j)
        if k == "Init": q[j["id"]] = (v.get("query_label") or v.get("instance_name") or "")
    plan_q = {j["id"]: (v.get("parent") or {}).get("query_id") for j in rows(s, "plan") for k, v in [state(j)] if k == "Declaration"}
    pipe = {}
    for j in rows(s, "operator"):
        k, v = state(j)
        if k == "Declaration": pipe[j["id"]] = (plan_q.get(v.get("plan_id")), v.get("instance_name"))
    tasks = collections.defaultdict(list)
    for j in rows(s, "task"):
        k, v = state(j); tasks[j["id"]].append((j["timestamp"], k, v))
    recs = []  # (key, t_res, t_res_end, t_end, req, ops, tid)
    for tid, ev in tasks.items():
        ev.sort(); pu = next((v.get("pipeline_uuid") for _, k, v in ev if k == "Created"), None)
        qid, pname = pipe.get(pu, (None, None)); lab = q.get(qid, ""); key = run_of.get(lab.split(":")[0][-12:], lab.split(":")[0][-12:])
        if targets and not any(key.startswith(t) for t in targets): continue
        t_res = t_res_end = t_end = None; req = 0; ops = []
        for i, (ts, k, v) in enumerate(ev):
            nxt = ev[i + 1][0] if i + 1 < len(ev) else ts
            if k == "Reserving": t_res = ts; t_res_end = nxt; req = v.get("requested_bytes") or 0
            if k == "Computing": ops.append(v.get("instance_name"))
            if k in ("Finalizing", "Exit"): t_end = ts
        if t_res is not None: recs.append((key, t_res, t_res_end, t_end or t_res_end, req, ops, tid, lab[-4:]))
    batches = []
    for j in rows(s, "data_batch"):
        pass
    by = collections.defaultdict(list)
    for j in rows(s, "data_batch"):
        k, v = state(j); by[j["id"]].append((j["timestamp"], k, v))
    biv = []
    for bid, ev in by.items():
        t = {k: ts for ts, k, v in ev}; cap = max((((v.get("memory") or {}).get("capacity") or {}).get("capacity_bytes") or 0) for _, k, v in ev)
        name = next((v.get("instance_name") for _, k, v in ev if k == "Constructed"), None)
        if t.get("Constructed") and name != "staging_lease": biv.append((t["Constructed"], t.get("Destructed") or float("inf"), cap))
    hist = collections.defaultdict(collections.Counter)
    for key, t0, t1, te, req, ops, tid, frag in recs: hist[key][round(req / 2**30, 1)] += 1
    for key in sorted(hist):
        top = sorted(hist[key].items(), key=lambda x: -x[1])[:6]
        print(f"{arm} {key} {cn}: requested_bytes GiB histogram (top): {top}  n={sum(hist[key].values())}")
    for key, t0, t1, te, req, ops, tid, frag in sorted(recs, key=lambda r: r[1]):
        wait = (t1 - t0) / 1e9 * 1000
        if wait < thresh_ms: continue
        conc = sum(r[4] for r in recs if r[6] != tid and r[1] <= t0 < r[3])
        live = sum(c for a, b, c in biv if a <= t0 <= b)
        print(f"  {key} {cn} frag=..{frag} task Reserving {wait:6.1f} ms: requested {req/2**30:5.1f} GiB; other live reservations at that instant {conc/2**30:5.1f} GiB; live data_batch capacity {live/2**30:5.1f} GiB; ops={ops[:3]}")
