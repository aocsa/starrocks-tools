#!/usr/bin/env python3
"""OOM-1 refutation helper: for a Quent session and a query-instance label suffix, print
   - task Computing concurrency (max, and at a given UTC time),
   - distinct executor threads used,
   - cumulative parked (Stationary) bytes of data batches produced by the fragment over time,
   - reservation vs peak for the first Computing attempts.
usage: oom1_refute_timeline.py <session_dir> <label_suffix> [utc_hhmmss.fff ...]"""
import sys, os, json, collections, datetime

sess, suffix = sys.argv[1], sys.argv[2]
marks = sys.argv[3:]

def rows(kind):
    d = os.path.join(sess, kind)
    if not os.path.isdir(d):
        return
    for f in os.listdir(d):
        for line in open(os.path.join(d, f), errors="replace"):
            line = line.strip()
            if line:
                yield json.loads(line)

def state(j):
    s = j["data"].get("state", j["data"])
    if isinstance(s, str):
        return s, {}
    if isinstance(s, dict) and s:
        k = next(iter(s))
        v = s[k]
        return k, (v if isinstance(v, dict) else {})
    return None, {}

# query id <- label
qids = set()
for j in rows("query"):
    k, v = state(j)
    if k == "Init" and str(v.get("instance_name", "")).endswith(suffix):
        qids.add(j["id"])
if not qids:
    sys.exit(f"no query with label suffix {suffix}")
qid = ",".join(sorted(qids))
plans = {j["id"] for j in rows("plan") if state(j)[0] == "Declaration" and (state(j)[1].get("parent") or {}).get("query_id") in qids}
pipes = {}
for j in rows("operator"):
    k, v = state(j)
    if k == "Declaration" and v.get("plan_id") in plans:
        pipes[j["id"]] = v.get("instance_name")

tasks = collections.defaultdict(list)
for j in rows("task"):
    k, v = state(j)
    tasks[j["id"]].append((j["timestamp"], k, v))
mine = {}
for tid, ev in tasks.items():
    ev.sort()
    pu = next((v["pipeline_uuid"] for _, k, v in ev if k == "Created"), None)
    if pu in pipes:
        mine[tid] = ev

def utc(ns):
    return datetime.datetime.utcfromtimestamp(ns / 1e9).strftime("%H:%M:%S.%f")[:-3]

# Computing intervals
iv = []
threads = set()
first_attempts = []
for tid, ev in mine.items():
    for i, (ts, k, v) in enumerate(ev):
        nxt = ev[i + 1][0] if i + 1 < len(ev) else ts
        if k == "Computing":
            iv.append((ts, nxt, v))
            th = (v.get("executor_thread") or {}).get("resource_id")
            if th:
                threads.add(th)
        if k == "Preparing":
            th = (v.get("executor_thread") or {}).get("resource_id")
            if th:
                threads.add(th)
    rs = [v for _, k, v in ev if k == "Reserving"]
    cs = [v for _, k, v in ev if k == "Computing"]
    if rs and cs:
        first_attempts.append((ev[0][0], rs[0].get("requested_bytes"), cs[0].get("peak_allocated_bytes")))
iv.sort()
t0 = min(ts for ts, _, _ in iv) if iv else 0
print(f"query {qid} label-suffix {suffix}: tasks={len(mine)} computing_intervals={len(iv)} executor_threads={len(threads)}")
print(f"first Computing at {utc(t0)}")
# sweep
evs = []
for a, b, _ in iv:
    evs.append((a, 1)); evs.append((b, -1))
evs.sort()
c = m = 0; tm = None
for t, d in evs:
    c += d
    if c > m:
        m, tm = c, t
print(f"max concurrent Computing tasks = {m} at {utc(tm) if tm else '-'}")
first_attempts.sort()
print("first 5 tasks: created, reserve_requested_MB, first_peak_MB")
for ts, r, p in first_attempts[:5]:
    print(f"   {utc(ts)} req={(r or 0)/1e6:.0f} peak={(p or 0)/1e6:.0f}")

# parked batches produced by this fragment
by = collections.defaultdict(list)
for j in rows("data_batch"):
    k, v = state(j)
    by[j["id"]].append((j["timestamp"], k, v))
parked = []
for bid, ev in by.items():
    ev.sort()
    pu = next((v.get("producer_pipeline_uuid") for _, k, v in ev if k == "Constructed"), None)
    if pu not in pipes:
        continue
    cap = max((((v.get("memory") or {}).get("capacity") or {}).get("capacity_bytes") or 0) for _, k, v in ev)
    t_st = next((ts for ts, k, v in ev if k == "Stationary"), None)
    t_de = next((ts for ts, k, v in ev if k == "Destructed"), None)
    parked.append((t_st or ev[0][0], t_de, cap))
parked.sort()
tot = sum(c for _, _, c in parked)
print(f"batches produced by fragment: {len(parked)} total {tot/1e9:.2f} GB")
if parked:
    print(f"   first Stationary {utc(parked[0][0])}, last Stationary {utc(parked[-1][0])}")
def parked_at(tns):
    return sum(c for a, b, c in parked if a <= tns and (b is None or b > tns)) / 1e9
def conc_at(tns):
    return sum(1 for a, b, _ in iv if a <= tns < b)
for mk in marks:
    hh, mm, ss = mk.split(":")
    d = datetime.datetime.utcfromtimestamp(t0 / 1e9).date()
    tt = datetime.datetime.combine(d, datetime.time(int(hh), int(mm), int(float(ss)), int(round((float(ss) % 1) * 1e6))), tzinfo=datetime.timezone.utc)
    tns = int(tt.timestamp() * 1e9)
    print(f"at {mk} UTC: parked by this fragment = {parked_at(tns):.2f} GB, tasks Computing = {conc_at(tns)}")
