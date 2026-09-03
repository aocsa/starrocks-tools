import re, csv, sys
from datetime import datetime, timedelta
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT='/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000'
def pts(s):
    # 2026-09-03T13:03:03.596049Z
    return datetime.strptime(s[:26], '%Y-%m-%dT%H:%M:%S.%f')
def pfe(s):
    # 2026-09-03 13:03:03.596+00:00
    return datetime.strptime(s[:23], '%Y-%m-%d %H:%M:%S.%f')
def dur(s):
    m=re.match(r'([\d.]+)(µs|ms|s)', s)
    v=float(m.group(1)); u=m.group(2)
    return v/1000 if u=='µs' else (v if u=='ms' else v*1000)
def load(arm):
    ev=[]
    for line in open(f'{ROOT}/{arm}/cluster.log', errors='replace'):
        l=ANSI.sub('', line).rstrip('\n')
        m=re.match(r'(\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(\w+)\s+(.*)', l)
        if m:
            t=pts(m.group(1)); rest=m.group(3)
            if 'get_file_schema: sirius_starrocks_cn::compute_node_service: close' in rest:
                b=re.search(r'time.busy=(\S+) time.idle=(\S+)', rest)
                peer=re.search(r'peer=(\S+)', rest)
                ev.append((t,'schema',dur(b.group(1)),dur(b.group(2)),peer.group(1) if peer else ''))
            elif 'exec_plan_fragment: sirius_starrocks_cn::compute_node_service: close' in rest:
                b=re.search(r'time.busy=(\S+) time.idle=(\S+)', rest)
                ev.append((t,'exec',dur(b.group(1)),dur(b.group(2)),''))
            elif 'exec_batch_plan_fragments' in rest and 'close' in rest:
                ev.append((t,'execbatch',0,0,''))
            elif 'fetch_data: sirius_starrocks_cn::compute_node_service: close' in rest:
                b=re.search(r'time.busy=(\S+) time.idle=(\S+)', rest)
                ev.append((t,'fetch',dur(b.group(1)),dur(b.group(2)),''))
            elif 'fragment run started' in rest:
                cn=re.search(r'cn=(\S+)', rest).group(1); role=re.search(r'role="(\w+)"', rest).group(1)
                ev.append((t,'start',0,0,cn+' '+role))
            elif 'fragment run finished' in rest:
                cn=re.search(r'cn=(\S+)', rest).group(1); role=re.search(r'role="(\w+)"', rest).group(1)
                e=re.search(r'elapsed_ms=(\d+)', rest)
                ev.append((t,'finish',float(e.group(1)),0,cn+' '+role))
            continue
        m=re.match(r'(\d{4}-\d\d-\d\d [\d:.]+)Z (\w+) \((.*?)\) \[(\S+)\] (.*)', l)
        if m:
            t=pfe(m.group(1)); where=m.group(4); rest=m.group(5)
            if 'listFileMeta' in where:
                ev.append((t,'list',0,0,rest[:80]))
            elif 'AuditLog' in where:
                ev.append((t,'audit',0,0,rest))
    ev.sort(key=lambda e:e[0])
    return ev
def runs(arm, qs):
    out=[]
    for r in csv.DictReader(open(f'{ROOT}/{arm}/runs/runs.csv')):
        if r['query'] in qs:
            out.append((r['query'],int(r['run']),datetime.strptime(r['start_utc'],'%Y-%m-%dT%H:%M:%S.%f'),int(r['ms'])))
    return out
def ms(a,b): return (b-a).total_seconds()*1000
def main(arm, qs, verbose=False):
    ev=load(arm)
    for q,run,start,wall in runs(arm,qs):
        end=start+timedelta(milliseconds=wall)
        w=[e for e in ev if start-timedelta(milliseconds=5)<=e[0]<=end+timedelta(milliseconds=1500)]
        sch=[e for e in w if e[1]=='schema' and e[0]<=end]
        ex=[e for e in w if e[1]=='exec' and e[0]<=end]
        st=[e for e in w if e[1]=='start' and e[0]<=end]
        fi=[e for e in w if e[1]=='finish' and e[0]<=end+timedelta(milliseconds=50)]
        li=[e for e in w if e[1]=='list' and e[0]<=end]
        au=[e for e in w if e[1]=='audit' and q in e[4] and f'r{run} ' in e[4]]
        if not st: print(f'{arm} {q}.r{run}: no fragment runs in window'); continue
        first_sch_start = sch[0][0]-timedelta(milliseconds=sch[0][2]+sch[0][3]) if sch else None
        head=ms(start, st[0][0]); span=ms(st[0][0], fi[-1][0]); tail=ms(fi[-1][0], end)
        # idle inside span: union of [start,finish] intervals per fragment
        ivs=[]
        # pair start/finish by order using elapsed_ms on finish
        for f in fi:
            ivs.append((f[0]-timedelta(milliseconds=f[2]), f[0]))
        ivs.sort(); busy=0; cur=None
        for a,b in ivs:
            if cur is None: cur=[a,b]
            elif a<=cur[1]: cur[1]=max(cur[1],b)
            else: busy+=ms(cur[0],cur[1]); cur=[a,b]
        if cur: busy+=ms(cur[0],cur[1])
        audit=''
        if au:
            m=re.search(r'Time=(\d+)', au[0][4]); audit=f' FE_audit_Time={m.group(1) if m else "?"}'
            m2=re.search(r'Timestamp=(\d+)', au[0][4])
        sch_str=''
        if sch:
            sch_str=(f' schema: n={len(sch)} first_start=+{ms(start,first_sch_start):.0f} last_end=+{ms(start,sch[-1][0]):.0f}'
                     f' sum_busy={sum(e[2] for e in sch):.1f} sum_idle={sum(e[3] for e in sch):.1f}')
        ex_str=''
        if ex:
            ex_str=f' exec_rpc: n={len(ex)} first=+{ms(start,ex[0][0]):.0f} last=+{ms(start,ex[-1][0]):.0f}'
        li_str=f' list: n={len(li)} first=+{ms(start,li[0][0]):.0f} last=+{ms(start,li[-1][0]):.0f}' if li else ''
        print(f'{arm} {q}.r{run} wall={wall} head(first_run-client_start)={head:.0f} engine_span={span:.0f} span_busy={busy:.0f} span_idle={span-busy:.0f} tail(client_end-last_finish)={tail:.0f} frags={len(st)}{audit}{sch_str}{li_str}{ex_str}')
        if verbose:
            for e in w:
                if e[0]<=end+timedelta(milliseconds=50):
                    print(f'   +{ms(start,e[0]):7.1f} {e[1]:6} {e[2]:.1f} {e[3]:.1f} {e[4][:90]}')
if __name__=='__main__':
    arm=sys.argv[1]; qs=sys.argv[2].split(','); verbose=len(sys.argv)>3
    main(arm,qs,verbose)
