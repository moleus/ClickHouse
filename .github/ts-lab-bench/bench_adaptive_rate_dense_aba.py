#!/usr/bin/env python3
"""Cross-workload TimeSeries C++ bucket grower A/B/C/A evaluation.

A=16-cell upstream grower, B=4-cell grower, C=8-cell grower,
A=recheck original. All four phases query the same on-disk data.
CI Jenkins: 282036 distinct series, one sample per series, SQL oracle.
Regular metrics: 20000 series x 120 points, 5m rate, grouped rate, exact
host and gauge rollup; CH variants must equal original CH point by point.
VM 1.134.0 is a contextual same-run reference, not a C++ correctness
oracle for rate() because MetricsQL and PromQL rollup semantics differ.
"""
import argparse
import json
import math
import os
import pathlib
import time
import urllib.parse
import urllib.request

from bench_jenkins_querypath import (
    compare, epoch, normalized_ch, normalized_events, normalized_vm,
    pct, post
)


def normalize_any(raw):
    out={}
    for item in raw.splitlines():
        if not item.strip():
            continue
        row=json.loads(item)
        tags=row.get("tags",[])
        if isinstance(tags,dict):
            labels=tuple(sorted(tags.items()))
        else:
            labels=tuple(sorted((str(k),str(v)) for k,v in tags))
        for t,v in row.get("samples",[]):
            if v is not None:
                key=(labels,epoch(t))
                if key in out:
                    raise AssertionError(("duplicate TimeSeries labels/timestamp",key))
                out[key]=float(v)
    return out


def to_json_rows(data):
    result=[]
    for (labels,timestamp),value in data.items():
        result.append([list([k,v] for k,v in labels),timestamp,value])
    return sorted(result,key=lambda r:repr(r[:2]))


def from_json_rows(data):
    return {(tuple(tuple(kv) for kv in labels),int(t)):float(val)
            for labels,t,val in data}


def assert_same(baseline,actual,case,phase):
    if set(baseline)!=set(actual):
        missing=set(baseline)-set(actual)
        extra=set(actual)-set(baseline)
        raise AssertionError(("labels/timestamps differ",case,phase,
                              len(missing),len(extra),list(missing)[:5],list(extra)[:5]))
    max_abs=0.
    for key,orig in baseline.items():
        v=actual[key]
        if not math.isclose(orig,v,rel_tol=1e-10,abs_tol=1e-8):
            raise AssertionError(("different value",case,phase,key,orig,v))
        max_abs=max(max_abs,abs(v-orig))
    return {"points":len(actual),"max_abs_error":max_abs}


