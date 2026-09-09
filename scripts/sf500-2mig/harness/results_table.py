#!/usr/bin/env python3
"""Merge runs.csv of several arms into one table: per query, per arm: status, cold ms, warm median ms.
usage: results_table.py <arm1>=<runs.csv> <arm2>=<runs.csv> ..."""
import sys, csv, statistics, collections
arms = [a.split('=', 1) for a in sys.argv[1:]]
data = collections.defaultdict(dict)
for name, path in arms:
    rows = list(csv.DictReader(open(path)))
    byq = collections.defaultdict(list)
    for r in rows: byq[r['query']].append(r)
    for q, rs in byq.items():
        cold = [int(r['ms']) for r in rs if r['phase'] == 'cold']; warm = [int(r['ms']) for r in rs if r['phase'] == 'warm' and r['status'] == 'pass']
        st = 'pass' if all(r['status'] == 'pass' for r in rs) else next(r['status'] for r in rs if r['status'] != 'pass')
        data[q][name] = (st, cold[0] if cold else None, statistics.median(warm) if warm else None, len(warm), rs[0]['rows'])
names = [n for n, _ in arms]
print('| query | ' + ' | '.join(f'{n} status | {n} cold ms | {n} warm median ms' for n in names) + ' |')
print('|---|' + '---|' * (3 * len(names)))
for q in sorted(data):
    cells = []
    for n in names:
        st, cold, warm, nw, rows = data[q].get(n, ('-', None, None, 0, ''))
        cells += [st, str(cold) if cold is not None else '-', f'{warm:.0f} (n={nw})' if warm is not None else '-']
    print(f'| {q} | ' + ' | '.join(cells) + ' |')
