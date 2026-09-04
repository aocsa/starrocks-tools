#!/usr/bin/env python3
"""Per run: CN-side timeline relative to the client start (runs.csv start_utc), from cluster.log:
first/last 'fragment run started' and 'finished' per role, sender start spread, first/last nixl transmit, first/last 'received remote batches',
first exec_plan_fragment / fetch_data span close, client end (start+ms). usage: timeline.py <arm_dir> [runs...]"""
import sys, re, csv, datetime, collections
arm = sys.argv[1]; only = set(sys.argv[2:])
rows = list(csv.DictReader(open(f"{arm}/runs/runs.csv")))
def ts(x): return datetime.datetime.strptime(x[:26].rstrip('Z'), '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp()
win = []
for i, r in enumerate(rows):
    t0 = ts(r['start_utc']); t1 = ts(rows[i+1]['start_utc']) if i+1 < len(rows) else t0 + float(r['ms'])/1000 + 5
    win.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def which(t):
    for a, b, n, r in win:
        if a - 0.5 <= t < b: return n
ANSI = re.compile(r'\x1b\[[0-9;]*m'); TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z')
ev = collections.defaultdict(lambda: collections.defaultdict(list))
for l in open(f"{arm}/cluster.log", errors='replace'):
    l = ANSI.sub('', l); m = TS.match(l)
    if not m: continue
    t = ts(m.group(1)); run = which(t)
    if not run: continue
    e = ev[run]
    if 'fragment run started' in l:
        role = re.search(r'role="(\w+)"', l).group(1); e[f"start_{role}"].append(t)
    elif 'fragment run finished' in l or 'fragment run failed' in l:
        role = re.search(r'role="(\w+)"', l).group(1); e[f"finish_{role}"].append(t)
    elif 'transmitted batches via nixl' in l: e["tx"].append(t)
    elif 'received remote batches' in l: e["rx"].append(t)
    elif 'exec_plan_fragment' in l and 'close' in l: e["exec_rpc_close"].append(t)
    elif 'fetch_data' in l and 'close' in l: e["fetch_close"].append(t)
    elif 'cancel_plan_fragment' in l and 'close' in l: e["cancel_close"].append(t)
    elif 'translated StarRocks plan fragment' in l: e["translated"].append(t)
print(f"{'run':8s} {'ms':>6s} {'n_snd':>5s} {'snd_start min..max':>20s} {'snd_finish min..max':>20s} {'res_start':>9s} {'res_fin':>8s} {'tx first..last':>16s} {'rx first..last':>16s} {'fetch_last':>10s} {'cancel_last':>11s} {'client_end':>10s}")
for t0, t1, name, r in win:
    if only and name not in only and name.split('.')[0] not in only: continue
    e = ev.get(name)
    if not e: print(f"{name}: no events"); continue
    def rel(k, f=min): return (f(e[k]) - t0) * 1000 if e.get(k) else float('nan')
    def rng(k): return f"{rel(k,min):7.0f}..{rel(k,max):7.0f}" if e.get(k) else f"{'-':>16s}"
    print(f"{name:8s} {r['ms']:>6s} {len(e.get('start_sender',[])):5d} {rng('start_sender'):>20s} {rng('finish_sender'):>20s} {rel('start_result'):9.0f} {rel('finish_result'):8.0f} {rng('tx'):>16s} {rng('rx'):>16s} {rel('fetch_close',max):10.0f} {rel('cancel_close',max):11.0f} {float(r['ms']):10.0f}")
