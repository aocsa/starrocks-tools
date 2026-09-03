#!/usr/bin/env python3
"""Per-task view of the scan fragments of q01/q06: GPU_SCAN Computing durations, Queued, reservation vs peak, concurrency."""
import json, glob, os, sys, collections, statistics
SP='/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000'
def rows(s, kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: j=json.loads(l)
            except Exception: continue
            yield j
def state(j):
    st=j["data"].get("state", j["data"])
    if isinstance(st,str): return st,{}
    k=next(iter(st)); v=st[k]; return k,(v if isinstance(v,dict) else {})
def analyze(sess, cn, run_of, want):
    q={}
    for j in rows(sess,"query"):
        k,v=state(j); e=q.setdefault(j["id"],{"t0":j["timestamp"],"t1":j["timestamp"],"label":None})
        e["t0"]=min(e["t0"],j["timestamp"]); e["t1"]=max(e["t1"],j["timestamp"])
        if k=="Init": e["label"]=v.get("query_label") or v.get("instance_name")
    plan_q={}
    for j in rows(sess,"plan"):
        k,v=state(j)
        if k=="Declaration": plan_q[j["id"]]=(v.get("parent") or {}).get("query_id")
    pipe={}
    for j in rows(sess,"operator"):
        k,v=state(j)
        if k=="Declaration": pipe[j["id"]]=(plan_q.get(v.get("plan_id")), v.get("instance_name"))
    tasks=collections.defaultdict(list)
    for j in rows(sess,"task"):
        k,v=state(j); tasks[j["id"]].append((j["timestamp"],k,v))
    per=collections.defaultdict(list)
    for tid,ev in tasks.items():
        ev.sort(); pu=next((v["pipeline_uuid"] for _,k,v in ev if k=="Created"),None)
        qid=pipe.get(pu,(None,))[0]
        if qid is None: continue
        lab=q.get(qid,{}).get("label") or ""
        head=lab.split(":")[0]
        run=run_of.get(head[-12:]) if run_of else lab
        if run is None or not any(w in run for w in want): continue
        rec={"queued":0,"comp":collections.defaultdict(float),"req":0,"basis":0,"peak":0,"t_comp0":None,"t_end":ev[-1][0],"created":ev[0][0]}
        for i,(ts,k,v) in enumerate(ev):
            nxt=ev[i+1][0] if i+1<len(ev) else ts; dt=(nxt-ts)/1e9
            if k=="Queued": rec["queued"]+=dt
            elif k=="Reserving": rec["req"]+=v.get("requested_bytes") or 0; rec["basis"]+=v.get("input_basis") or 0
            elif k=="Computing":
                rec["comp"][v.get("instance_name")]+=dt; rec["peak"]=max(rec["peak"],v.get("peak_allocated_bytes") or 0)
                if rec["t_comp0"] is None: rec["t_comp0"]=ts
                rec["t_comp_end"]=nxt
        per[(run,cn)].append(rec)
    return per,q
def pct(xs,p):
    xs=sorted(xs); return xs[min(len(xs)-1,int(p*len(xs)))]
def report(per):
    for (run,cn),recs in sorted(per.items()):
        scans=[r for r in recs if any(op.startswith("GPU_SCAN") for op in r["comp"])]
        if not scans: continue
        d=[r["comp"][[o for o in r["comp"] if o.startswith("GPU_SCAN")][0]] for r in scans]
        tot=[sum(r["comp"].values()) for r in scans]
        qd=[r["queued"] for r in scans]
        req=[r["req"]/1e9 for r in scans]; basis=[r["basis"]/1e9 for r in scans]; peak=[r["peak"]/1e9 for r in scans]
        # concurrency timeline of Computing across all tasks of the run/cn
        ev=[]
        for r in recs:
            if r["t_comp0"]: ev+= [(r["t_comp0"],1),(r["t_comp_end"],-1)]
        ev.sort(); c=0; last=None; busy=collections.Counter()
        for t,dv in ev:
            if last is not None: busy[c]+=(t-last)/1e9
            c+=dv; last=t
        span=(max(r["t_end"] for r in recs)-min(r["created"] for r in recs))/1e9
        first_created=min(r["created"] for r in recs); last_created=max(r["created"] for r in scans)
        print(f"{run:>28s} {cn:>6s} scan_tasks={len(scans):3d} GPU_SCAN s: sum={sum(d):6.2f} min={min(d):.3f} med={statistics.median(d):.3f} p90={pct(d,.9):.3f} max={max(d):.3f} | task total med={statistics.median(tot):.3f} | Queued med={statistics.median(qd):.2f} max={max(qd):.2f} | req/task med={statistics.median(req):.2f}GB basis med={statistics.median(basis):.2f}GB peak med={statistics.median(peak):.2f}GB req/peak={sum(req)/max(sum(peak),1e-9):.2f} | span={span:.2f}s created_window={(last_created-first_created)/1e9:.3f}s busy: " + " ".join(f"{k}:{v:.2f}" for k,v in sorted(busy.items())))
if __name__=="__main__":
    want=sys.argv[1:] or ["q01.r1","q06.r1"]
    for arm in ["cn1","cn4"]:
        runs=json.load(open(f"{SP}/{arm}-cnlog.json")); run_of={}
        for run,o in runs.items():
            for qid in o.get("query_ids",[]): run_of[qid]=run
        allper={}
        for sess in sorted(glob.glob(f"{SP}/{arm}/quent/.cn*/*/")):
            cn=os.path.basename(os.path.dirname(os.path.dirname(sess)))
            per,_=analyze(sess,cn,run_of,want); allper.update(per)
        print(f"## {arm}"); report(allper)
    allper={}
    for sess in sorted(glob.glob(f"{SP}/standalone/quent/*/")):
        per,_=analyze(sess,"alone",None,want); allper.update(per)
    print("## standalone"); report(allper)
