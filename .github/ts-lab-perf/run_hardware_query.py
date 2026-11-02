#!/usr/bin/env python3
"""Attach sudo perf hardware counters or DWARF call stacks to the actual CH child.

Runs one query only. Restarts and source hashes are controlled by the CI workflow.
Performance is instrumented; do not compare this wall time to bare benchmark.
"""
import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error

sys.path.insert(0, ".github/ts-lab-perf")
from capture import (Sampler,aggregate_process,ch_sql,ch_logs,ch_explain,
                     host_counters,environment,delta,json_eachrow)

def child_pid(root):
    # ClickHouse binary starts a ClickHouseWatch parent on this CI runner.
    todo=[root];seen=set()
    while todo and len(seen)<32:
        pid=todo.pop()
        if pid in seen:continue
        seen.add(pid)
        comm=pathlib.Path(f"/proc/{pid}/comm")
        label=comm.read_text().strip() if comm.exists() else ""
        if pid!=root and label.startswith("clickhouse"):
            return pid
        children=pathlib.Path(f"/proc/{pid}/task/{pid}/children")
        if children.exists():
            todo.extend(int(x) for x in children.read_text().split() if x.isdigit())
    if pathlib.Path(f"/proc/{root}/comm").read_text().strip()=="clickhouse":
        return root
    raise RuntimeError("cannot locate clickhouse server descendant of watchdog: "+str(seen))

def perf_command(kind,pid,out):
    if kind=="stat":
        events="task-clock,cpu-clock,context-switches,page-faults,cycles,instructions,cache-misses,branch-misses,branches"
        return ["sudo","-n","perf","stat","-x,","-e",events,"-p",str(pid),
                "-o",str(out/"perf-stat.csv")]
    return ["sudo","-n","perf","record","-e","cpu-clock","-F","99","-g",
            "--call-graph","dwarf,4096","-p",str(pid),
            "-o",str(out/"perf.data")]

def stop_perf(proc,out):
    if proc.poll() is None:
        try:os.killpg(proc.pid,signal.SIGINT)
        except ProcessLookupError:pass
        try:proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            proc.wait(timeout=5)
    return proc.returncode

