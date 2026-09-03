#!/usr/bin/env python3
"""replay.py -- discrete-event replay of batch movement between StarRocks plan
fragments running on Sirius compute nodes.  stdlib only.  Input is the JSON
written by extract.py from one cluster generation (one Quent session per CN,
all on one host clock).

What is simulated (nothing SQL-level):
  * per CN: N executor threads (from the session's `executor_thread` entities),
    one engine thread (fragments run one at a time), one transport thread
    (remote drains run one destination at a time);
  * per fragment: FE translate + DuckDB build (constant), split discovery
    (measured `Init -> first Preparing`), tasks list-scheduled FIFO onto the
    executor threads with the service time measured on each task
    (`Finalizing.timestamp - Preparing.timestamp`), pipeline barriers
    (P1 starts when every P0 task exited, ...);
  * sender park + drain: local destination = pointer hand-off at compute end;
    each remote destination = one serial drain (constant + bytes / bandwidth);
  * receiver rendezvous (take_ready): a receiver starts when every sender's
    frame for it has landed and its CN's engine thread is free;
  * FE two-phase deploy: phase 1 = the top-most fragment instance on each CN,
    phase 2 (rest) is sent when every phase-1 RPC returned; a sender's RPC
    returns when its drains are joined, a receiver's returns immediately;
  * FE overhead (parse/plan/deploy/fetch/MySQL) = constant residual.

usage: replay.py sf1000-4cn-warm.json [--onecn sf1000-1cn-warm.json]
                 [--fe-ms q01:7488,7430,7088,6893 --fe-ms q06:1332,1219,1331,1245]
"""
import argparse
import json
import statistics
import sys

# --- calibration constants (ms); each is named in the output with its source ---
K = {
    "build_ms": 20.0,        # scan fragment: 'translated StarRocks plan fragment' -> Quent query.Init (05:59:52.499 -> .518 on cn1..3, 53.863 -> .883 on cn0; dec-sf1000-4cn/cluster.log vs Quent)
    "build_recv_ms": 2.0,    # receiver/result fragment build at dispatch (receivers' Init lands 6-15 ms after the straggler's last task Exit incl. 3 drains)
    "teardown_ms": 3.0,      # last task Exit -> sender parked / first drain issued (q06 cold gen: 53.346 -> 53.351 incl. one drain)
    "drain_ms": 3.0,         # one remote destination: lease RTT + nixl WRITE + transmit RPC + eos (q01: receivers start 6-15 ms after the straggler's last task with 3 remote destinations)
    "dispatch_ms": 1.0,      # take_ready -> dispatch worker -> engine Run
    "barrier_ms": 0.1,       # pipeline k done -> pipeline k+1 tasks created (P0 last Exit +6855.6 -> P1 Created +6855.7)
    "fe_phase_gap_ms": 2.0,  # last phase-1 RPC close 53.861 -> phase-2 'translated' 53.863
    "fe_overhead_ms": 175.0, # FE ms - (result Exit - (first scan Init - build_ms)): 165/186/183/193 (q01) 165/160/168/169 (q06) on this generation
    "nixl_gbps": 350.0,      # 'nixl bandwidth canary ... gbps' 302-404 to 9112/9122/9132 (85-107 to 9102)
}


def kind(f):
    if f["label"] == "sirius_ffi":
        return "result"
    if any(p["chain"].startswith("STREAMING_SOURCE") for p in f["pipelines"]):
        return "recv"
    return "scan"


def complete(f):
    return all(f.get(k) is not None for k in ("init", "executing", "exit"))


def task_model(session, f):
    """Per pipeline: list of (arrival_offset_ms from fragment Init, service_ms, op_sums)."""
    pipes = []
    for p in f["pipelines"]:
        ts = []
        for tid in p["tasks"]:
            t = session["tasks"][tid]
            if not (t["created"] and t["preparing"] and t["finalizing"]):
                continue
            svc = (t["finalizing"] - t["preparing"]) / 1e6
            ops = {c[0]: (c[2] - c[1]) / 1e6 for c in t["computing"] if c[2]}
            ts.append(((t["created"] - f["init"]) / 1e6, svc, ops, (t["exit"] - f["init"]) / 1e6))
        ts.sort()
        bytes_out = sum((session["batches"][b]["bytes"] or 0) for b in p["batches_out"])
        pipes.append({"pid": p["pipeline_id"], "chain": p["chain"], "tasks": ts,
                      "bytes_out": bytes_out, "n_batches_out": len(p["batches_out"])})
    pipes.sort(key=lambda p: p["pid"])
    return pipes


