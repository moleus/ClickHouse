#!/usr/bin/env python3
"""Ingest exact dense/sparse JSONEachRow fixture into VictoriaMetrics Prometheus format.

No data synthesis here: every VM point comes from a ClickHouse fixture sample.
"""
import argparse,datetime,hashlib,json,pathlib,time,urllib.request,urllib.error

def prom_quote(x):
    s=str(x)
    return s.replace("\\","\\\\").replace("\n","\\n").replace('"','\\"')

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--ch-manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--endpoint",default="http://127.0.0.1:8430/api/v1/import/prometheus")
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.ch_manifest).read_text())
    source=pathlib.Path(a.fixture)
    sha=hashlib.sha256()
    counts={"tslab_sparse_40d":0,"tslab_dense_40d":0}
    series={"tslab_sparse_40d":0,"tslab_dense_40d":0}
    batch=[]
    batch_bytes=0
    requests=0
    total_bytes=0
    elapsed=[]
    def flush():
        nonlocal requests,batch_bytes,total_bytes
        if not batch:return
        body="".join(batch).encode("utf-8")
        req=urllib.request.Request(a.endpoint,data=body,method="POST")
        before=time.perf_counter()
        try:
            with urllib.request.urlopen(req,timeout=180) as resp:
                reply=resp.read().decode("utf-8","replace")
                if resp.status!=204 and resp.status!=200:
                    raise RuntimeError(("unexpected import status",resp.status,reply[:500]))
        except urllib.error.HTTPError as e:
            raise RuntimeError(("VM import error",e.code,e.read().decode("utf-8","replace")[:2500])) from e
        elapsed.append(round((time.perf_counter()-before)*1000,3))
        requests+=1
        total_bytes+=len(body)
        batch.clear();batch_bytes=0
    with source.open("rb") as fp:
        for raw in fp:
            sha.update(raw)
            row=json.loads(raw)
            metric=row["metric_name"]
            assert metric in counts,metric
            tags=row["tags"]
            labels=",".join(f'{k}="{prom_quote(v)}"' for k,v in sorted(tags.items()))
            name=f"{metric}{{{labels}}}"
            series[metric]+=1
            for stamp,value in row["samples"]:
                timestamp=datetime.datetime.strptime(stamp,"%Y-%m-%d %H:%M:%S.%f").replace(
                    tzinfo=datetime.timezone.utc)
                ms=int(timestamp.timestamp())*1000+timestamp.microsecond//1000
                assert isinstance(value,(int,float)) and value==value,(metric,value)
                line=f"{name} {repr(float(value))} {ms}\n"
                batch.append(line)
                batch_bytes+=len(line)
                counts[metric]+=1
                if batch_bytes>=3_000_000:flush()
        flush()
    assert sha.hexdigest()==m["sha256"],("fixture hash changed",sha.hexdigest(),m["sha256"])
    assert counts=={"tslab_sparse_40d":m["sparse_samples"],
                   "tslab_dense_40d":m["dense_samples"]},counts
    assert series=={"tslab_sparse_40d":m["sparse_series"],
                   "tslab_dense_40d":m["dense_series"]},series
    report={"fixture_sha256":sha.hexdigest(),"samples":counts,"series":series,
            "requests":requests,"bytes_uploaded":total_bytes,"post_times_ms":elapsed}
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("PAIRED_VM_INGEST",json.dumps({
         k:v for k,v in report.items() if k!="post_times_ms"},separators=(",",":")),flush=True)
if __name__=="__main__":main()
