#!/usr/bin/env python3
"""WP2 P0: byte-budget predicate over StarRocks EXPLAIN COSTS plans (PLAN-top3 §4 P0).

For every HASH JOIN edge, estimate each child's bytes = cardinality x sum(avgRowSize of its column
statistics) (the FE's Statistics.computeSize shape) and evaluate two NECESSARY conditions:
  R1  no over-budget broadcast:  join op BROADCAST and build_bytes x nodes > budget           -> FAIL
  R2  no shuffle beside a budget-fitting partner:  join op PARTITIONED (both sides shuffled) where
      the smaller side fits (bytes x nodes <= budget) and the larger side > ratio x smaller       -> FAIL
Provenance rule (plan 12: unknown != 1): a side whose every column statistic is UNKNOWN, or whose
cardinality is 1 while any statistic is UNKNOWN, is UNKNOWN; an edge with an UNKNOWN side is UNKNOWN,
never PASS. The predicate reads cardinalities and avg sizes only: no table names, no column-name
lookup, no query-specific constants.

usage: budget_predicate.py [--budget BYTES] [--nodes N] [--ratio R] [--json] <costs.txt>...
defaults: budget = 2 GiB (2 x pool/40 at a 40 GiB pool), nodes = 2, ratio = 4
exit 0 when no edge is FAIL (UNKNOWN edges are reported, not failures of the plan under test)."""
import json, os, re, sys

BUDGET = 2 * 1024 ** 3; NODES = 2; RATIO = 4.0; JSON = False
args = sys.argv[1:]
while args and args[0].startswith("--"):
    k = args.pop(0)
    if k == "--budget": BUDGET = int(float(args.pop(0)))
    elif k == "--nodes": NODES = int(args.pop(0))
    elif k == "--ratio": RATIO = float(args.pop(0))
    elif k == "--json": JSON = True
    else: sys.exit(f"unknown option {k}")

NODE = re.compile(r'^(\s*)(\|----)?(\d+):([A-Z][A-Za-z \-]+?)(?:\s*\(.*\))?\s*$')
STAT = re.compile(r'\*\s+\S+-->\[([^\]]*)\]\s*(UNKNOWN|ESTIMATE|\w*)')


def parse_nodes(text):
    """Plan nodes in text order with (indent, id, name, is_build_child, body_lines). An EXCHANGE node
    carries no statistics of its own in COSTS output; it is resolved to the root node of the fragment
    whose "OutPut Exchange Id" it is (the producer's estimate is the exchange's estimate)."""
    lines = text.splitlines(); nodes = []
    for i, l in enumerate(lines):
        m = NODE.match(l)
        if not m: continue
        nodes.append({"line": i, "indent": len(m.group(1)) + (5 if m.group(2) else 0), "id": int(m.group(3)),
                      "name": m.group(4).strip(), "build": bool(m.group(2))})
    for k, n in enumerate(nodes):
        end = nodes[k + 1]["line"] if k + 1 < len(nodes) else len(lines)
        n["body"] = lines[n["line"] + 1:end]
    producer = {}   # exchange id -> root node of the producing fragment
    frag_starts = [i for i, l in enumerate(lines) if l.startswith("PLAN FRAGMENT ")]
    for a, b in zip(frag_starts, frag_starts[1:] + [len(lines)]):
        m = re.search(r'OutPut Exchange Id: (\d+)', "\n".join(lines[a:b]))
        root = next((x for x in nodes if a < x["line"] < b), None)
        if m and root: producer[int(m.group(1))] = root
    for n in nodes:
        if "EXCHANGE" in n["name"] and n["id"] in producer: n["source"] = producer[n["id"]]
    return nodes


def estimate(node):
    """(bytes, provenance) for one plan node from its own cardinality and column statistics."""
    if "source" in node: node = node["source"]
    body = "\n".join(node["body"])
    m = re.search(r'cardinality:\s*(\d+)', body)
    card = int(m.group(1)) if m else None
    stats = STAT.findall(body)
    if card is None or not stats:
        return None, "absent"
    avg = 0.0; unknown = 0
    for tup, prov in stats:
        parts = [p.strip() for p in tup.split(",")]
        try: avg += float(parts[3])
        except (IndexError, ValueError): pass
        if prov == "UNKNOWN": unknown += 1
    if unknown == len(stats) or (card == 1 and unknown):
        return card * avg, "unknown"
    return card * avg, "derived" if unknown else "estimated"


def evaluate(path):
    nodes = parse_nodes(open(path).read()); edges = []
    for k, n in enumerate(nodes):
        if "JOIN" not in n["name"]: continue
        body = "\n".join(n["body"])
        op = re.search(r'join op: ([^\n]+)', body); op = op.group(1).strip() if op else n["name"]
        later = nodes[k + 1:]
        build = next((x for x in later if x["build"] and x["indent"] > n["indent"]), None)
        probe = next((x for x in later if not x["build"] and x["indent"] == n["indent"]), None)
        bb, bp = estimate(build) if build else (None, "absent"); pb, pp = estimate(probe) if probe else (None, "absent")
        dist = "BROADCAST" if "BROADCAST" in op else "PARTITIONED" if "PARTITIONED" in op else "OTHER"
        verdict, why = "PASS", ""
        if "unknown" in (bp, pp) or "absent" in (bp, pp):
            verdict, why = "UNKNOWN", f"build={bp} probe={pp}"
        elif dist == "BROADCAST" and bb * NODES > BUDGET:
            verdict, why = "FAIL", f"R1 broadcast build {bb:.3g} B x {NODES} > budget {BUDGET}"
        elif dist == "PARTITIONED":
            small, large = min(bb, pb), max(bb, pb)
            if small * NODES <= BUDGET and large > RATIO * small:
                verdict, why = "FAIL", f"R2 shuffle of {large:.3g} B beside a budget-fitting {small:.3g} B partner"
        edges.append({"join": n["id"], "op": op, "dist": dist, "build_bytes": bb, "build_prov": bp, "probe_bytes": pb,
                      "probe_prov": pp, "verdict": verdict, "why": why})
    return {"query": os.path.basename(path).split(".")[0], "edges": edges,
            "counts": {v: sum(1 for e in edges if e["verdict"] == v) for v in ("PASS", "FAIL", "UNKNOWN")}}


results = [evaluate(p) for p in args]
if JSON:
    print(json.dumps(results, indent=1))
else:
    print(f"budget={BUDGET} nodes={NODES} ratio={RATIO}")
    print("| query | joins | PASS | FAIL | UNKNOWN | failing / unknown edges |")
    print("|---|---|---|---|---|---|")
    for r in results:
        bad = "; ".join(f"{e['join']}:{e['dist']} {e['verdict']} {e['why']}" for e in r["edges"] if e["verdict"] != "PASS")
        c = r["counts"]
        print(f"| {r['query']} | {len(r['edges'])} | {c['PASS']} | {c['FAIL']} | {c['UNKNOWN']} | {bad} |")
sys.exit(1 if any(r["counts"]["FAIL"] for r in results) else 0)
