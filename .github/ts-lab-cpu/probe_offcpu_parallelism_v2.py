#!/usr/bin/env python3
"""Low-overhead Linux scheduler/off-CPU attribution for native TimeSeries queries.

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

def snap_tid(pid,tid,name_cache,prev=None):
    """Per-sample cheap schedstat/stat; no per-idle-thread /status or wchan."""
    base=f"/proc/{pid}/task/{tid}"
    raw=read_bytes(base+"/schedstat").split()
    if len(raw)!=3:return None
    try:
        running,runqueue,switches=map(int,raw)
        stat=read_bytes(base+"/stat")
        cut=stat.rfind(b")")
        if cut<0:return None
        state=stat[cut+2:cut+3].decode("ascii","replace")
        name=name_cache.get(tid)
        if name is None:
            name=read_bytes(base+"/comm").decode("utf-8","replace").strip()[:25]
            name_cache[tid]=name
        # Do not sample all idle background workers' futex sleeps.
        # Only inspect workers with CPU progress during this interval or
        # threads that have an active query-related name.
        moved=bool(prev and (running>prev[0] or runqueue>prev[1]))
        query_named=("QueryPipeline" in name or "HTTPHandler" in name)
        wchan=read_bytes(base+"/wchan").decode("utf-8","replace").strip()[:100] if state in ("S","D") and (moved or query_named) else ""
        return (running,runqueue,switches,state,name,wchan,moved)
    except (ValueError,IndexError,UnicodeError):return None

class TaskSampler:
    """Low-overhead periodic query-worker sampling plus accurate schedstat deltas.

    Scheduler stats include runnable queue delay but not blocked mutex wait.
    Thread ownership for a query is resolved later with system.query_thread_log.
    """
    def __init__(self,server_pid,interval_ms):
        self.pid=server_pid
        self.interval=max(interval_ms/1000,0.075)
        self.done=threading.Event()
        self.samples=[]
        self.name_cache={}
        self.previous={}
        self.per_tid=collections.defaultdict(lambda: {"run_ns":0,"queue_ns":0,
                                                       "sampled_states":collections.Counter(),
                                                       "sampled_wchan":collections.Counter(),
                                                       "name":""})
        self.errors=collections.Counter()
        self.collection_ns=0
        self.max_rss_kib=0
        self.thread=None
        self.t0=None
    def sample(self):
        t0=time.perf_counter_ns()
        now=time.monotonic_ns()
        try:
            names=list(pathlib.Path(f"/proc/{self.pid}/task").iterdir())
        except (OSError,PermissionError) as exc:
            self.errors["task_listing_"+type(exc).__name__]+=1
            return
        seen={}
        running_ns=waiting_ns=0
        active=parked=0
        n_runnable=0
        for path in names:
            try:tid=int(path.name)
            except ValueError:continue
            old=self.previous.get(tid)
            item=snap_tid(self.pid,tid,self.name_cache,old)
            if item is None:
                self.errors["partial_tid"]+=1
                continue
            run,wait,switches,state,name,wchan,moved=item
            seen[tid]=(run,wait,switches)
            if old and run>=old[0] and wait>=old[1]:
                drun=run-old[0]
                dwait=wait-old[1]
                running_ns+=drun
                waiting_ns+=dwait
                x=self.per_tid[tid]
                x["run_ns"]+=drun
                x["queue_ns"]+=dwait
            relevant=moved or "QueryPipeline" in name or "HTTPHandler" in name
            if relevant:
                active+=1
                x=self.per_tid[tid]
                x["name"]=name
                x["sampled_states"][state]+=1
                if wchan:
                    x["sampled_wchan"][wchan]+=1
                    if "futex" in wchan.lower():parked+=1
            if state=="R":n_runnable+=1
        self.previous=seen
        if len(self.samples)==0:
            running_ns=waiting_ns=0
        dt=(now-self.samples[-1]["t"]) if self.samples else 0
        self.samples.append({"t":now,"thread_count":len(seen),
            "cpu_delta_ms":round(running_ns/1e6,3),
            "runqueue_delta_ms":round(waiting_ns/1e6,3),
            "avg_running_cores":round(running_ns/max(dt,1),3) if dt else 0,
            "runnable_threads":n_runnable,
            "recently_active_threads":active,
            "sampled_parked_futex":parked})
        self.collection_ns+=time.perf_counter_ns()-t0
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
        self.thread.join(timeout=10)
        duration=time.monotonic_ns()-self.t0
        cpu=sum(x["run_ns"] for x in self.per_tid.values())
        runqueue=sum(x["queue_ns"] for x in self.per_tid.values())
        wchan=collections.Counter()
        for x in self.per_tid.values():wchan.update(x["sampled_wchan"])
        output={
            "server_pid":self.pid,
            "sampling_interval_ms":int(self.interval*1000),
            "duration_ms":round(duration/1e6,3),
            "samples":len(self.samples),
            "unique_threads_observed":len(self.name_cache),
            "active_threads_observed":len(self.per_tid),
            "total_cpu_thread_ms":round(cpu/1e6,3),
            "total_runnable_queue_wait_ms":round(runqueue/1e6,3),
            "avg_running_cores":round(cpu/max(duration,1),3),
            "mean_runnable_threads_sampled":round(statistics.mean(s["runnable_threads"] for s in self.samples),3) if self.samples else 0,
            "mean_active_threads_sampled":round(statistics.mean(s["recently_active_threads"] for s in self.samples),3) if self.samples else 0,
            "mean_query_named_futex_sleeps_sampled":round(statistics.mean(s["sampled_parked_futex"] for s in self.samples),3) if self.samples else 0,
            "top_query_worker_wchan":wchan.most_common(20),
            "collection_overhead_ms":round(self.collection_ns/1e6,3),
            "collection_overhead_wall_pct":round(100*self.collection_ns/max(duration,1),3),
            "errors":dict(self.errors),
            "per_tid":{
                str(tid):{"comm":x["name"],"cpu_ms":round(x["run_ns"]/1e6,3),
                          "runnable_queue_ms":round(x["queue_ns"]/1e6,3),
                          "states":dict(x["sampled_states"]),
                          "wchans":dict(x["sampled_wchan"])}
                for tid,x in self.per_tid.items()},
            "interpretation":"Only recently active threads sampled for sleep; no proof of mutex contention without lock owner/call stack. Accumulated schedstat per-process interval includes background activity."
        }
        return output

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
    p.add_argument("--interval-ms",type=int,default=100)
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