def perf_stat_summary(raw):
    counters={}
    for line in raw.splitlines():
        s=line.strip()
        if not s or s.startswith("#"):continue
        items=s.split(",")
        if len(items)<3:continue
        value=items[0].replace(" ","").replace(",","")
        event=items[2].strip()
        try:counters[event]=float(value)
        except ValueError:counters[event]=None
    instructions=counters.get("instructions") or 0
    cycles=counters.get("cycles") or 0
    if instructions and cycles:counters["IPC"]=round(instructions/cycles,4)
    counters["hardware_pmu_supported"]=any(counters.get(k) is not None for k in ("cycles","instructions","cache-misses"))
    counters["software_clock_supported"]=bool(counters.get("task-clock"))
    return counters

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pid-file",required=True)
    p.add_argument("--sql-file",required=True)
    p.add_argument("--kind",required=True,choices=["stat","record"])
    p.add_argument("--case",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--expected-points",type=int,required=True)
    p.add_argument("--data-path",required=True)
    a=p.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    parent=int(pathlib.Path(a.pid_file).read_text())
    target=child_pid(parent)
    source=pathlib.Path(a.sql_file).read_text()
    profile={"case":a.case,"kind":a.kind,"watchdog_pid":parent,"server_pid":target,
             "query_sha256":__import__("hashlib").sha256(source.encode()).hexdigest(),
             "expected_points":a.expected_points,
             "environment":environment([a.data_path],target)}
    if any(x.get("free_fraction",1)<.20 for x in profile["environment"]["disk"]):
        raise SystemExit("INVALID_ENVIRONMENT: disk free under 20%")
    before=host_counters();proc_before=aggregate_process(target)
    qid="hw_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+a.case+"_"+a.kind
    profile["query_id"]=qid
    start_err=out/"perf-start.stderr.txt"
    with start_err.open("w") as err:
        command=perf_command(a.kind,target,out)
        perf=subprocess.Popen(command,start_new_session=True,
                              stdout=subprocess.DEVNULL,stderr=err,stdin=subprocess.DEVNULL)
        time.sleep(.30)
        if perf.poll() is not None:
            profile["perf_start_error"]=start_err.read_text()[:1600]
    sampler=Sampler(target,.15);sampler.start()
    start=time.perf_counter()
    try:
        raw=ch_sql("http://127.0.0.1:18191",source,query_id=qid,timeout=180)
        data=json_eachrow(raw)
        profile["points"]=sum(len(r.get("samples",[])) for r in data)
        (out/"result.json").write_text(json.dumps(data,separators=(",",":"))+"\n")
        profile["ok"]=profile["points"]==a.expected_points
        if not profile["ok"]:profile["error"]="unexpected output count"
    except Exception as exc:
        profile["ok"]=False;profile["error"]=str(exc)[:2000]
    finally:
        profile["query_wall_ms"]=round((time.perf_counter()-start)*1000,2)
        sampler.stop()
        profile["perf_returncode"]=stop_perf(perf,out)
        profile["proc_delta"]=delta(proc_before,aggregate_process(target))
        profile["host_delta"]=delta(before,host_counters())
        profile["sampled_rss_peak_kib"]=max((v.get("VmRSS_kib",0) for v in sampler.points),default=None)
        (out/"process_samples.json").write_text(json.dumps(sampler.points,separators=(",",":"))+"\n")
    if a.kind=="record":
        # sudo created the recording as root. Do not use sudo for uploads.
        subprocess.run(["sudo","-n","chown",str(os.getuid())+":"+str(os.getgid()),
                        str(out/"perf.data")],check=False)
        cmd=["perf","report","--stdio","--no-children","--sort","symbol",
             "--percent-limit","0.5","-i",str(out/"perf.data")]
        try:
            r=subprocess.run(cmd,text=True,capture_output=True,timeout=40)
            profile["perf_report_exit"]=r.returncode
            (out/"perf-report.txt").write_text(r.stdout[:150000]+"\n"+r.stderr[:12000])
        except Exception as exc:profile["perf_report_error"]=str(exc)
    else:
        try:
            profile["perf_counters"]=perf_stat_summary((out/"perf-stat.csv").read_text())
        except Exception as exc:profile["perf_stat_error"]=str(exc)
    ch_logs("http://127.0.0.1:18191",qid,profile.setdefault("query_log",{}))
    # Explain is a separate after-query diagnostic, not part of PMU measurement.
    ch_explain("http://127.0.0.1:18191",source,profile["query_log"])
    profile["environment_after"]=environment([a.data_path],target)
    (out/"result_summary.json").write_text(json.dumps(profile,indent=2)+"\n")
    print("PERF_HARDWARE",json.dumps({
        "case":a.case,"kind":a.kind,"ok":profile["ok"],
        "wall_ms":profile["query_wall_ms"],"points":profile.get("points"),
        "perf_exit":profile["perf_returncode"],"IPC":profile.get("perf_counters",{}).get("IPC"),
        "rss_kib":profile["sampled_rss_peak_kib"],
        "error":profile.get("error") or profile.get("perf_start_error")},separators=(",",":")),flush=True)
    if not profile["ok"]:
        raise SystemExit(1)
    # perf exits -SIGINT (-2) when attached sampling is stopped after the query.
    # A signal exit is acceptable only when actual counter/profile bytes exist.
    if profile["perf_returncode"] not in (0,130,-2):
        raise SystemExit(1)
    if a.kind=="stat" and not profile.get("perf_counters",{}).get("software_clock_supported"):
        raise SystemExit("Missing software task-clock counters")
    if a.kind=="record" and (not (out/"perf.data").exists() or (out/"perf.data").stat().st_size<4096):
        raise SystemExit("Missing perf sample data")

if __name__=="__main__":main()
