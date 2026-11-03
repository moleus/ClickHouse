#!/usr/bin/env python3
"""R32 end-to-end Grafana Prometheus HTTP API parity vs SQL PromQL.

Runs against ONE unchanged stock ClickHouse TimeSeries binary. All Grafana
queries are real HTTP /prometheus/api/v1/query_range requests with concrete
start/end/step and fully expanded label regexes, NOT SQL ID-stage shortcuts.
This is a strict semantic/transport experiment, not a VM comparison.
"""
import argparse
import hashlib
import json
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, ".github/ts-lab-r32")
sys.path.insert(0, ".github/ts-lab-r30")
from generate_grafana_ci_v2 import validate
from grafana_query_catalog import (catalog,api_url,instant_url,metadata_url,
                                   exact_response_parity,sql_statement)
from bench_timeranges_any_sql import request,save

HOST="http://127.0.0.1:18191"
SAMPLES_PER_BATCH=2000
LABELS={"app","build","branch","jenkins_job","project","stage","result"}


def get_json(url,timeout=300):
    started=time.perf_counter()
    try:
        req=urllib.request.Request(url,headers={"Accept":"application/json"},method="GET")
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read()
            status=r.status
            content_type=r.headers.get("Content-Type","")
    except urllib.error.HTTPError as e:
        body=e.read().decode("utf-8","replace")[:1500]
        raise RuntimeError(f"Prometheus HTTP {e.code} at {url[:180]}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Prometheus endpoint failed {url[:180]}: {e}") from e
    elapsed_ms=(time.perf_counter()-started)*1000
    if status!=200:raise ValueError(f"Expected 200 from {url[:150]}, got {status}")
    payload=json.loads(raw)
    if payload.get("status")!="success":
        raise ValueError(f"Prometheus response not success: {str(payload)[:1400]}")
    return payload,{"wall_ms":elapsed_ms,"bytes":len(raw),"content_type":content_type}


def sql_json(query,qid,timeout=300):
    started=time.perf_counter()
    raw=request(query,query_id=qid,timeout=timeout)
    elapsed=(time.perf_counter()-started)*1000
    values=[json.loads(s) for s in raw.splitlines() if s.strip()]
    return values,{"wall_ms":elapsed,"bytes":len(raw.encode()),"rows":len(values)}


def ingest(table,fixture):
    started=time.perf_counter()
    query="INSERT INTO "+table+" (metric_name,tags,samples) FORMAT JSONEachRow"
    endpoint=HOST+"/?"+urllib.parse.urlencode({"wait_end_of_query":1,"query":query})
    inserted=0
    batches=0
    digest=hashlib.sha256()
    with pathlib.Path(fixture).open("rb") as f:
        while True:
            lines=[f.readline() for _ in range(SAMPLES_PER_BATCH)]
            lines=[ln for ln in lines if ln]
            if not lines:break
            data=b"".join(lines)
            digest.update(data)
            r=urllib.request.Request(endpoint,method="POST",data=data)
            try:
                with urllib.request.urlopen(r,timeout=240) as response:
                    response.read()
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"R32 INSERT {e.code}: {e.read().decode('utf-8','replace')[:1300]}") from e
            inserted+=len(lines)
            batches+=1
            if batches%50==0:print("R32_INGEST_PROGRESS",inserted,flush=True)
    return {"series_rows":inserted,"batches":batches,
            "sha256":digest.hexdigest(),
            "wall_seconds":round(time.perf_counter()-started,3)}


def check_metadata(table,manifest):
    result={}
    base=HOST
    start=manifest["start"]
    end=manifest["end"]
    targets={
      "labels":metadata_url(base,table,"labels",{"start":str(start),"end":str(end)}),
      "stage_values":metadata_url(base,table,"label/stage/values",
           {"match[]":['{__name__="jenkins_avs_build_stage_duration"}']}),
      "job_values":metadata_url(base,table,"label/jenkins_job/values",
           {"match[]":['{__name__="jenkins_avs_build_stage_duration"}']}),
      "sample_series":metadata_url(base,table,"series",{
           "match[]":['{__name__="jenkins_avs_build_stage_duration",stage="stage-0007",jenkins_job="job-07"}'],
           "start":str(start),"end":str(end)})
    }
    for name,url in targets.items():
        payload,perf=get_json(url,timeout=180)
        data=payload["data"]
        result[name]={"count":len(data),"url":url,"perf":perf,
                      "sample":data[:4] if isinstance(data,list) else repr(data)[:200]}
        if name=="labels":
            if not LABELS.issubset(set(data)):
                raise ValueError("Grafana /labels missing model labels "+str(LABELS-set(data)))
        if name=="stage_values" and set(data)!={f"stage-{k:04d}" for k in range(38)}:
            raise ValueError(f"Grafana stage label_values missed stages: {data[:50]}")
        if name=="job_values" and not data:
            raise ValueError("Empty Grafana jenkins_job template variable")
        # Narrow /series may be empty in deliberately small fixtures but the
        # endpoint must return a valid array, not HTML or SQL syntax.
        if not isinstance(data,list):
            raise ValueError(f"Non-array metadata response {name}")
        print("R32_METADATA",name,len(data),flush=True)
    return result


