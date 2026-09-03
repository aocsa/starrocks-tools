#!/usr/bin/env python3
"""sim8.py -- agent 8 wrapper around ../sim/replay.py (unchanged) adding the knobs 07-local-experiments.md section 3 asked for.

  replay     <trace.json> [--fe-ms q01:a,b,..] [--fe-ms q06:..] [--async] [--fe-overhead MS] [--threads N] [--quiet]
             per run: predicted FE wall (deploy rule as-run: --async = sender RPC returns at dispatch), the counterfactual
             (other deploy rule), err %, straggler pred/meas, measured scan-Init spread across CNs, measured G = FE - (result Exit - first scan Init),
             per-CN GPU_SCAN task counts and measured compute ends.
  standalone <trace.json> [--fe-ms q01:..] [--fe-ms q06:..] [--threads N]
             each `unnamed_query` fragment = scan+result in one process: list-schedule its pipelines on the executor threads,
             no deploy, no hop; prints predicted compute end vs measured (Executing->Exit) and the client wall.
  service    <trace.json>   per run / per CN: sum of task service (Preparing->Finalizing) of the scan pipeline and per-operator Computing sums.
"""
import argparse, importlib.util, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("replay", os.path.join(HERE, "..", "sim", "replay.py"))
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)


def load(trace):
    sessions = json.load(open(trace))["sessions"]
    threads = {s["cn"]: R.n_threads_of(s) for s in sessions}
    return sessions, threads


def fe_map(specs):
    fe = {}
    for spec_ in specs:
        q, vals = spec_.split(":")
        fe[q] = [float(v) for v in vals.split(",")]
    return fe


def scan_task_count(session, frag):
    pipes = R.task_model(session, frag)
    return len(pipes[0]["tasks"]) if pipes else 0


def cmd_replay(a):
    sessions, threads = load(a.trace)
    if a.threads:
        threads = {cn: a.threads for cn in threads}
    if a.fe_overhead is not None:
        R.K["fe_overhead_ms"] = a.fe_overhead
    fe = fe_map(a.fe_ms)
    runs = R.group_runs(sessions)
    print(f"trace {os.path.basename(a.trace)}: {len(sessions)} CN, threads {threads}, {len(runs)} runs, deploy rule as-run = {'ASYNC sender dispatch' if a.async_ else 'serialized (sender RPC blocks)'}, fe_overhead_ms={R.K['fe_overhead_ms']}")
    idx = {"q01": 0, "q06": 0}
    print("q    run  FE_meas   pred  err%  counterfactual  strag(pred/meas)  merge_host  InitSpread_ms  G_meas  scan_tasks/CN  compute_end_meas/CN")
    for run in runs:
        q = run["q"]; i = idx[q]; idx[q] += 1
        fm = fe.get(q, [])[i] if i < len(fe.get(q, [])) else None
        scans = [x for x in run["frags"] if x[2] == "scan"]
        result = next(x for x in run["frags"] if x[2] == "result")
        empty = [f"cn{x[1]}:{x[2]}" for x in run["frags"] if not any(p["tasks"] for p in x[3]["pipelines"])]
        if empty:
            print(f"{q}  r{i}   FE={fm}  SKIPPED: fragments without flushed pipelines/tasks: {empty}")
            continue
        if len(scans) != len(sessions):
            print(f"{q}  r{i}   FE={fm}  SKIPPED: {len(scans)} scan fragments for {len(sessions)} CNs (partial flush)")
            continue
        if not a.quiet:
            print(f"\n== {q} run{i} (FE measured {fm})")
        try:
            pred, res_end, meas_res_end, cend, meas = R.simulate_run(run, threads, async_sender=a.async_, verbose=not a.quiet)
            cf, *_ = R.simulate_run(run, threads, async_sender=not a.async_, verbose=False)
        except (IndexError, KeyError, ValueError) as e:
            print(f"{q}  r{i}   FE={fm}  SKIPPED: fragment with no flushed tasks/pipelines ({type(e).__name__})")
            continue
        strag_p = max(cend, key=cend.get); strag_m = max(meas, key=lambda c: meas[c]["cend"])
        inits = [x[0] for x in scans]
        spread = (max(inits) - min(inits)) / 1e6
        g = fm - (result[3]["exit"] - min(inits)) / 1e6 if fm else float("nan")
        err = (pred - fm) / fm * 100 if fm else float("nan")
        counts = [scan_task_count(x[4], x[3]) for x in sorted(scans, key=lambda x: x[1])]
        cends = [round(meas[c]["cend"]) for c in sorted(meas)]
        print(f"{q}  r{i}  {fm!s:>7} {pred:6.0f} {err:5.1f}  {cf:9.0f}        cn{strag_p}/cn{strag_m}        cn{result[1]}     {spread:8.1f}    {g:6.0f}   {counts}  {cends}")


