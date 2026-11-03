#!/usr/bin/env python3
"""R03: import the exact R01 multi-cadence TimeSeries JSON fixture into VM1.134.

Converts all original sample pairs, including the 15s/60s/300s gauges, to
VM's standard Prometheus line import. No synthetic values or timestamps are
reconstructed. SHA256 and exact record/point totals guard against truncation.
"""
import argparse
import datetime
import hashlib
import json
import math
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

FIXTURE="ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
N_SERIES=28896
N_POINTS=2488320
URL="http://127.0.0.1:8430"


def quote(value):
    return str(value).replace("\\","\\\\").replace("\n","\\n").replace('"','\\"')


def submit(endpoint,buffer):
    req=urllib.request.Request(
        endpoint+"/api/v1/import/prometheus",data="".join(buffer).encode(),
        method="POST"
    )
    try:
        with urllib.request.urlopen(req,timeout=240) as response:
            if response.status not in (200,204):
                raise RuntimeError(("VictoriaMetrics unexpected HTTP response",response.status))
            response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(("VictoriaMetrics import failed",e.code,e.read()[:1600])) from e


def vm_visible(endpoint,end):
    query="sum(count_over_time(tslab_node_cpu_ratio[1d]))"
    url=endpoint+"/api/v1/query?"+urllib.parse.urlencode({
        "query":query,"time":end,"nocache":1
    })
    for attempt in range(60):
        try:
            with urllib.request.urlopen(url,timeout=30) as response:
                result=json.loads(response.read())
            if result.get("status")=="success":
                samples=result["data"]["result"]
                value=float(samples[0]["value"][1]) if samples else 0
                if value==576000:
                    return {"query":query,"last_day_node_sample_count":value,
                            "attempts":attempt+1}
                print("R03_VM_VISIBILITY",attempt,value,flush=True)
        except Exception as ex:
            print("R03_VM_VISIBILITY_ERROR",attempt,str(ex)[:150],flush=True)
        time.sleep(2)
    raise AssertionError("VictoriaMetrics failed last-day expected 576000 node sample visibility")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--endpoint",default=URL)
    opts=p.parse_args()
    manifest=json.loads(pathlib.Path(opts.manifest).read_text())
    if (manifest.get("sha256")!=FIXTURE or manifest.get("series")!=N_SERIES
       or manifest.get("samples")!=N_POINTS or manifest.get("days")!=3
       or manifest.get("node_slots")!=400):
        raise AssertionError("Wrong R01 frozen 400-slot 3d fixture manifest")
    sha=hashlib.sha256()
    output={"source_sha256":FIXTURE,"series":0,"samples":0,
            "prometheus_import_requests":0,"prometheus_wire_bytes":0,
            "failures":[]}
    batch=[]
    bytes_now=0
    try:
        with open(opts.fixture,"rb") as data:
            for raw in data:
                sha.update(raw)
                rec=json.loads(raw)
                if rec["metric_name"] not in ("tslab_node_cpu_ratio","tslab_pod_cpu_ratio"):
                    raise AssertionError("Unrecognized source metric")
                tags=rec["tags"]
                if set(tags).intersection(("__name__",)):
                    raise AssertionError("Reserved VM name label in source")
                metric=rec["metric_name"]+"{"+",".join(
                    f'{k}="{quote(v)}"' for k,v in sorted(tags.items())
                )+"}"
                samples=rec["samples"]
                if not samples:
                    raise AssertionError("Empty series")
                for timestamp,value in samples:
                    dt=datetime.datetime.fromisoformat(timestamp).replace(
                        tzinfo=datetime.timezone.utc
                    )
                    ms=int(dt.timestamp()*1000)
                    x=float(value)
                    if not math.isfinite(x):
                        raise AssertionError("Nonfinite fixture sample")
                    line=f"{metric} {repr(x)} {ms}\n"
                    batch.append(line)
                    bytes_now+=len(line)
                    output["samples"]+=1
                    if bytes_now>=4_000_000:
                        submit(opts.endpoint,batch)
                        output["prometheus_import_requests"]+=1
                        output["prometheus_wire_bytes"]+=bytes_now
                        batch.clear()
                        bytes_now=0
                output["series"]+=1
        if batch:
            submit(opts.endpoint,batch)
            output["prometheus_import_requests"]+=1
            output["prometheus_wire_bytes"]+=bytes_now
        if sha.hexdigest()!=FIXTURE:
            raise AssertionError(("Fixture file SHA mismatch",sha.hexdigest()))
        if output["series"]!=N_SERIES or output["samples"]!=N_POINTS:
            raise AssertionError(("Incomplete imported data",output))
        output["vm_visibility"]=vm_visible(opts.endpoint,int(manifest["end"]))
        output["passed"]=True
    except Exception as ex:
        output["failures"].append(str(ex)[:1600])
        output["passed"]=False
    finally:
        dest=pathlib.Path(opts.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(output,indent=2)+"\n")
        print("R03_VM_IMPORT",json.dumps(output,separators=(",",":")),flush=True)
    if not output["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