def vm_call(expr,start,end,step):
    q=urllib.parse.urlencode({"query":expr,"start":start,"end":end,"step":step,"nocache":1})
    start_time=time.perf_counter()
    with urllib.request.urlopen("http://127.0.0.1:8430/api/v1/query_range?"+q,timeout=300) as response:
        payload=json.loads(response.read())
    ms=round((time.perf_counter()-start_time)*1000,3)
    if payload.get("status")!="success":
        raise AssertionError(("VM query failed",expr,payload))
    return ms,payload


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--jenkins-manifest",required=True)
    p.add_argument("--dense-manifest",required=True)
    p.add_argument("--phase",required=True,choices=["base_before","adaptive_rate","base_after"])
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=4)
    p.add_argument("--warmups",type=int,default=1)
    a=p.parse_args()

    j=json.loads(pathlib.Path(a.jenkins_manifest).read_text())
    d=json.loads(pathlib.Path(a.dense_manifest).read_text())
    jstart,jend,jstep=(j[x] for x in ("query_start_epoch","query_end_epoch","step_seconds"))
    dstart,dend,dstep=(d[x] for x in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    jcases=[
        ("jenkins_all","", ""),
        ("jenkins_branch_main",'{branch="main"}',"AND branch='main'"),
        ("jenkins_project00",'{project="project-00"}',"AND project='project-00'"),
        ("jenkins_stage10",'{stage=~"stage-000[0-9]"}',
         "AND stage IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"),
    ]
    dcases=[
        ("dense_sum_rate","sum(rate(bench_metric[5m]))"),
        ("dense_sum_rate_team","sum by(team)(rate(bench_metric[5m]))"),
        ("dense_sum_rate_region","sum by(region)(rate(bench_metric[5m]))"),
        ("dense_host_exact",'bench_metric{host="h0001"}'),
    ]
    suites={}
    for key,selector,sql_filter in jcases:
        expr=f"avg by(stage)(avg_over_time({j['metric']}{selector}[1d]))"
        sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
             f"stage,avg(duration) AS value FROM jenkins_events "
             f"WHERE ts >= fromUnixTimestamp({jstart-86400}) AND ts < fromUnixTimestamp({jend}) "
             f"{sql_filter} GROUP BY epoch,stage FORMAT JSONEachRow")
        _,raw=post("http://127.0.0.1:18191",sql)
        event_rows=normalized_events(raw)
        reference={(tuple([("stage",key[0])]),key[1]):v for key,v in event_rows.items()}
        suites[key]={"table":"jenkins_ts","expr":expr,"start":jstart,"end":jend,"step":jstep,
                     "source":"independent_native_sql","reference":reference}
    for key,expr in dcases:
        suites[key]={"table":"dense_ts","expr":expr,"start":dstart,"end":dend,"step":dstep,
                     "source":"unmodified_clickhouse","reference":None}

    canonical_path=pathlib.Path(a.canonical)
    if a.phase=="base_before":
        if canonical_path.exists():
            raise AssertionError(("canonical already exists",str(canonical_path)))
        canonical={}
    else:
        canonical=json.loads(canonical_path.read_text())
        if set(canonical)!=set(suites):
            raise AssertionError(("incompatible canonical cases",list(canonical),list(suites)))

    run_id=os.environ.get("GITHUB_RUN_ID","local")
    qprefix="tslab_adaptive_"+run_id+"_"+a.phase
    outputs={"run":run_id,"phase":a.phase,"jenkins":j,"dense":d,
             "runner":os.environ.get("RUNNER_NAME"),
             "optimizations":"same pinned O1 C++ baseline and grower 4/8",
             "cases":{}}
    for key,suite in suites.items():
        table,expr,start,end,step=(suite[x] for x in ("table","expr","start","end","step"))
        query=(f"SELECT * FROM prometheusQueryRange({table}, "
               f"'{expr.replace(chr(39),chr(39)*2)}', {start}, {end}, {step}) FORMAT JSONEachRow")
        timings=[]
        max_abs_error=0.
        reference=suite["reference"]
        for i in range(a.warmups+a.repeats):
            query_id=qprefix+"_"+key+"_"+str(i)
            ms,raw=post("http://127.0.0.1:18191",query,query_id)
            parsed=normalize_any(raw)
            if a.phase=="base_before" and reference is None:
                reference=parsed
            if a.phase!="base_before" and suite["source"]=="unmodified_clickhouse":
                reference=from_json_rows(canonical[key])
            validation=assert_same(reference,parsed,key,a.phase)
            max_abs_error=max(max_abs_error,validation["max_abs_error"])
            if i>=a.warmups:
                timings.append(ms)
        if a.phase=="base_before":
            canonical[key]=to_json_rows(reference)
        if a.phase=="base_before":
            # VM 1.134.0 is evaluated in the same runner. For Jenkins
            # explicit [1d] the SQL event oracle checks every VM value.
            # For dense rate() do NOT silently equate VM MetricsQL rate
            # with CH PromQL rate; only time it and capture output count.
            vm_times=[]
            vm_check=None
            vm_points=None
            for trial in range(a.warmups+a.repeats):
                vm_ms,vm_payload=vm_call(expr,start,end,step)
                vm_points=sum(len(x["values"]) for x in vm_payload["data"]["result"])
                if key.startswith("jenkins_"):
                    vm=normalized_vm(vm_payload)
                    vm_rows={(tuple([("stage",stage)]),timestamp):v for (stage,timestamp),v in vm.items()}
                    vm_check=assert_same(reference,vm_rows,key,"VM")
                if trial>=a.warmups:
                    vm_times.append(vm_ms)
            outputs["cases"][key]={"vm_p50_ms":pct(vm_times,.5),
                                   "vm_p95_ms":pct(vm_times,.95),
                                   "vm_http_timings_ms":vm_times,
                                   "vm_points":vm_points,
                                   "vm_exact_sql":vm_check if key.startswith("jenkins_") else "not asserted: MetricsQL/PromQL semantics differ"}
        else:
            outputs["cases"][key]={}
        stats=outputs["cases"][key]
        stats.update({"expr":expr,"table":table,"points":len(reference),
                      "max_abs_error":max_abs_error,"result_equals_reference":True,
                      "warmups":a.warmups,"repetitions":a.repeats,
                      "p50_ms":pct(timings,.5),"p95_ms":pct(timings,.95),
                      "p99_ms":pct(timings,.99),"timings_ms":timings})
        print("ADAPTIVE_QUERY",json.dumps({"phase":a.phase,"case":key,
              "p50_ms":stats["p50_ms"],"p95_ms":stats["p95_ms"],
              "points":stats["points"],"max_abs_error":max_abs_error},sort_keys=True),flush=True)

    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,log=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{qprefix}') FORMAT JSONEachRow")
    log_rows={x["query_id"]:x for line in log.splitlines() if line.strip()
              for x in [json.loads(line)]}
    for key,stats in outputs["cases"].items():
        ids=[qprefix+"_"+key+"_"+str(i) for i in range(a.warmups,a.warmups+a.repeats)]
        selected=[log_rows[q] for q in ids if q in log_rows]
        if len(selected)!=a.repeats:
            raise AssertionError(("incomplete QueryFinish accounting",key,a.phase,len(selected),a.repeats))
        assert all(item["exception_code"]==0 for item in selected),(key,selected)
        for k,field,p in [("read_rows_p50","read_rows",.5),
                          ("read_bytes_p50","read_bytes",.5),
                          ("tracked_memory_p50_bytes","memory_usage",.5),
                          ("tracked_memory_p95_bytes","memory_usage",.95)]:
            stats[k]=pct([x[field] for x in selected],p)
        stats["tracked_memory_peak_bytes"]=max(x["memory_usage"] for x in selected)

    if a.phase=="base_before":
        canonical_path.parent.mkdir(parents=True,exist_ok=True)
        canonical_path.write_text(json.dumps(canonical,separators=(",",":"))+"\n")
    output=pathlib.Path(a.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(outputs,indent=2)+"\n")
    print("ADAPTIVE_PHASE_DONE",json.dumps({"phase":a.phase,"cases":len(outputs["cases"]),
          "all_outputs_valid":True,
          "one_sample_series":j["total_samples"],"dense_samples":d["samples_total"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
