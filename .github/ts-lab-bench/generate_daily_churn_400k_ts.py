#!/usr/bin/env python3
"""Generate 90 days of churned Jenkins-style one-sample TimeSeries via public INSERT."""
import argparse,datetime,hashlib,json,pathlib,time
from bench_bucket_cxx_abca import post

DAYS=40
PER_DAY=10000
METRIC="tslab_daily_churn_scale"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--fixture",required=True)
    a=p.parse_args()
    base=int(datetime.datetime(2026,8,4,tzinfo=datetime.timezone.utc).timestamp())
    end=base+DAYS*86400
    path=pathlib.Path(a.fixture)
    digest=hashlib.sha256()
    with path.open("wb") as out:
        for day in range(DAYS):
            for i in range(PER_DAY):
                sample_time=base+day*86400+3600+(i*1049)%76000
                timestamp=datetime.datetime.fromtimestamp(
                    sample_time,datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
                branch="main" if i%7==0 else f"feature-{i%19}"
                row={"metric_name":METRIC,
                     "tags":{"build":f"day-{day:03d}-build-{i:05d}",
                             "stage":f"stage-{i%38:04d}",
                             "branch":branch,
                             "project":f"project-{i%8:02d}"},
                     "samples":[[timestamp,(i%1900+1)*.1+day*.001]]}
                encoded=(json.dumps(row,separators=(",",":"))+"\n").encode()
                out.write(encoded)
                digest.update(encoded)
    print("CHURN_FIXTURE",json.dumps({
        "days":DAYS,"series_per_day":PER_DAY,"rows":DAYS*PER_DAY,
        "sha256":digest.hexdigest(),"bytes":path.stat().st_size
    }),flush=True)
    post("http://127.0.0.1:18191",
         "CREATE TABLE churn_ts ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
    with path.open("rb") as data:
        while True:
            batch=[data.readline() for _ in range(2000)]
            batch=[x for x in batch if x]
            if not batch:
                break
            sql=("INSERT INTO churn_ts (metric_name,tags,samples) FORMAT JSONEachRow\n"
                 +b"".join(batch).decode())
            post("http://127.0.0.1:18191",sql)
    _,raw=post("http://127.0.0.1:18191",
              "SELECT count() FROM churn_ts WHERE metric_name='tslab_daily_churn' FORMAT TSV")
    count=int(raw.decode().strip())
    assert count==DAYS*PER_DAY,(count,DAYS*PER_DAY)
    manifest={"base":base,"end":end,"days":DAYS,"per_day":PER_DAY,
              "series":count,"sha256":digest.hexdigest(),"fixture_bytes":path.stat().st_size}
    pathlib.Path(a.manifest).write_text(json.dumps(manifest,indent=2)+"\n")
    print("CHURN_INPUT_VERIFIED",json.dumps(manifest,separators=(",",":")),flush=True)
if __name__=="__main__":
    main()
