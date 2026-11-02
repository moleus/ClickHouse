#!/usr/bin/env python3
"""Native v11 TimeSeries metadata-path A/B: same IDs, no ID-size changes.

Three independent interventions: bypass TimeRanges, materialize a TAGS stage
column, and sort TAGS by stage. All use the same canonical 1.33m sample input
and existing optimized O2 C++ binary; no private source or data.
"""
import argparse,hashlib,json,os,pathlib,random,sys,time,statistics
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_layout_full import (CANONICAL,QUERY_SELECTORS,run_phase,physical_probe,
                              actual_sql,_sha_file)
from bench_granularity_v11 import (streamed_insert,stats,inner,wait_no_merges)
from bench_stage_cpp_vs_original_vm_95k import save

VARIANTS={
 "norange": "SETTINGS recent_samples_ttl_seconds=0, store_time_ranges=0",
 "stagecol": "SETTINGS recent_samples_ttl_seconds=0, tags_to_columns={'stage':'stage'}",
 "stagetree": ("SETTINGS recent_samples_ttl_seconds=0, tags_to_columns={'stage':'stage'} "
               "TAGS INNER ENGINE=ReplacingMergeTree PRIMARY KEY (metric_name,stage) "
               "ORDER BY (metric_name,stage,id)"),
 "both": ("SETTINGS recent_samples_ttl_seconds=0, store_time_ranges=0,"
          "tags_to_columns={'stage':'stage'}"),
}
def table_of(variant):return "churn_meta_"+variant
def physical(table,skip_range):
    out={}
    for kind in ("samples","tags","timeranges"):
        if skip_range and kind=="timeranges":
            out[kind]={"absent":True}
            continue
        name=inner(table,kind)
        t=time.monotonic()
        actual_sql("OPTIMIZE TABLE "+name+" FINAL SETTINGS optimize_throw_if_noop=0")
        st=stats(table)[kind]
        out[kind]={"optimize_s":round(time.monotonic()-t,3),"parts":st["parts"],
                   "rows":st["rows"],"marks":st["marks"],"bytes":st["bytes"]}
    return out
def smoke_candidate(table,variant):
    ddl=f"CREATE TABLE {table} ENGINE=TimeSeries {VARIANTS[variant]}"
    actual_sql(ddl)
    # 0-row check: tags and samples must use the old Tuple(UInt64, LC(UUID)) ID;
    # this test does not allow the previous compound-ID memory regression.
    name=inner(table,"tags")
    txt=actual_sql("SHOW CREATE TABLE "+name+" FORMAT TSVRaw")
    if "Tuple(UInt64, LowCardinality(UUID))" not in txt:
        raise RuntimeError("TAGS ID type changed; invalid metadata-only comparison")
    return {"ddl":ddl,"tags_ddl":txt[:9000]}
def digest_case(phase):
    return {k:v for k,v in phase.items() if k not in ("trials","before","after")}
def main():
    p=argparse.ArgumentParser()
    for key in ("manifest","fixture","binary","restart-helper","variant","out"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--fastpath",type=int,choices=(0,1),default=1)
    a=p.parse_args()
    if a.variant not in VARIANTS:raise SystemExit("Invalid variant "+a.variant)
    m=json.loads(pathlib.Path(a.manifest).read_text())
    if m["sha256"]!=CANONICAL or m["series"]!=1330000 or m["days"]!=14:
        raise RuntimeError("Wrong dataset")
    root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    table=table_of(a.variant);skip_range=a.variant in ("norange","both")
    report={"variant":a.variant,"source_sha256":CANONICAL,"binary_sha256":_sha_file(a.binary),
        "id_type":"Tuple(UInt64, LowCardinality(UUID))","fastpath":a.fastpath,
        "note":"All candidate IDs identical to baseline by design; change only metadata/index path",
        "cases":{},"errors":[],"diag":{},"checks":{}}
    save(root/"report.json",report)
    try:
        report["candidate"]=smoke_candidate(table,a.variant)
        t=time.monotonic()
        load=streamed_insert(table,a.fixture)
        if load["rows"]!=1330000:raise RuntimeError("Candidate import incomplete")
        report["ingest"]={"rows":load["rows"],"batches":load["batches"],
                           "duration_s":round(time.monotonic()-t,3)}
        got=int(actual_sql(f"SELECT count() FROM {table} FORMAT TSV").strip())
        if got!=1330000:raise RuntimeError("Missing outer rows "+str(got))
        for name in ("churn_ts",table):
            report["diag"][name]=physical(name,skip_range and name==table)
        wait_no_merges()
        save(root/"report.json",report)
        base=int(m["end"])-13*86400;end=int(m["end"])
        # Selective first, broad last: preserve useful partial data if
        # broad query OOMs. Broad remains mandatory for PASS.
        cases=["stage","project","project_stage","regex","whole"]
        for case in cases:
            ref=None;phases=[]
            for k,side in enumerate(("A","B","B","A")):
                name="churn_ts" if side=="A" else table
                phase=side+str(k)
                ref,details=run_phase(name,case,phase,a.binary,a.restart_helper,ref,m,
                    root/(f"{case}-{phase}.json"),a.fastpath)
                phases.append(details)
                report["cases"][case]={"phases":[digest_case(x) for x in phases],
                                       "parity":True}
                save(root/"report.json",report)
                print("METADATA_AB_PHASE",json.dumps({"variant":a.variant,"case":case,"phase":phase,
                  "wall_ms":details["wall_p50_ms"],"cpu_ms":details["cpu_p50_ms"],
                  "read_rows":details["read_rows_p50"]},separators=(",",":")),flush=True)
            base_ms=statistics.mean(z["wall_p50_ms"] for z in phases if z["phase"].startswith("A"))
            alt_ms=statistics.mean(z["wall_p50_ms"] for z in phases if z["phase"].startswith("B"))
            aa=[z["wall_p50_ms"] for z in phases if z["phase"].startswith("A")]
            drift=abs(aa[0]-aa[1])/base_ms
            if drift>.30:report["errors"].append({"case":case,"error":"30% baseline drift","drift":drift})
            report["cases"][case]["comparison"]={
                 "baseline_ms":round(base_ms,3),"candidate_ms":round(alt_ms,3),
                 "wall_speedup":round(base_ms/alt_ms,4),"drift_fraction":round(drift,4),
                 "baseline_cpu_ms":round(statistics.mean(z["cpu_p50_ms"] for z in phases if z["phase"].startswith("A")),3),
                 "candidate_cpu_ms":round(statistics.mean(z["cpu_p50_ms"] for z in phases if z["phase"].startswith("B")),3)}
            save(root/"report.json",report)
        report["sparse_index"]={}
        for name in ("churn_ts",table):
            report["sparse_index"][name]={}
            for selector in ("stage","project","project_stage","regex"):
                report["sparse_index"][name][selector]=physical_probe(name,selector)
                save(root/"report.json",report)
        report["passed"]=not report["errors"] and len(report["cases"])==len(cases)
        save(root/"report.json",report)
        print("METADATA_AB_DONE",json.dumps({"variant":a.variant,"passed":report["passed"],
          "comparisons":{k:v.get("comparison") for k,v in report["cases"].items()},
          "errors":report["errors"]},separators=(",",":")),flush=True)
        if not report["passed"]:raise SystemExit(1)
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2600]})
        save(root/"report.json",report)
        print("METADATA_AB_FAILURE",json.dumps({"variant":a.variant,"error":str(e)[:500]},
         separators=(",",":")),flush=True)
        raise
if __name__=="__main__":main()
