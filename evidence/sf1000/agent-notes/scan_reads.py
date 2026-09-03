#!/usr/bin/env python3
"""scan_split_read Statistics events (compressed_bytes, output_bytes, materialize_ns, sources) per arm/CN for q01/q06,
joined to GPU_SCAN task durations; plus one task's full FSM and the staging-lease timestamps around q06.r2 on cn4."""
import json, glob, os, sys, collections, statistics, re
SP='/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000'
def rows(s, kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: yield json.loads(l)
            except Exception: continue
def state(j):
    st=j["data"].get("state", j["data"])
    if isinstance(st,str): return st,{}
    k=next(iter(st)); v=st[k]; return k,(v if isinstance(v,dict) else {})
def load(sess):
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
        if k=="Declaration": pipe[j["id"]]=plan_q.get(v.get("plan_id"))
    reads=[]
    for d in os.listdir(sess):
        if not os.path.isdir(f"{sess}/{d}"): continue
        for j in rows(sess,d):
            dd=j["data"]
            if isinstance(dd,dict) and "Statistics" in dd:
                attrs={a["key"]:next(iter(a["value"].values())) for a in dd["Statistics"]["custom_attributes"]}
                if attrs.get("event")=="scan_split_read":
                    attrs["ts"]=j["timestamp"]; attrs["qid"]=pipe.get(j["id"]); reads.append(attrs)
    tasks=collections.defaultdict(list)
    for j in rows(sess,"task"):
        k,v=state(j); tasks[j["id"]].append((j["timestamp"],k,v))
    return q,pipe,reads,tasks
def run_map(arm):
    runs=json.load(open(f"{SP}/{arm}-cnlog.json")); m={}
    for run,o in runs.items():
        for qid in o.get("query_ids",[]): m[qid]=run
    return m
def pct(xs,p): xs=sorted(xs); return xs[min(len(xs)-1,int(p*len(xs)))]
want=sys.argv[1:] or ["q01.r1","q01.r2","q06.r1"]
shown_task=False
for arm in ["cn1","cn4","standalone"]:
    rm=run_map(arm) if arm!="standalone" else None
    for sess in sorted(glob.glob(f"{SP}/{arm}/quent/.cn*/*/") if arm!="standalone" else glob.glob(f"{SP}/standalone/quent/*/")):
        cn=os.path.basename(os.path.dirname(os.path.dirname(sess))) if arm!="standalone" else "alone"
        q,pipe,reads,tasks=load(sess)
        def runname(qid):
            lab=(q.get(qid) or {}).get("label") or ""
            head=lab.split(":")[0]
            return rm.get(head[-12:]) if rm else lab
        by=collections.defaultdict(list)
        for r in reads:
            rn=runname(r["qid"])
            if rn and any(w in rn for w in want): by[rn].append(r)
        for rn,rs in sorted(by.items()):
            cb=[int(r["compressed_bytes"]) for r in rs]; ob=[int(r["output_bytes"]) for r in rs]; mn=[int(r["materialize_ns"])/1e9 for r in rs]; fn=[int(r["finish_ns"])/1e9 for r in rs]
            files=collections.Counter(int(r["source_files"]) for r in rs)
            print(f"{arm} {cn} {rn}: splits={len(rs)} compressed={sum(cb)/1e9:.1f}GB (med {statistics.median(cb)/1e9:.2f}) output={sum(ob)/1e9:.1f}GB materialize_s: sum={sum(mn):.2f} med={statistics.median(mn):.3f} p90={pct(mn,.9):.3f} max={max(mn):.3f} finish_s sum={sum(fn):.2f} files/split={dict(files)}")
            slow=sorted(rs,key=lambda r:-int(r["materialize_ns"]))[:4]
            for r in slow: print(f"      slow split materialize={int(r['materialize_ns'])/1e9:.3f}s comp={int(r['compressed_bytes'])/1e9:.2f}GB rows={r['rows']} sources={str(r['sources'])[:140]}")
        # one scan task's full FSM (first arm only)
        if not shown_task and arm=="cn1":
            for tid,ev in tasks.items():
                ev.sort()
                if any(k=="Computing" and str(v.get("instance_name","")).startswith("GPU_SCAN") for _,k,v in ev) and len(ev)>8:
                    print("### one scan task FSM:")
                    for ts,k,v in ev: print(f"   {ts} {k} {json.dumps({kk:vv for kk,vv in v.items() if kk in ('instance_name','requested_bytes','input_basis','peak_estimate','peak_allocated_bytes','input_bytes','origin_tier','target_tier','reservation')})}")
                    shown_task=True; break
        # lease timestamps vs q06.r2 span (cn4 only)
        if arm=="cn4":
            spans={runname(qid):(info["t0"],info["t1"]) for qid,info in q.items()}
            tgt=[(k,v) for k,v in spans.items() if k and k.startswith("q06.r2")]
            nxt=[(k,v) for k,v in spans.items() if k and k.startswith("q07.r0")]
            leases=[]
            for j in rows(sess,"data_batch"):
                k,v=state(j)
                if k=="Constructed" and v.get("instance_name")=="staging_lease": leases.append(j["timestamp"])
            if tgt:
                t0=min(v[0] for _,v in tgt); t1=max(v[1] for _,v in tgt)
                inside=[t for t in leases if t0<=t<=t1]; after=[t for t in leases if t1<t<=t1+5e8]
                n0=min((v[0] for _,v in nxt),default=None)
                print(f"   [{cn}] q06.r2 span {t0}..{t1} ({(t1-t0)/1e9:.3f}s): leases inside={len(inside)} within +0.5s after={len(after)} q07.r0 starts {((n0-t1)/1e9 if n0 else None)}s after q06.r2 ends")
