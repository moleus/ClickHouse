#!/usr/bin/env python3
"""Pre-resolved Grafana Prometheus requests for R33.

Each case is a concrete /api/v1/query_range request after Grafana expands its
variables and aligns the panel range. Never send '$__interval' literally.
Metadata endpoints and /query are separate from expensive range tests.
"""
import math
import urllib.parse

DAY=86400
QUERY_TYPE="range"
METRIC="jenkins_avs_build_stage_duration"


def catalog(manifest):
    end=int(manifest["end"])
    # Static real-looking selectors; exact dashboard inspector JSON is NOT
    # available, so this is a controlled proxy of Grafana HTTP request shapes.
    cases=[
        ("unfiltered14d_1d","avg by(stage)(avg_over_time("+METRIC+"[1d]))",
         end-13*DAY,end,DAY),
        ("branch_job_regex14d_1d",
         'avg by(stage)(avg_over_time('+METRIC+
         '{branch=~"(main|feature-03)",jenkins_job=~"job-0(2|3|4)"}[1d]))',
         end-13*DAY,end,DAY),
        ("stage_project_regex14d_1d",
         'avg by(stage)(avg_over_time('+METRIC+
         '{project="project-01",stage=~"stage-000[7-9]"}[1d]))',
         end-13*DAY,end,DAY),
        ("negative_result14d_1d",
         'avg by(stage)(avg_over_time('+METRIC+'{result!="failure"}[1d]))',
         end-13*DAY,end,DAY),
        ("all_branches14d_1d",
         'avg by(stage)(avg_over_time('+METRIC+'{branch=~".*"}[1d]))',
         end-13*DAY,end,DAY),
        ("shifted_6h_14d",
         'avg by(stage)(avg_over_time('+METRIC+'{branch="main"}[1d]))',
         end-13*DAY+21600,end+21600,DAY),
        ("last_day_hourly",
         'avg by(stage)(avg_over_time('+METRIC+'{branch=~"(main|release-.*)"}[1d]))',
         end-DAY,end,3600),
        ("instant_vector_daily",
         'avg by(stage)('+METRIC+'{branch="main"})',
         end-13*DAY,end,DAY),
    ]
    return [{"id":id,"query":query,"start":start,"end":finish,"step":step,
             "eval_count":int((finish-start)//step+1),"type":"range"}
            for id,query,start,finish,step in cases]


def api_url(host, table, case):
    if case["type"]!="range":
        raise ValueError("Not a Grafana Range request")
    query={
      "database":"default",
      "table":table,
      "query":case["query"],
      "start":str(case["start"]),
      "end":str(case["end"]),
      "step":str(case["step"]),
    }
    return host+"/prometheus/api/v1/query_range?"+urllib.parse.urlencode(query)


def instant_url(host,table,query,when):
    q={"database":"default","table":table,"query":query,"time":str(when)}
    return host+"/prometheus/api/v1/query?"+urllib.parse.urlencode(q)


def metadata_url(host,table,path,params=None):
    q={"database":"default","table":table}
    if params:q.update(params)
    return host+"/prometheus/api/v1/"+path+"?"+urllib.parse.urlencode(q,doseq=True)


def normalize_sql(rows):
    result={}
    for row in rows:
        tags=row.get("tags",{})
        if isinstance(tags,dict):
            labels=tuple(sorted((str(k),str(v)) for k,v in tags.items()))
        else:
            labels=tuple(sorted((str(k),str(v)) for k,v in tags))
        for stamp,value in row.get("samples",[]):
            if value is None:
                continue
            # SQL returns timestamps as formatted date string, millisecond
            # epoch, or numeric seconds depending on server JSON format.
            if isinstance(stamp,(int,float)):
                epoch=float(stamp)
                if epoch>100_000_000_000:epoch/=1000
            else:
                import datetime as dt
                parsed=dt.datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
                if parsed.tzinfo is None:
                    parsed=parsed.replace(tzinfo=dt.timezone.utc)
                epoch=parsed.timestamp()
            key=(labels,round(epoch,3))
            if key in result:raise ValueError("Duplicate SQL (labels,time)")
            result[key]=float(value)
    return result


def normalize_api(payload):
    if payload.get("status")!="success":
        raise ValueError("HTTP Prometheus API failed: "+repr(payload)[:700])
    data=payload.get("data",{})
    if data.get("resultType")!="matrix":
        raise ValueError(f"Expected matrix, got {data.get('resultType')}")
    result={}
    for row in data.get("result",[]):
        labels=tuple(sorted((str(k),str(v)) for k,v in row.get("metric",{}).items()))
        for stamp,value in row.get("values",[]):
            key=(labels,round(float(stamp),3))
            if key in result:raise ValueError("Duplicate API (labels,time)")
            result[key]=float(value)
    return result


def exact_response_parity(sql_rows,api_payload,case):
    baseline=normalize_sql(sql_rows)
    actual=normalize_api(api_payload)
    if set(baseline)!=set(actual):
        missing=set(baseline)-set(actual)
        extra=set(actual)-set(baseline)
        raise ValueError({"case":case,"missing":len(missing),"extra":len(extra),
                          "missing_examples":[str(x) for x in list(missing)[:3]],
                          "extra_examples":[str(x) for x in list(extra)[:3]]})
    for key in baseline:
        a,b=baseline[key],actual[key]
        if not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8):
            raise ValueError({"case":case,"value_mismatch":str(key),"SQL":a,"API":b})
    return {"equal":True,"points":len(baseline),
            "distinct_output_label_sets":len({k[0] for k in baseline}),
            "min_time":min((k[1] for k in baseline),default=None),
            "max_time":max((k[1] for k in baseline),default=None)}


def quote_sql_string(value):
    # Standard SQL literal escape, not Python shell quoting.
    return "'"+str(value).replace("'","''")+"'"


def sql_statement(table,case):
    if case["type"]!="range":raise ValueError("Not a range case")
    return (f"SELECT tags,samples FROM prometheusQueryRange(default.{table},"
       f"{quote_sql_string(case['query'])},{case['start']},{case['end']},"
       f"{case['step']}) FORMAT JSONEachRow")


if __name__=="__main__":
    print("R33 catalog module")
