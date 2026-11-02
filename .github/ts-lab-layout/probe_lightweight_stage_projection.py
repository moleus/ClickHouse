#!/usr/bin/env python3
"""Prove _part_offset stage projection on native TimeSeries TAGS ReplacingMergeTree.

Keeps the normal TAGS ORDER BY(metric_name,id) and original fixed-size ID.
A projection can accelerate the TAGS filter without reordering TimeRanges JOIN.
This does not index SAMPLES unless tag data is physically available there.
"""
import argparse,hashlib,json,pathlib,time,sys
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
from layout_id_smoke import (query,insert,data_rows,data_bytes,METRIC,START,DAY)
from bench_bucket_cxx_abca import normalize_any,assert_same

def save(path,x):path.write_text(json.dumps(x,indent=2)+"\n")
def target(table,kind):
    u=query(f"SELECT toString(uuid) FROM system.tables WHERE database='default' AND name='{table}' FORMAT TSV").strip()
    if len(u)!=36:raise RuntimeError(f"Missing outer uuid {table} {u!r}")
    return f"default.\x60.inner_id.{kind}.{u}\x60"
def query_profile(tags,has_projection):
    predicate=f"metric_name='{METRIC}' AND stage='stage-01'"
    base=f"SELECT count() FROM {tags} WHERE {predicate} SETTINGS optimize_trivial_count_query=0"
    results={}
    for enabled in (0,1):
        sql=base+f",optimize_use_projections={enabled}"
        explain=query("EXPLAIN indexes=1 "+sql,timeout=110)
        t=time.perf_counter()
        n=int(query(sql+" FORMAT TSV",timeout=110).strip())
        results[str(enabled)]={"count":n,"plan":explain[:22000],
                               "elapsed_ms_diagnostic":round(1000*(time.perf_counter()-t),3)}
        if n!=640:raise RuntimeError(f"wrong stage filter count: {n} (expected 640)")
    return results
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args();root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    rows=data_rows();raw=data_bytes(rows)
    result={"schema":1,"source_sha256":hashlib.sha256(raw).hexdigest(),
       "source_rows":len(rows),"cases":{},"errors":[],
       "limit":"3200-row smoke: correctness and plan only, no large-query speed claim"}
    save(root/"lightweight-stage-index.json",result)
    baseline={}
    for variant in ("without_projection","light_projection"):
        table="tsl_stage_projection_"+variant
        item={"version":query("SELECT version() FORMAT TSV").strip()}
        result["cases"][variant]=item
        try:
            create=(f"CREATE TABLE {table} ENGINE=TimeSeries "
                    "SETTINGS recent_samples_ttl_seconds=0,tags_to_columns={'stage':'stage'}")
            query(create)
            tags=target(table,"tags");item["tags"]=tags
            if variant=="light_projection":
                # ALTER allows one secondary sort order without rewriting original TAGS.
                item["projection_ddl"]=f"ALTER TABLE {tags} ADD PROJECTION p_stage_label (SELECT _part_offset ORDER BY (metric_name,stage))"
                query(item["projection_ddl"])
            insert(table,data_bytes(rows[:len(rows)//2]))
            insert(table,data_bytes(rows[len(rows)//2:]))
            query(f"OPTIMIZE TABLE {table} FINAL",timeout=120)
            if variant=="light_projection":
                query(f"ALTER TABLE {tags} MATERIALIZE PROJECTION p_stage_label SETTINGS mutations_sync=2",timeout=120)
            item["tags_ddl"]=query("SHOW CREATE TABLE "+tags+" FORMAT TSVRaw")
            item["index_plan"]=query_profile(tags,variant=="light_projection")
            promql=f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-01\"}}[1d]))"
            sql=(f"SELECT * FROM prometheusQueryRange({table},'{promql}',"
                 f"{START+DAY},{START+4*DAY},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow")
            data=normalize_any(query(sql,timeout=110).encode())
            if variant=="without_projection":baseline=data
            else:assert_same(baseline,data,"stage","light_projection")
            item["promql_points"]=len(data);item["native_parity"]=True
            print("STAGE_PROJECTION_CASE",json.dumps({"variant":variant,"promql_points":len(data),
                  "native_parity":True,"stage_filter_rows":640},separators=(",",":")),flush=True)
        except Exception as e:
            item["error"]=str(e)[:2600]
            result["errors"].append({"variant":variant,"error":item["error"]})
            print("STAGE_PROJECTION_UNSUPPORTED",json.dumps(result["errors"][-1],separators=(",",":")),flush=True)
        save(root/"lightweight-stage-index.json",result)
    result["passed"]=len(result["cases"])==2 and not result["errors"]
    save(root/"lightweight-stage-index.json",result)
    print("STAGE_PROJECTION_DONE",json.dumps({"passed":result["passed"],"errors":result["errors"]},
          separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
