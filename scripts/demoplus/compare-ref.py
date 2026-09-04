#!/usr/bin/env python3
"""Merge the user's feat/pin-table-cn reference (pinned), yesterday's perf/profile-sf1000 arms (unpinned) and the demo-plus arms
(unpinned, fixes 1+2+4) into one SF1000 table. usage: compare-ref.py <out.md>"""
import csv, statistics, collections, sys, os
SP='/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad'
def med(path):
    d = {}
    if not os.path.exists(path): return d
    by = collections.defaultdict(list); st = collections.defaultdict(set)
    for r in csv.DictReader(open(path)):
        st[r['query']].add(r['status'])
        if r['phase'] == 'warm' and r['status'] == 'pass': by[r['query']].append(int(r['ms']) / 1000)
    for q in st: d[q] = (statistics.median(by[q]) if by[q] else None, 'pass' if st[q] == {'pass'} else next(s for s in st[q] if s != 'pass'))
    return d
ref = {}
for l in open(f'{SP}/demoplus/reference-pin-table-cn.md'):
    if l[:1] == 'q' and ',' in l:
        q, c1, c2, c4, c8 = l.strip().split(','); ref[q] = {'1': c1, '2': c2, '4': c4, '8': c8}
perf4 = med(f'{SP}/perf/sf1000/cn4/runs/runs.csv'); perf1 = med(f'{SP}/perf/sf1000/cn1/runs/runs.csv')
dp4 = med(f'{SP}/demoplus/arms/dp-cn4/runs/runs.csv'); dp1 = med(f'{SP}/demoplus/arms/dp-cn1-six/runs/runs.csv'); dp4s = med(f'{SP}/demoplus/arms/dp-cn4-six-stg48/runs/runs.csv')
def f(d, q):
    if q not in d: return '-'
    m, s = d[q]; return f'{m:.2f}' if m is not None else s
def num(x):
    try: return float(str(x).rstrip('†*'))
    except: return None
rows = []; tot = collections.defaultdict(float); cnt = collections.defaultdict(int)
out = ['| query | pin-table-cn 1 CN (pinned) | perf 1 CN | demo-plus 1 CN | pin-table-cn 4 CN (pinned) | perf 4 CN | demo-plus 4 CN (16 GiB arena) | demo-plus 4 CN (48 GiB arena, six only) | demo-plus 4 CN / pinned 4 CN |', '|---|--:|--:|--:|--:|--:|--:|--:|--:|']
for q in sorted(set(ref) | set(perf4) | set(dp4)):
    r4 = num(ref.get(q, {}).get('4')); d4 = dp4.get(q, (None, ''))[0]
    d4s = dp4s.get(q, (None, ''))[0]; best4 = d4 if d4 else d4s
    ratio = f'{best4 / r4:.2f}' if (r4 and best4) else '-'
    out.append(f"| {q} | {ref.get(q, {}).get('1', '-')} | {f(perf1, q)} | {f(dp1, q)} | {ref.get(q, {}).get('4', '-')} | {f(perf4, q)} | {f(dp4, q)} | {f(dp4s, q)} | {ratio} |")
    for k, v in (('ref4', r4), ('perf4', perf4.get(q, (None,))[0]), ('dp4', d4), ('dp4best', best4)):
        if v: tot[k] += v; cnt[k] += 1
out.append(f"| **sum of warm medians (passing only)** | | | | {tot['ref4']:.2f} ({cnt['ref4']} q) | {tot['perf4']:.2f} ({cnt['perf4']} q) | {tot['dp4']:.2f} ({cnt['dp4']} q) | 16+48 GiB best: {tot['dp4best']:.2f} ({cnt['dp4best']} q) | |")
open(sys.argv[1], 'w').write('\n'.join(out) + '\n'); print('\n'.join(out[:6])); print('...')
