#!/usr/bin/env python3
"""Backpressure / estimation view of Quent ndjson sessions.
usage: quent_bp.py <session_dir>... [--json out.json]
Per query label (CN labels are '<query id>:<fragment instance id>'; the query id prefix groups fragments) it reports:
  wall, tasks, per-state time (Queued, Routing, Reserving, Preparing, Computing, Finalizing) summed over tasks,
  Reserving requested/peak_estimate vs Computing peak_allocated (memory estimation accuracy),
  data batches: count, bytes, dwell (Stationary -> hop/Destructed), staging leases: count, bytes, held time, InTransit time,
  max concurrent leases, stream hops (relayed/pushed) and scan_split_read statistics.
Sessions from several CNs may be passed together; results are keyed by label and session."""
import sys, os, json, glob, collections
paths = [p for p in sys.argv[1:] if not p.startswith('--')]
jout = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
paths = [p for i, p in enumerate(sys.argv[1:], 1) if not p.startswith('--') and not sys.argv[i - 1].startswith('--')]
# optional: name CN query groups by (query.run) via cnlog_extract.py's json (query_ids are the last 12 chars of the StarRocks query id)
run_of = {}
if '--runs' in sys.argv:
    for run, o in json.load(open(sys.argv[sys.argv.index('--runs') + 1])).items():
        for qid in o.get("query_ids", []): run_of[qid] = run
def name_of(key):
    return run_of.get(key[-12:], key)
