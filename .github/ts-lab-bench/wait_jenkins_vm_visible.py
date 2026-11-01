#!/usr/bin/env python3
"""Wait for read-after-write visibility of all single-sample Jenkins days in VM.

Do not confuse HTTP 204 from /api/v1/import/prometheus with query visibility.
The invariant is count(avg_over_time(metric[1d])) == exact source sample count
at every evaluation midnight. CH source counts are validated separately.
"""
import argparse
import json
import pathlib
import time
import urllib.parse
import urllib.request


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--url",default="http://127.0.0.1:8430")
    p.add_argument("--attempts",type=int,default=90)
    p.add_argument("--sleep",type=float,default=2.)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end=m["query_start_epoch"],m["query_end_epoch"]
    expected=m["builds_per_day"]*m["stages_per_build"]
    expr="count(avg_over_time("+m["metric"]+"[1d]))"
    params={"query":expr,"start":start,"end":end,"step":86400,"nocache":1}
    link=a.url.rstrip("/")+"/api/v1/query_range?"+urllib.parse.urlencode(params)
    history=[]
    done=False
    for i in range(a.attempts):
        t0=time.perf_counter()
        with urllib.request.urlopen(link,timeout=180) as response:
            payload=json.loads(response.read())
        if payload.get("status")!="success":
            raise RuntimeError(("VM count query failed",payload))
        data=payload["data"]["result"]
        seen={int(float(t)):int(round(float(v))) for series in data for t,v in series.get("values",[])}
        missing=[]
        for day in range(m["days"]):
            stamp=start+day*86400
            if seen.get(stamp)!=expected:
                missing.append({"day":day,"epoch":stamp,"actual":seen.get(stamp),"expected":expected})
        elapsed=round((time.perf_counter()-t0)*1000,3)
        row={"attempt":i+1,"missing_days":len(missing),"sample_missing":missing[:8],"wall_ms":elapsed,
             "series_results":len(data)}
        history.append(row)
        print("JENKINS_VM_VISIBILITY",json.dumps(row,sort_keys=True),flush=True)
        if not missing:
            done=True
            break
        time.sleep(a.sleep)
    result={"query":expr,"complete":done,"days":m["days"],"per_day":expected,
            "total_input_samples":m["total_samples"],"attempts":history}
    path=pathlib.Path(a.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n")
    if not done:raise AssertionError(("VictoriaMetrics read-after-write incomplete",history[-1]))
    print("JENKINS_VM_READY",json.dumps({"attempts":len(history),"days":m["days"],
          "total_samples":m["total_samples"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
