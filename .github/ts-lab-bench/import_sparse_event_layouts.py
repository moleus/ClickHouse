#!/usr/bin/env python3
"""Replay original TimeSeries one-sample sparse rows into 3 event layouts.

Only the input JSONL fixture is authoritative. Stop on duplicate IDs or bad data.
"""
import argparse,datetime,hashlib,json,pathlib,time
from bench_bucket_cxx_abca import post

TABLES={
  "events_time":"(metric_name,ts,stage,project)",
  "events_stage":"(metric_name,stage,ts,project)",
  "events_project":"(metric_name,project,ts,stage)",
}
DDL=(
 "ts DateTime64(3,'UTC'),"
 "metric_name LowCardinality(String),"
 "stage LowCardinality(String),"
 "project LowCardinality(String),"
 "branch LowCardinality(String),"
 "build String,"
 "value Float64"
)
HOST="http://127.0.0.1:18191"

def sql(query):
    elapsed,raw=post(HOST,query)
    return elapsed,raw.decode("utf-8","replace").strip()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    path=pathlib.Path(a.fixture)
    sha=hashlib.sha256()
    # All three tables use one source stream. Keep each insert batch moderate.
    for table,order in TABLES.items():
        sql(f"CREATE TABLE {table} ({DDL}) ENGINE=MergeTree "
            f"PARTITION BY toYYYYMM(ts) ORDER BY {order} "
            "SETTINGS index_granularity=256")
    batches=[]
    size=0
    inserted=0
    sparse_ids=set()
    def flush():
        nonlocal size,inserted
        if not batches:return
        body="".join(batches)
        for table in TABLES:
            sql(f"INSERT INTO {table} FORMAT JSONEachRow\n"+body)
        inserted+=len(batches)
        batches.clear()
        size=0
    with path.open("rb") as fp:
        for line in fp:
            sha.update(line)
            obj=json.loads(line)
            if obj["metric_name"]!="tslab_sparse_40d":
                continue
            samples=obj["samples"]
            assert len(samples)==1,("not exactly 1 sample per series",obj)
            tags=obj["tags"]
            key=(obj["metric_name"],tuple(sorted(tags.items())))
            assert key not in sparse_ids,("duplicate ID/labels",key)
            sparse_ids.add(key)
            stamp,value=samples[0]
            assert isinstance(value,(float,int)) and 0<=value<1e300
            assert datetime.datetime.strptime(stamp,"%Y-%m-%d %H:%M:%S.%f")
            event={"ts":stamp,"metric_name":obj["metric_name"],
                   "stage":tags["stage"],"project":tags["project"],
                   "branch":tags["branch"],"build":tags["build"],
                   "value":value}
            row=json.dumps(event,separators=(",",":"))+"\n"
            batches.append(row);size+=len(row)
            if size>1_000_000:flush()
    flush()
    assert sha.hexdigest()==manifest["sha256"],"dataset mutated"
    assert inserted==manifest["sparse_series"]==manifest["sparse_samples"]==40000
    results={}
    for table in TABLES:
        _,raw=sql(f"SELECT count(),uniqExact(build),sum(value) FROM {table} FORMAT TSV")
        fields=raw.split("\t")
        assert int(fields[0])==int(fields[1])==inserted,(table,fields)
        _,parts=sql("SELECT sum(bytes_on_disk),sum(marks),count() "
                    "FROM system.parts WHERE active AND database='default' "
                    f"AND table='{table}' FORMAT TSV")
        byte_count,marks,part_count=(int(x) for x in parts.split("\t"))
        results[table]={"rows":inserted,"sum_value":float(fields[2]),
                        "disk_bytes":byte_count,"marks":marks,"parts":part_count}
        print("EVENT_LAYOUT_IMPORT",json.dumps({"table":table,**results[table]},
             separators=(",",":")),flush=True)
    output={"sha256":sha.hexdigest(),"unique_series":len(sparse_ids),
            "one_sample_per_series_verified":True,"tables":results}
    pathlib.Path(a.output).write_text(json.dumps(output,indent=2)+"\n")
    print("EVENT_LAYOUT_IMPORT_COMPLETE",json.dumps({
         "series":len(sparse_ids),"tables":len(TABLES)},separators=(",",":")),flush=True)
if __name__=="__main__":main()
