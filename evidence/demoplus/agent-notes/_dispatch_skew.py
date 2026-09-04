#!/usr/bin/env python3
"""Per run: leaf-sender start skew (max-min of 'fragment run started' with inputs=0), exec_plan_fragment RPC idle times,
nixl transmit bytes (time-window mapped), cancel reasons / released_leases. usage: _dispatch_skew.py <cluster.log> <runs.csv> <label>"""
import re, sys, csv, datetime, collections, statistics
log, runs, lab = sys.argv[1:4]
ANSI = re.compile(r'\x1b\[[0-9;]*m')
def ts(s): return datetime.datetime.strptime(s[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp()
R = list(csv.DictReader(open(runs))); win = []
for i, r in enumerate(R):
    t0 = ts(r['start_utc']); t1 = ts(R[i + 1]['start_utc']) if i + 1 < len(R) else t0 + float(r['ms']) / 1000 + 5
    win.append((t0, t1, f"{r['query']}.r{r['run']}", r))
def wof(t):
    for a, b, n, r in win:
        if a - 0.5 <= t < b: return n
per = collections.defaultdict(lambda: {"starts": [], "leaf_starts": [], "finish": [], "rpc_idle": [], "tx_bytes": 0, "tx_n": 0, "cancel": collections.Counter(), "released": 0, "cards": []})
TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z')
def idle_ms(s):
    m = re.search(r'time\.idle=([0-9.]+)(µs|ms|s|ns)', s)
    if not m: return None
    v = float(m.group(1)); u = m.group(2)
    return v / 1000 if u == 'µs' else v if u == 'ms' else v * 1000 if u == 's' else v / 1e6
for l in open(log, errors='replace'):
    l = ANSI.sub('', l); m = TS.match(l)
    if not m: continue
    t = ts(m.group(1)); n = wof(t)
    if n is None: continue
    p = per[n]
    if 'fragment run started' in l:
        cn = re.search(r'cn=(\S+)', l).group(1); inp = int(re.search(r'inputs=(\d+)', l).group(1)); role = re.search(r'role="(\w+)"', l).group(1)
        p["starts"].append((t, cn, role, inp))
        if role == 'sender' and inp == 0: p["leaf_starts"].append((t, cn))
    elif 'fragment run finished' in l: p["finish"].append(t)
    elif 'exec_plan_fragment' in l and 'close' in l:
        v = idle_ms(l)
        if v is not None: p["rpc_idle"].append(v)
    elif 'transmitted batches via nixl' in l:
        p["tx_bytes"] += int(re.search(r'bytes=(\d+)', l).group(1)); p["tx_n"] += 1
    elif 'cancel_plan_fragment retired the query' in l or 'acknowledging cancel_plan_fragment' in l:
        rr = re.search(r'reason="?(\w+)"?', l) or re.search(r'cancel_reason=(\d+)', l); p["cancel"][rr.group(1) if rr else '?'] += 1
        rl = re.search(r'released_leases=(\d+)', l)
        if rl: p["released"] += int(rl.group(1))
    elif 'declared input stream cardinality' in l:
        p["cards"].append(int(re.search(r'rows=(\d+)', l).group(1)))
print(f"##### {lab}")
print(f"{'run':8s} {'st':4s} {'ms':>6s} {'frags':>5s} {'leaf':>4s} {'leaf_skew_ms':>12s} {'leaf_start_groups(ms from first)':>34s} {'rpc_idle_ms p50/max':>20s} {'nixl_n':>6s} {'nixl_GB':>8s} {'cards_n/sum':>16s} cancels/released")
for a, b, n, r in win:
    p = per[n]; ls = sorted(p["leaf_starts"])
    skew = (ls[-1][0] - ls[0][0]) * 1000 if ls else 0
    groups = ' '.join(f"{(t - ls[0][0]) * 1000:.0f}" for t, cn in ls) if ls else '-'
    ri = p["rpc_idle"]; rid = f"{statistics.median(ri):.1f}/{max(ri):.0f}" if ri else '-'
    print(f"{n:8s} {r['status'][:4]:4s} {r['ms']:>6s} {len(p['starts']):>5d} {len(ls):>4d} {skew:12.0f} {groups[:34]:>34s} {rid:>20s} {p['tx_n']:>6d} {p['tx_bytes']/1e9:8.2f} {len(p['cards']):>5d}/{sum(p['cards']):>10,d} {dict(p['cancel'])}/{p['released']}")