def list_schedule(tasks, n_threads, t0):
    """FIFO list scheduling of (arrival, service) on n identical threads. Returns end time."""
    free = [t0] * n_threads
    end = t0
    for arr, svc, *_ in tasks:
        i = min(range(n_threads), key=lambda k: free[k])
        start = max(t0 + arr, free[i])
        free[i] = start + svc
        end = max(end, free[i])
    return end


def run_fragment(pipes, n_threads, t_init, closed=False):
    """Replay a fragment's pipelines from Quent Init at t_init; returns compute end (last task exit)."""
    t = t_init
    end = t_init
    for k, p in enumerate(pipes):
        if not p["tasks"]:
            continue
        if k == 0:
            tasks = p["tasks"]
            if closed:  # every split available at the first one's discovery time
                first = tasks[0][0]
                tasks = [(first, svc, ops, ex) for _, svc, ops, ex in tasks]
            end = list_schedule(tasks, n_threads, t_init)
        else:
            t = end + K["barrier_ms"]
            tasks = [(0.0, svc, ops, ex) for _, svc, ops, ex in p["tasks"]]
            end = list_schedule(tasks, n_threads, t)
    return end


def measured_compute_end(pipes):
    return max((ex for p in pipes for *_, ex in p["tasks"]), default=0.0)


def group_runs(sessions):
    allf = []
    for s in sessions:
        for f in s["fragments"]:
            if not complete(f):
                continue
            allf.append((f["init"], s["cn"], kind(f), f, s))
    allf.sort(key=lambda x: x[0])
    runs, cur = [], []
    for x in allf:
        cur.append(x)
        if x[2] == "result":
            runs.append(cur)
            cur = []
    out = []
    for r in runs:
        scans = [x for x in r if x[2] == "scan"]
        q = "q01" if any("HASH_GROUP_BY" in p["chain"] for x in scans for p in x[3]["pipelines"]) else "q06"
        out.append({"q": q, "frags": r})
    return out


def n_threads_of(session):
    n = session["n_executor_threads"]
    if not n:  # session killed before the executor_thread file was flushed: count from tasks
        n = len({t["executor_thread"] for t in session["tasks"].values() if t["executor_thread"]})
    return n or 4


def hop_ms(bytes_):
    return K["drain_ms"] + bytes_ * 8 / (K["nixl_gbps"] * 1e6)


