#!/usr/bin/env python3
"""Pass criteria for the fix-1 arms (INTEGRATION.md V1a/V1b, oom-failfast-SPEC 6.1/6.2).
usage: check-V1.py <arm_dir_V1a> [<arm_dir_V1b> <baseline_compare_txt>]"""
import sys, os, csv, json, re, glob, collections
def load(arm):
    runs = list(csv.DictReader(open(f"{arm}/runs/runs.csv"))); cn = json.load(open(f"{arm}/cnlog.json")) if os.path.exists(f"{arm}/cnlog.json") else {}
    return runs, cn
a = sys.argv[1]; runs, cn = load(a)
bounds = {'q05': 4.0, 'q08': 6.8, 'q09': 7.3, 'q17': 4.0, 'q18': 11.0, 'q21': 5.3}
print(f"== V1a {a}")
ok_all = True
for r in runs:
    key = f"{r['query']}.r{r['run']}"; o = cn.get(key, {}); e = o.get('engine', {})
    err = open(f"{a}/runs/{r['query']}.r{r['run']}.err").read() if os.path.exists(f"{a}/runs/{r['query']}.r{r['run']}.err") else ''
    txt_ok = ('gave up after' in err) and ('held outside any task reservation' in err) and ('freed 0 bytes' in err) and ('exceeded maximum retry limit' not in err)
    futile = e.get('futile_aborts', 0); exhausted = e.get('retry_exhausted', 0); resched = e.get('oom_reschedules', 0)
    wall = int(r['ms']) / 1000; b = bounds.get(r['query'])
    ok = (r['status'] != 'pass') and txt_ok and futile == 1 and exhausted == 0 and (b is None or wall <= b * 1.5)
    ok_all &= ok
    print(f"{key}: status={r['status']} wall={wall:.1f}s (bound {b}) futile={futile} exhausted={exhausted} reschedules={resched} err_text_ok={txt_ok} -> {'PASS' if ok else 'FAIL'}")
    if not txt_ok: print("   err:", err[:300].replace('\n', ' '))
print("V1a:", "PASS" if ok_all else "FAIL")
if len(sys.argv) > 3:
    b_arm, base_cmp = sys.argv[2], sys.argv[3]; runs_b, cn_b = load(b_arm)
    print(f"== V1b {b_arm}")
    st = collections.defaultdict(set)
    for r in runs_b: st[r['query']].add(r['status'])
    allpass = all(v == {'pass'} for v in st.values())
    resched = sum(cn_b.get(k, {}).get('engine', {}).get('oom_reschedules', 0) for k in cn_b if not k.startswith('_'))
    futile = sum(cn_b.get(k, {}).get('engine', {}).get('futile_aborts', 0) for k in cn_b if not k.startswith('_'))
    partial = sum(cn_b.get(k, {}).get('engine', {}).get('partial_proceed', 0) for k in cn_b if not k.startswith('_'))
    print(f"all pass={allpass} reschedules={resched} futile={futile} partial_proceed={partial}")
    print("V1b:", "PASS" if (allpass and resched == 0 and futile == 0 and partial == 0) else "FAIL (check compare + medians separately)")
