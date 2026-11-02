#!/usr/bin/env python3
"""Deep causal profile: same v4 binary on/off, plan, query log, processors, CPU trace.

Cost of standalone stages is non-additive and cannot be subtracted as pipeline time.
"""
import argparse, json, os, pathlib, random, re, statistics, time
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import sql_string
from bench_native_one_point_sql import translated_query_no_sql_dedup

HOST="http://127.0.0.1:18191"
DAY=86400

def call(sql,qid=None):
    ms,raw=post(HOST,sql,qid)
    return ms,raw.decode("utf-8","replace")

def save(path,data):
    pathlib.Path(path).write_text(json.dumps(data,indent=2,default=str)+"\n")

def table_for(prefix):
    _,s=call("SELECT name FROM system.tables WHERE database='default' "
             f"AND startsWith(name,'.inner_id.{prefix}.') FORMAT TSVRaw")
    names=s.strip().splitlines()
    if len(names)!=1: raise AssertionError((prefix,names))
    return "default."+chr(96)+names[0].replace(chr(96),chr(96)*2)+chr(96)

def load_jsonlines(s):
    return [json.loads(x) for x in s.splitlines() if x.strip()]

def make_cases(meta):
    end=int(meta["end"])
    work=[]
    for metric in ("tslab_sparse_40d","tslab_dense_40d"):
        for e in (14,40):
            start=end-(e-1)*DAY
            for name,suffix in (("whole",""),("exact",'{stage="stage-0007"}'),
                                ("project",'{project="project-00"}')):
                selector=metric+suffix
                expr=f"avg by(stage)(avg_over_time({selector}[1d]))"
                base=f"SELECT * FROM prometheusQueryRange(cost_ts,{sql_string(expr)},{start},{end},86400)"
                work.append((f"{metric.split('_')[1]}_{name}_e{e}",base))
    return work

def timed_interleaved(work, run):
    metrics=[]
    refs={}
    timings={}
    # Warm up both variants first, without including the warmups in samples.
    for name,base in work:
        for lab in ("off","on"):
            query=base+f" SETTINGS promql_enable_native_one_point_lab={1 if lab=='on' else 0} FORMAT JSONEachRow"
            for j in range(2):
                _,raw=call(query)
                now=normalize_any(raw.encode())
                if name not in refs: refs[name]=now
                else:assert_same(refs[name],now,name,lab+"_warmup")
    rng=random.Random(27011987)
    for rep in range(6):
        cases=list(work);rng.shuffle(cases)
        for name,base in cases:
            modes=("off","on") if rep%2==0 else ("on","off")
            for lab in modes:
                query=base+f" SETTINGS promql_enable_native_one_point_lab={1 if lab=='on' else 0} FORMAT JSONEachRow"
                qid=f"deep_{run}_{name}_{lab}_{rep}"
                elapsed,raw=call(query,qid)
                parsed=normalize_any(raw.encode())
                assert_same(refs[name],parsed,name,lab)
                timings.setdefault(name,{}).setdefault(lab,[]).append((qid,elapsed))
    for name,results in timings.items():
        off=[x[1] for x in results["off"]]
        on=[x[1] for x in results["on"]]
        row={"case":name,"output_points":len(refs[name]),
             "off_p50_ms":pct(off,.5),"on_p50_ms":pct(on,.5),
             "off_times_ms":off,"on_times_ms":on,
             "ratio_on_to_off":pct(on,.5)/max(pct(off,.5),.00001)}
        metrics.append(row)
        print("DEEP_PROFILE_AB",json.dumps({k:v for k,v in row.items()
              if k not in ("off_times_ms","on_times_ms")},separators=(",",":")),flush=True)
    return metrics,timings

