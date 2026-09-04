#!/usr/bin/env python3
"""Per-CN fragment timeline for one run: every 'fragment run started/finished' (role, cn, elapsed), exec_plan_fragment RPC closes
(busy/idle), nixl transmits, all in ms relative to the client start. usage: percn_timeline.py <arm> <run> [--rx]"""
import sys, re, csv, datetime
arm, want = sys.argv[1], sys.argv[2]; show_rx = '--rx' in sys.argv
rows = list(csv.DictReader(open(f"{arm}/runs/runs.csv")))
def ts(x): return datetime.datetime.strptime(x[:26].rstrip('Z'), '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc).timestamp()
win = None
for i, r in enumerate(rows):
    t0 = ts(r['start_utc']); t1 = ts(rows[i+1]['start_utc']) if i+1 < len(rows) else t0 + float(r['ms'])/1000 + 5
    if f"{r['query']}.r{r['run']}" == want: win = (t0, t1, r)
t0, t1, r = win
ANSI = re.compile(r'\x1b\[[0-9;]*m'); TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z')
def g(pat, l):
    m = re.search(pat, l); return m.group(1) if m else '?'
def dur_ms(s):
    m = re.match(r'([0-9.]+)(µs|ms|ns|s)', s)
    if not m: return -1
    v = float(m.group(1)); u = m.group(2)
    return v / 1000 if u == 'µs' else v if u == 'ms' else v / 1e6 if u == 'ns' else v * 1000
out = []
for l in open(f"{arm}/cluster.log", errors='replace'):
    l = ANSI.sub('', l); m = TS.match(l)
    if not m: continue
    t = ts(m.group(1))
    if not (t0 - 0.5 <= t < t1): continue
    rel = (t - t0) * 1000
    role = g(r'role="(\w+)"', l); cn = g(r'cn=(\S+)', l); fid = g(r'fragment_instance_id=([0-9a-f-]{36})', l)[-4:]
    if 'fragment run started' in l:
        out.append((rel, f"START  {role:6s} cn={cn} fid=..{fid} inputs={g(r'inputs=(\d+)', l)} outputs={g(r'outputs=(\d+)', l)}"))
    elif 'fragment run finished' in l or 'fragment run failed' in l:
        out.append((rel, f"FINISH {role:6s} cn={cn} fid=..{fid} elapsed={g(r'elapsed_ms=(\d+)', l)}ms{' FAILED' if 'failed' in l else ''}"))
    elif 'exec_plan_fragment' in l and 'close' in l:
        out.append((rel, f"RPC exec_plan_fragment close peer={g(r'peer=(\S+?)\}', l)} busy={dur_ms(g(r'time.busy=(\S+)', l)):.1f}ms idle={dur_ms(g(r'time.idle=(\S+)', l)):.1f}ms"))
    elif 'transmitted batches via nixl' in l:
        out.append((rel, "TX " + re.sub(r'^.*agent_tier: transmitted batches via nixl ', '', l).strip()[:170]))
    elif show_rx and 'received remote batches' in l:
        out.append((rel, "RX " + re.sub(r'^.*engine: ', '', l).strip()[:120]))
    elif 'fetch_data' in l and 'close' in l:
        out.append((rel, "RPC fetch_data close"))
out.sort()
print(f"### {arm.rstrip('/').split('/')[-1]} {want} client ms={r['ms']} (ms since client start)")
for rel, s in out: print(f"{rel:8.1f}  {s}")
