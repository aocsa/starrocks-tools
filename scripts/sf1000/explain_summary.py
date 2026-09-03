#!/usr/bin/env python3
"""Summarize StarRocks EXPLAIN COSTS output: fragments, joins (op, distribution, conjunct, probe/build children),
aggregations, exchanges, cardinality estimates. usage: explain_summary.py <costs.txt> [...]"""
import re, sys, os, json
def parse(path):
    t = open(path).read(); lines = t.splitlines()
    frags = re.findall(r'^PLAN FRAGMENT (\d+)\(F(\d+)\)', t, re.M)
    cards = [int(x) for x in re.findall(r'cardinality:\s*(\d+)', t)]
    # node lines: indentation + "N:NAME"
    nodes = []
    for i, l in enumerate(lines):
        m = re.match(r'^(\s*)(\|----)?(\d+):([A-Z][A-Z \-]+?)(?:\s*\(.*\))?\s*$', l)
        if m: nodes.append((i, len(m.group(1)) + (5 if m.group(2) else 0), int(m.group(3)), m.group(4).strip(), bool(m.group(2))))
    # fragment -> output exchange id and scanned tables (by column prefix under FileScanNode)
    PFX = {'l_':'lineitem','o_':'orders','c_':'customer','p_':'part','ps_':'partsupp','s_':'supplier','n_':'nation','r_':'region'}
    frag_blocks = re.split(r'^PLAN FRAGMENT ', t, flags=re.M)[1:]
    exch_src = {}   # exchange id -> tables produced by the fragment that outputs it
    frag_tables = {}
    for fb in frag_blocks:
        fid = re.match(r'(\d+)', fb).group(1)
        m = re.search(r'OutPut Exchange Id: (\d+)', fb)
        tabs = set()
        for scan in re.finditer(r'\d+:FileScanNode\n((?:.*\n){1,40}?)(?=\n\s*\n|\d+:|PLAN FRAGMENT|\Z)', fb):
            for col in re.findall(r'\* (ps_|l_|o_|c_|p_|s_|n_|r_)', scan.group(1)): tabs.add(PFX[col])
        # exchanges consumed inside this fragment carry their sources too
        for eid in re.findall(r'(\d+):(?:MERGING-)?EXCHANGE', fb): tabs |= set(exch_src.get(eid, set()))
        frag_tables[fid] = tabs
        if m: exch_src[str(int(m.group(1)))] = tabs
    joins = []
    for idx, (i, ind, nid, name, right) in enumerate(nodes):
        if 'JOIN' not in name: continue
        body = '\n'.join(lines[i+1:i+40])
        op = re.search(r'join op: ([^\n]+)', body); conj = re.findall(r'equal join conjunct: ([^\n]+)', body)
        later = nodes[idx+1:]
        rk = next((n for n in later if n[4] and n[1] > ind), None)
        # probe child: first later node at the join's own indentation that is not a build child
        pk = next((n for n in later if not n[4] and n[1] == ind), None)
        def desc(n):
            if n is None: return '?'
            src = ''
            if 'EXCHANGE' in n[3]:
                src = '<-' + '+'.join(sorted(exch_src.get(str(n[2]), []))) if exch_src.get(str(n[2])) else ''
            return f"{n[2]}:{n[3]}{src}"
        joins.append({'node': nid, 'op': op.group(1).strip() if op else name, 'conjuncts': [c.strip() for c in conj],
                      'probe_child': desc(pk), 'build_child': desc(rk)})
    aggs = re.findall(r'(\d+):AGGREGATE \(([a-z ]+)\)', t)
    exch = re.findall(r'(\d+):(?:MERGING-)?EXCHANGE\n\s*distribution type: ([A-Z_]+)', t)
    scans = re.findall(r'(\d+):FileScanNode', t)
    tables = re.findall(r'file://[^/]*/[^/]*/[^/]*/[^/]*/([a-z]+)/\*', t)  # weak; FILES path may not appear in COSTS
    topn = re.findall(r'(\d+):TOP-N', t)
    return {'query': os.path.basename(path).split('.')[0], 'fragments': len(frags), 'nodes': len(nodes), 'scans': len(scans),
            'joins': joins, 'aggs': [f"{n}:{p}" for n, p in aggs], 'exchanges': [f"{n}:{d}" for n, d in exch], 'topn': topn,
            'cardinalities': {'n': len(cards), 'all_one': all(c == 1 for c in cards), 'max': max(cards) if cards else None}}
if __name__ == '__main__':
    out = [parse(p) for p in sys.argv[1:]]
    if len(out) == 1: print(json.dumps(out[0], indent=1)); sys.exit()
    for o in out:
        js = '; '.join(f"{j['node']}:{j['op']} build={j['build_child']} probe={j['probe_child']}" for j in o['joins'])
        print(f"{o['query']} frags={o['fragments']} scans={o['scans']} card_all_one={o['cardinalities']['all_one']} aggs={','.join(o['aggs'])} exch={','.join(o['exchanges'])}\n    joins: {js}")
