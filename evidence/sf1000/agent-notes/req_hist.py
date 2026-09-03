import json,glob,os,collections,statistics
SP='/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000'
def rows(s,kind):
    for f in glob.glob(f"{s}/{kind}/*.ndjson"):
        for l in open(f):
            try: yield json.loads(l)
            except Exception: continue
def state(j):
    st=j["data"].get("state",j["data"])
    if isinstance(st,str): return st,{}
    k=next(iter(st)); v=st[k]; return k,(v if isinstance(v,dict) else {})
def run_map(arm):
    m={}
    for run,o in json.load(open(f"{SP}/{arm}-cnlog.json")).items():
        for qid in o.get("query_ids",[]): m[qid]=run
    return m
for arm in ['cn1','cn4']:
    rm=run_map(arm)
    for sess in sorted(glob.glob(f"{SP}/{arm}/quent/.cn*/*/")):
        cn=os.path.basename(os.path.dirname(os.path.dirname(sess)))
        q={}
        for j in rows(sess,"query"):
            k,v=state(j)
            if k=="Init": q[j["id"]]=v.get("query_label") or v.get("instance_name")
        plan_q={}
        for j in rows(sess,"plan"):
            k,v=state(j)
            if k=="Declaration": plan_q[j["id"]]=(v.get("parent") or {}).get("query_id")
        pipe_q={}
        for j in rows(sess,"operator"):
            k,v=state(j)
            if k=="Declaration": pipe_q[j["id"]]=plan_q.get(v.get("plan_id"))
        # scan_split_read events for source_files correlation
        reads=collections.defaultdict(list)
        for d in os.listdir(sess):
            if not os.path.isdir(f"{sess}/{d}"): continue
            for j in rows(sess,d):
                dd=j["data"]
                if isinstance(dd,dict) and "Statistics" in dd:
                    a={x["key"]:next(iter(x["value"].values())) for x in dd["Statistics"]["custom_attributes"]}
                    if a.get("event")=="scan_split_read":
                        run=rm.get((q.get(pipe_q.get(j["id"])) or "").split(":")[0][-12:])
                        if run in ("q01.r1","q01.r2"): reads[run].append((int(a["source_files"]),int(a["materialize_ns"])/1e9))
        tasks=collections.defaultdict(list)
        for j in rows(sess,"task"):
            k,v=state(j); tasks[j["id"]].append((j["timestamp"],k,v))
        hist=collections.defaultdict(collections.Counter); order=collections.defaultdict(list)
        for tid,ev in tasks.items():
            ev.sort(); pu=next((v.get("pipeline_uuid") for _,k,v in ev if k=="Created"),None)
            run=rm.get((q.get(pipe_q.get(pu)) or "").split(":")[0][-12:])
            if run not in ("q01.r1","q06.r1"): continue
            if not any(k=="Computing" and str(v.get("instance_name","")).startswith("GPU_SCAN") for _,k,v in ev): continue
            res=next((v for _,k,v in ev if k=="Reserving"),None)
            if not res: continue
            t_res=next(ts for ts,k,v in ev if k=="Reserving")
            hist[run][round(res["requested_bytes"]/max(res["input_basis"],1),1)]+=1; order[run].append((t_res,round(res["requested_bytes"]/1e9,2)))
        for run in sorted(hist):
            seq=[r for _,r in sorted(order[run])]
            print(f"{arm} {cn} {run}: request/input_basis histogram {dict(hist[run])}; requests (GB) in Reserving order: {seq[:6]} ... {seq[-2:]}; sum={sum(seq):.0f}GB")
        for run,rs in sorted(reads.items()):
            by=collections.defaultdict(list)
            for nf,t in rs: by[nf].append(t)
            print(f"   {arm} {cn} {run} materialize_s by source_files/split: "+"; ".join(f"{nf}f: n={len(v)} med={statistics.median(v):.3f} max={max(v):.3f}" for nf,v in sorted(by.items())))
