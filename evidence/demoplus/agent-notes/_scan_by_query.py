#!/usr/bin/env python3
"""Regroup quent_bp.py json fragments into (query.run) windows from runs.csv and sum per-op Computing seconds.
usage: _scan_by_query.py <quent.json> <runs.csv> [label]"""
import json, csv, sys, datetime, collections, statistics
J = json.load(open(sys.argv[1])); R = list(csv.DictReader(open(sys.argv[2]))); lab = sys.argv[3] if len(sys.argv) > 3 else sys.argv[1]
def ts(x): return datetime.datetime.strptime(x[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp() * 1e9
win = []
for i, r in enumerate(R):
    t0 = ts(r['start_utc']); t1 = ts(R[i + 1]['start_utc']) if i + 1 < len(R) else t0 + float(r['ms']) * 1e6 + 5e9
    win.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def wof(t):
    for a, b, n, r in win:
        if a - 5e8 <= t < b: return n
    return None
per = collections.defaultdict(lambda: {"frags": 0, "tasks": 0, "ops": collections.Counter(), "comp": 0.0, "queued": 0.0, "scan_reads": [0, 0, 0], "cn": set()})
for k, e in J.items():
    if k.startswith('_'): continue
    for f in e.get("fragments", []):
        name = k if k[:1] == 'q' and '.r' in k else wof(f["t0"])
        if name is None: continue
        p = per[name]; p["frags"] += 1; p["tasks"] += f["tasks"]; p["comp"] += f["state_s"].get("Computing", 0); p["queued"] += f["state_s"].get("Queued", 0); p["cn"].add(f["cn"])
        for op, n, sec, gb in f["top_ops"]: p["ops"][op.split('(')[0]] += sec
        sr = f.get("scan_reads")
        if sr: p["scan_reads"][0] += sr["n"]; p["scan_reads"][1] += sr["bytes"]; p["scan_reads"][2] += sr["ns"]
print(f"##### {lab}")
print(f"{'run':8s} {'st':4s} {'ms':>6s} {'frags':>5s} {'tasks':>5s} {'Comp_s':>8s} {'Scan_s':>8s} {'scan%':>6s} {'Join_s':>8s} {'GrpBy_s':>8s} {'Sink_s':>8s} {'Src_s':>7s} {'Queued_s':>9s} {'scan_reads n/GB/s':>22s} top3")
for a, b, n, r in win:
    p = per.get(n)
    if not p: print(f"{n:8s} {r['status'][:4]:4s} {r['ms']:>6s}  (no quent fragments in window)"); continue
    o = p["ops"]; scan = o.get("GPU_SCAN", 0); comp = p["comp"]
    top3 = ', '.join(f"{k}={v:.2f}" for k, v in o.most_common(3))
    sr = p["scan_reads"]
    print(f"{n:8s} {r['status'][:4]:4s} {r['ms']:>6s} {p['frags']:>5d} {p['tasks']:>5d} {comp:8.2f} {scan:8.2f} {100*scan/comp if comp else 0:5.0f}% {o.get('HASH_JOIN',0):8.2f} {o.get('HASH_GROUP_BY',0)+o.get('MERGE_GROUP_BY',0):8.2f} {o.get('STREAMING_SINK',0):8.2f} {o.get('STREAMING_SOURCE',0):7.2f} {p['queued']:9.2f} {sr[0]:>6d}/{sr[1]/1e9:6.1f}/{sr[2]/1e9:7.2f}  {top3}")
