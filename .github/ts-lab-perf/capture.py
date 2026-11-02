#!/usr/bin/env python3
"""Cross-engine, low-overhead black-box query recorder. No external Python dependencies.

This is an observation run, NOT an uninstrumented latency benchmark. Process
counters and host counters are distinct. Missing metrics remain missing.
"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

def read(path):
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except (OSError, ValueError):
        return ""

def num_stat(pid):
    raw = read(f"/proc/{pid}/stat")
    if not raw or ") " not in raw:
        return {}
    fields = raw.rsplit(") ", 1)[1].split()
    try:
        return {"minflt": int(fields[7]), "majflt": int(fields[9]),
                "utime_ticks": int(fields[11]), "stime_ticks": int(fields[12]),
                "start_ticks": int(fields[19])}
    except (IndexError, ValueError):
        return {}

def named_kib(pid):
    result = {}
    for line in read(f"/proc/{pid}/status").splitlines():
        parts = line.split()
        if parts and parts[0].rstrip(":") in ("VmRSS", "VmHWM", "RssAnon", "RssFile", "RssShmem", "VmSwap"):
            try:
                result[parts[0].rstrip(":")+"_kib"] = int(parts[1])
            except (ValueError, IndexError):
                pass
    for line in read(f"/proc/{pid}/status").splitlines():
        if line.startswith(("voluntary_ctxt_switches:", "nonvoluntary_ctxt_switches:")):
            k,v=line.split(":",1)
            try:result[k]=int(v.strip())
            except ValueError:pass
    return result

def proc_io(pid):
    d = {}
    for line in read(f"/proc/{pid}/io").splitlines():
        if ": " in line:
            name, value = line.split(": ", 1)
            try: d[name] = int(value)
            except ValueError: pass
    return d

def proc_descendants(pid):
    todo=[pid]; seen=set()
    while todo and len(seen)<1024:
        p=todo.pop()
        if p in seen: continue
        if not pathlib.Path(f"/proc/{p}").exists(): continue
        seen.add(p)
        children=read(f"/proc/{p}/task/{p}/children").split()
        todo.extend(int(c) for c in children if c.isdigit())
    return sorted(seen)

def aggregate_process(pid):
    procs=proc_descendants(pid)
    d={"pid_count":len(procs)}
    for p in procs:
        for obj in (num_stat(p), named_kib(p), proc_io(p)):
            for k,v in obj.items():
                if k=="start_ticks": continue
                d[k]=d.get(k,0)+v
    return d

def pressure():
    return {kind:read("/proc/pressure/"+kind).strip()[:256] for kind in ("cpu","memory","io")}

def host_counters():
    disks={}
    for line in read("/proc/diskstats").splitlines():
        p=line.split()
        if len(p)>=14:
            name=p[2]
            if name.startswith(("loop","ram","fd","sr")): continue
            try:
                disks[name]={"reads":int(p[3]),"read_sectors":int(p[5]),
                             "writes":int(p[7]),"write_sectors":int(p[9]),
                             "io_ms":int(p[12]),"weighted_io_ms":int(p[13])}
            except ValueError: pass
    net={}
    for line in read("/proc/net/dev").splitlines()[2:]:
        if ":" not in line: continue
        name, values=line.split(":",1)
        fields=values.split()
        if len(fields)>=16:
            try:net[name.strip()]={"rx_bytes":int(fields[0]),"tx_bytes":int(fields[8]),
                                    "rx_packets":int(fields[1]),"tx_packets":int(fields[9])}
            except ValueError:pass
    vm={}
    for line in read("/proc/vmstat").splitlines():
        p=line.split()
        if len(p)==2 and p[0] in ("pgpgin","pgpgout","pgfault","pgmajfault","pswpin","pswpout"):
            try:vm[p[0]]=int(p[1])
            except ValueError:pass
    return {"diskstats":disks, "netdev":net, "vmstat":vm, "pressure":pressure(),
            "proc_stat_cpu":read("/proc/stat").splitlines()[0] if read("/proc/stat") else ""}

def delta(before, after):
    if isinstance(before, (float,int)) and isinstance(after, (float,int)):
        return after-before
    if isinstance(before,dict) and isinstance(after,dict):
        return {k:delta(v,after[k]) for k,v in before.items() if k in after}
    return None

def disk_space(path):
    try:
        usage=shutil.disk_usage(path)
        st=os.statvfs(path)
        return {"path":str(path), "total_bytes":usage.total, "free_bytes":usage.free,
                "free_fraction":round(usage.free/max(1,usage.total),6),
                "free_inodes":st.f_favail,"total_inodes":st.f_files}
    except OSError as exc:
        return {"path":str(path),"error":str(exc)}

def environment(paths, pid):
    cpu=read("/proc/cpuinfo")
    model=next((x.split(":",1)[1].strip() for x in cpu.splitlines() if x.startswith("model name")),None)
    governors=set()
    for p in pathlib.Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor"):
        v=read(p).strip()
        if v:governors.add(v)
    return {"timestamp_utc":datetime.now(timezone.utc).isoformat(),
            "platform":platform.platform(),"machine":platform.machine(),
            "kernel":platform.release(),"cpu_model":model,"logical_cpus":os.cpu_count(),
            "clock_ticks_per_second":os.sysconf("SC_CLK_TCK"),
            "perf_event_paranoid":read("/proc/sys/kernel/perf_event_paranoid").strip(),
            "governors":sorted(governors),"uptime":read("/proc/uptime").strip(),
            "meminfo":{a.split(":")[0]:a.split(":")[1].strip()
                       for a in read("/proc/meminfo").splitlines()[:7] if ":" in a},
            "disk":[disk_space(p) for p in paths],
            "process_start_ticks":num_stat(pid).get("start_ticks"),
            "perf_available":shutil.which("perf") is not None,
            "strace_available":shutil.which("strace") is not None,
            "pressure":pressure()}

class Sampler:
    def __init__(self,pid,interval=0.2):
        self.pid=pid;self.interval=interval
        self.points=[];self.done=threading.Event()
        self.started=time.monotonic()
    def sample(self):
        s=aggregate_process(self.pid)
        s["t_ms"]=round((time.monotonic()-self.started)*1000,3)
        self.points.append(s)
    def loop(self):
        while not self.done.is_set():
            self.sample()
            self.done.wait(self.interval)
    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.thread.start()
    def stop(self):
        self.done.set();self.thread.join(timeout=2)
        self.sample()

def request(url, data=None, timeout=200):
    req=urllib.request.Request(url, data=data, headers={"Content-Type":"text/plain; charset=utf-8"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()

def ch_sql(base, sql, query_id=None, timeout=240):
    params={"query_id":query_id} if query_id else {}
    url=base+"/?"+urllib.parse.urlencode(params)
    return request(url, sql.encode(), timeout=timeout).decode(errors="replace")

def json_eachrow(raw):
    return [json.loads(x) for x in raw.splitlines() if x.strip()]

def ch_context(base, out):
    checks={
        "parts":"SELECT database,table,count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes FROM system.parts WHERE active GROUP BY database,table FORMAT JSONEachRow",
        "merges":"SELECT database,table,elapsed,progress,num_parts,bytes_read_uncompressed,bytes_written_uncompressed FROM system.merges FORMAT JSONEachRow",
        "metrics":"SELECT metric,value FROM system.metrics WHERE metric IN ('Query','Merge','BackgroundMergesAndMutationsPoolTask') FORMAT JSONEachRow",
    }
    for k,sql in checks.items():
        try:out[k]=json_eachrow(ch_sql(base,sql))
        except Exception as exc:out[k+"_error"]=str(exc)[:700]

def ch_logs(base,qid,out):
    try:ch_sql(base,"SYSTEM FLUSH LOGS")
    except Exception as exc:out["flush_error"]=str(exc)[:500]
    quote="'"+qid.replace("'","''")+"'"
    checks={
        "query_log":("SELECT type,query_duration_ms,read_rows,read_bytes,memory_usage,"
                     "exception_code,ProfileEvents,query_id FROM system.query_log WHERE query_id="+quote+
                     " ORDER BY event_time_microseconds FORMAT JSONEachRow"),
        "processors":("SELECT name,elapsed_us,input_wait_elapsed_us,output_wait_elapsed_us,"
                      "input_rows,input_bytes,output_rows,output_bytes "
                      "FROM system.processors_profile_log WHERE query_id="+quote+" FORMAT JSONEachRow"),
        "threads":("SELECT thread_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
                   "FROM system.query_thread_log WHERE query_id="+quote+" FORMAT JSONEachRow"),
        "cpu_leaf":("SELECT trace_type,arrayElement(symbols,1) AS leaf,count() AS samples "
                    "FROM system.trace_log WHERE query_id="+quote+
                    " GROUP BY trace_type,leaf ORDER BY samples DESC LIMIT 100 FORMAT JSONEachRow"),
        "cpu_stack_count":("SELECT trace_type,count() AS count FROM system.trace_log WHERE query_id="+quote+
                           " GROUP BY trace_type FORMAT JSONEachRow"),
    }
    for name,sql in checks.items():
        try:out[name]=json_eachrow(ch_sql(base,sql))
        except Exception as exc:out[name+"_error"]=str(exc)[:1000]

def ch_explain(base,sql,out):
    if "FORMAT JSONEachRow" in sql:
        sql=sql.replace("FORMAT JSONEachRow","")
    for mode,name in [("PLAN indexes=1","explain_indexes"),("PIPELINE","explain_pipeline")]:
        try:
            x=ch_sql(base,"EXPLAIN "+mode+" "+sql)
            out[name]=x[:150000]
        except Exception as exc:out[name+"_error"]=str(exc)[:1000]

def metrics(base,out):
    try:
        raw=request(base+"/metrics",timeout=20)
        out["sha256"]=hashlib.sha256(raw).hexdigest()
        out["bytes"]=len(raw)
        # Limit raw size; this data is external to the timed HTTP request.
        out["raw"]=raw.decode(errors="replace")[:500000]
    except Exception as exc:out["error"]=str(exc)[:900]

def vm_query(base,expr,start,end,step,trace=False,timeout=240):
    params={"query":expr,"start":str(start),"end":str(end),"step":str(step),"nocache":"1"}
    if trace:params["trace"]="1"
    raw=request(base+"/api/v1/query_range?"+urllib.parse.urlencode(params),timeout=timeout)
    body=json.loads(raw)
    if body.get("status")!="success":raise RuntimeError(str(body)[:1000])
    return body

def pprof_heap(base,outdir):
    try:
        raw=request(base+"/debug/pprof/heap",timeout=20)
        if len(raw)<10:raise RuntimeError("empty heap profile")
        (outdir/"vm_heap.pprof").write_bytes(raw)
        return {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
    except Exception as exc:return {"error":str(exc)[:800]}

def raw_digest(obj):
    data=json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    return hashlib.sha256(data).hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--engine",required=True,choices=["ch","vm"])
    p.add_argument("--url",required=True)
    p.add_argument("--pid",required=True,type=int)
    p.add_argument("--data-path",action="append",default=[])
    p.add_argument("--output",required=True)
    p.add_argument("--query-file",help="full ClickHouse SQL including SETTINGS and FORMAT JSONEachRow")
    p.add_argument("--promql",help="VictoriaMetrics query")
    p.add_argument("--start",type=int)
    p.add_argument("--end",type=int)
    p.add_argument("--step",type=int,default=86400)
    p.add_argument("--case",default="profile")
    p.add_argument("--timeout",type=int,default=180)
    p.add_argument("--min-free-fraction",type=float,default=.20)
    p.add_argument("--allow-low-disk",action="store_true")
    args=p.parse_args()
    outdir=pathlib.Path(args.output)
    outdir.mkdir(parents=True,exist_ok=True)
    if args.engine=="ch" and not args.query_file:p.error("CH needs --query-file")
    if args.engine=="vm" and (not args.promql or args.start is None or args.end is None):p.error("VM needs --promql/start/end")
    metadata={"case":args.case,"engine":args.engine,
              "run_id":os.environ.get("GITHUB_RUN_ID"),
              "measurement_kind":"diagnostic_instrumented_not_latency_baseline",
              "environment":environment(args.data_path or ["/"],args.pid),
              "sampling_interval_ms":200}
    bad=[d for d in metadata["environment"]["disk"]
         if "free_fraction" in d and d["free_fraction"]<args.min_free_fraction]
    if bad and not args.allow_low_disk:
        metadata["result"]="invalid_environment_disk"
        (outdir/"record.json").write_text(json.dumps(metadata,indent=2)+"\n")
        raise SystemExit("INVALID: insufficient free disk ("+str(bad)+")")
    if args.engine=="ch":
        ch_context(args.url, metadata.setdefault("ch_before",{}))
    else:
        metrics(args.url,metadata.setdefault("vm_before",{}))
    before_host=host_counters()
    before_proc=aggregate_process(args.pid)
    qid=f"perf_{os.environ.get('GITHUB_RUN_ID','local')}_{args.case}_{int(time.time()*1000)}"
    metadata["query_id"]=qid
    sampler=Sampler(args.pid)
    sampler.start()
    began=time.perf_counter()
    try:
        if args.engine=="ch":
            sql=pathlib.Path(args.query_file).read_text()
            # This measured request includes SQL-level trace settings from the workload.
            raw=ch_sql(args.url,sql,query_id=qid,timeout=args.timeout)
            rows=json_eachrow(raw)
            metadata["result_points"]=sum(len(x.get("samples",[])) for x in rows)
            metadata["results_sha256"]=raw_digest(rows)
            (outdir/"query_result.json").write_text(json.dumps(rows,separators=(",",":"))+"\n")
        else:
            body=vm_query(args.url,args.promql,args.start,args.end,args.step,timeout=args.timeout)
            metadata["result_points"]=sum(len(s.get("values",[])) for s in body.get("data",{}).get("result",[]))
            metadata["results_sha256"]=raw_digest(body.get("data"))
            (outdir/"query_result.json").write_text(json.dumps(body.get("data"),separators=(",",":"))+"\n")
        metadata["ok"]=True
    except Exception as exc:
        metadata["ok"]=False
        metadata["error"]=str(exc)[:2400]
    finally:
        metadata["wall_ms"]=round((time.perf_counter()-began)*1000,3)
        sampler.stop()
        metadata["proc_before"]=before_proc
        metadata["proc_after"]=aggregate_process(args.pid)
        metadata["proc_delta"]=delta(before_proc,metadata["proc_after"])
        metadata["host_delta"]=delta(before_host,host_counters())
        metadata["proc_peak_rss_kib"]=max((s.get("VmRSS_kib",0) for s in sampler.points),default=0)
        metadata["proc_peak_anon_kib"]=max((s.get("RssAnon_kib",0) for s in sampler.points),default=0)
        (outdir/"process_samples.json").write_text(json.dumps(sampler.points,separators=(",",":"))+"\n")
    if args.engine=="ch":
        ch_context(args.url,metadata.setdefault("ch_after",{}))
        ch_logs(args.url,qid,metadata.setdefault("clickhouse",{}))
        ch_explain(args.url,pathlib.Path(args.query_file).read_text(),metadata["clickhouse"])
    else:
        metrics(args.url,metadata.setdefault("vm_after",{}))
        metadata["vm_heap"]=pprof_heap(args.url,outdir)
        try:
            trace=vm_query(args.url,args.promql,args.start,args.end,args.step,trace=True,timeout=args.timeout)
            metadata["vm_trace"]=trace.get("trace")
            if not metadata["vm_trace"]:metadata["vm_trace_error"]="trace field absent"
        except Exception as exc:metadata["vm_trace_error"]=str(exc)[:900]
    metadata["environment_after"]=environment(args.data_path or ["/"],args.pid)
    (outdir/"record.json").write_text(json.dumps(metadata,indent=2)+"\n")
    print("PERF_LENS",json.dumps({"case":args.case,"engine":args.engine,"ok":metadata["ok"],
        "wall_ms":metadata["wall_ms"],"points":metadata.get("result_points"),
        "rss_peak_kib":metadata["proc_peak_rss_kib"],
        "query_id":qid,"errors":{k:v for k,v in metadata.get("clickhouse",{}).items() if k.endswith("_error")}}),flush=True)
    if not metadata["ok"]:raise SystemExit(1)

if __name__=="__main__":main()
