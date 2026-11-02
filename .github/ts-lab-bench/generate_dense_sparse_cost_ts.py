#!/usr/bin/env python3
"""Generate 40-day long-lived dense vs daily-new sparse TimeSeries cost fixture."""
import argparse,datetime,hashlib,json,pathlib,os
from bench_bucket_cxx_abca import post
DAY=86400
BASE=int(datetime.datetime(2026,9,1,tzinfo=datetime.timezone.utc).timestamp())
DAYS=40
PER_DAY=1000
DENSE="tslab_dense_40d"
SPARSE="tslab_sparse_40d"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--input",required=True)
    a=p.parse_args()
    source=pathlib.Path(a.input)
    sha=hashlib.sha256()
    dense_timestamps=[
        datetime.datetime.fromtimestamp(BASE+d*DAY+h*3600+7,datetime.timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S.000")
        for d in range(DAYS) for h in range(24)]
    sparse_timestamps=[
        datetime.datetime.fromtimestamp(BASE+d*DAY+3600+(i*59)%80000,datetime.timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S.000")
        for d in range(DAYS) for i in range(PER_DAY)]
    counts={"dense_series":0,"sparse_series":0,"dense_samples":0,"sparse_samples":0}
    # Materialize a fixture with SHA256; we will re-read exactly these bytes for INSERT.
    with source.open("wb") as output:
        for sid in range(PER_DAY):
            tags={"stage":f"stage-{sid%38:04d}",
                  "build":f"dense-series-{sid:06d}",
                  "branch":"main" if sid%7==0 else f"feature-{sid%19}",
                  "project":f"project-{sid%8:02d}"}
            samples=[[stamp,10.0+(sid%27)*.1+(i//24)*.01+(i%24)*.001]
                     for i,stamp in enumerate(dense_timestamps)]
            line=(json.dumps({"metric_name":DENSE,"tags":tags,"samples":samples},
                             separators=(",",":"))+"\n").encode()
            output.write(line);sha.update(line)
            counts["dense_series"]+=1;counts["dense_samples"]+=len(samples)
        for d in range(DAYS):
            for i in range(PER_DAY):
                idx=d*PER_DAY+i
                tags={"stage":f"stage-{i%38:04d}",
                      "build":f"day-{d:03d}-build-{i:05d}",
                      "branch":"main" if i%7==0 else f"feature-{i%19}",
                      "project":f"project-{i%8:02d}"}
                sample=[[sparse_timestamps[idx],10.0+(i%27)*.1+d*.01]]
                line=(json.dumps({"metric_name":SPARSE,"tags":tags,"samples":sample},
                                 separators=(",",":"))+"\n").encode()
                output.write(line);sha.update(line)
                counts["sparse_series"]+=1;counts["sparse_samples"]+=1
    assert counts=={"dense_series":1000,"sparse_series":40000,
                    "dense_samples":960000,"sparse_samples":40000},counts
    print("DENSE_SPARSE_FIXTURE",json.dumps({**counts,"bytes":source.stat().st_size,
          "sha256":sha.hexdigest()}),flush=True)
    post("http://127.0.0.1:18191",
         "CREATE TABLE cost_ts ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
    batch=[]
    inserted=0
    with source.open("rb") as f:
        for line in f:
            batch.append(line)
            # Dense rows are ~35kb each; 25 per INSERT. Sparse rows are small.
            if len(batch)>=25 if inserted<1000 else len(batch)>=1000:
                sql=("INSERT INTO cost_ts (metric_name,tags,samples) FORMAT JSONEachRow\n"
                     +b"".join(batch).decode())
                post("http://127.0.0.1:18191",sql)
                inserted+=len(batch)
                batch=[]
        if batch:
            sql=("INSERT INTO cost_ts (metric_name,tags,samples) FORMAT JSONEachRow\n"
                 +b"".join(batch).decode())
            post("http://127.0.0.1:18191",sql)
            inserted+=len(batch)
    assert inserted==41000,inserted
    _,raw=post("http://127.0.0.1:18191",
         "SELECT metric_name,count() AS n FROM cost_ts "
         "WHERE metric_name IN ('tslab_dense_40d','tslab_sparse_40d') "
         "GROUP BY metric_name ORDER BY metric_name FORMAT JSONEachRow")
    actual={r["metric_name"]:int(r["n"]) for line in raw.splitlines()
            if line.strip() for r in [json.loads(line)]}
    assert actual=={DENSE:1000,SPARSE:40000},actual
    manifest={"start":BASE+DAY,"end":BASE+DAYS*DAY,
              "days":DAYS,"series_per_day":PER_DAY,**counts,
              "fixture_bytes":source.stat().st_size,"sha256":sha.hexdigest(),
              "outer_series_count":actual}
    pathlib.Path(a.manifest).write_text(json.dumps(manifest,indent=2)+"\n")
    print("DENSE_SPARSE_IMPORTED",json.dumps(manifest,separators=(",",":")),flush=True)
if __name__=="__main__":main()
