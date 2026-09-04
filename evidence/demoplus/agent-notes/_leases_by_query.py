#!/usr/bin/env python3
"""Per run window: staging leases (count, GB, max concurrent per CN), batches, from quent_bp json. usage: <quent.json> <runs.csv>"""
import json, csv, sys, datetime, collections
J = json.load(open(sys.argv[1])); R = list(csv.DictReader(open(sys.argv[2])))
def ts(x): return datetime.datetime.strptime(x[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp() * 1e9
win = []
for i, r in enumerate(R):
    t0 = ts(r['start_utc']); t1 = ts(R[i + 1]['start_utc']) if i + 1 < len(R) else t0 + float(r['ms']) * 1e6 + 5e9
    win.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def wof(t):
    for a, b, n, r in win:
        if a - 5e8 <= t < b: return n
per = collections.defaultdict(lambda: {"leases": 0, "lease_GB": 0.0, "held": 0.0, "maxconc": collections.Counter(), "batches": 0, "batch_GB": 0.0, "req_GB": 0.0, "alloc_GB": 0.0, "walls": []})
for k, e in J.items():
    if k.startswith('_'): continue
    for f in e.get("fragments", []):
        n = k if k[:1] == 'q' and '.r' in k else wof(f["t0"])
        if n is None: continue
        p = per[n]; p["leases"] += f["leases"]; p["lease_GB"] += f["lease_GB"]; p["held"] += f["lease_held_s"]; p["maxconc"][f["cn"]] = max(p["maxconc"][f["cn"]], f["lease_max_concurrent"])
        p["batches"] += f["batches"]; p["batch_GB"] += f["batch_GB"]; p["req_GB"] += f["reserve_requested_GB"]; p["alloc_GB"] += f["peak_alloc_sum_GB"]; p["walls"].append(f["wall_s"])
print(f"{'run':8s} {'st':4s} {'ms':>6s} {'leases':>6s} {'lease_GB':>8s} {'held_s':>8s} {'maxconc/CN':>28s} {'batches':>7s} {'batch_GB':>8s} {'maxfragwall':>11s}")
for a, b, n, r in win:
    p = per.get(n)
    if not p: continue
    print(f"{n:8s} {r['status'][:4]:4s} {r['ms']:>6s} {p['leases']:>6d} {p['lease_GB']:8.1f} {p['held']:8.1f} {str(dict(p['maxconc'])):>28s} {p['batches']:>7d} {p['batch_GB']:8.1f} {max(p['walls']):11.2f}")
