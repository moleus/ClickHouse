#!/usr/bin/env python3
"""Prepare isolated TimeSeries TAGS row policy and check direct non-admin scope."""
import datetime,json,pathlib,urllib.parse,urllib.request,urllib.error
from bench_bucket_cxx_abca import post

METRIC="tslab_acl_metric"
USER="tslab_reader"

def reader_post(sql):
    args=urllib.parse.urlencode({"user":USER,"allow_experimental_time_series_table":1,
                                 "enable_time_series_aggregate_functions":1,
                                 "max_threads":4})
    url="http://127.0.0.1:18191/?"+args
    request=urllib.request.Request(url,data=sql.encode("utf-8"),method="POST")
    try:
        with urllib.request.urlopen(request,timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body=exc.read().decode("utf-8","replace")
        print("ACL_READER_HTTP_ERROR",json.dumps({"status":exc.code,"query":sql,
              "body":body[:10000]}),flush=True)
        raise

def main():
    start=int(datetime.datetime(2026,11,2,tzinfo=datetime.timezone.utc).timestamp())
    t=datetime.datetime.fromtimestamp(start-12*3600,datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
    rows=[]
    for project in ("allowed","denied"):
        rows.append({"metric_name":METRIC,"tags":{"project":project,
              "stage":"stage-"+project,"build":"build-"+project},
              "samples":[[t,4.0 if project=="allowed" else 9.0]]})
    post("http://127.0.0.1:18191",
         "INSERT INTO access_ts (metric_name,tags,samples) FORMAT JSONEachRow\n"
         +"\n".join(json.dumps(x,separators=(",",":")) for x in rows)+"\n")
    _,raw=post("http://127.0.0.1:18191",
          "SELECT name FROM system.tables WHERE database='default' "
          "AND startsWith(name,'.inner_id.tags.') FORMAT TSVRaw")
    names=[x.decode().strip() for x in raw.splitlines() if x.strip()]
    if len(names)!=1:
        raise AssertionError(("expected one internal TAGS target",names))
    tags_target=names[0]
    tick=chr(96)
    fq="default."+tick+tags_target.replace(tick,tick+tick)+tick
    _,raw=post("http://127.0.0.1:18191",
        f"SELECT tags['project'] AS project, tags['stage'] AS stage FROM {fq} "
        f"WHERE metric_name='{METRIC}' ORDER BY stage FORMAT JSONEachRow")
    admin_tags=[json.loads(x) for x in raw.splitlines() if x.strip()]
    assert {x["project"] for x in admin_tags}=={"allowed","denied"},admin_tags
    post("http://127.0.0.1:18191",
         f"CREATE USER {USER} IDENTIFIED WITH no_password HOST IP '127.0.0.1'")
    post("http://127.0.0.1:18191",f"GRANT SELECT ON default.* TO {USER}")
    # PrometheusQueryRange uses temporary tables for internal state; explicit read-only user needs this grant.
    post("http://127.0.0.1:18191",f"GRANT CREATE TEMPORARY TABLE ON *.* TO {USER}")
    row_policy=(f"CREATE ROW POLICY lab_allowed_only ON {fq} FOR SELECT "
                f"USING tags['project']='allowed' TO {USER}")
    post("http://127.0.0.1:18191",row_policy)
    body=reader_post(
        f"SELECT tags['project'] AS project, tags['stage'] AS stage FROM {fq} "
        f"WHERE metric_name='{METRIC}' ORDER BY stage FORMAT JSONEachRow")
    visible=[json.loads(x) for x in body.splitlines() if x.strip()]
    if {x["project"] for x in visible}!={"allowed"}:
        raise AssertionError(("ROW POLICY PRECONDITION FAILED",visible,admin_tags))
    manifest={"start":start,"end":start+86400,"step":86400,
              "reader":USER,"metric":METRIC,"tags_target":tags_target,
              "admin_tags":admin_tags,"reader_tags":visible,
              "row_policy_sql":row_policy}
    pathlib.Path("evidence/acl-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("ACL_ROW_POLICY_PRECONDITION",json.dumps(manifest,separators=(",",":")),flush=True)

if __name__=="__main__":main()
