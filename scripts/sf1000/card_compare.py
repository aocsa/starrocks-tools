#!/usr/bin/env python3
"""FE cardinality estimate vs actual rows per exchange. Stream ids in the engine equal the FE exchange node ids, and the
receiver declares the exact parked row count per stream ('declared input stream cardinality'), so for every exchange we
can put the FE's estimate (from EXPLAIN COSTS) next to the actual rows that crossed it.
usage: card_compare.py <explain_dir> <cnlog_extract.json> [arm]"""
import sys, re, json, glob, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from explain_summary import parse
edir, cnj = sys.argv[1], sys.argv[2]; arm = sys.argv[3] if len(sys.argv) > 3 else ''
cn = json.load(open(cnj))
rows = []
for q in sorted({k.split('.')[0] for k in cn}):
    costs = f"{edir}/{q}.costs.txt"
    if not os.path.exists(costs): continue
    t = open(costs).read(); p = parse(costs)
    # exchange node -> (distribution type, FE cardinality printed right under it)
    ex = {}
    for m in re.finditer(r'(\d+):(MERGING-)?EXCHANGE\n[\s|]*distribution type: ([A-Z_]+)\n(?:[\s|].*\n){0,4}?[\s|]*cardinality: (\d+)', t):
        ex[int(m.group(1))] = (m.group(3), int(m.group(4)))
    # which join consumes each exchange as build side
    build_of = {}
    for j in p['joins']:
        m = re.match(r'(\d+):', j['build_child'] or '')
        if m: build_of[int(m.group(1))] = f"{j['node']}:{j['op']}"
    # actual: first passing run's declared cardinalities (identical across runs)
    runs = [k for k in cn if k.startswith(q + '.') and 'declared_cardinalities' in cn[k]]
    if not runs: continue
    r = cn[sorted(runs)[0]]
    actual = collections.defaultdict(int)
    for c in r['declared_cardinalities']: actual[c['stream']] += c['rows']
    for sid in sorted(set(ex) | set(actual)):
        d, est = ex.get(sid, ('?', None))
        rows.append((q, sid, d, est, actual.get(sid), build_of.get(sid, ''), r['status']))
print(f"{'q':4s} {'exch':4s} {'FE type':10s} {'FE est':>8s} {'actual rows':>15s} {'ratio':>10s}  consumed as build side of")
for q, sid, d, est, act, b, st in rows:
    ratio = f"{act/est:,.0f}x" if est and act is not None else ''
    print(f"{q:4s} {sid:<4d} {d:10s} {str(est):>8s} {('%s' % format(act, ',')) if act is not None else '-':>15s} {ratio:>10s}  {b}")
big = [(q, sid, act) for q, sid, d, est, act, b, st in rows if act and act >= 100_000_000]
print("\nexchanges carrying >= 100M rows:", ', '.join(f"{q}/exch{sid}={act/1e6:,.0f}M" for q, sid, act in big))
