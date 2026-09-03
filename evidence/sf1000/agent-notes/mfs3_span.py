import csv,re,sys,datetime as dt
ansi=re.compile(r'\x1b\[[0-9;]*m')
def parse(ts): return dt.datetime.strptime(ts[:26],'%Y-%m-%dT%H:%M:%S.%f')
for arm in ['cn1','cn4']:
    lines=[]
    with open(f'{arm}/cluster.log',errors='replace') as f:
        for l in f:
            l=ansi.sub('',l)
            if l.startswith('2026-09-03T') and ('fragment run started' in l or 'fragment run finished' in l or 'get_file_schema: sirius' in l or 'exec_plan_fragment: sirius' in l):
                lines.append((parse(l[:27]),l))
    rows=list(csv.DictReader(open(f'{arm}/runs/runs.csv')))
    for r in rows:
        if r['query'] not in ('q02','q11','q15','q22'): continue
        s=parse(r['start_utc']+'000'); e=s+dt.timedelta(milliseconds=int(r['ms']))
        win=[(t,l) for t,l in lines if s<=t<=e]
        st=[t for t,l in win if 'fragment run started' in l]; fi=[t for t,l in win if 'fragment run finished' in l]
        sch=[t for t,l in win if 'get_file_schema' in l]; epf=[t for t,l in win if 'exec_plan_fragment' in l]
        if not st: continue
        span=(max(fi)-min(st)).total_seconds()*1000; pre=(min(st)-s).total_seconds()*1000; tail=(e-max(fi)).total_seconds()*1000
        print(f"{arm} {r['query']} r{r['run']} client={r['ms']} pre_first_frag={pre:.0f} span={span:.0f} tail={tail:.0f} schema_rpcs={len(sch)} schema_end=+{(max(sch)-s).total_seconds()*1000:.0f} first_epf=+{(min(epf)-s).total_seconds()*1000:.0f} epf_n={len(epf)}")
