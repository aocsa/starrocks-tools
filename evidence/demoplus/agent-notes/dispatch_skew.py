#!/usr/bin/env python3
"""Per run: leaf-sender (inputs=0) start skew, count of exec_plan_fragment RPCs whose idle time >= 100 ms, sum of RPC idle,
and the number of leaf senders that started after the first leaf sender finished (late wave). usage: dispatch_skew.py <arm>"""
import sys, re, csv, datetime, collections
arm = sys.argv[1]
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
def dur_ms(s):
    m = re.match(r'([0-9.]+)(µs|ms|ns|s)', s); v = float(m.group(1)); u = m.group(2)
    return v / 1000 if u == 'µs' else v if u == 'ms' else v / 1e6 if u == 'ns' else v * 1000
E = collections.defaultdict(lambda: {"leaf_start": [], "leaf_finish": {}, "rpc_idle": [], "leaf_by_cn": collections.defaultdict(list)})
for l in open(f"{arm}/cluster.log", errors='replace'):
    l = ANSI.sub('', l); m = TS.match(l)
    if not m: continue
    t = ts(m.group(1)); run = which(t)
    if not run: continue
    e = E[run]
    if 'fragment run started' in l and 'inputs=0' in l and 'role="sender"' in l:
        fid = re.search(r'fragment_instance_id=([0-9a-f-]{36})', l).group(1); cn = re.search(r'cn=(\S+)', l).group(1)
        e["leaf_start"].append((t, fid, cn)); e["leaf_by_cn"][cn].append(t)
    elif 'fragment run finished' in l and 'role="sender"' in l:
        e["leaf_finish"][re.search(r'fragment_instance_id=([0-9a-f-]{36})', l).group(1)] = t
    elif 'exec_plan_fragment' in l and 'close' in l:
        i = re.search(r'time.idle=(\S+)', l); e["rpc_idle"].append(dur_ms(i.group(1)) if i else 0)
print(f"{'run':8s} {'ms':>6s} {'leaf':>4s} {'leaf_start min..max (ms)':>26s} {'skew':>6s} {'late_leaves':>11s} {'rpc>=100ms':>10s} {'rpc_idle_sum_s':>14s}")
for t0, t1, name, r in win:
    e = E.get(name)
    if not e or not e["leaf_start"]: print(f"{name:8s} {r['ms']:>6s} no leaf senders"); continue
    st = sorted(e["leaf_start"]); first_fin = min((e["leaf_finish"].get(f, 1e18) for _, f, _ in st), default=1e18)
    late = sum(1 for t, f, c in st if t > first_fin)
    big = sum(1 for x in e["rpc_idle"] if x >= 100)
    print(f"{name:8s} {r['ms']:>6s} {len(st):4d} {(st[0][0]-t0)*1000:11.0f}..{(st[-1][0]-t0)*1000:11.0f} {(st[-1][0]-st[0][0])*1000:6.0f} {late:11d} {big:10d} {sum(e['rpc_idle'])/1000:14.1f}")
