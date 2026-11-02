#!/usr/bin/env python3
"""Same source, same runner: v4 original vs stage-projected C++ and VM.

Fresh CH process for each variant and case. Never count an OOM as success.
All filters run in the normal native TimeSeries selector.
"""
import argparse, json, os, pathlib, random, subprocess, threading, time
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized
from bench_filtered_selector_v4_sql import sql_string

HOST="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"
DAY=86400
SELECTORS={
 "whole":"",
 "stage":'{stage="stage-0007"}',
 "project":'{project="project-00"}',
 "regex":'{branch=~"main|feature-0[0-9]"}'}
VARIANTS=(("original_off","original","0"),
          ("original_on","original","1"),
          ("projected_off","projected","0"),
          ("projected_on","projected","1"))

def rss(pid):
    pending=[pid];seen=set();total=0
    while pending:
        v=pending.pop()
        if v in seen:continue
        seen.add(v)
        try:
            for line in pathlib.Path(f"/proc/{v}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total+=int(line.split()[1])
                    break
            pending.extend(int(x) for x in pathlib.Path(
                f"/proc/{v}/task/{v}/children").read_text().split())
        except (OSError,ValueError):continue
    return total

def monitored(statement,qid,server_pid):
    start=time.perf_counter()
    rows=[]
    stop=threading.Event()
    def worker():
        while not stop.is_set():
            rows.append(rss(server_pid))
            stop.wait(.20)
    th=threading.Thread(target=worker,daemon=True)
    th.start()
    try:
        ms,result=post(HOST,statement,qid)
        out={"ok":True,"duration_ms":round(ms,3),
             "points":normalize_any(result)}
    except Exception as e:
        msg=str(e)
        out={"ok":False,"duration_ms":round((time.perf_counter()-start)*1000,3),
             "error":msg[:2200],"oom":"MEMORY_LIMIT_EXCEEDED" in msg}
    finally:
        stop.set()
        th.join(timeout=5)
    out["query_id"]=qid
    out["rss_peak_kib"]=max(rows,default=0)
    out["rss_after_kib"]=rss(server_pid)
    return out

def server_restart(helper,binary,case):
    subprocess.run([helper,binary,case],check=True,timeout=180)
    pid=int(pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text())
    _,answer=post(HOST,"SELECT getSetting('promql_enable_native_one_point_lab')")
    if answer.strip() not in (b"0",b"false"):
        raise AssertionError(("default not disabled",answer))
    return pid

def save(path,report):
    pathlib.Path(path).write_text(json.dumps(report,indent=2)+"\n")

def expected_points(selector, days):
    # Exact property of generate_95k_daily_churn_ts.py: 38 stages,
    # project-00 contains the 19 even-numbered stage labels.
    if selector=="stage": return days
    if selector=="project": return 19*days
    return 38*days

def wait_vm_complete(meta, output_path):
    """Fail closed until all 14 daily windows contain 95,000 samples.

    An import HTTP 204 means the ingest endpoint accepted the request;
    it is NOT a proof that the VM index can serve every label selector.
    This gate is run before any performance timing.
    """
    target=int(meta["per_day"])
    assert target==95000 and meta["days"]==14
    first=int(meta["base"])+DAY
    last=int(meta["end"])
    expr=f"sum(count_over_time({METRIC}[1d]))"
    deadline=time.monotonic()+240
    good=0
    attempt=0
    history=[]
    while time.monotonic()<deadline:
        attempt+=1
        try:
            ms,payload=vm_get({"query":expr,
                "start":first,"end":last,"step":DAY})
            got=vm_normalized(payload)
            times={ts:float(value) for (_,ts),value in got.items()}
            expected={first+i*DAY for i in range(14)}
            ready=(set(times)==expected
                   and all(value==target for value in times.values()))
            info={"attempt":attempt,"ms":round(ms,2),
                  "days":len(times),
                  "min_count":min(times.values(),default=None),
                  "max_count":max(times.values(),default=None),
                  "ready":ready}
            good=good+1 if ready else 0
            history.append(info)
            print("STAGE_VM_READINESS",json.dumps(info,separators=(",",":")),flush=True)
            if good>=2:
                return {"attempts":attempt,"last_checks":history[-6:],
                        "days":14,"samples_per_day":target}
        except Exception as exc:
            good=0
            info={"attempt":attempt,"error":str(exc)[:1200]}
            history.append(info)
            print("STAGE_VM_READINESS_ERROR",json.dumps(info),flush=True)
        time.sleep(2)
    pathlib.Path(output_path).write_text(json.dumps({
        "complete":False,"attempts":attempt,"recent":history[-30:]},indent=2))
    raise AssertionError(("VictoriaMetrics did not expose all 95k samples on all 14 days",
                          history[-8:]))

def main():
    p=argparse.ArgumentParser()
    for field in ("manifest","original-bin","projected-bin","restart-helper","output"):
        p.add_argument("--"+field,required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert meta["series"]==1330000 and meta["days"]==14
    run=os.getenv("GITHUB_RUN_ID","local")
    report={"run_id":run,"source_sha256":meta["sha256"],
            "variants":list(VARIANTS),"cases":[],"failures":[],
            "note":"O1 build; 5GB server cap; 14d VM strict readiness before A/B; 2 warmups + 4 timed VM trials"}
    save(a.output,report)
    report["vm_readiness"]=wait_vm_complete(
        meta,str(pathlib.Path(a.output).with_name("vm-readiness.json")))
    save(a.output,report)
    controls=[("whole",14),("stage",14),("project",14),("regex",14),
              ("whole",3),("stage",3)]
    for sel,days in controls:
        name=sel+"_e"+str(days)
        t_end=int(meta["end"])
        t_start=t_end-(days-1)*DAY
        promql=f"avg by(stage)(avg_over_time({METRIC}{SELECTORS[sel]}[1d]))"
        reference=None
        vm_trials=[]
        for i in range(6):
            try:
                ms,payload=vm_get({"query":promql,
                    "start":t_start,"end":t_end,"step":DAY})
                got=vm_normalized(payload)
                expected=expected_points(sel,days)
                if len(got)!=expected:
                    raise AssertionError(("VM partial query response",
                                          name,i,len(got),expected))
                if reference is None:reference=got
                else:assert_same(reference,got,name,"vm")
                if i>=2:vm_trials.append(round(ms,3))
            except Exception as ex:
                report["failures"].append({"case":name,"variant":"VM","error":str(ex)[:1800]})
                save(a.output,report)
                raise
        rec={"case":name,"vm":{"points":len(reference),"p50_ms":pct(vm_trials,.5),
            "trials_ms":vm_trials},"variants":{}}
        vorder=list(VARIANTS)
        random.Random(88199+days+len(sel)).shuffle(vorder)
        for label,version,opt in vorder:
            binary=a.original_bin if version=="original" else a.projected_bin
            pid=server_restart(a.restart_helper,binary,f"stageperf_{name}_{label}")
            query=(f"SELECT * FROM prometheusQueryRange(churn_ts,"
                   f"{sql_string(promql)},{t_start},{t_end},{DAY}) "
                   f"SETTINGS promql_enable_native_one_point_lab={opt},"
                   "max_threads=4 FORMAT JSONEachRow")
            trials=[]
            for trial in range(5):
                qid=f"stageperf_{run}_{name}_{label}_{trial}"
                measured=monitored(query,qid,pid)
                if measured["ok"]:
                    try:assert_same(reference,measured.pop("points"),name,label)
                    except Exception as ex:
                        measured["ok"]=False
                        measured["mismatch"]=str(ex)[:3000]
                        report["failures"].append({"case":name,
                          "variant":label,"trial":trial,"mismatch":str(ex)[:2000]})
                else:report["failures"].append({"case":name,"variant":label,
                         "trial":trial,"error":measured["error"][:1000]})
                trials.append(measured)
                if not measured["ok"]:break
            passed=len(trials)==5 and all(x["ok"] for x in trials)
            measured=trials[1:] if passed else []
            rec["variants"][label]={"ok":passed,
                "p50_ms":pct([x["duration_ms"] for x in measured],.5) if passed else None,
                "p95_ms":pct([x["duration_ms"] for x in measured],.95) if passed else None,
                "peak_rss_p95_kib":pct([x["rss_peak_kib"] for x in measured],.95) if passed else None,
                "trials":trials}
            print("STAGE_CPP_PAIRED_CASE",json.dumps({
                "case":name,"variant":label,
                "vm_ms":rec["vm"]["p50_ms"],"pass":passed,
                "p50_ms":rec["variants"][label]["p50_ms"],
                "peak_rss_p95_kib":rec["variants"][label]["peak_rss_p95_kib"],
                "error":trials[-1].get("error","")[:200],
                "mismatch":trials[-1].get("mismatch","")[:200]},
                separators=(",",":")),flush=True)
            report["cases"].append(rec) if rec not in report["cases"] else None
            save(a.output,report)
        print("STAGE_CPP_PAIRED_SUMMARY",json.dumps({
            "case":name,"vm_ms":rec["vm"]["p50_ms"],
            "variants_ms":{k:v["p50_ms"] for k,v in rec["variants"].items()},
            "all_equal":all(v["ok"] for v in rec["variants"].values())},
            separators=(",",":")),flush=True)

    post(HOST,"SYSTEM FLUSH LOGS")
    _,raw=post(HOST,"SELECT query_id,type,exception_code,exception,query_duration_ms,"
        "read_rows,read_bytes,memory_usage,ProfileEvents FROM system.query_log "
        f"WHERE startsWith(query_id,{sql_string('stageperf_'+run+'_')}) FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip()
          for x in [json.loads(line)] if x.get("type") in ("QueryFinish","ExceptionWhileProcessing")}
    for rec in report["cases"]:
        for label,stats in rec["variants"].items():
            for trial in stats["trials"]:
                item=logs.get(trial["query_id"])
                if item:
                    trial["tracked_memory"]=item["memory_usage"]
                    trial["read_rows"]=item["read_rows"]
                    trial["read_bytes"]=item["read_bytes"]
                    trial["cpu_us"]=item["ProfileEvents"].get("OSCPUVirtualTimeMicroseconds",0)
            if stats["ok"]:
                entries=stats["trials"][1:]
                if all("tracked_memory" in v for v in entries):
                    stats["tracked_peak_p95"]=pct([v["tracked_memory"] for v in entries],.95)
                    stats["read_rows_p50"]=pct([v["read_rows"] for v in entries],.5)
                    stats["cpu_us_p50"]=pct([v["cpu_us"] for v in entries],.5)
    report["passed"]=len(report["failures"])==0
    save(a.output,report)
    save(str(pathlib.Path(a.output).with_name("stage-cpp-querylogs.json")),logs)
    print("STAGE_CPP_PAIRED_DONE",json.dumps({
        "cases":len(report["cases"]),"passed":report["passed"],
        "failed":len(report["failures"])},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit("Stage projection requires investigation")
if __name__=="__main__":main()
