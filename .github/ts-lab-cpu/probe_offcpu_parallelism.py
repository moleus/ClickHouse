#!/usr/bin/env python3
"""Isolated Linux scheduler/off-CPU attribution for native TimeSeries queries.

On-CPU: query_log/ProfileEvents plus complete trace_log CPU samples.
Off-CPU lower bound: /proc/TID/schedstat runqueue wait (NOT mutex waiting).
Sleeping thread state and wchan are sampled, not timed by the kernel:
futex sleep includes idle worker condition variables; no lock-owner claim.
Compare probe on/off in A/B/B/A. Keep all exact native PromQL output parity.
"""
import argparse,collections,concurrent.futures,json,os,pathlib,statistics,sys,threading,time
sys.path[:0] = [".github/ts-lab-perf", ".github/ts-lab-bench"]
from bench_granularity_v11 import sql,querylogs
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string

HOST="http://127.0.0.1:18191"
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SEL={"stage":('{stage="stage-0007"}',1,14),
     "project":('{project="project-00"}',19,14),
     "whole":("",38,1)}

def save(path,obj):
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
def records(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow").splitlines() if line.strip()]
def read_bytes(path):
    try:return pathlib.Path(path).read_bytes()
    except (OSError,PermissionError):return b""
def parse_pid(pid):
    tasks=pathlib.Path(f"/proc/{pid}/task")
    try:return sum(1 for _ in tasks.iterdir())
    except (OSError,PermissionError):return 0
def find_server(root):
    """The clickhouse watchdog may fork one real server process."""
    root=int(root);found=[root];pending=[root];seen={root}
    while pending:
        pid=pending.pop()
        data=read_bytes(f"/proc/{pid}/task/{pid}/children")
        for word in data.split():
            try:child=int(word)
            except ValueError:continue
            if child not in seen:seen.add(child);found.append(child);pending.append(child)
    good=[(parse_pid(i),i) for i in found]
    if not good:raise RuntimeError("No live ClickHouse PID found")
    count,pid=max(good)
    if count<5:raise RuntimeError("ClickHouse process appears not to have server threads "+str(good))
    return pid

def snap_tid(pid,tid):
    base=f"/proc/{pid}/task/{tid}"
    sch=read_bytes(base+"/schedstat").split()
    if len(sch)!=3:return None
    try:
        run,queue,switches=map(int,sch)
        stat=read_bytes(base+"/stat")
        if not stat:return None
        # /proc/<tid>/stat comm can contain spaces and ')', so use rfind.
        cut=stat.rfind(b")")
        state=stat[cut+2:cut+3].decode("ascii","replace") if cut>=0 else "?"
        name=read_bytes(base+"/comm").decode("utf-8","replace").strip()[:25]
        wchan=read_bytes(base+"/wchan").decode("utf-8","replace").strip()[:120] if state in ("S","D") else ""
        status=read_bytes(base+"/status").decode("utf-8","replace")
        voluntary=0;involuntary=0
        for line in status.splitlines():
            if line.startswith("voluntary_ctxt_switches:"):
                voluntary=int(line.split()[-1])
            elif line.startswith("nonvoluntary_ctxt_switches:"):
                involuntary=int(line.split()[-1])
        return (run,queue,switches,voluntary,involuntary,state,name,wchan)
    except (ValueError,IndexError,UnicodeError):return None

class TaskSampler:
    def __init__(self,server_pid,interval_ms):
        self.pid=server_pid;self.interval=interval_ms/1000
        self.done=threading.Event()
        self.samples=[]
        self.wchans=collections.Counter()
        self.names=collections.Counter()
        self.errors=collections.Counter()
        self.previous={}
        self.elapsed_ns=0
        self.collection_ns=0
        self.total_cpu_ns=0
        self.total_runqueue_ns=0
        self.total_voluntary=0
        self.total_involuntary=0
        self.observed_tasks=set()
        self.thread_counts=[]
        self.working_cores=[]
        self.runnable=[]
        self.parked_futex=[]
        self.max_rss_kib=0
        self.thread=None
    def sample(self):
        st=time.perf_counter_ns()
        now=time.monotonic_ns()
        try:
            names=list(pathlib.Path(f"/proc/{self.pid}/task").iterdir())
        except (OSError,PermissionError) as e:
            self.errors["list_tasks_"+type(e).__name__]+=1
            return
        seen={};states=collections.Counter();wc=collections.Counter()
        run_ns=wait_ns=vol=invol=0
        for path in names:
            try:tid=int(path.name)
            except ValueError:continue
            item=snap_tid(self.pid,tid)
            if item is None:
                self.errors["partial_tid"]+=1;continue
            self.observed_tasks.add(tid)
            seen[tid]=item
            run,queue,_switch,vs,ivs,state,comm,wchan=item
            states[state]+=1
            self.names[comm]+=1
            if state in ("S","D"):wc[wchan or "UNKNOWN"]+=1
            prev=self.previous.get(tid)
            if prev and run>=prev[0] and queue>=prev[1]:
                run_ns+=run-prev[0]
                wait_ns+=queue-prev[1]
                vol+=max(0,vs-prev[3])
                invol+=max(0,ivs-prev[4])
        self.previous=seen
        if self.samples:
            dt=max(1,now-self.samples[-1]["t_ns"])
            cores=run_ns/dt
            self.working_cores.append(cores)
            self.total_cpu_ns+=run_ns
            self.total_runqueue_ns+=wait_ns
            self.total_voluntary+=vol
            self.total_involuntary+=invol
        else:
            cores=0
        rss=read_bytes(f"/proc/{self.pid}/status").decode("utf-8","replace")
        for ln in rss.splitlines():
            if ln.startswith("VmRSS:"):
                try:self.max_rss_kib=max(self.max_rss_kib,int(ln.split()[1]))
                except (ValueError,IndexError):pass
                break
        self.thread_counts.append(len(seen))
        self.runnable.append(states.get("R",0))
        futex=sum(n for w,n in wc.items() if "futex" in w.lower())
        self.parked_futex.append(futex)
        self.wchans.update(wc)
        self.samples.append({"t_ns":now,"tasks":len(seen),"runnable_sampled":states.get("R",0),
                             "sleeping_sampled":states.get("S",0)+states.get("D",0),
                             "futex_wait_sampled":futex,
                             "working_cores_delta":round(cores,3),
                             "cpu_delta_ms":round(run_ns/1e6,3),
                             "runqueue_delta_ms":round(wait_ns/1e6,3),
                             "rss_kib":self.max_rss_kib,
                             "top_wchan":wc.most_common(4)})
        self.collection_ns+=time.perf_counter_ns()-st
    def _loop(self):
        self.sample()
        while not self.done.wait(self.interval):self.sample()
        self.sample()
    def start(self):
        self.t0=time.monotonic_ns()
        self.thread=threading.Thread(target=self._loop,name="ts-offcpu-sampler",daemon=True)
        self.thread.start()
    def stop(self):
        self.done.set()
        self.thread.join(timeout=5)
        self.elapsed_ns=time.monotonic_ns()-self.t0
        data={"server_pid":self.pid,"duration_ms":round(self.elapsed_ns/1e6,3),
              "samples":len(self.samples),"unique_threads_observed":len(self.observed_tasks),
              "total_cpu_thread_ms":round(self.total_cpu_ns/1e6,3),
              "total_runnable_queue_wait_ms":round(self.total_runqueue_ns/1e6,3),
              "context_switch_voluntary_delta":self.total_voluntary,
              "context_switch_involuntary_delta":self.total_involuntary,
              "avg_running_cores":round(self.total_cpu_ns/max(self.elapsed_ns,1),3),
              "mean_runnable_threads_sampled":round(statistics.mean(self.runnable),3) if self.runnable else 0,
              "p95_runnable_threads_sampled":round(sorted(self.runnable)[int((len(self.runnable)-1)*0.95)],3) if self.runnable else 0,
              "mean_futex_sleep_threads_sampled":round(statistics.mean(self.parked_futex),3) if self.parked_futex else 0,
              "peak_server_rss_kib":self.max_rss_kib,
              "collection_overhead_ms":round(self.collection_ns/1e6,3),
              "collection_overhead_wall_pct":round(100*self.collection_ns/max(self.elapsed_ns,1),3),
              "top_wchan_sample_counts":self.wchans.most_common(22),
              "top_thread_name_sample_counts":self.names.most_common(20),
              "errors":dict(self.errors)}
        # Important: sleeping worker samples may include idle background workers.
        data["interpretation"]="Runqueue wait is NOT mutex waiting; futex wchan includes idle workers. Values are whole-server samples."
        return data

def query(case,start,end):
    selector,_,_=SEL[case]
    expr=f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{selector}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
            f"{start},{end},86400) SETTINGS max_threads=4,max_memory_usage=3500000000,"
            "log_queries=1,log_processors_profiles=1,"
            "query_profiler_cpu_time_period_ns=1000000 "
            "FORMAT JSONEachRow")

