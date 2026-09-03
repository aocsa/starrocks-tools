#!/usr/bin/env python3
"""extract.py -- flatten one Quent session directory (ndjson) into the event
JSON that replay.py consumes.  stdlib only.

usage: extract.py <session-dir> [<session-dir> ...] -o out.json [--cn-from-path]

Every session dir is `<...>/.cn<i>/telemetry/<session-uuid>/` (CN) or a
standalone session dir.  With --cn-from-path the `.cn<i>` component names the
CN; otherwise CNs are numbered in argument order.

Output (one dict per session under "sessions"):
  fragments : one per Quent `query` entity
      id, label (query.Init.instance_name), init/planning/executing/exit (ns),
      pipelines: [{id, pipeline_id (from operator.type_name "Pipeline Id N"),
                   chain (operator.instance_name), tasks:[...], batches_out:[...]}]
  tasks     : per task: pipeline, created, queued (list of ts), routing,
              reserving, preparing, computing:[(op_name, start_ns, end_ns)],
              finalizing, exit, executor_thread, success
  batches   : per data_batch: data_batch_id, producer_pipeline, bytes
              (Stationary.memory.capacity.capacity_bytes), constructed, destructed
  placements: per batch_placement: batch_id, consumer pipeline, port, origin,
              bytes, registered/queued/packaged/processing/consumed ts, task_uuid
Field names are the on-disk ones; nothing is inferred beyond the joins listed
in 01-trace-schema.md section 0.
"""
import glob
import json
import os
import re
import sys