def simulate_run(run, threads, async_sender=False, closed=False, verbose=True):
    """Returns predicted FE ms and per-fragment predicted/measured compute ends."""
    frags = run["frags"]
    t_run0 = min(x[0] for x in frags) - K["build_ms"] * 1e6  # sim origin = FE deploy of the first scans
    cns = sorted({x[1] for x in frags})
    scans = {x[1]: x for x in frags if x[2] == "scan"}
    recvs = {x[1]: x for x in frags if x[2] == "recv"}
    result = next(x for x in frags if x[2] == "result")
    result_cn = result[1]
    models = {id(x[3]): task_model(x[4], x[3]) for x in frags}

    # --- FE two-phase deploy -------------------------------------------------
    # phase 1 = the top-most fragment instance per CN (result > recv > scan)
    phase1 = {}
    for cn in cns:
        if cn == result_cn:
            phase1[cn] = "result"
        elif cn in recvs:
            phase1[cn] = "recv"
        else:
            phase1[cn] = "scan"
    # receivers/result register and return at once; scans block until their drains join,
    # unless the sender RPC is asynchronous (the perf-worktree fix).
    t_phase2 = 0.0
    engine_free = {cn: 0.0 for cn in cns}
    scan_start, scan_cend, scan_rpc_ret = {}, {}, {}
    # scans whose deploy is in phase 1 start at t=0
    for cn, x in scans.items():
        if phase1[cn] == "scan":
            scan_start[cn] = 0.0
    # everything else in phase 2; need phase-1 RPC returns -> need phase-1 scan drains
    recv_cns = sorted(recvs) if recvs else [result_cn]  # destinations of the scans
    remote_dest = {cn: [d for d in recv_cns if d != cn] for cn in scans}

    def run_scan(cn, t_start):
        x = scans[cn]
        pipes = models[id(x[3])]
        t_init = t_start + K["build_ms"]
        engine_free[cn] = t_init
        cend = run_fragment(pipes, threads[cn], t_init, closed)
        scan_cend[cn] = cend
        # park + drains (serial on the transport thread, FE destination order)
        t = cend + K["teardown_ms"]
        landed = {}
        for d in recv_cns:
            if d == cn:
                landed[d] = cend + K["teardown_ms"]  # local: pointer hand-off at push_sender
            else:
                t += hop_ms(pipes[-1]["bytes_out"] / max(1, len(recv_cns)))
                landed[d] = t
        scan_rpc_ret[cn] = t
        engine_free[cn] = cend
        return landed

    landed_at = {}  # (sender_cn) -> {dest_cn: t}
    for cn in scans:
        if phase1[cn] == "scan":
            landed_at[cn] = run_scan(cn, 0.0)
            t_phase2 = max(t_phase2, 0.0 if async_sender else scan_rpc_ret[cn])
    t_phase2 += K["fe_phase_gap_ms"]
    for cn in scans:
        if phase1[cn] != "scan":
            scan_start[cn] = t_phase2
            landed_at[cn] = run_scan(cn, t_phase2)

    # --- receivers (q01) ---------------------------------------------------
    recv_cend, recv_landed = {}, {}
    for cn, x in recvs.items():
        ready = max(landed_at[s][cn] for s in scans)
        t_start = max(ready, engine_free[cn]) + K["dispatch_ms"]
        t_init = t_start + K["build_recv_ms"]
        cend = run_fragment(models[id(x[3])], threads[cn], t_init, closed)
        recv_cend[cn] = (t_init, cend)
        engine_free[cn] = cend
        pipes = models[id(x[3])]
        if cn == result_cn:
            recv_landed[cn] = cend + K["teardown_ms"]
        else:
            recv_landed[cn] = cend + K["teardown_ms"] + hop_ms(pipes[-1]["bytes_out"])

    # --- result fragment ---------------------------------------------------
    if recvs:
        ready = max(recv_landed.values())
    else:
        ready = max(landed_at[s][result_cn] for s in scans)
    t_start = max(ready, engine_free[result_cn]) + K["dispatch_ms"]
    t_init = t_start + K["build_recv_ms"]
    res_end = run_fragment(models[id(result[3])], threads[result_cn], t_init, closed)
    pred_fe = res_end + K["fe_overhead_ms"]

    # --- measured -----------------------------------------------------------
    meas = {}
    for cn, x in scans.items():
        f = x[3]
        meas[cn] = {
            "start": (f["init"] - t_run0) / 1e6 - K["build_ms"],  # FE deploy ~ Init - build
            "cend": (f["init"] - t_run0) / 1e6 + measured_compute_end(models[id(f)]),
            "exit": (f["exit"] - t_run0) / 1e6,
        }
    meas_res_end = (result[3]["exit"] - t_run0) / 1e6
    meas_recv = {cn: ((x[3]["init"] - t_run0) / 1e6, (x[3]["init"] - t_run0) / 1e6 + measured_compute_end(models[id(x[3])])) for cn, x in recvs.items()}

    if verbose:
        strag_p = max(scan_cend, key=scan_cend.get)
        strag_m = max(meas, key=lambda c: meas[c]["cend"])
        for cn in sorted(scans):
            n = len(models[id(scans[cn][3])][0]["tasks"])
            print(f"    cn{cn} scan  tasks={n:3d} start pred={scan_start[cn]:7.1f} meas={meas[cn]['start']:7.1f}"
                  f"  compute_end pred={scan_cend[cn]:8.1f} meas={meas[cn]['cend']:8.1f} ({scan_cend[cn]-meas[cn]['cend']:+6.1f})"
                  f"  rpc_ret pred={scan_rpc_ret[cn]:8.1f} meas(Exit)={meas[cn]['exit']:8.1f}")
        for cn in sorted(recvs):
            print(f"    cn{cn} recv  start pred={recv_cend[cn][0]:8.1f} meas(Init)={meas_recv[cn][0]:8.1f}  end pred={recv_cend[cn][1]:8.1f} meas={meas_recv[cn][1]:8.1f}")
        print(f"    cn{result_cn} result end pred={res_end:8.1f} meas(Exit)={meas_res_end:8.1f}   straggler pred=cn{strag_p} meas=cn{strag_m}")
    return pred_fe, res_end, meas_res_end, scan_cend, meas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--onecn")
    ap.add_argument("--fe-ms", action="append", default=[])
    ap.add_argument("--closed", action="store_true")
    a = ap.parse_args()
    fe = {}
    for spec in a.fe_ms:
        q, vals = spec.split(":")
        fe[q] = [float(v) for v in vals.split(",")]

    sessions = json.load(open(a.trace))["sessions"]
    threads = {s["cn"]: n_threads_of(s) for s in sessions}
    runs = group_runs(sessions)
    print(f"trace {a.trace}: {len(sessions)} CN sessions, executor threads {threads}, {len(runs)} runs")
    print("constants (ms):", ", ".join(f"{k}={v}" for k, v in K.items()))

    idx = {"q01": 0, "q06": 0}
    rows = []
    for run in runs:
        q = run["q"]
        i = idx[q]
        idx[q] += 1
        fe_meas = fe.get(q, [None] * 8)[i] if i < len(fe.get(q, [])) else None
        print(f"\n== {q} run{i}  (FE measured {fe_meas} ms)")
        pred_fe, res_end, meas_res_end, cend, meas = simulate_run(run, threads, closed=a.closed)
        pred_async, *_ = simulate_run(run, threads, async_sender=True, verbose=False)
        span_pred = res_end
        rows.append((q, i, fe_meas, pred_fe, meas_res_end + K["fe_overhead_ms"], pred_async))
        print(f"    query wall: predicted {pred_fe:7.0f} ms   measured FE {fe_meas} ms   "
              f"(trace span + fe_overhead = {meas_res_end + K['fe_overhead_ms']:.0f})   "
              f"counterfactual async sender dispatch: {pred_async:7.0f} ms")

    print("\n== summary (ms)")
    print("q    run  FE_meas  pred   err%   pred_async_sender")
    for q, i, fm, pf, _, pa in rows:
        err = (pf - fm) / fm * 100 if fm else float("nan")
        print(f"{q}  r{i}   {fm!s:>7} {pf:6.0f} {err:6.1f}   {pa:6.0f}")

    # --- out-of-sample: N-CN prediction from the 1-CN session -----------------
    if a.onecn:
        one = json.load(open(a.onecn))["sessions"][0]
        thr = n_threads_of(one)
        runs1 = group_runs([one])
        print(f"\n== N-CN prediction from the 1-CN trace {a.onecn} ({thr} executor threads)")
        for run in runs1:
            q = run["q"]
            scan = next(x for x in run["frags"] if x[2] == "scan")
            pipes = task_model(scan[4], scan[3])
            tasks = pipes[0]["tasks"]
            n = len(tasks)
            svcs = [t[1] for t in tasks]
            print(f"  {q}: {n} scan tasks, service mean {statistics.mean(svcs):.0f} ms, median {statistics.median(svcs):.0f}, "
                  f"max {max(svcs):.0f}, sum {sum(svcs)/1000:.1f} s; measured scan fragment compute {measured_compute_end(pipes):.0f} ms")
            first = tasks[0][0]
            for N in (1, 2, 4, 8):
                # contiguous byte ranges per CN (scan_paths.rs assigns disjoint ranges per instance)
                chunks = [tasks[k * n // N:(k + 1) * n // N] for k in range(N)]
                ends = []
                for ch in chunks:
                    ch = [(first, svc, ops, ex) for _, svc, ops, ex in ch]
                    e = list_schedule(ch, thr, K["build_ms"])
                    # P1/P2 tails
                    for p in pipes[1:]:
                        e = list_schedule([(0.0, svc, ops, ex) for _, svc, ops, ex in p["tasks"]], thr, e + K["barrier_ms"])
                    ends.append(e)
                strag = max(ends)
                if q == "q01":
                    hop = K["teardown_ms"] + (N - 1) * hop_ms(300) + K["dispatch_ms"] + K["build_recv_ms"] + 12.0  # receiver compute ~12 ms measured
                    hop2 = K["teardown_ms"] + hop_ms(400) + K["dispatch_ms"] + K["build_recv_ms"] + 5.0
                    pred = strag + hop + hop2 + K["fe_overhead_ms"]
                    pred_note = ""
                else:
                    # today's deploy: coordinator scan runs after the remote scans' RPCs return
                    remote = max(ends[1:]) if N > 1 else 0.0
                    coord = ends[0]
                    pred = (remote + K["teardown_ms"] + hop_ms(64) + K["fe_phase_gap_ms"] if N > 1 else 0.0) + coord + K["teardown_ms"] + K["dispatch_ms"] + K["build_recv_ms"] + 2.0 + K["fe_overhead_ms"]
                    pred_async = strag + K["teardown_ms"] + hop_ms(64) + K["dispatch_ms"] + K["build_recv_ms"] + 2.0 + K["fe_overhead_ms"]
                    pred_note = f"  (async sender dispatch: {pred_async:.0f})"
                print(f"    N={N}: per-CN tasks {[len(c) for c in chunks]}, predicted scan compute per CN {[round(e) for e in ends]}, predicted FE wall {pred:.0f} ms{pred_note}")


if __name__ == "__main__":
    sys.exit(main())