def check_instant(table,manifest):
    expression='avg by(stage)(jenkins_avs_build_stage_duration{branch="main"})'
    samples={}
    for name,when in (("aligned",manifest["end"]),
                      ("shifted",manifest["end"]-86400+3600)):
        payload,perf=get_json(instant_url(HOST,table,expression,when))
        if payload["data"]["resultType"]!="vector":
            raise ValueError(f"Expected Prometheus vector for instant query: {name}")
        samples[name]={"result_type":"vector",
                       "series":len(payload["data"]["result"]),
                       "wall_ms":perf["wall_ms"],
                       "sample":payload["data"]["result"][:2]}
        print("R32_INSTANT",name,samples[name]["series"],flush=True)
    return samples


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--table",default="grafana_r32")
    p.add_argument("--selection",default="all",choices=("all","quick"))
    args=p.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,45}",args.table):
        raise ValueError("Invalid test table identifier")
    out=pathlib.Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    if manifest["metric_name"]!="jenkins_avs_build_stage_duration" or manifest["stages_per_build"]!=38:
        raise ValueError("Not the R32 fixture")
    report={"question_id":"R32","status":"INCONCLUSIVE",
            "fixture":manifest,"errors":[],"workload":{},"api_endpoints":{},
            "scientific_scope":"Grafana-compatible HTTP API vs native SQL, not VictoriaMetrics",
            "altered_clickhouse_cpp":False}
    save(out/"report.json",report)
    try:
        report["preflight"]=validate(args.fixture,manifest)
        report["server_version"]=request("SELECT version() FORMAT TSV").strip()
        request("CREATE TABLE "+args.table+" ENGINE=TimeSeries "
                "SETTINGS recent_samples_ttl_seconds=0")
        report["ingest"]=ingest(args.table,args.fixture)
        if report["ingest"]["series_rows"]!=manifest["unique_series"]:
            raise ValueError("Partial ingest")
        if report["ingest"]["sha256"]!=manifest["sha256"]:
            raise ValueError("Fixture changed between generation and ingestion")
        # Confirm physical TimeSeries ID/counter rows before any query.
        report["tags_count"]=int(request("SELECT count() FROM "+args.table+
                                         " FORMAT TSV").strip())
        if report["tags_count"]!=manifest["unique_series"]:
            raise ValueError(f"Expected {manifest['unique_series']} series in TimeSeries source")
        save(out/"report.json",report)
        all_cases=catalog(manifest)
        if args.selection=="quick":
            selected={"unfiltered14d_1d","branch_job_regex14d_1d",
                      "shifted_6h_14d","instant_vector_daily"}
            all_cases=[x for x in all_cases if x["id"] in selected]
        for case in all_cases:
            name=case["id"]
            sql=sql_statement(args.table,case)
            url=api_url(HOST,args.table,case)
            # Alternating HTTP/SQL order avoids a fixed advantage of warmup.
            if len(report["workload"])%2==0:
                api,perf_api=get_json(url)
                sql_rows,perf_sql=sql_json(sql,"r32_sql_"+name)
            else:
                sql_rows,perf_sql=sql_json(sql,"r32_sql_"+name)
                api,perf_api=get_json(url)
            parity=exact_response_parity(sql_rows,api,name)
            entry={"case":case,"http_url":url,"sql":sql,"parity":parity,
                   "api":perf_api,"sql_perf":perf_sql,
                   "raw_api_result_type":api.get("data",{}).get("resultType")}
            report["workload"][name]=entry
            save(out/"report.json",report)
            print("R32_QUERY",name,json.dumps({
                "points":parity["points"],"label_sets":parity["distinct_output_label_sets"],
                "sql_ms":round(perf_sql["wall_ms"],3),
                "http_ms":round(perf_api["wall_ms"],3)
            },sort_keys=True),flush=True)
        report["api_endpoints"]["metadata"]=check_metadata(args.table,manifest)
        report["api_endpoints"]["instant"]=check_instant(args.table,manifest)
        report["status"]="PASS_API_AND_SQL_PARITY"
    except Exception as e:
        report["errors"].append(repr(e))
        report["status"]="INCONCLUSIVE_OR_CORRECTNESS_FAILURE"
    finally:
        save(out/"report.json",report)
        print("R32_DONE",json.dumps({"status":report["status"],
                                    "errors":report["errors"],
                                    "queries":list(report["workload"]),
                                    "metadata":list(report["api_endpoints"])
                                   },sort_keys=True),flush=True)
    if report["status"]!="PASS_API_AND_SQL_PARITY":raise SystemExit(1)

if __name__=="__main__":
    main()
