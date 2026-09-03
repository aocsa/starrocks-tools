#!/usr/bin/env python3
"""BJ-6 check: per query run and CN, from raw Quent ndjson:
 max concurrent reservation bytes held (Reserving -> Finalizing/Exit, clamped 100GiB), max concurrent tasks in Computing,
 max single requested_bytes, count of Downgrading states, Finalizing success=false, max concurrent staging-lease bytes."""
import sys, os, json, glob, collections
arm = sys.argv[1]; targets = set(sys.argv[2:])
base = f"/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000"
runs = json.load(open(f"{base}/{arm}-cnlog.json"))
run_of = {}
for run, o in runs.items():
    for qid in o.get("query_ids", []): run_of[qid[-12:]] = run
CAP = 100 * 2**30
def rows(s, kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: yield json.loads(l)
            except Exception: pass
def state(j):
    st = j["data"].get("state", j["data"])
    if isinstance(st, str): return st, {}
    k = next(iter(st)); v = st[k]; return k, (v if isinstance(v, dict) else {})
def maxconc(iv):  # iv: list of (t0, t1, w)
    ev = []
    for t0, t1, w in iv:
        if t0 is None or t1 is None: continue
        ev += [(t0, 1, w), (t1, -1, w)]
    ev.sort(key=lambda e: (e[0], e[1])); c = m = 0; n = mn = 0
    for _, d, w in ev:
        c += d * w; n += d; m = max(m, c); mn = max(mn, n)
    return m, mn
for s in sorted(glob.glob(f"{base}/{arm}/quent/.cn*/*/")):
    cn = s.rstrip('/').split('/')[-2]
    q = {}
    for j in rows(s, "query"):
        k, v = state(j)
        if k == "Init": q[j["id"]] = (v.get("query_label") or v.get("instance_name") or "")
    plan_q = {}
    for j in rows(s, "plan"):
        k, v = state(j)
        if k == "Declaration": plan_q[j["id"]] = (v.get("parent") or {}).get("query_id")
    pipe = {}
    for j in rows(s, "operator"):
        k, v = state(j)
        if k == "Declaration": pipe[j["id"]] = plan_q.get(v.get("plan_id"))
    tasks = collections.defaultdict(list)
    for j in rows(s, "task"):
        k, v = state(j); tasks[j["id"]].append((j["timestamp"], k, v))
    per = collections.defaultdict(lambda: {"res_iv": [], "comp_iv": [], "max_req": 0, "downgrading": 0, "fin_fail": 0, "tasks": 0, "rsv_wait_max": 0.0, "q_iv": []})
    for tid, ev in tasks.items():
        ev.sort(); pu = next((v.get("pipeline_uuid") for _, k, v in ev if k == "Created"), None)
        qid = pipe.get(pu); lab = q.get(qid, ""); key = run_of.get(lab.split(":")[0][-12:], lab.split(":")[0][-12:])
        if targets and not any(key.startswith(t) for t in targets): continue
        a = per[(key, cn)]; a["tasks"] += 1
        t_res = t_end = t_comp0 = t_comp1 = None; req = 0
        for i, (ts, k, v) in enumerate(ev):
            nxt = ev[i + 1][0] if i + 1 < len(ev) else ts
            if k == "Reserving":
                t_res = ts; req = v.get("requested_bytes") or 0; a["max_req"] = max(a["max_req"], req); a["rsv_wait_max"] = max(a["rsv_wait_max"], (nxt - ts) / 1e9)
            if k == "Downgrading": a["downgrading"] += 1
            if k == "Computing":
                t_comp0 = ts if t_comp0 is None else t_comp0; t_comp1 = nxt
            if k == "Finalizing":
                t_end = ts
                if v.get("success") is False: a["fin_fail"] += 1
            if k == "Exit": t_end = ts
        if t_res is not None: a["res_iv"].append((t_res, t_end, min(req, CAP)))
        if t_comp0 is not None: a["comp_iv"].append((t_comp0, t_comp1, 1))
    # staging leases
    lease_iv = collections.defaultdict(list)
    by = collections.defaultdict(list)
    for j in rows(s, "data_batch"):
        k, v = state(j); by[j["id"]].append((j["timestamp"], k, v))
    spans = {}
    for qid, lab in q.items():
        key = run_of.get(lab.split(":")[0][-12:], lab.split(":")[0][-12:])
        pass
    # query spans from task timestamps
    qspan = collections.defaultdict(lambda: [None, None])
    for (key, c), a in per.items():
        for t0, t1, _ in a["res_iv"]:
            sp = qspan[key]; sp[0] = t0 if sp[0] is None else min(sp[0], t0); sp[1] = t1 if sp[1] is None else max(sp[1], t1 or t0)
    for bid, ev in by.items():
        ev.sort(); name = next((v.get("instance_name") for _, k, v in ev if k == "Constructed"), None)
        if name != "staging_lease": continue
        t = {k: ts for ts, k, v in ev}; cap = max((((v.get("memory") or {}).get("capacity") or {}).get("capacity_bytes") or 0) for _, k, v in ev)
        tc = t.get("Constructed"); td = t.get("Destructed")
        for key, (a0, a1) in qspan.items():
            if tc is not None and a0 is not None and a0 - 5e8 <= tc <= (a1 or a0) + 5e8:
                lease_iv[(key, cn)].append((tc, td, cap)); break
    for (key, c), a in sorted(per.items()):
        mres, ntask = maxconc(a["res_iv"]); _, mcomp = maxconc(a["comp_iv"]); ml, nl = maxconc(lease_iv.get((key, c), []))
        print(f"{arm} {key:8s} {c}: tasks={a['tasks']:4d} max_conc_reserved={mres/2**30:6.1f}GiB (n={ntask}) max_conc_computing={mcomp} max_single_req={a['max_req']/2**30:5.1f}GiB rsv_wait_max={a['rsv_wait_max']*1000:.1f}ms downgrading={a['downgrading']} fin_fail={a['fin_fail']} leases={len(lease_iv.get((key,c),[]))} max_conc_lease={ml/2**30:.2f}GiB (n={nl})")