def run_layers(meta,run):
    ts=table_for("samples")
    tags=table_for("tags")
    timeranges=table_for("timeranges")
    end=int(meta["end"])
    e=40;start=end-(e-1)*DAY
    start_ms=start*1000;end_ms=end*1000;min_ms=start_ms-DAY*1000+1
    cases={}
    for kind in ("sparse","dense"):
        metric=f"tslab_{kind}_40d"
        for label,filter_text in (("whole",""),("stage7",'{stage="stage-0007"}')):
            selector=metric+filter_text
            condition=f"metric_name='{metric}'"
            if label=="stage7":condition+=" AND tags['stage']='stage-0007'"
            prefix=f"{kind}_{label}"
            selector_sql=f"timeSeriesSelector('default','cost_ts',{sql_string(selector)},"+(
                 f"fromUnixTimestamp64Milli({min_ms}),fromUnixTimestamp64Milli({end_ms}),'samples')")
            q={
                "tags_selected":f"SELECT sum(sipHash64(toString(id))) FROM {tags} WHERE {condition} FORMAT TSV",
                "timeranges_scan":f"SELECT count(), min(min_time),max(max_time) FROM {timeranges} FORMAT TSV",
                "raw_samples":f"SELECT sum(length(samples)) FROM {ts} WHERE "
                   f"min_time <= fromUnixTimestamp64Milli({end_ms}) AND "
                   f"max_time >= fromUnixTimestamp64Milli({min_ms}) FORMAT TSV",
                "selector_rows":f"SELECT count(),sum(length(samples)) FROM {selector_sql} FORMAT TSV",
                "selector_hash":f"SELECT uniqExact(id),sum(length(samples)) FROM {selector_sql} FORMAT TSV",
                "selector_decode":f"SELECT sum(arraySum(arrayMap(x -> tupleElement(x,2), samples))) "
                   f"FROM {selector_sql} FORMAT TSV",
            }
            sql=translated_query_no_sql_dedup(start,end,DAY,table="cost_ts",metric=metric)
            if label=="stage7":
                before=f"FROM timeSeriesSelector('default','cost_ts','{metric}',"
                after=f"FROM timeSeriesSelector('default','cost_ts',{sql_string(selector)},"
                if sql.count(before)!=1:raise AssertionError(("selector replacement",prefix))
                sql=sql.replace(before,after)
            marker="    SELECT timeSeriesGroupToTags(stage_group)"
            if sql.count(marker)==1:
                head=sql[:sql.index(marker)]
                q["per_series_aggregate"]=head+" SELECT count(),sum(series_avg) FROM per_series FORMAT TSV"
                q["per_stage_aggregate"]=head+" SELECT count(),sum(stage_avg) FROM per_stage FORMAT TSV"
            for stage,stmt in q.items():cases[prefix+"_"+stage]=stmt
    out={}
    rng=random.Random(27002026)
    for name,stmt in cases.items():
        # Confirm actual work and response success before timing.
        try:
            _,response=call(stmt)
            assert response.strip(),("empty diagnostic",name)
            values=[];qids=[]
            for i in range(2):
                call(stmt)
            for i in range(5):
                qid=f"layer_{run}_{name}_{i}"
                ms,result=call(stmt,qid)
                assert result==response,("unstable diagnostic output",name)
                values.append(ms);qids.append(qid)
            out[name]={"p50_ms":pct(values,.5),"times_ms":values,
                       "query_ids":qids,"result":response[:300],"sql":stmt}
            print("DEEP_PROFILE_LAYER",json.dumps({"name":name,
                  "p50_ms":pct(values,.5),"result":response[:180]},separators=(",",":")),flush=True)
        except Exception as exc:
            out[name]={"unsupported_or_failed":str(exc)[:4000],"sql":stmt}
            print("DEEP_PROFILE_LAYER_ERROR",json.dumps({"name":name,
                  "error":str(exc)[:2000]},separators=(",",":")),flush=True)
    return out

def querylog(prefix):
    call("SYSTEM FLUSH LOGS")
    _,body=call("SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,"
                "ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND "
                f"startsWith(query_id,{sql_string(prefix)}) FORMAT JSONEachRow")
    return {x["query_id"]:x for x in load_jsonlines(body)}

def profiler(name,sql,run,available,outdir):
    settings=[]
    for key,val in [
       ("log_processors_profiles","1"),
       ("log_query_threads","1"),
       ("query_profiler_cpu_time_period_ns","10000000"),
       ("query_profiler_real_time_period_ns","10000000")]:
       if key in available:settings.append(f"{key}={val}")
    settings.append("max_threads=4")
    settings.append("promql_enable_native_one_point_lab="+("1" if name.endswith("_on") else "0"))
    # sql is a SELECT without FORMAT, optional SETTINGS may be appended.
    q=sql+" SETTINGS "+",".join(settings)+" FORMAT JSONEachRow"
    results=[]
    for i in range(4):
        qid=f"trace_{run}_{name}_{i}"
        elapsed,raw=call(q,qid)
        results.append({"qid":qid,"ms":elapsed,"result_points":len(normalize_any(raw.encode()))})
    try:
        _,text=call("EXPLAIN PIPELINE "+sql+" SETTINGS max_threads=4,promql_enable_native_one_point_lab="+("1" if name.endswith("_on") else "0"))
        pathlib.Path(outdir,f"pipeline-{name}.txt").write_text(text)
    except Exception as e:
        pathlib.Path(outdir,f"pipeline-{name}-error.txt").write_text(str(e))
    try:
        _,text=call("EXPLAIN indexes=1 "+sql+" SETTINGS max_threads=4,promql_enable_native_one_point_lab="+("1" if name.endswith("_on") else "0"))
        pathlib.Path(outdir,f"plan-{name}.txt").write_text(text)
    except Exception as e:
        pathlib.Path(outdir,f"plan-{name}-error.txt").write_text(str(e))
    return results