def run_wave(stmt,prefix,clients,reference,expected_count,grid):
    def run_one(i):
        qid=prefix+"_"+str(i)
        ms,body=post(HOST,stmt,qid)
        return {"qid":qid,"client_ms":round(ms,3)},normalize_any(body)
    start=time.monotonic_ns()
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as ex:
        tasks=[ex.submit(run_one,i) for i in range(clients)]
        results=[f.result() for f in tasks]
    elapsed=(time.monotonic_ns()-start)/1e6
    for item,got in results:
        if len(got)!=expected_count or {ts for (_labels,ts) in got}!=grid:
            raise AssertionError(("bad output",clients,len(got),expected_count))
        if reference is None:reference=got
        else:assert_same(reference,got,"offcpu-concurrency",str(clients))
    return [x[0] for x in results],reference,round(elapsed,3)

def measure(case,workers,fixture,root_pid,interval_ms,report,filename):
    selector,groups,days=SEL[case]
    end=int(fixture["end"]);start=end-(days-1)*86400
    statement=query(case,start,end)
    points=groups*days;grid={start+i*86400 for i in range(days)}
    reference=None
    phases=[];probe_modes=(False,True,True,False)
    for phase,enabled in enumerate(probe_modes):
        phase_prefix=f"offcpu_{case}_{workers}_{phase}_{os.getenv('GITHUB_RUN_ID','local')}"
        batches=[];monitored=[]
        for rep in range(5):
            sampler=None
            if enabled:
                pid=find_server(root_pid)
                sampler=TaskSampler(pid,interval_ms)
                sampler.start()
            try:
                trials,reference,elapsed=run_wave(statement,phase_prefix+"_r"+str(rep),workers,
                                                 reference,points,grid)
            finally:
                profile=sampler.stop() if sampler else None
            batches.append({"warmup":rep==0,"wave_ms":elapsed,"trials":trials,
                            "probe":profile})
            if profile:monitored.append(profile)
        entry={"phase":phase,"sampler_on":enabled,"workers":workers,"batches":batches}
        measurements=[x for x in batches if not x["warmup"]]
        entry["median_wave_ms"]=statistics.median(x["wave_ms"] for x in measurements)
        entry["p95_wave_ms"]=round(sorted(x["wave_ms"] for x in measurements)[-1],3)
        entry["p50_single_request_ms"]=statistics.median(t["client_ms"] for w in measurements for t in w["trials"])
        if monitored:
            entry["server_cpu_total_sample_ms"]=round(sum(x["total_cpu_thread_ms"] for x in monitored[1:]),3)
            entry["server_runnable_wait_total_sample_ms"]=round(sum(x["total_runnable_queue_wait_ms"] for x in monitored[1:]),3)
            entry["mean_working_cores"]=round(statistics.mean(x["avg_running_cores"] for x in monitored[1:]),3)
        phases.append(entry)
        report["cases"][str(workers)]={"phases":phases}
        save(filename,report)
        print("OFFCPU_PHASE",json.dumps({"case":case,"workers":workers,"phase":phase,
              "sampler":enabled,"median_wave_ms":entry["median_wave_ms"],
              "p50_req_ms":entry["p50_single_request_ms"],
              "mean_cores":entry.get("mean_working_cores")}),flush=True)
    logs={row["query_id"]:row for row in querylogs(f"offcpu_{case}_{workers}_")
          if row.get("type")=="QueryFinish"}
    for entry in phases:
        for batch in entry["batches"]:
            for trial in batch["trials"]:
                q=logs.get(trial["qid"])
                if not q:raise AssertionError("Missing QueryFinish: "+trial["qid"])
                pe=q.get("ProfileEvents",{})
                trial.update({"server_wall_ms":q["query_duration_ms"],
                              "query_cpu_ms":round(pe.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                              "read_rows":q["read_rows"],"read_bytes":q["read_bytes"],
                              "query_peak_memory":q["memory_usage"],
                              "selected_marks":pe.get("SelectedMarks")})
        measured=[t for b in entry["batches"][1:] for t in b["trials"]]
        entry["median_query_cpu_ms"]=statistics.median(t["query_cpu_ms"] for t in measured)
        entry["max_query_peak_memory"]=max(t["query_peak_memory"] for t in measured)
    a=[x for x in phases if not x["sampler_on"]]
    b=[x for x in phases if x["sampler_on"]]
    control=statistics.mean(x["median_wave_ms"] for x in a)
    candidate=statistics.mean(x["median_wave_ms"] for x in b)
    drift=abs(a[0]["median_wave_ms"]-a[1]["median_wave_ms"])/control
    summary={"workers":workers,"baseline_wave_ms":round(control,3),
             "sampler_wave_ms":round(candidate,3),
             "sampler_wall_overhead_ratio":round(candidate/control,3),
             "baseline_cpu_per_request_ms":round(statistics.mean(x["median_query_cpu_ms"] for x in a),3),
             "sampler_cpu_per_request_ms":round(statistics.mean(x["median_query_cpu_ms"] for x in b),3),
             "baseline_drift":round(drift,4),"valid":drift<=0.25}
    report["cases"][str(workers)]["summary"]=summary
    save(filename,report)
    print("OFFCPU_WAVE",json.dumps({"case":case,**summary}),flush=True)
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",choices=SEL,required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--pidfile",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--interval-ms",type=int,default=25)
    args=p.parse_args()
    f=json.loads(pathlib.Path(args.manifest).read_text())
    if f.get("sha256")!=SHA or f.get("series")!=1330000:raise AssertionError("Wrong fixture")
    root=int(pathlib.Path(args.pidfile).read_text().strip())
    report={"source_sha":SHA,"case":args.case,"pidfile":args.pidfile,"interval_ms":args.interval_ms,
            "cases":{},"errors":[],"offcpu_limitations":"proc global task snapshots, not lock-owner trace or exact critical path"}
    save(args.output,report)
    try:
        pid=find_server(root)
        report["server_pid"]=pid
        report["preflight"]={"schedstat":read_bytes(f"/proc/{pid}/task/{pid}/schedstat").decode("ascii","replace"),
                             "wchan":read_bytes(f"/proc/{pid}/task/{pid}/wchan").decode("utf-8","replace")}
        for workers in (1,4,8):
            summary=measure(args.case,workers,f,root,args.interval_ms,report,args.output)
            if not summary["valid"]:report["errors"].append({"workers":workers,"reason":"drift", "drift":summary["baseline_drift"]})
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:1800]});report["passed"]=False
    finally:
        save(args.output,report)
        compact={"case":args.case,"passed":report.get("passed"),"errors":report["errors"],
                 "cases":{k:v.get("summary") for k,v in report["cases"].items()}}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("OFFCPU_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":main()
