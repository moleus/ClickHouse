#!/usr/bin/env python3
"""Test actual CTE setting propagation and missing CREATE TEMP TABLE privileges."""
import argparse,json,urllib.parse,urllib.request,urllib.error,pathlib,time
from bench_bucket_cxx_abca import normalize_any

BASE="http://127.0.0.1:18191/"
USER="tslab_reader"

def attempt(sql,settings=None):
    p={"user":USER,"allow_experimental_time_series_table":"1",
       "enable_time_series_aggregate_functions":"1","max_threads":"4"}
    p.update(settings or {})
    link=BASE+"?"+urllib.parse.urlencode(p)
    req=urllib.request.Request(link,data=sql.encode(),method="POST")
    began=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=100) as result:
            return {"success":True,"status":result.status,
                    "duration_ms":round((time.perf_counter()-began)*1000,3),
                    "raw":result.read().decode("utf-8","replace")}
    except urllib.error.HTTPError as exc:
        return {"success":False,"status":exc.code,
                "duration_ms":round((time.perf_counter()-began)*1000,3),
                "raw":exc.read().decode("utf-8","replace")}

def exact_allowed(result):
    out=normalize_any(result["raw"].encode())
    stages={dict(k[0]).get("stage") for k in out}
    assert stages=={"stage-allowed"},(stages,out)
    assert all(abs(x-4.0)<1e-12 for x in out.values()),out
    return len(out)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--compiled-v4",action="store_true")
    a=parser.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    expr="avg by(stage)(avg_over_time(tslab_acl_metric[1d]))"
    sql=(f"SELECT * FROM prometheusQueryRange(access_ts,'{expr}',"
         f"{m['start']},{m['end']},86400)")
    configs=[
       ("default",sql+" FORMAT JSONEachRow",{}),
       ("outer_sql_zero",sql+" SETTINGS enable_materialized_cte=0 FORMAT JSONEachRow",{}),
       ("http_zero",sql+" FORMAT JSONEachRow",{"enable_materialized_cte":"0"}),
       ("outer_and_http_zero",sql+" SETTINGS enable_materialized_cte=0 FORMAT JSONEachRow",
             {"enable_materialized_cte":"0"}),
       ("http_one",sql+" FORMAT JSONEachRow",{"enable_materialized_cte":"1"}),
    ]
    if a.compiled_v4:
        configs.extend([
          ("v4_http_zero",sql+" FORMAT JSONEachRow",
              {"enable_materialized_cte":"0","promql_enable_native_one_point_lab":"1"}),
          ("v4_outer_http_zero",sql+
              " SETTINGS promql_enable_native_one_point_lab=1, enable_materialized_cte=0 FORMAT JSONEachRow",
              {"enable_materialized_cte":"0"})
        ])
    controls={}
    for setting in ("0","1"):
        probe=attempt("SELECT getSetting('enable_materialized_cte') AS val FORMAT TSV",
                      {"enable_materialized_cte":setting})
        assert probe["success"] and probe["raw"].strip() in ("false","true","0","1"),probe
        controls[setting]=probe["raw"].strip()
    assert controls["0"] in ("false","0") and controls["1"] in ("true","1"),controls
    results=[]
    for name,query,settings in configs:
        observation=attempt(query,settings)
        record={"phase":"compiled_v4" if a.compiled_v4 else "baseline","name":name,
                "sql":query,"http_settings":settings,**observation}
        if observation["success"]:
            record["allowed_points"]=exact_allowed(observation)
        else:
            record["permission_error"]="CREATE TEMPORARY TABLE" in observation["raw"]
        results.append(record)
        print("CTE_CONTEXT_DIAGNOSTIC",json.dumps({
            k:v for k,v in record.items() if k not in ("sql","raw")},separators=(",",":")),
            flush=True)
    assert not results[0]["success"] and results[0]["permission_error"],(
         "negative control changed","default",results[0])
    if any(x["success"] for x in results):
        print("CTE_READONLY_POSSIBLE",json.dumps({
            "successes":[x["name"] for x in results if x["success"]]}),flush=True)
    else:
        assert all(x["status"]==403 and x.get("permission_error")
                   for x in results),("unexpected other errors",results)
        print("CTE_READONLY_ALL_DENIED",json.dumps({
            "tested":len(results),"http_setting_verified":controls}),flush=True)
    pathlib.Path(a.output).write_text(json.dumps({
        "controls":controls,"phases":results},indent=2)+"\n")
if __name__=="__main__":main()
