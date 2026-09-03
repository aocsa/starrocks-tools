#!/usr/bin/env python3
"""Map CN cluster.log events to (query, run) using runs.csv start times, then summarize per run:
fragments (role, inputs/outputs, elapsed), declared input stream cardinalities, relayed batches, nixl transmits
(bytes, elapsed/lease/write ms, gbps), failures/cancels. usage: cnlog_extract.py <cluster.log> <runs.csv> [--json out]"""
import re, sys, csv, json, collections, datetime
log, runs = sys.argv[1], sys.argv[2]
jout = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
import glob as _glob
engine_logs = _glob.glob(sys.argv[sys.argv.index('--engine') + 1]) if '--engine' in sys.argv else []
ANSI = re.compile(r'\x1b\[[0-9;]*m')
def ts(s):
    return datetime.datetime.strptime(s[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp()
R = list(csv.DictReader(open(runs)))
windows = []
for i, r in enumerate(R):
    t0 = ts(r['start_utc']); t1 = ts(R[i + 1]['start_utc']) if i + 1 < len(R) else t0 + float(r['ms']) / 1000 + 5
    windows.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def which(t):
    for t0, t1, name, r in windows:
        if t0 - 0.5 <= t < t1: return name
    return None
qid_run = {}; per = collections.defaultdict(lambda: {"fragments": [], "cardinalities": [], "relayed": [], "transmits": [], "failures": [], "cancels": 0, "other_warn": collections.Counter()})
lines = [ANSI.sub('', l) for l in open(log, errors='replace')]
TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z')
for l in lines:
    m = TS.match(l)
    if not m: continue
    t = ts(m.group(1)); q = re.search(r'query_id=(?:Some\(FragmentInstanceId\()?([0-9a-f-]{36})', l)
    if not q: continue
    qid = q.group(1)
    if 'fragment run started' in l:
        run = which(t); qid_run.setdefault(qid, run)
    run = qid_run.get(qid) or which(t)
    if run is None: continue
    p = per[run]
    if 'fragment run started' in l:
        p["fragments"].append({"qid": qid[-12:], "fid": re.search(r'fragment_instance_id=([0-9a-f-]{36})', l).group(1)[-12:], "role": re.search(r'role="(\w+)"', l).group(1),
                               "cn": re.search(r'cn=(\S+)', l).group(1), "inputs": int(re.search(r'inputs=(\d+)', l).group(1)), "outputs": int(re.search(r'outputs=(\d+)', l).group(1)), "t_start": t})
    elif 'fragment run finished' in l or 'fragment run failed' in l:
        fid = re.search(r'fragment_instance_id=([0-9a-f-]{36})', l).group(1)[-12:]
        for f in p["fragments"]:
            if f["fid"] == fid and "elapsed_ms" not in f:
                f["elapsed_ms"] = int(re.search(r'elapsed_ms=(\d+)', l).group(1)); f["status"] = "failed" if 'failed' in l else "ok"
                if 'failed' in l: p["failures"].append(re.search(r'error=(.*)', l).group(1)[:300])
    elif 'declared input stream cardinality' in l:
        p["cardinalities"].append({"fid": re.search(r'fragment_instance_id=([0-9a-f-]{36})', l).group(1)[-12:], "stream": int(re.search(r'stream_id=(\d+)', l).group(1)), "rows": int(re.search(r'rows=(\d+)', l).group(1))})
    elif 'relayed native batches' in l:
        p["relayed"].append({"stream": int(re.search(r'stream_id=(\d+)', l).group(1)), "sender": int(re.search(r'sender_id=(\d+)', l).group(1)), "batches": int(re.search(r'batches=(\d+)', l).group(1))})
    elif 'transmitted batches via nixl' in l:
        g = lambda k: (lambda m: m.group(1) if m else None)(re.search(k + r'="?([0-9.]+)"?', l))
        p["transmits"].append({"stream": g('stream_id'), "sender": g('sender_id'), "dest": (re.search(r'dest=(\S+)', l) or [None, None])[1], "batches": g('batches'), "bytes": g('bytes'), "elapsed_ms": g('elapsed_ms'), "lease_ms": g('lease_ms'), "write_ms": g('write_ms'), "write_gbps": g('write_gbps')})
    elif 'cancel_plan_fragment' in l: p["cancels"] += 1
    elif 'skipping fragment of a retired query' in l: p["fix2_skipped"] = p.get("fix2_skipped", 0) + 1
    elif "retired a query's parked sender outputs" in l or 'retired a query' in l: p["fix2_retired"] = p.get("fix2_retired", 0) + 1
    elif 'fused sender fragment into its local receiver' in l: p["fix4_fused"] = p.get("fix4_fused", 0) + 1
    elif 'fragment fusion skipped' in l: p["fix4_skipped"] = p.get("fix4_skipped", 0) + 1
    elif 'WARN' in l or 'ERROR' in l: p["other_warn"][re.sub(r'[0-9a-f-]{36}|\d+', 'N', l.split(': ', 2)[-1])[:120]] += 1
# engine (C++) logs: "[2026-09-03 13:03:44.795] [warning] [gpu_pipeline_executor.cpp:366] ... reschedule (retry 1/100) ... OOM at operator X"
eng = collections.defaultdict(lambda: {"oom_reschedules": 0, "oom_ops": collections.Counter(), "retry_exhausted": 0, "reservation_failed": 0, "reservation_clamped": 0, "downgrade": 0, "futile_aborts": 0, "partial_proceed": 0})
ETS = re.compile(r'^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+)\]')
for el in engine_logs:
    for l in open(el, errors='replace'):
        m = ETS.match(l)
        if not m: continue
        t = ts(m.group(1) + 'T' + m.group(2)); run = which(t)
        if run is None: continue
        e = eng[run]
        if 'reschedule (retry' in l:
            e["oom_reschedules"] += 1; mo = re.search(r'OOM at operator (\S+)', l); e["oom_ops"][mo.group(1) if mo else '?'] += 1
        elif 'exceeded' in l and 'retries' in l: e["retry_exhausted"] += 1
        elif 'is futile' in l: e["futile_aborts"] += 1
        elif 'proceeding with partial reservation' in l: e["partial_proceed"] += 1
        elif 'Failed to acquire memory reservation' in l: e["reservation_failed"] += 1
        elif 'clamping reservation request' in l: e["reservation_clamped"] += 1
        elif 'downgrade' in l.lower(): e["downgrade"] += 1
out = {}
for t0, t1, name, r in windows:
    p = per.get(name); 
    if not p: out[name] = {"status": r['status'], "ms": int(r['ms']), "note": "no CN events mapped"}; continue
    frs = p["fragments"]; roles = collections.Counter(f["role"] for f in frs)
    out[name] = {"status": r['status'], "ms": int(r['ms']), "rows": int(r['rows']), "query_ids": sorted({f["qid"] for f in frs}), "fragments": len(frs), "roles": dict(roles),
                 "fragment_ms": sorted(((f["role"], f["cn"], f.get("elapsed_ms")) for f in frs), key=lambda x: -(x[2] or 0))[:12],
                 "declared_cardinalities": p["cardinalities"], "relayed_batches": sum(x["batches"] for x in p["relayed"]), "relayed_streams": len(p["relayed"]),
                 "transmits": p["transmits"], "transmit_bytes": sum(float(x["bytes"] or 0) for x in p["transmits"]), "failures": p["failures"], "cancels": p["cancels"], "warnings": dict(p["other_warn"].most_common(5)),
                 "fix_counters": {k: p.get(k, 0) for k in ("fix2_skipped", "fix2_retired", "fix4_fused", "fix4_skipped")},
                 "engine": {"oom_reschedules": eng[name]["oom_reschedules"], "oom_ops": dict(eng[name]["oom_ops"]), "retry_exhausted": eng[name]["retry_exhausted"], "reservation_failed": eng[name]["reservation_failed"], "reservation_clamped": eng[name]["reservation_clamped"], "downgrade_lines": eng[name]["downgrade"], "futile_aborts": eng[name]["futile_aborts"], "partial_proceed": eng[name]["partial_proceed"]}}
if jout: json.dump(out, open(jout, 'w'), indent=1)
for name, o in out.items():
    if "note" in o: print(f"{name}: {o['status']} {o['ms']}ms  ({o['note']}) oom_reschedules={eng[name]['oom_reschedules']}"); continue
    cards = ', '.join(f"s{c['stream']}={c['rows']:,}" for c in o["declared_cardinalities"][:8])
    tx = f"nixl={len(o['transmits'])} tx_bytes={o['transmit_bytes']/1e9:.2f}GB" if o["transmits"] else ""
    print(f"{name}: {o['status']} {o['ms']}ms frags={o['fragments']} {o['roles']} relayed={o['relayed_batches']}b/{o['relayed_streams']}s {tx} cards[{cards}] slowest={o['fragment_ms'][:3]} fail={len(o['failures'])} cancels={o['cancels']} oom_resched={o['engine']['oom_reschedules']}{('/' + str(o['engine']['oom_ops'])) if o['engine']['oom_reschedules'] else ''} clamped={o['engine']['reservation_clamped']} futile={o['engine']['futile_aborts']} partial={o['engine']['partial_proceed']} fix={ {k: v for k, v in o['fix_counters'].items() if v} }")