def load(session, typ):
    out = []
    for f in glob.glob(os.path.join(session, typ, "*.ndjson")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    out.sort(key=lambda r: (r["timestamp"], r["data"].get("seq", 0)))
    return out


def fsm(records):
    """entity id -> [(state_name, ts, attrs)] in seq order."""
    ents = {}
    for r in records:
        st = r["data"]["state"]
        if isinstance(st, dict):
            name = next(iter(st))
            attrs = st[name]
        else:
            name, attrs = st, {}
        ents.setdefault(r["id"], []).append((name, r["timestamp"], attrs))
    return ents


def decl(records):
    """entity id -> (ts, Declaration dict)."""
    return {r["id"]: (r["timestamp"], r["data"]["Declaration"]) for r in records}


def extract_session(session):
    queries = fsm(load(session, "query"))
    plans = decl(load(session, "plan"))
    operators = decl(load(session, "operator"))
    ports = decl(load(session, "port"))
    tasks = fsm(load(session, "task"))
    batches = fsm(load(session, "data_batch"))
    placements = fsm(load(session, "batch_placement"))
    exec_threads = fsm(load(session, "executor_thread"))
    gpu = decl(load(session, "gpu_device"))
    engine = load(session, "engine")

    # --- static plan ------------------------------------------------------
    plan_to_query = {pid: d["parent"]["query_id"] for pid, (_, d) in plans.items()}
    port_to_op = {pid: (d["operator_id"], d["instance_name"]) for pid, (_, d) in ports.items()}
    pipelines = {}
    for oid, (ts, d) in operators.items():
        m = re.search(r"Pipeline Id (\d+)", d["type_name"])
        pipelines[oid] = {
            "id": oid,
            "plan_id": d["plan_id"],
            "query_id": plan_to_query.get(d["plan_id"]),
            "pipeline_id": int(m.group(1)) if m else None,
            "chain": d["instance_name"],
            "declared": ts,
            "tasks": [],
            "batches_out": [],
            "edges_out": [],  # consumer pipeline ids (from plan.edges)
        }
    for pid, (_, d) in plans.items():
        for e in d["edges"]:
            src = port_to_op.get(e["source"], (None, None))[0]
            dst = port_to_op.get(e["target"], (None, None))[0]
            if src in pipelines and dst is not None:
                pipelines[src]["edges_out"].append(dst)

    # --- tasks -------------------------------------------------------------
    task_out = {}
    for tid, trs in tasks.items():
        t = {"id": tid, "pipeline": None, "name": None, "created": None, "queued": [],
             "routing": None, "reserving": None, "reserving_attrs": None,
             "preparing": None, "preparing_attrs": None,
             "computing": [], "finalizing": None, "exit": None,
             "executor_thread": None, "success": None}
        prev_comp = None
        for name, ts, a in trs:
            if name == "Created":
                t["pipeline"] = a["pipeline_uuid"]
                t["name"] = a["instance_name"]
                t["created"] = ts
            elif name == "Queued":
                t["queued"].append((ts, a["queue"]["resource_id"]))
            elif name == "Routing":
                t["routing"] = ts
            elif name == "Reserving":
                t["reserving"] = ts
                t["reserving_attrs"] = {k: a[k] for k in ("requested_bytes", "input_basis", "peak_estimate", "bytes_to_materialize")}
            elif name == "Preparing":
                t["preparing"] = ts
                t["preparing_attrs"] = {k: a[k] for k in ("origin_tier", "target_tier", "input_bytes")}
                t["executor_thread"] = a["executor_thread"]["resource_id"]
            elif name == "Computing":
                if prev_comp is not None:
                    prev_comp[2] = ts
                prev_comp = [a["instance_name"], ts, None, a["input_bytes"], a["peak_allocated_bytes"]]
                t["computing"].append(prev_comp)
                t["executor_thread"] = a["executor_thread"]["resource_id"]
            elif name == "Finalizing":
                if prev_comp is not None and prev_comp[2] is None:
                    prev_comp[2] = ts
                t["finalizing"] = ts
                t["success"] = a.get("success")
            elif name == "Exit":
                t["exit"] = ts
        task_out[tid] = t
        if t["pipeline"] in pipelines:
            pipelines[t["pipeline"]]["tasks"].append(tid)

    # --- batches -----------------------------------------------------------
    batch_out = {}
    for bid, brs in batches.items():
        b = {"id": bid, "data_batch_id": None, "producer_pipeline": None, "bytes": None,
             "memory": None, "constructed": None, "destructed": None, "n_stationary": 0,
             "in_transit": 0}
        for name, ts, a in brs:
            if name == "Constructed":
                b["data_batch_id"] = a["data_batch_id"]
                b["producer_pipeline"] = a["producer_pipeline_uuid"]
                b["constructed"] = ts
            elif name == "Stationary":
                b["n_stationary"] += 1
                b["memory"] = a["memory"]["resource_id"]
                cap = a["memory"].get("capacity")
                if cap:
                    b["bytes"] = cap.get("capacity_bytes")
            elif name == "InTransit":
                b["in_transit"] += 1
            elif name == "Destructed":
                b["destructed"] = ts
        batch_out[bid] = b
        if b["producer_pipeline"] in pipelines:
            pipelines[b["producer_pipeline"]]["batches_out"].append(bid)

    # --- placements --------------------------------------------------------
    pl_out = {}
    for pid, prs in placements.items():
        p = {"id": pid, "batch_id": None, "consumer_pipeline": None, "port": None, "origin": None,
             "bytes": None, "registered": None, "queued": None, "packaged": None,
             "processing": None, "consumed": None, "reason": None, "task_uuid": None}
        for name, ts, a in prs:
            if name == "BatchRegistered":
                p["batch_id"] = a["batch_id"]
                p["consumer_pipeline"] = a["pipeline_uuid"]
                p["port"] = a["port_uuid"]
                p["origin"] = a["origin"]
                p["registered"] = ts
                cap = a["tier"].get("capacity")
                if cap:
                    p["bytes"] = cap.get("capacity_bytes")
            elif name == "BatchQueued":
                p["queued"] = ts
            elif name == "BatchPackaged":
                p["packaged"] = ts
                p["task_uuid"] = a["task_uuid"]
            elif name == "BatchProcessing":
                p["processing"] = ts
            elif name == "BatchConsumed":
                p["consumed"] = ts
                p["reason"] = a["reason"]
        pl_out[pid] = p

    # --- fragments (= Quent query entities) -------------------------------
    frags = []
    for qid, qrs in queries.items():
        f = {"id": qid, "label": None, "init": None, "planning": None, "executing": None, "exit": None,
             "pipelines": []}
        for name, ts, a in qrs:
            if name == "Init":
                f["label"] = a["instance_name"]
                f["init"] = ts
            elif name == "Planning":
                f["planning"] = ts
            elif name == "Executing":
                f["executing"] = ts
            elif name == "Exit":
                f["exit"] = ts
        f["pipelines"] = sorted([p for p in pipelines.values() if p["query_id"] == qid],
                                key=lambda p: (p["pipeline_id"] if p["pipeline_id"] is not None else 99))
        frags.append(f)
    frags.sort(key=lambda f: f["init"] or 0)

    return {
        "session": session,
        "engine_init": engine[0]["timestamp"] if engine else None,
        "n_executor_threads": len(exec_threads),
        "executor_threads": {tid: rs[0][2].get("instance_name") for tid, rs in exec_threads.items()},
        "gpu_devices": {gid: d for gid, (_, d) in gpu.items()},
        "fragments": frags,
        "tasks": task_out,
        "batches": batch_out,
        "placements": pl_out,
    }


def cn_from_path(path):
    m = re.search(r"\.cn(\d+)/", path + "/")
    return int(m.group(1)) if m else None


def main(argv):
    out = None
    use_path = False
    sessions = []
    i = 0
    while i < len(argv):
        if argv[i] == "-o":
            out = argv[i + 1]
            i += 2
        elif argv[i] == "--cn-from-path":
            use_path = True
            i += 1
        else:
            sessions.append(argv[i].rstrip("/"))
            i += 1
    if not sessions or not out:
        print(__doc__)
        return 2
    result = {"sessions": []}
    for n, s in enumerate(sessions):
        ex = extract_session(s)
        ex["cn"] = cn_from_path(s) if use_path else n
        if ex["cn"] is None:
            ex["cn"] = n
        result["sessions"].append(ex)
        nt = len(ex["tasks"])
        nb = len(ex["batches"])
        print(f"cn{ex['cn']} {os.path.basename(s)}: {len(ex['fragments'])} fragments, {nt} tasks, "
              f"{nb} data_batches, {len(ex['placements'])} placements, "
              f"{ex['n_executor_threads']} executor threads", file=sys.stderr)
    with open(out, "w") as fh:
        json.dump(result, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
