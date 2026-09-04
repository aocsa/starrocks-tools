#!/usr/bin/env python3
"""Per (query,run): plan shapes printed by the engine ('Pipeline #N: A (id=..) -> B ...' lines under 'Query Plan:'), mapped by engine-log timestamp
(UTC) to runs.csv windows. Prints per run the multiset of pipeline operator chains (ids stripped) across the 4 engine logs."""
import sys, re, csv, glob, datetime, collections
arm = sys.argv[1]
rows = list(csv.DictReader(open(f"{arm}/runs/runs.csv")))
def ts(x): return datetime.datetime.strptime(x[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp()
win = []
for i, r in enumerate(rows):
    t0 = ts(r['start_utc']); t1 = ts(rows[i+1]['start_utc']) if i+1 < len(rows) else t0 + float(r['ms'])/1000 + 5
    win.append((t0, t1, f"{r['query']}.r{r['run']}"))
def which(t):
    for a, b, n in win:
        if a - 0.5 <= t < b: return n
ETS = re.compile(r'^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+)\]')
per = collections.defaultdict(collections.Counter); plans = collections.Counter()
for f in sorted(glob.glob(f"{arm}/engine-.cn*.log")):
    cur = None; inplan = False; chain = []
    for l in open(f, errors='replace'):
        m = ETS.match(l)
        if m:
            cur = which(ts(m.group(1) + 'T' + m.group(2)))
            if inplan and chain and cur:
                plans[cur] += 1
            inplan = 'Query Plan:' in l; chain = []
            continue
        if inplan and cur and l.startswith('Pipeline #'):
            ops = re.sub(r' \(id=\d+\)', '', l.split(':', 1)[1]).strip()
            per[cur][ops] += 1
for _, _, n in win:
    if n not in per: print(f"{n}: no plans"); continue
    print(f"{n}: plans={sum(per[n].values())} pipelines; distinct chains:")
    for ops, c in sorted(per[n].items(), key=lambda x: -x[1]): print(f"    {c:3d}x {ops}")
