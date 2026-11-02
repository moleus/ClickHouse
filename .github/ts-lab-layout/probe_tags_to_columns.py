#!/usr/bin/env python3
"""Native TimeSeries tags_to_columns and alternate TAGS order proof.

Only small synthetic dataset; tests plan and correctness, not benchmark speed.
"""
import argparse,hashlib,json,pathlib,time,sys
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
from layout_id_smoke import (query,insert,data_rows,data_bytes,target_stats,METRIC,START,DAY)
from bench_bucket_cxx_abca import normalize_any,assert_same

VARIANTS={
 "default":"ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0",
 "stage_column":"ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0, tags_to_columns={'stage':'stage'}",
 "stage_sorted":("ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0, tags_to_columns={'stage':'stage'} "
         "TAGS INNER ENGINE = ReplacingMergeTree PRIMARY KEY (metric_name, stage) ORDER BY (metric_name, stage, id)"),
}
def save(path,report):path.write_text(json.dumps(report,indent=2)+"\n")
def target_uuid(table):
    uuid=query("SELECT toString(uuid) FROM system.tables WHERE database='default' "
               f"AND name='{table}' FORMAT TSV").strip()
    assert len(uuid)==36,uuid
    return uuid
def diagnostics(table,col):
    uuid=target_uuid(table);name=".inner_id.tags."+uuid
    d={"tags_target":name,"ddl":query(f"SHOW CREATE TABLE default.\x60{name}\x60 FORMAT TSVRaw")[:10000]}
    filters=[
        ("stage_map",f"metric_name='{METRIC}' AND tags['stage']='stage-01'"),
        ("stage_column",f"metric_name='{METRIC}' AND stage='stage-01'")
    ]
    for k,cond in filters:
        if k=="stage_column" and not col:continue
        q=f"SELECT count() FROM default.\x60{name}\x60 WHERE {cond} SETTINGS optimize_trivial_count_query=0"
        d[k]={"count":int(query(q+" FORMAT TSV").strip()),
              "explain":query("EXPLAIN indexes=1 "+q)[:12000]}
    return d
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args();root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    rows=data_rows();buf=data_bytes(rows)
    result={"fixture_sha256":hashlib.sha256(buf).hexdigest(),"rows":len(rows),
            "cases":{},"errors":[],"notes":[
              "TAGS column is NOT SAMPLES, changes physical TAGS layout only",
              "Any speed benefit needs separate large controlled trial",
              "CHECK: full_sorting_merge JOIN by id might need expensive sort when TAGS order changes"]}
    save(root/"tags-to-columns.json",result)
    select={
      "whole":f"avg by(stage)(avg_over_time({METRIC}[1d]))",
      "stage":f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-01\"}}[1d]))",
      "project":f"avg by(stage)(avg_over_time({METRIC}{{project=\"project-01\"}}[1d]))",
      "regex":f"avg by(stage)(avg_over_time({METRIC}{{job=~\"job-0[1-4]\"}}[1d]))",
    }
    baseline={}
    for variant,ddl in VARIANTS.items():
        table="tsl_tagindex_"+variant
        x={"create_sql":f"CREATE TABLE {table} {ddl}","sql_outputs":{}}
        result["cases"][variant]=x;save(root/"tags-to-columns.json",result)
        try:
            query(x["create_sql"])
            insert(table,data_bytes(rows[:len(rows)//2]))
            insert(table,data_bytes(rows[len(rows)//2:]))
            query(f"OPTIMIZE TABLE {table} FINAL",timeout=110)
            x["tags"]=diagnostics(table,col=variant!="default")
            x["samples"]=target_stats(table)["samples"]
            for name,expr in select.items():
                q=(f"SELECT * FROM prometheusQueryRange({table},'{expr}',"
                   f"{START+DAY},{START+4*DAY},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow")
                t=time.perf_counter()
                d=normalize_any(query(q,timeout=110).encode())
                if variant=="default":baseline[name]=d
                else:assert_same(baseline[name],d,name,variant)
                x["sql_outputs"][name]={"points":len(d),"wall_ms_diagnostic":round(1000*(time.perf_counter()-t),3),
                                         "parity":True}
                print("TAGS_COLUMN_PROBE",json.dumps({"variant":variant,"case":name,
                     "points":len(d),"parity":True},separators=(",",":")),flush=True)
        except Exception as e:
            x["error"]=str(e)[:2200]
            result["errors"].append({"variant":variant,"error":x["error"]})
            # Keep the evidence, then move to the next variant.
        save(root/"tags-to-columns.json",result)
    # Probe that an added SAMPLES column does not magically inherit TAGS stage.
    try:
        table="tsl_tagindex_stage_column"
        uuid=target_uuid(table);name=".inner_id.samples."+uuid
        query(f"ALTER TABLE default.\x60{name}\x60 ADD COLUMN stage String DEFAULT ''")
        stage_summary=query(f"SELECT count() AS rows,uniqExact(stage) AS unique_stages,"
                           f"min(stage) AS first_stage FROM default.\x60{name}\x60 FORMAT TSV").strip()
        result["samples_extra_column_probe"]={
           "alter":"ADD COLUMN stage String DEFAULT ''",
           "observed":stage_summary,
           "point":"Adding a SAMPLES stage column does not fill it from TAGS"}
        print("SAMPLES_STAGE_PROBE",stage_summary,flush=True)
    except Exception as e:
        result["samples_extra_column_probe"]={"error":str(e)[:1700]}
    result["passed"]=(not result["errors"] and len(result["cases"])==3
                      and all(len(x["sql_outputs"])==len(select) for x in result["cases"].values()))
    save(root/"tags-to-columns.json",result)
    print("TAGS_COLUMN_DONE",json.dumps({"passed":result["passed"],"variant_count":len(result["cases"]),
           "errors":result["errors"],"samples_added_column":result.get("samples_extra_column_probe")},
           separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