def rows(s, kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: j = json.loads(l)
            except Exception: continue
            yield j
def state(j):
    st = j["data"].get("state", j["data"])
    if isinstance(st, str): return st, {}
    k = next(iter(st)); v = st[k]
    return k, (v if isinstance(v, dict) else {})
import re as _re
def qkey(label):
    if not label: return "unnamed"
    if ":" in label:
        head, tail = label.split(":", 1)
        # CN labels are '<StarRocks query id>:<fragment instance id>' (group by query id); standalone labels are
        # '<tag>:qNN.rK' (group by the full label)
        if _re.fullmatch(r'q\d+\.r\d+', tail): return label
        return head
    return label
out = {}
for s in paths:
    sess = os.path.basename(os.path.normpath(s)); cn = os.path.basename(os.path.dirname(os.path.normpath(s)))
    # queries
    q = {}
    for j in rows(s, "query"):
        k, v = state(j); e = q.setdefault(j["id"], {"t0": j["timestamp"], "t1": j["timestamp"], "label": None, "session_label": None})
        e["t0"] = min(e["t0"], j["timestamp"]); e["t1"] = max(e["t1"], j["timestamp"])
        if k == "Init":
            e["label"] = v.get("query_label") or v.get("instance_name"); e["session_label"] = v.get("session_label")
    plan_q = {}
    for j in rows(s, "plan"):
        k, v = state(j)
        if k == "Declaration": plan_q[j["id"]] = (v.get("parent") or {}).get("query_id")
    pipe = {}
    for j in rows(s, "operator"):
        k, v = state(j)
        if k == "Declaration": pipe[j["id"]] = (plan_q.get(v.get("plan_id")), v.get("instance_name"), v.get("type_name"))
    # scan_split_read statistics live as Statistics records (any dir); collect by executor/pipeline id
    scan_reads = collections.defaultdict(lambda: {"n": 0, "bytes": 0, "ns": 0})
    for d in os.listdir(s):
        if not os.path.isdir(f"{s}/{d}"): continue
        for j in rows(s, d):
            dd = j["data"]
            if isinstance(dd, dict) and "Statistics" in dd:
                attrs = {a["key"]: next(iter(a["value"].values())) for a in dd["Statistics"]["custom_attributes"]}
                if attrs.get("event") == "scan_split_read":
                    qid = plan_q.get(j["id"]) or pipe.get(j["id"], (None,))[0]
                    r = scan_reads[qid]; r["n"] += 1
                    r["bytes"] += int(attrs.get("bytes") or attrs.get("read_bytes") or 0); r["ns"] += int(attrs.get("elapsed_ns") or attrs.get("duration_ns") or 0)
    # tasks
    tasks = collections.defaultdict(list)
    for j in rows(s, "task"):
        k, v = state(j); tasks[j["id"]].append((j["timestamp"], k, v))
    per_q = collections.defaultdict(lambda: {"tasks": 0, "state_s": collections.Counter(), "ops": collections.defaultdict(lambda: [0, 0.0, 0]),
                                             "reserve_req": 0, "reserve_peak_est": 0, "peak_alloc": 0, "input_bytes": 0})
    for tid, ev in tasks.items():
        ev.sort(); pu = next((v["pipeline_uuid"] for _, k, v in ev if k == "Created"), None)
        qid = pipe.get(pu, (None,))[0]; a = per_q[qid]; a["tasks"] += 1
        maxpeak = 0
        for i, (ts, k, v) in enumerate(ev):
            nxt = ev[i + 1][0] if i + 1 < len(ev) else ts; dt = (nxt - ts) / 1e9
            a["state_s"][k] += dt
            if k == "Computing":
                o = a["ops"][v.get("instance_name")]; o[0] += 1; o[1] += dt; o[2] += v.get("input_bytes") or 0
                maxpeak = max(maxpeak, v.get("peak_allocated_bytes") or 0)
            if k == "Reserving":
                a["reserve_req"] += v.get("requested_bytes") or 0; a["reserve_peak_est"] += v.get("peak_estimate") or 0
            if k == "Preparing": a["input_bytes"] += v.get("input_bytes") or 0
        a["peak_alloc"] += maxpeak
    # batches and leases
    by = collections.defaultdict(list)
    for j in rows(s, "data_batch"):
        k, v = state(j); by[j["id"]].append((j["timestamp"], k, v))
    leases = []; batches = []
    for bid, ev in by.items():
        ev.sort(); name = next((v.get("instance_name") for _, k, v in ev if k == "Constructed"), None)
        pu = next((v.get("producer_pipeline_uuid") for _, k, v in ev if k == "Constructed"), None)
        t = {k: ts for ts, k, v in ev}; cap = max((((v.get("memory") or {}).get("capacity") or {}).get("capacity_bytes") or 0) for _, k, v in ev)
        qid_b = pipe.get(pu, (None,))[0]
        if qid_b is None and name == "staging_lease":
            # leases carry no producer pipeline: attribute by time to the query whose span contains the lease start
            tc = t.get("Constructed")
            for qq, info in q.items():
                if tc is not None and info["t0"] - 5e8 <= tc <= info["t1"] + 5e8: qid_b = qq; break
        rec = {"id": bid, "qid": qid_b, "bytes": cap, "t_constructed": t.get("Constructed"), "t_stationary": t.get("Stationary"),
               "t_intransit": t.get("InTransit"), "t_destructed": t.get("Destructed"), "states": [k for _, k, _ in ev]}
        (leases if name == "staging_lease" else batches).append(rec)
    # placements (hops)
    hops = collections.Counter(); hop_bytes = collections.Counter(); hop_batch_t = {}
    for j in rows(s, "batch_placement"):
        k, v = state(j); o = v.get("origin")
        if o and str(o).startswith("stream_"):
            hops[o] += 1; hop_bytes[o] += ((v.get("tier") or {}).get("capacity") or {}).get("capacity_bytes") or 0
            hop_batch_t[v.get("batch_id")] = j["timestamp"]
    # dwell: sink Stationary -> hop placement (local) or InTransit/Destructed
    def dwell(b):
        t0 = b["t_stationary"] or b["t_constructed"]; t1 = b["t_intransit"] or b["t_destructed"]
        return (t1 - t0) / 1e9 if t0 and t1 and t1 >= t0 else None
    # lease concurrency
    def max_conc(recs):
        ev = []
        for r in recs:
            if r["t_constructed"] and r["t_destructed"]: ev += [(r["t_constructed"], 1), (r["t_destructed"], -1)]
        ev.sort(); c = m = 0
        for _, d in ev: c += d; m = max(m, c)
        return m
    for qid, info in q.items():
        lab = info["label"]; key = f"{qkey(lab)}"; entry = out.setdefault(key, {"fragments": []})
        a = per_q.get(qid, None)
        qb = [b for b in batches if b["qid"] == qid]; ql = [l for l in leases if l["qid"] == qid]
        dw = [d for d in (dwell(b) for b in qb) if d is not None]
        frag = {"cn": cn, "session": sess[-12:], "label": lab, "wall_s": (info["t1"] - info["t0"]) / 1e9, "t0": info["t0"],
                "tasks": a["tasks"] if a else 0,
                "state_s": {k: round(v, 4) for k, v in (a["state_s"].items() if a else [])},
                "top_ops": sorted(((op, n, round(sec, 4), gb / 1e9) for op, (n, sec, gb) in (a["ops"].items() if a else [])), key=lambda x: -x[2])[:8],
                "reserve_requested_GB": (a["reserve_req"] if a else 0) / 1e9, "reserve_peak_est_GB": (a["reserve_peak_est"] if a else 0) / 1e9,
                "peak_alloc_sum_GB": (a["peak_alloc"] if a else 0) / 1e9, "input_GB": (a["input_bytes"] if a else 0) / 1e9,
                "batches": len(qb), "batch_GB": sum(b["bytes"] for b in qb) / 1e9,
                "batch_dwell_s": {"n": len(dw), "sum": round(sum(dw), 4), "max": round(max(dw), 4) if dw else 0},
                "leases": len(ql), "lease_GB": sum(l["bytes"] for l in ql) / 1e9,
                "lease_held_s": round(sum(((l["t_destructed"] or l["t_constructed"]) - l["t_constructed"]) / 1e9 for l in ql), 4),
                "lease_intransit_s": round(sum(((l["t_destructed"] or l["t_intransit"]) - l["t_intransit"]) / 1e9 for l in ql if l["t_intransit"]), 4),
                "lease_max_concurrent": max_conc(ql),
                "scan_reads": scan_reads.get(qid)}
        entry["fragments"].append(frag)
    # session-wide hop counters (not per query: placements carry batch ids only)
    out.setdefault("_session_" + cn + "_" + sess[-12:], {})["hops"] = {k: [hops[k], hop_bytes[k] / 1e9] for k in hops}
    out["_session_" + cn + "_" + sess[-12:]]["leases_total"] = len(leases); out["_session_" + cn + "_" + sess[-12:]]["lease_max_concurrent"] = max_conc(leases)
if jout: json.dump({name_of(k) if not k.startswith("_") else k: v for k, v in out.items()}, open(jout, "w"), indent=1, default=str)
for key, e in sorted(out.items(), key=lambda x: (x[0].startswith("_"), min((f["t0"] for f in x[1].get("fragments", [])), default=0))):
    if key.startswith("_"): print(f"## {key}: {json.dumps(e)}"); continue
    key_disp = name_of(key)
    fr = e["fragments"]; wall = (max(f["t0"] + f["wall_s"] * 1e9 for f in fr) - min(f["t0"] for f in fr)) / 1e9
    print(f"## {key_disp} [{key[-12:]}]: fragments={len(fr)} span={wall:.3f}s tasks={sum(f['tasks'] for f in fr)} leases={sum(f['leases'] for f in fr)} lease_GB={sum(f['lease_GB'] for f in fr):.2f} lease_held={sum(f['lease_held_s'] for f in fr):.3f}s batches={sum(f['batches'] for f in fr)} dwell_sum={sum(f['batch_dwell_s']['sum'] for f in fr):.3f}s")
    for f in sorted(fr, key=lambda f: f["t0"]):
        st = f["state_s"]; print(f"   [{f['cn']}] {str(f['label'])[-40:]:40s} wall={f['wall_s']:.3f}s tasks={f['tasks']} Q={st.get('Queued',0):.3f} Rsv={st.get('Reserving',0):.3f} Prep={st.get('Preparing',0):.3f} Comp={st.get('Computing',0):.3f} Fin={st.get('Finalizing',0):.3f} req={f['reserve_requested_GB']:.1f}GB est={f['reserve_peak_est_GB']:.1f}GB alloc={f['peak_alloc_sum_GB']:.1f}GB in={f['input_GB']:.1f}GB leases={f['leases']}/{f['lease_GB']:.2f}GB held={f['lease_held_s']:.3f}s maxconc={f['lease_max_concurrent']} batches={f['batches']} dwell_max={f['batch_dwell_s']['max']:.3f}s")
        for op, n, sec, gb in f["top_ops"][:4]: print(f"        {op:<22} n={n:<4} sum={sec:8.3f}s in={gb:7.2f}GB")