def cmd_standalone(a):
    sessions, threads = load(a.trace)
    s = sessions[0]
    thr = a.threads or threads[0]
    fe = fe_map(a.fe_ms)
    print(f"trace {os.path.basename(a.trace)}: standalone, {thr} executor threads, {len(s['fragments'])} queries")
    print("q    run  client_ms  quent_exec_ms  pred_compute_ms  err_vs_quent%  client_floor_ms  scan_tasks  sum_svc_s  mean_task_ms  sum_ops(ms)")
    idx = {"q01": 0, "q06": 0}
    for f in s["fragments"]:
        if not R.complete(f):
            continue
        pipes = R.task_model(s, f)
        q = "q01" if any("HASH_GROUP_BY" in p["chain"] for p in pipes) else "q06"
        i = idx[q]; idx[q] += 1
        fm = fe.get(q, [])[i] if i < len(fe.get(q, [])) else None
        # first pipeline = scan; run_fragment replays all pipelines with barriers
        cend = R.run_fragment(pipes, thr, 0.0)
        exec_ms = (f["exit"] - f["executing"]) / 1e6
        tasks = pipes[0]["tasks"]
        svcs = [t[1] for t in tasks]
        ops = {}
        for p in pipes:
            for t in p["tasks"]:
                for k, v in t[2].items():
                    ops[k] = ops.get(k, 0.0) + v
        opstr = " ".join(f"{k}={v:.0f}" for k, v in sorted(ops.items(), key=lambda kv: -kv[1])[:3])
        floor = fm - exec_ms if fm else float("nan")
        print(f"{q}  r{i}  {fm!s:>8}  {exec_ms:12.0f}  {cend:15.0f}  {(cend-exec_ms)/exec_ms*100:12.1f}  {floor:14.0f}  {len(tasks):9d}  {sum(svcs)/1000:8.1f}  {statistics.mean(svcs):11.0f}  {opstr}")


def cmd_service(a):
    sessions, threads = load(a.trace)
    runs = R.group_runs(sessions)
    idx = {"q01": 0, "q06": 0}
    print(f"trace {os.path.basename(a.trace)}")
    print("q    run  cn  scan_tasks  sum_svc_ms  mean_ms  max_ms  compute_end_ms  ops(ms)")
    for run in runs:
        q = run["q"]; i = idx[q]; idx[q] += 1
        for x in sorted((x for x in run["frags"] if x[2] == "scan"), key=lambda x: x[1]):
            pipes = R.task_model(x[4], x[3])
            if not pipes or not pipes[0]["tasks"]:
                print(f"{q}  r{i}  cn{x[1]}  (no flushed pipelines/tasks)"); continue
            tasks = pipes[0]["tasks"]
            svcs = [t[1] for t in tasks]
            ops = {}
            for t in tasks:
                for k, v in t[2].items():
                    ops[k] = ops.get(k, 0.0) + v
            opstr = " ".join(f"{k}={v:.0f}" for k, v in sorted(ops.items(), key=lambda kv: -kv[1])[:3])
            print(f"{q}  r{i}  cn{x[1]}  {len(tasks):9d}  {sum(svcs):10.0f}  {statistics.mean(svcs):7.0f}  {max(svcs):6.0f}  {R.measured_compute_end(pipes):14.0f}  {opstr}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("replay"); p.add_argument("trace"); p.add_argument("--fe-ms", action="append", default=[])
    p.add_argument("--async", dest="async_", action="store_true"); p.add_argument("--fe-overhead", type=float); p.add_argument("--threads", type=int); p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_replay)
    p = sub.add_parser("standalone"); p.add_argument("trace"); p.add_argument("--fe-ms", action="append", default=[]); p.add_argument("--threads", type=int); p.set_defaults(fn=cmd_standalone)
    p = sub.add_parser("service"); p.add_argument("trace"); p.set_defaults(fn=cmd_service)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
