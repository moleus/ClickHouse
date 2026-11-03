#!/usr/bin/env python3
"""R32 full native TimeSeries stock-O2 vs single-bucket finalizer O2, ABBA, fresh processes.

Same frozen on-disk TimeSeries v11 snapshot restored for EVERY phase.
No production changes. Never infer cross-binary speedups without identical
source/LLVM/O2/allocator flags, correct full output, and valid A/A drift.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

sys.path[:0]=[".github/ts-lab-r30",".github/ts-lab-r06",
              ".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_timeranges_any_sql import (
    request,write_fixture,write_batches_from_fixture,inner_target,
    physical_census,read_query_logs,quoted,save,END,METRIC
)
from bench_bucket_cxx_abca import normalize_any,assert_same

SELECTORS={
    "stage":'{stage="stage-01"}',
    "project":'{project="project-01"}',
    "whole":"",
    "regex":'{stage=~"stage-0[0-2]"}',
}
PHASES=("stock","onebucket","onebucket","stock")
SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
STOCK_SHA="5d629ddfda91919402fdf8475ec3da9fe718d48ef386bc51be677264df6dc9ef"
QUERY_SETTINGS="max_threads=4,max_memory_usage=1800000000,log_queries=1,log_processors_profiles=1"


def digest_file(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):h.update(chunk)
    return h.hexdigest()


def configure(root):
    path=root/"config.xml"
    body=f"""<clickhouse>