def extra_profile(run):
    result={}
    call("SYSTEM FLUSH LOGS")
    try:
        _,body=call("SELECT query_id,name,sum(elapsed_us) AS elapsed_us,"
                    "sum(input_wait_elapsed_us) AS input_wait_us,"
                    "sum(output_wait_elapsed_us) AS output_wait_us,"
                    "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
                    "FROM system.processors_profile_log WHERE "
                    f"startsWith(query_id,'trace_{run}') "
                    "GROUP BY query_id,name ORDER BY query_id,elapsed_us DESC "
                    "FORMAT JSONEachRow")
        result["processors"]=load_jsonlines(body)
    except Exception as exc: result["processors_error"]=str(exc)[:2500]
    try:
        _,body=call("SELECT query_id,addressToSymbol(arrayJoin(trace)) AS symbol,count() AS n "
                    "FROM system.trace_log WHERE "
                    f"startsWith(query_id,'trace_{run}') "
                    "GROUP BY query_id,symbol ORDER BY query_id,n DESC LIMIT 120 FORMAT JSONEachRow")
        result["cpu_trace_symbols"]=load_jsonlines(body)
    except Exception as exc: result["trace_error"]=str(exc)[:2500]
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    outdir=str(pathlib.Path(a.output).parent)
    pathlib.Path(outdir).mkdir(parents=True,exist_ok=True)
    run=os.environ.get("GITHUB_RUN_ID","local")
    _,setting_text=call("SELECT name FROM system.settings "
      "WHERE name IN ('log_processors_profiles','log_query_threads',"
      "'query_profiler_cpu_time_period_ns','query_profiler_real_time_period_ns') FORMAT TSVRaw")
    available=set(setting_text.splitlines())
    work=make_cases(manifest)
    metrics,timings=timed_interleaved(work,run)
    layers=run_layers(manifest,run)
    sampled={}
    candidates=[item for item in work if item[0] in (
        "sparse_whole_e40","sparse_exact_e40","dense_whole_e40","dense_exact_e40")]
    for name,base in candidates:
        for mode in ("off","on"):
            sample_name=name+"_"+mode
            sampled[sample_name]=profiler(sample_name,base,run,available,outdir)
    logs=querylog(f"deep_{run}_")
    logs.update(querylog(f"layer_{run}_"))
    logs.update(querylog(f"trace_{run}_"))
    expected=sum(len(x) for case in timings.values() for x in case.values())
    assert len([x for x in logs if x.startswith(f"deep_{run}_")])==expected,(
        "missing query profile",expected,len(logs))
    for row in metrics:
        for mode in ("off","on"):
            qids=[x[0] for x in timings[row["case"]][mode]]
            data=[logs[x] for x in qids]
            key=mode+"_"
            row[key+"server_p50_ms"]=pct([x["query_duration_ms"] for x in data],.5)
            row[key+"peak_memory_p95"]=pct([x["memory_usage"] for x in data],.95)
            row[key+"read_rows_p50"]=pct([x["read_rows"] for x in data],.5)
            row[key+"cpu_us_p50"]=pct([
                sum(int(x["ProfileEvents"].get(e,0)) for e in
                    ("OSCPUVirtualTimeMicroseconds","UserTimeMicroseconds"))
                for x in data],.5)
            row[key+"os_read_bytes_p50"]=pct([
                 int(x["ProfileEvents"].get("OSReadBytes",0)) for x in data],.5)
    for name,row in layers.items():
        if "query_ids" not in row:continue
        logs_part=[logs[q] for q in row["query_ids"]]
        row["read_rows_p50"]=pct([x["read_rows"] for x in logs_part],.5)
        row["memory_p95"]=pct([x["memory_usage"] for x in logs_part],.95)
    save(str(pathlib.Path(outdir)/"profile-querylog.json"),logs)
    profile_events={}
    for row in metrics:
        case=row["case"]
        for mode in ("off","on"):
            qids=[x[0] for x in timings[case][mode]]
            keys=set(k for qid in qids for k in logs[qid]["ProfileEvents"])
            profile_events[case+"_"+mode]={
                k:pct([int(logs[qid]["ProfileEvents"].get(k,0)) for qid in qids],.5)
                for k in sorted(keys)}
    save(str(pathlib.Path(outdir)/"profile-events-p50.json"),profile_events)
    additional=extra_profile(run)
    save(str(pathlib.Path(outdir)/"processor-and-trace.json"),additional)
    result={"all_equal":True,"run":run,"setting_support":sorted(available),
            "timed_cases":metrics,"layer_cases":layers,
            "profiled":sampled,
            "processor_trace":{k:len(v) if isinstance(v,list) else v for k,v in additional.items()}}
    save(a.output,result)
    print("DEEP_PROFILE_COMPLETE",json.dumps({"cases":len(metrics),
          "layer_probes":len(layers),"query_events":len(logs),
          "processor_rows":result["processor_trace"].get("processors"),
          "trace_samples":result["processor_trace"].get("cpu_trace_symbols")},
          separators=(",",":")),flush=True)

if __name__=="__main__":main()
