#!/usr/bin/env python3
"""Replay the same 95k/day JSONEachRow source file into VictoriaMetrics."""
import argparse,datetime,hashlib,json,pathlib,time,urllib.request,urllib.error
METRIC="jenkins_ci_95k_daily"

def quoted(s):
    return str(s).replace("\\","\\\\").replace("\n","\\n").replace('"','\\"')
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    digest=hashlib.sha256()
    requests=0;bytes_written=0;count=0;batch=[];batch_size=0
    def flush():
        nonlocal requests,bytes_written,batch_size
        if not batch:return
        body="".join(batch).encode()
        req=urllib.request.Request("http://127.0.0.1:8430/api/v1/import/prometheus",
                                  data=body,method="POST")
        try:
            with urllib.request.urlopen(req,timeout=180) as r:
                assert r.status in (200,204),r.status
                r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(("VM import rejected",e.code,e.read()[:2000])) from e
        requests+=1;bytes_written+=len(body)
        batch.clear();batch_size=0
    with pathlib.Path(a.fixture).open("rb") as f:
        for raw in f:
            digest.update(raw)
            record=json.loads(raw)
            assert record["metric_name"]==METRIC and len(record["samples"])==1
            tags=record["tags"]
            assert {"stage","build","branch","project"} <= set(tags)
            stamp,value=record["samples"][0]
            dt=datetime.datetime.strptime(stamp,"%Y-%m-%d %H:%M:%S.%f").replace(
                tzinfo=datetime.timezone.utc)
            millis=int(dt.timestamp())*1000+dt.microsecond//1000
            labels=",".join(f'{k}="{quoted(v)}"' for k,v in sorted(tags.items()))
            line=f'{METRIC}{{{labels}}} {repr(float(value))} {millis}\n'
            batch.append(line);batch_size+=len(line);count+=1
            if batch_size>=4_000_000:flush()
        flush()
    assert digest.hexdigest()==meta["sha256"]
    assert count==meta["series"]==1330000,(count,meta)
    output={"metric":METRIC,"series_and_samples":count,
            "source_sha256":digest.hexdigest(),
            "prom_bytes":bytes_written,"requests":requests}
    pathlib.Path(a.output).write_text(json.dumps(output,indent=2)+"\n")
    print("JENKINS_95K_VM_INGEST",json.dumps(output,separators=(",",":")),flush=True)
if __name__=="__main__":main()
