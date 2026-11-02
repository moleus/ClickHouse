#!/usr/bin/env python3
"""Locate missing CREATE TEMPORARY TABLE privilege in TimeSeries read path."""
import json,pathlib,urllib.parse,urllib.request,urllib.error
from setup_v4_cte_readonly import reader_post
from bench_bucket_cxx_abca import post

def execute(sql):
    try:
        raw=reader_post(sql)
        return {"success":True,"data":raw.decode("utf-8","replace")}
    except urllib.error.HTTPError as e:
        # reader_post already logged the request body; make this expected diagnostic.
        return {"success":False,"http":e.code,"message":str(e)}

def main():
    m=json.loads(pathlib.Path("evidence/acl-manifest.json").read_text())
    ticks=chr(96)
    tags="default."+ticks+m["tags_target"].replace(ticks,ticks+ticks)+ticks
    # Inner samples target belongs to the same isolated TimeSeries fixture.
    sys_query=("SELECT name FROM system.tables WHERE database='default' "
               "AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw")
    _,sample_raw=post("http://127.0.0.1:18191",sys_query)
    names=[x.decode("utf-8","replace").strip() for x in sample_raw.splitlines() if x.strip()]
    assert len(names)==1,("expected exactly one samples target",names)
    samples="default."+ticks+names[0].replace(ticks,ticks+ticks)+ticks
    s_start=(m["start"]-86400)*1000+1
    s_end=m["end"]*1000
    selector=("timeSeriesSelector('default','access_ts','tslab_acl_metric',"
              f"fromUnixTimestamp64Milli({s_start}),"
              f"fromUnixTimestamp64Milli({s_end}),")
    expression="avg by(stage)(avg_over_time(tslab_acl_metric[1d]))"
    probes={
      "direct_tags":f"SELECT tags['project'] AS project FROM {tags} "
         "WHERE metric_name='tslab_acl_metric' ORDER BY project FORMAT JSONEachRow",
      "direct_samples":f"SELECT count() AS n FROM {samples} FORMAT TSV",
      "selector_samples":f"SELECT count() AS n FROM {selector}'samples') FORMAT TSV",
      "selector_timestamp_value":f"SELECT count() AS n FROM {selector}'timestamp_and_value') FORMAT TSV",
      "selector_groups":f"SELECT count() FROM (SELECT "
          "timeSeriesIdToGroup(id) AS g "
          f"FROM {selector}'samples')) FORMAT TSV",
      "selector_group_tags":f"SELECT count() FROM (SELECT "
          "timeSeriesGroupToTags(timeSeriesIdToGroup(id)) AS tags "
          f"FROM {selector}'samples')) FORMAT TSV",
      "original_promql":f"SELECT * FROM prometheusQueryRange(access_ts,"
          f"'{expression}',{m['start']},{m['end']},86400) FORMAT JSONEachRow"
    }
    results={}
    for name,sql in probes.items():
        response=execute(sql)
        results[name]={"sql":sql,**response}
        print("TEMP_GRANT_ISOLATION",json.dumps({
          "case":name,"success":response["success"],
          "data":response.get("data","")[:500],
          "http":response.get("http")},separators=(",",":")),flush=True)
    assert results["direct_tags"]["success"],"precondition: direct TAGS SELECT"
    allowed=[json.loads(x)["project"] for x in results["direct_tags"]["data"].splitlines() if x.strip()]
    assert allowed==["allowed"],("TAGS policy did not filter",allowed)
    assert not results["original_promql"]["success"],"negative control unexpectedly succeeded"
    pathlib.Path("evidence/temp-grant-isolation.json").write_text(
        json.dumps(results,indent=2)+"\n")
    print("TEMP_GRANT_ISOLATION_COMPLETE",json.dumps({
      "cases":len(results),"succeeded":[k for k,v in results.items() if v["success"]],
      "denied":[k for k,v in results.items() if not v["success"]]
    },separators=(",",":")),flush=True)
if __name__=="__main__":main()
