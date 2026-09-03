#!/usr/bin/env python3
"""repair.py -- rebuild pipelines for fragments whose `operator`/`plan` declarations were not flushed
(per-type Quent buffers flush independently; the `task` buffer usually is on disk). Orphan tasks are grouped by
`Created.pipeline_uuid`, assigned to the fragment whose [Init, Exit] window contains the group's first Created,
ordered by the smallest operator id in their `Computing.instance_name` (e.g. GPU_SCAN(0) < MERGE_AGGREGATE(3)).
usage: repair.py in.json out.json"""
import json, re, sys
d = json.load(open(sys.argv[1])); n_fixed = 0
for s in d["sessions"]:
    known = {p["id"] for f in s["fragments"] for p in f["pipelines"]}
    groups = {}
    for tid, t in s["tasks"].items():
        if t["pipeline"] and t["pipeline"] not in known and t["created"]:
            groups.setdefault(t["pipeline"], []).append(t)
    for pid, ts in groups.items():
        first = min(t["created"] for t in ts)
        frag = next((f for f in s["fragments"] if f["init"] and f["exit"] and f["init"] <= first <= f["exit"]), None)
        if frag is None:
            continue
        ops = []
        for t in ts:
            for c in t["computing"]:
                if c[0] not in ops: ops.append(c[0])
        opid = min((int(m.group(1)) for o in ops for m in [re.search(r"\((\d+)\)", o)] if m), default=99)
        frag["pipelines"].append({"id": pid, "plan_id": None, "query_id": frag["id"], "pipeline_id": None, "_opid": opid,
                                  "chain": " -> ".join(ops) if ops else "?", "declared": first, "tasks": [t["id"] for t in ts],
                                  "batches_out": [], "edges_out": [], "_rebuilt": True})
        n_fixed += 1
    for f in s["fragments"]:
        if any(p.get("_rebuilt") for p in f["pipelines"]):
            f["pipelines"].sort(key=lambda p: p.get("_opid", p["pipeline_id"] if p["pipeline_id"] is not None else 99))
            for k, p in enumerate(f["pipelines"]): p["pipeline_id"] = k
json.dump(d, open(sys.argv[2], "w")); print(f"{sys.argv[1]}: rebuilt {n_fixed} pipelines")