<logger><level>warning</level><console>true</console></logger>
<listen_host>127.0.0.1</listen_host><http_port>18191</http_port><tcp_port>19000</tcp_port>
<path>{root/'data'}/</path><tmp_path>{root/'tmp'}/</tmp_path>
<user_files_path>{root/'userfiles'}/</user_files_path>
<max_server_memory_usage>4800000000</max_server_memory_usage>
<users><default><password></password><networks><ip>127.0.0.1</ip></networks>
<profile>default</profile><quota>default</quota></default></users>
<profiles><default>
<allow_experimental_time_series_table>1</allow_experimental_time_series_table>
<enable_time_series_aggregate_functions>1</enable_time_series_aggregate_functions>
<log_queries>1</log_queries><log_processors_profiles>1</log_processors_profiles>
<max_memory_usage>1800000000</max_memory_usage>
</default></profiles>
<quotas><default><interval><duration>3600</duration></interval></default></quotas>
<query_log><database>system</database><table>query_log</table>
<flush_interval_milliseconds>1000</flush_interval_milliseconds></query_log>
<processors_profile_log><database>system</database><table>processors_profile_log</table>
<flush_interval_milliseconds>1000</flush_interval_milliseconds></processors_profile_log>
<default_profile>default</default_profile><default_database>default</default_database>
</clickhouse>"""
    path.write_text(body)
    return path


class Server:
    def __init__(self,binary,config,log):
        self.binary=str(binary)
        self.config=str(config)
        self.log=str(log)
        self.proc=None
        self.output=None

    def start(self):
        self.output=open(self.log,"ab")
        self.proc=subprocess.Popen([self.binary,"server",f"--config-file={self.config}"],
              stdout=self.output,stderr=subprocess.STDOUT,start_new_session=True)
        for attempt in range(120):
            if self.proc.poll() is not None:
                recent=pathlib.Path(self.log).read_bytes()[-16000:].decode("utf-8","replace")
                raise RuntimeError(f"ClickHouse exited immediately {self.binary} with {self.proc.returncode}; server log tail:\\n{recent}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:18191/ping",timeout=1) as response:
                    if response.read().strip()==b"Ok.":
                        return
            except Exception:
                pass
            time.sleep(.5)
        raise RuntimeError("ClickHouse did not answer ping after 60s")

    def stop(self):
        if self.proc:
            try:
                os.killpg(self.proc.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:self.proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid,signal.SIGKILL)
                self.proc.wait(timeout=15)
            self.proc=None
        if self.output:
            self.output.close()
            self.output=None
        for attempt in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:18191/ping",timeout=.5)
            except Exception:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Old ClickHouse listener survived process group shutdown")


def node_pids(root):
    visited=set()
    stack=[root]
    while stack and len(visited)<32:
        pid=stack.pop()
        if pid in visited:continue
        visited.add(pid)
        try:
            raw=pathlib.Path(f"/proc/{pid}/task/{pid}/children").read_text()
            stack.extend(int(x) for x in raw.split())
        except (OSError,ValueError):
            continue
    return visited


def rss(pid):
    found=[]
    for p in node_pids(pid):
        try:
            body=pathlib.Path(f"/proc/{p}/status").read_text()
            memory={}
            for line in body.splitlines():
                if line.startswith(("VmRSS:","VmHWM:")):
                    memory[line.split(":")[0]]=int(line.split()[1])*1024
            if "VmRSS" in memory:found.append(memory)
        except (OSError,ValueError,IndexError):
            pass
    return max(found,key=lambda x:x["VmRSS"]) if found else {"VmRSS":0,"VmHWM":0}


class ResidentSample:
    def __init__(self,pid):
        self.pid=pid
        self.samples=[]
        self.stop_event=threading.Event()
        self.monitor=threading.Thread(target=self._sample,daemon=True)
        self.overhead_seconds=0
    def _sample(self):
        while not self.stop_event.is_set():
            ts=time.perf_counter()
            self.samples.append(rss(self.pid))
            self.overhead_seconds+=time.perf_counter()-ts
            self.stop_event.wait(.1)
    def __enter__(self):
        self.monitor.start()
        return self
    def __exit__(self,*exc):
        self.stop_event.set()
        self.monitor.join(timeout=3)
    def summary(self):
        return {"samples":len(self.samples),
                "peak_rss_bytes":max([x.get("VmRSS",0) for x in self.samples] or [0]),
                "peak_hwm_bytes":max([x.get("VmHWM",0) for x in self.samples] or [0]),
                "observer_overhead_seconds":self.overhead_seconds}


def inner_parts():
    result={}
    for kind in ("tags","timeranges","samples"):
        if kind=="samples":
            statement=("SELECT name FROM system.tables WHERE database='default' "
                       "AND startsWith(name,'.inner_id.samples.') FORMAT JSONEachRow")
            values=[json.loads(s)["name"] for s in request(statement).splitlines() if s.strip()]
            if len(values)!=1:raise ValueError(f"Expected single samples target, got {values}")
            target=values[0]
        else:
            target=inner_target(kind).split(chr(96))[1]
        statement=(
          "SELECT name,rows,marks,bytes_on_disk FROM system.parts "
          "WHERE database='default' AND active AND table="+quoted(target)
          +" ORDER BY name FORMAT JSONEachRow"
        )
        values=[json.loads(s) for s in request(statement).splitlines() if s.strip()]
        if not values:raise ValueError(f"Empty {kind} parts")
        result[kind]={"target":target,"parts":values}
    return result


def stock_sql(case):
    expr=f"avg by(stage)(avg_over_time({METRIC}{SELECTORS[case]}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange(r30_ts,{quoted(expr)},"
            f"{END},{END},86400) SETTINGS {QUERY_SETTINGS} FORMAT JSONEachRow")


def run_phase(mode,index,binary,config,root,reference):
    data=root/"data"
    template=root/"template"
    if data.exists():shutil.rmtree(data)
    shutil.copytree(template,data,symlinks=True)
    # Explicitly exclude ephemeral ClickHouse lock/status files from each
    # restored snapshot. Never mutate replicated table data or metadata.
    for item in ("status","status.tmp"):
        transient=data/item
        if transient.exists():transient.unlink()
    # Atomic databases store metadata/data links; copied links must stay links.
    system_link=data/"data"/"system"/"processors_profile_log"
    if system_link.exists() and not system_link.is_symlink():
        raise ValueError("Atomic system data metadata symlink was flattened during copy")
    server=Server(binary,config,root/"evidence"/f"phase-{index}-{mode}.log")
    server.start()
    try:
        ranges=inner_target("timeranges")
        request("SYSTEM STOP MERGES "+ranges)
        observed=physical_census(require_duplicates=False)
        if int(observed["timeranges"]["physical_rows"])!=5120 or int(observed["timeranges"]["ids"])!=5120:
            raise ValueError("Physical TimeRanges multiplicity changed before measurement")
        sample_target = inner_target("samples")
        sample_stats = json.loads(request(
            f"SELECT count() AS bucket_rows, sum(length(samples)) AS sample_points, "
            f"max(length(samples)) AS max_samples_per_row FROM {sample_target} FORMAT JSONEachRow"
        ).strip())
        if int(sample_stats["max_samples_per_row"]) != 1:
            raise ValueError("R32 requires every physical SAMPLES bucket to contain one sample")
        if int(sample_stats["sample_points"]) != 5120:
            raise ValueError("R32 requires exactly 5120 samples in frozen one-update fixture")
        parts=inner_parts()
        if parts!=reference["parts"]:
            raise ValueError("Frozen source parts changed between stock/candidate phases")
        per_case={}
        all_qids=[]
        phase_references={}
        with ResidentSample(server.proc.pid) as monitor:
            for case in SELECTORS:
                query=stock_sql(case)
                measured=[]
                for i in range(7):
                    qid=f"r32native_{index}_{mode}_{case}_{i}_{os.getenv('GITHUB_RUN_ID','local')}"
                    result=normalize_any(request(query,qid,timeout=300))
                    if not result:
                        raise ValueError(f"Empty native output for {mode}/{case}")
                    if case in reference["outputs"]:
                        parity=assert_same(reference["outputs"][case],result,case,mode)
                    else:
                        parity={"max_abs_error":0}
                        phase_references[case]=result
                    if i==0 and mode=="stock" and index==0:
                        phase_references[case]=result
                    all_qids.append(qid)
                    measured.append({"qid":qid,"warmup":i<3,"points":len(result),
                                     "max_abs_error":parity.get("max_abs_error",0)})
                per_case[case]={"query":query,"trials":measured}
        logs=read_query_logs(all_qids)
        for case,info in per_case.items():
            series=[]
            for trial in info["trials"]:
                row=logs[trial["qid"]]
                cpu=(row.get("ProfileEvents") or {}).get("OSCPUVirtualTimeMicroseconds",0)/1000
                trial.update({"wall_ms":row["query_duration_ms"],"cpu_ms":cpu,
                              "query_memory_bytes":row["memory_usage"],
                              "read_rows":row["read_rows"],"read_bytes":row["read_bytes"]})
                if not trial["warmup"]:series.append(trial)
            info["median"]={k:statistics.median(x[k] for x in series) for k in (
                "wall_ms","cpu_ms","query_memory_bytes","read_rows","read_bytes"
            )}
        parts_after=inner_parts()
        if parts_after!=parts:raise ValueError("Physical parts changed during timed native queries")
        return {"sample_stats":sample_stats,"mode":mode,"phase":index,"binary_sha256":digest_file(binary),
                "parts":parts,"physical":observed,"queries":per_case,
                "rss":monitor.summary()},phase_references
    finally:
        server.stop()


def summarize(report):
    for case in SELECTORS:
        variants={}
        for mode in ("stock","onebucket"):
            chosen=[p["queries"][case]["median"] for p in report["phases"] if p["mode"]==mode]
            variants[mode]={k:statistics.mean(x[k] for x in chosen) for k in chosen[0]}
            variants[mode]["control_wall_drift"]=abs(chosen[0]["wall_ms"]-chosen[1]["wall_ms"])/max(1,variants[mode]["wall_ms"])
        report["summary"][case]=variants
        if any(v["control_wall_drift"]>.25 for v in variants.values()):
            report["errors"].append(f"{case}: A/A wall drift exceeds 25%")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stock",required=True)
    p.add_argument("--candidate",required=True)
    p.add_argument("--work",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--series",type=int,default=4096)
    p.add_argument("--updates",type=int,default=1)
    args=p.parse_args()
    if args.series!=4096 or args.updates!=1:
        raise ValueError("Only frozen 4096*1 fixture accepted for one-bucket test")
    root=pathlib.Path(args.work)
    root.mkdir(parents=True,exist_ok=True)
    for name in ("data","tmp","userfiles","evidence"):
        (root/name).mkdir(exist_ok=True)
    result=pathlib.Path(args.out)
    result.mkdir(parents=True,exist_ok=True)
    cfg=configure(root)
    stock=pathlib.Path(args.stock).resolve()
    candidate=pathlib.Path(args.candidate).resolve()
    if digest_file(stock)!=STOCK_SHA:raise ValueError("Stock R29 binary mismatch")
    cand_sha=digest_file(candidate)
    if cand_sha==STOCK_SHA:raise ValueError("Candidate is identical to stock")
    report={"kind":"R32_FULL_NATIVE_ONEBUCKET_O2_A_B_B_A","status":"INCONCLUSIVE",
            "source_sha":SOURCE_SHA,"stock_sha256":STOCK_SHA,
            "candidate_sha256":cand_sha,"phases":[],"summary":{},"errors":[],
            "full_native_promql":True,"input_frozen_across_phases":True}
    save(result/"report.json",report)
    init=Server(stock,cfg,root/"evidence"/"init-server.log")
    try:
        init.start()
        data=result/"fixture.jsonl"
        report["fixture"]=write_fixture(SimpleNamespace(series=args.series,updates=args.updates),data)
        request("CREATE TABLE r30_ts ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
        request("SYSTEM STOP MERGES "+inner_target("timeranges"))
        write_batches_from_fixture(data,SimpleNamespace(series=args.series,updates=args.updates))
        report["physical"]=physical_census(require_duplicates=False)
        report["initial_parts"]=inner_parts()
        if int(report["physical"]["timeranges"]["physical_rows"])!=5120:
            raise ValueError("Unexpected physical TimeRanges series count; expected 4096 plus two 512-series control cohorts")
        request("SYSTEM FLUSH LOGS")
        save(result/"report.json",report)
    except Exception as e:
        report["errors"].append("ingest/preflight: "+str(e))
        save(result/"report.json",report)
        raise
    finally:
        init.stop()
    if (root/"template").exists():shutil.rmtree(root/"template")
    # PID/lock files and cached config are runtime state, not immutable data.
    # Copying ClickHouse status into a fresh server can preserve a stale lock.
    source_data=root/"data"
    report["runtime_files_excluded"]=[]
    for relative in ("status","status.tmp"):
        live=source_data/relative
        if live.exists():
            report["runtime_files_excluded"].append(relative)
    shutil.copytree(source_data,root/"template",symlinks=True,
      ignore=shutil.ignore_patterns("status","status.tmp","preprocessed_configs"))
    expected={"parts":report["initial_parts"],"outputs":{}}
    try:
        for i,mode in enumerate(PHASES):
            binary=stock if mode=="stock" else candidate
            phase,refs=run_phase(mode,i,binary,cfg,root,expected)
            if refs and i==0:
                expected["outputs"].update(refs)
            report["phases"].append(phase)
            save(result/"report.json",report)
            print("R32_NATIVE_PHASE",i,mode,
                {c:r["median"] for c,r in phase["queries"].items()},
                "rss_peak",phase["rss"]["peak_rss_bytes"],flush=True)
        summarize(report)
        report["status"]="PASS" if not report["errors"] else "INCONCLUSIVE_DRIFT"
    except Exception as e:
        report["errors"].append(str(e))
        report["status"]="CORRECTNESS_FAIL_OR_INVALID_METHOD"
    finally:
        # Preserve all server diagnostic logs even when the first phase fails.
        for f in sorted((root/"evidence").glob("*.log")):
            shutil.copy2(f,result/f.name)
        shutil.copy2(cfg,result/"server-config.xml")
        save(result/"report.json",report)
        print("R32_NATIVE_DONE",json.dumps({
           "status":report["status"],"errors":report["errors"],
           "cases":list(report["summary"]),"completed_phases":len(report["phases"])}),flush=True)
    if report["status"]!="PASS":raise SystemExit(1)


if __name__=="__main__":
    from types import SimpleNamespace
    main()
