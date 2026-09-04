#!/usr/bin/env python3
"""Per-run, per-operator Quent profile for one arm, mapped to (query, run) by the task Created timestamp
against runs.csv windows (needed because the demo-plus tree labels every Quent query 'sirius_streaming_fragment').
usage: profile_diff.py <arm_dir> <out.json>
arm_dir has runs/runs.csv, quent/.cnN/<session>/{task,operator,plan,query,data_batch}/*.ndjson, cluster.log
Per run: tasks, state sums (Queued, Routing, Reserving, Preparing, Computing, Finalizing), per-operator Computing
[n, sec, input_bytes] keyed by instance_name and by type (name without '(id)'), Reserving requested / peak_estimate sums,
peak_alloc (max per task) sum, batches (count, bytes), staging leases (count, bytes, held s, max concurrent),
nixl transmits from cluster.log by time window (count, batches, bytes), per-CN breakdown of Computing and Queued."""
import sys, os, json, glob, csv, re, collections, datetime
arm, jout = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(f"{arm}/runs/runs.csv")))
def ts_ns(x): return datetime.datetime.strptime(x[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp() * 1e9
windows = []
for i, r in enumerate(rows):
    t0 = ts_ns(r['start_utc']); t1 = ts_ns(rows[i + 1]['start_utc']) if i + 1 < len(rows) else t0 + float(r['ms']) * 1e6 + 5e9
    windows.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def which(t):
    for a, b, n, _ in windows:
        if a - 5e8 <= t < b: return n
    return None
def recs(sess, kind):
    for f in glob.glob(f"{sess}/{kind}/*.ndjson"):
        for l in open(f):
            try: j = json.loads(l)
            except Exception: continue
            yield j
def state(j):
    st = j["data"].get("state", j["data"])
    if isinstance(st, str): return st, {}
    k = next(iter(st)); v = st[k]
    return k, (v if isinstance(v, dict) else {})
def new_run():
    return {"tasks": 0, "state_s": collections.Counter(), "ops": collections.defaultdict(lambda: [0, 0.0, 0]),
            "reserve_req": 0, "reserve_peak_est": 0, "peak_alloc": 0, "input_bytes": 0, "fragments": 0,
            "per_cn": collections.defaultdict(lambda: {"comp": 0.0, "queued": 0.0, "tasks": 0, "scan": 0.0}),
            "batches": 0, "batch_bytes": 0, "leases": 0, "lease_bytes": 0, "lease_held_s": 0.0, "lease_ev": [],
            "tx": 0, "tx_batches": 0, "tx_bytes": 0, "q_t0": None, "q_t1": None}
per = collections.defaultdict(new_run)
for sess in sorted(glob.glob(f"{arm}/quent/.cn*/*/")):
    cn = os.path.basename(os.path.dirname(sess.rstrip('/')))
    # queries -> windows (span)
    q = {}
    for j in recs(sess, "query"):
        e = q.setdefault(j["id"], [j["timestamp"], j["timestamp"]]); e[0] = min(e[0], j["timestamp"]); e[1] = max(e[1], j["timestamp"])
    for qid, (a, b) in q.items():
        run = which(a)
        if not run: continue
        p = per[run]; p["fragments"] += 1
        p["q_t0"] = a if p["q_t0"] is None else min(p["q_t0"], a); p["q_t1"] = b if p["q_t1"] is None else max(p["q_t1"], b)
    tasks = collections.defaultdict(list)
    for j in recs(sess, "task"):
        k, v = state(j); tasks[j["id"]].append((j["timestamp"], k, v))
    for tid, ev in tasks.items():
        ev.sort(); run = which(ev[0][0])
        if not run: continue
        p = per[run]; p["tasks"] += 1; p["per_cn"][cn]["tasks"] += 1; maxpeak = 0
        for i, (ts, k, v) in enumerate(ev):
            nxt = ev[i + 1][0] if i + 1 < len(ev) else ts; dt = (nxt - ts) / 1e9
            p["state_s"][k] += dt
            if k == "Computing":
                o = p["ops"][v.get("instance_name")]; o[0] += 1; o[1] += dt; o[2] += v.get("input_bytes") or 0
                maxpeak = max(maxpeak, v.get("peak_allocated_bytes") or 0); p["per_cn"][cn]["comp"] += dt
                if str(v.get("instance_name", "")).startswith("GPU_SCAN"): p["per_cn"][cn]["scan"] += dt
            elif k == "Queued": p["per_cn"][cn]["queued"] += dt
            elif k == "Reserving":
                p["reserve_req"] += v.get("requested_bytes") or 0; p["reserve_peak_est"] += v.get("peak_estimate") or 0
            elif k == "Preparing": p["input_bytes"] += v.get("input_bytes") or 0
        p["peak_alloc"] += maxpeak
    by = collections.defaultdict(list)
    for j in recs(sess, "data_batch"):
        k, v = state(j); by[j["id"]].append((j["timestamp"], k, v))
    for bid, ev in by.items():
        ev.sort(); name = next((v.get("instance_name") for _, k, v in ev if k == "Constructed"), None)
        t = {k: ts for ts, k, v in ev}; cap = max((((v.get("memory") or {}).get("capacity") or {}).get("capacity_bytes") or 0) for _, k, v in ev)
        tc = t.get("Constructed") or ev[0][0]; run = which(tc)
        if not run: continue
        p = per[run]
        if name == "staging_lease":
            p["leases"] += 1; p["lease_bytes"] += cap; p["lease_held_s"] += ((t.get("Destructed") or tc) - tc) / 1e9
            if t.get("Destructed"): p["lease_ev"] += [(tc, 1), (t["Destructed"], -1)]
        else:
            p["batches"] += 1; p["batch_bytes"] += cap
# nixl transmits by time window (both log vocabularies: with or without query_id)
ANSI = re.compile(r'\x1b\[[0-9;]*m'); TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z')
for l in open(f"{arm}/cluster.log", errors="replace"):
    if 'transmitted batches via nixl' not in l: continue
    l = ANSI.sub('', l); m = TS.match(l)
    if not m: continue
    run = which(ts_ns(m.group(1)))
    if not run: continue
    p = per[run]; p["tx"] += 1
    mb = re.search(r'batches=(\d+)', l); mby = re.search(r' bytes=(\d+)', l)
    p["tx_batches"] += int(mb.group(1)) if mb else 0; p["tx_bytes"] += int(mby.group(1)) if mby else 0
out = {}
for t0, t1, name, r in windows:
    p = per.get(name)
    if not p: out[name] = {"status": r["status"], "ms": int(r["ms"]), "note": "no quent events"}; continue
    ev = sorted(p["lease_ev"]); c = mx = 0
    for _, d in ev: c += d; mx = max(mx, c)
    ops_type = collections.defaultdict(lambda: [0, 0.0, 0])
    for op, (n, s, b) in p["ops"].items():
        t = re.sub(r'\(\d+\)$', '', str(op)); o = ops_type[t]; o[0] += n; o[1] += s; o[2] += b
    out[name] = {"status": r["status"], "ms": int(r["ms"]), "phase": r["phase"], "fragments": p["fragments"], "tasks": p["tasks"],
                 "span_s": ((p["q_t1"] - p["q_t0"]) / 1e9) if p["q_t0"] else None,
                 "state_s": {k: round(v, 4) for k, v in p["state_s"].items()},
                 "ops": {op: [n, round(s, 4), b] for op, (n, s, b) in sorted(p["ops"].items(), key=lambda x: -x[1][1])},
                 "ops_type": {op: [n, round(s, 4), b] for op, (n, s, b) in sorted(ops_type.items(), key=lambda x: -x[1][1])},
                 "reserve_req_GB": p["reserve_req"] / 1e9, "reserve_peak_est_GB": p["reserve_peak_est"] / 1e9, "peak_alloc_GB": p["peak_alloc"] / 1e9,
                 "input_GB": p["input_bytes"] / 1e9, "batches": p["batches"], "batch_GB": p["batch_bytes"] / 1e9,
                 "leases": p["leases"], "lease_GB": p["lease_bytes"] / 1e9, "lease_held_s": round(p["lease_held_s"], 4), "lease_max_conc": mx,
                 "tx": p["tx"], "tx_batches": p["tx_batches"], "tx_GB": p["tx_bytes"] / 1e9,
                 "per_cn": {cn: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()} for cn, d in sorted(p["per_cn"].items())}}
json.dump(out, open(jout, "w"), indent=1)
for name, o in out.items():
    if "note" in o: print(f"{name}: {o['status']} {o['ms']}ms ({o['note']})"); continue
    st = o["state_s"]
    print(f"{name}: {o['status']} {o['ms']}ms frags={o['fragments']} tasks={o['tasks']} span={(o['span_s'] or 0):.3f}s Q={st.get('Queued',0):.2f} Rsv={st.get('Reserving',0):.2f} Prep={st.get('Preparing',0):.2f} Comp={st.get('Computing',0):.2f} Fin={st.get('Finalizing',0):.2f} req={o['reserve_req_GB']:.1f}GB est={o['reserve_peak_est_GB']:.1f}GB alloc={o['peak_alloc_GB']:.1f}GB in={o['input_GB']:.1f}GB batches={o['batches']}/{o['batch_GB']:.2f}GB leases={o['leases']}/{o['lease_GB']:.2f}GB held={o['lease_held_s']:.2f}s maxconc={o['lease_max_conc']} nixl={o['tx']}/{o['tx_batches']}b/{o['tx_GB']:.2f}GB")
    for op, (n, s, b) in list(o["ops_type"].items())[:8]: print(f"      {op:<20} n={n:<5} sum={s:8.3f}s in={b/1e9:8.2f}GB")
