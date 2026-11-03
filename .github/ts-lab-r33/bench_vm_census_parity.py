#!/usr/bin/env python3
"""R33 native Grafana API/SQL oracle for VMUI *marginal census* proxy.

The 53,205 synthetic series exactly match Oct30 VMUI label marginal counts,
not proven production joint label distribution or temporal arrival rate.
No source modifications. This is a strict semantic+plan coverage test.
"""
import argparse
import hashlib
import json
import pathlib
import re
import statistics
import sys
import time

sys.path[:0]=[".github/ts-lab-r33",".github/ts-lab-r30"]
from generate_vm_census_proxy import validate, COUNTS, METRIC, BRANCHES, PROFILE
from grafana_query_catalog import (
    api_url, metadata_url, exact_response_parity, sql_statement
)
from bench_grafana_http_parity import get_json,sql_json,ingest,HOST
from bench_timeranges_any_sql import request,save


def cases(meta):
    end=meta["end"]
    day=86400
    all_branches="|".join(BRANCHES)
    source=METRIC
    entries=[
        ("stage14",f"avg by(stage)(avg_over_time({source}[1d]))",
         end-13*day,end,day),
        ("app14",f"avg by(app)(avg_over_time({source}[1d]))",
         end-13*day,end,day),
        ("project14",f"avg by(project)(avg_over_time({source}[1d]))",
         end-13*day,end,day),
        ("appname14",f"avg by(app_name)(avg_over_time({source}[1d]))",
         end-13*day,end,day),
        ("branch_main_job_regex",
         f'avg by(stage)(avg_over_time({source}{{branch=~"(main|feature-001|feature-002)",jenkins_job=~"jenkins-job-(0[0-9]|1[0-9])"}}[1d]))',
         end-13*day,end,day),
        ("project_stage_path_regex",
         f'avg by(stage)(avg_over_time({source}{{project="project-01",stage=~"pipeline/.*/app-00.*/stage-.*"}}[1d]))',
         end-13*day,end,day),
        ("result_negative",
         f'avg by(stage)(avg_over_time({source}{{result!="failure"}}[1d]))',
         end-13*day,end,day),
        ("branch_explicit_all",
         f'avg by(stage)(avg_over_time({source}{{branch=~"({all_branches})"}}[1d]))',
         end-13*day,end,day),
        ("branch_dot_star",
         f'avg by(stage)(avg_over_time({source}{{branch=~".*"}}[1d]))',
         end-13*day,end,day),
        ("shifted_6h",f'avg by(stage)(avg_over_time({source}{{branch="main"}}[1d]))',
         end-13*day+21600,end+21600,day),
        ("instant_sparse",f'avg by(stage)({source}{{branch="main"}})',
         end-13*day,end,day),
        ("lastday_hourly",
         f'avg by(stage)(avg_over_time({source}{{branch=~"(main|feature-001)"}}[1d]))',
         end-day,end,3600),
    ]
    return [{"id":name,"query":query,"start":a,"end":b,
             "step":step,"eval_count":(b-a)//step+1,"type":"range"}
            for name,query,a,b,step in entries]


def metadata(table,meta):
    names=set(COUNTS)
    payload,_=get_json(metadata_url(HOST,table,"labels",{}),timeout=240)
    observed_labels=set(payload["data"])
    if not names.issubset(observed_labels):
        raise ValueError("Missing Grafana label names: "+str(names-observed_labels))
    result={"label_names":sorted(observed_labels),"values":{}}
    for label,expected in COUNTS.items():
        payload,perf=get_json(metadata_url(HOST,table,
                  f"label/{label}/values",{"match[]":[f'{{__name__="{METRIC}"}}']}),timeout=240)
        values=payload["data"]
        if len(values)!=expected:
            raise ValueError(f"VMUI census label {label}: HTTP {len(values)} != observed {expected}")
        result["values"][label]={"count":len(values),"wall_ms":perf["wall_ms"],
                                 "first_values":values[:4]}
        print("R33_VM_LABEL",label,len(values),flush=True)
    return result


def check_sql_marginals(table):
    # SELECT on TimeSeries is intentionally explicit; do not read
    # system.parts estimates as exact label cardinalities.
    values={}
    for label,count in COUNTS.items():
        statement=f"SELECT uniqExact(tags['{label}']) FROM {table} FORMAT TSV"
        actual=int(request(statement,timeout=240).strip())
        if actual!=count:
            raise ValueError(f"SQL label census differs: {label}={actual}, expected={count}")
        values[label]=actual
    return values


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--table",default="grafana_vm_r33")
    p.add_argument("--out",required=True)
    args=p.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,50}",args.table):
        raise ValueError("Invalid table name")
    meta=json.loads(pathlib.Path(args.manifest).read_text())
    if meta.get("profile")!=PROFILE or meta["series"]!=53205:
        raise ValueError("Expected exact R33 VMUI synthetic census")
    report={"kind":"R33_VMUI_MARGINAL_CARDINALITY_HTTP_SQL",
            "status":"INCONCLUSIVE","source":"October 30 VMUI screenshot, joint distribution is synthetic",
            "errors":[],"fixture":meta,"queries":{},
            "variable_metadata":{},"physical":{},"compiled_cpp_change":False}
    out=pathlib.Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    save(out/"report.json",report)
    try:
        report["preflight"]=validate(args.fixture,meta)
        request(f"CREATE TABLE {args.table} ENGINE=TimeSeries "
                "SETTINGS recent_samples_ttl_seconds=0")
        report["ingest"]=ingest(args.table,args.fixture)
        if report["ingest"]["sha256"]!=meta["sha256"] or report["ingest"]["series_rows"]!=meta["series"]:
            raise ValueError("Ingest did not preserve identity")
        report["sql_cardinalities"]=check_sql_marginals(args.table)
        report["variable_metadata"]=metadata(args.table,meta)
        save(out/"report.json",report)
        all_cases=cases(meta)
        for case in all_cases:
            cid=case["id"]
            url=api_url(HOST,args.table,case)
            sql=sql_statement(args.table,case)
            # Exact correctness for ALL points, not 64-row hashes. Timings
            # are single calls and MUST NOT be attributed to a C++ win.
            http,http_perf=get_json(url,timeout=300)
            sq,sql_perf=sql_json(sql,"r33_vm_"+cid,timeout=300)
            parity=exact_response_parity(sq,http,cid)
            result={"case":case,"api_url":url,"sql":sql,
                    "http_perf":http_perf,"sql_perf":sql_perf,"parity":parity}
            report["queries"][cid]=result
            save(out/"report.json",report)
            print("R33_VM_QUERY",cid,json.dumps({
                "points":parity["points"],"groups":parity["distinct_output_label_sets"],
                "http_ms":round(http_perf["wall_ms"],2),
                "sql_ms":round(sql_perf["wall_ms"],2)
            },sort_keys=True),flush=True)
        a=report["queries"]["branch_explicit_all"]["parity"]
        b=report["queries"]["branch_dot_star"]["parity"]
        if a["points"]!=b["points"] or a["distinct_output_label_sets"]!=b["distinct_output_label_sets"]:
            raise ValueError("Grafana branch=All expanded alternation changed aggregate output count")
        report["status"]="PASS_VMUI_MARGINALS_AND_GRAFANA_SQL_HTTP_PARITY"
    except Exception as e:
        report["errors"].append(repr(e))
        report["status"]="INCONCLUSIVE_OR_REJECTED"
    finally:
        save(out/"report.json",report)
        print("R33_VM_DONE",json.dumps({
            "status":report["status"],"errors":report["errors"],
            "queries":list(report["queries"]),
            "label_counts":list(report["variable_metadata"].get("values",{}))
        },sort_keys=True),flush=True)
    if report["status"]!="PASS_VMUI_MARGINALS_AND_GRAFANA_SQL_HTTP_PARITY":
        raise SystemExit(1)


if __name__=="__main__":
    main()
