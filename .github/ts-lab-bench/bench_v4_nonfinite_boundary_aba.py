#!/usr/bin/env python3
"""Original / v4-on / v4-off / original IEEE payload and 1ms boundary differential test."""
import argparse, json, math, os, pathlib, statistics
from bench_bucket_cxx_abca import post, pct
from bench_jenkins_querypath import epoch

PHASES=("base_before","v4_on","v4_off","base_after")
METRIC="tslab_ieee_edge"

def classify(value):
    if value is None:
        return {"kind":"null","repr":None}
    number=float(value)
    if math.isnan(number):
        return {"kind":"nan","repr":str(value)}
    if math.isinf(number):
        return {"kind":"positive_inf" if number>0 else "negative_inf","repr":str(value)}
    if number==0:
        return {"kind":"zero","sign":int(math.copysign(1.0,number)),"repr":str(value)}
    return {"kind":"finite","number":number,"repr":str(value)}

def decode(raw):
    results={}
    for line in raw.splitlines():
        if not line.strip():continue
        row=json.loads(line)
        tags=row["tags"]
        tags=tuple(sorted((str(k),str(v)) for k,v in
                          (tags.items() if isinstance(tags,dict) else tags)))
        for ts,value in row["samples"]:
            key=json.dumps([tags,epoch(ts)],separators=(",",":"))
            if key in results:raise AssertionError(("duplicate output key",key))
            results[key]={"stage":dict(tags).get("stage",""),"ts":epoch(ts),
                          "class":classify(value),"tags":tags}
    return results

def difference(left,right):
    if set(left)!=set(right):
        return {"keys_left_only":list(set(left)-set(right))[:8],
                "keys_right_only":list(set(right)-set(left))[:8]}
    bad=[]
    for key,a in left.items():
        b=right[key]
        x,y=a["class"],b["class"]
        if x["kind"]!=y["kind"]:
            bad.append({"key":key,"reason":"classification","left":x,"right":y})
        elif x["kind"]=="zero" and x["sign"]!=y["sign"]:
            bad.append({"key":key,"reason":"zero_sign","left":x,"right":y})
        elif x["kind"]=="finite" and not math.isclose(x["number"],y["number"],
                rel_tol=1e-10,abs_tol=1e-8):
            bad.append({"key":key,"reason":"finite_value","left":x,"right":y})
    return {"differences":bad[:20]}

def assert_time_windows(items,start):
    actual={(v["stage"],v["ts"]) for v in items.values()}
    expected={
        ("boundary_left_plus_1ms",start),
        ("boundary_right_exact",start),
        ("boundary_next_plus_1ms",start+86400),
        ("boundary_next_right_exact",start+86400),
    }
    missing=expected-actual
    extra={(s,t) for s,t in actual if s.startswith("boundary_")}-expected
    assert not missing and not extra,("wrong boundary semantics",sorted(missing),sorted(extra))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--phase",required=True,choices=PHASES)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--canonical",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    expr=f"avg by(stage)(avg_over_time({METRIC}[1d]))"
    sql=(f"SELECT * FROM prometheusQueryRange(precision_ts,'{expr}',"
         f"{m['start']},{m['end']},{m['step']})")
    if args.phase=="v4_on":
        sql+=" SETTINGS promql_enable_native_one_point_lab=1"
    sql+=" FORMAT JSONEachRow"
    first=None
    timings=[]
    raw_first=""
    for i in range(7):
        query_id=f"ieee_{os.environ.get('GITHUB_RUN_ID','local')}_{args.phase}_{i}"
        ms,raw=post("http://127.0.0.1:18191",sql,query_id)
        try:observed=decode(raw)
        except Exception as err:
            print("IEEE_PARSE_ERROR",json.dumps({"phase":args.phase,"trial":i,
                  "raw":raw.decode('utf-8','replace')[:18000]}),flush=True)
            raise err
        assert_time_windows(observed,m["start"])
        if first is None:
            first=observed
            raw_first=raw.decode("utf-8","replace")
        else:
            diff=difference(first,observed)
            if any(diff.values()):
                raise AssertionError(("within-phase mismatch",args.phase,i,diff))
        if i>=2:timings.append(ms)
    path=pathlib.Path(args.canonical)
    if args.phase=="base_before":
        assert not path.exists()
        path.write_text(json.dumps(first,indent=2,allow_nan=False)+"\n")
    else:
        prev=json.loads(path.read_text())
        diff=difference(prev,first)
        if any(diff.values()):
            print("IEEE_DIFFERENTIAL_FAILURE",json.dumps({
                "phase":args.phase,"diff":diff,"raw":raw_first,
                "query":sql},separators=(",",":")),flush=True)
            raise AssertionError(("cross-phase mismatch",args.phase,diff))
    classes={}
    for item in first.values():
        classes[item["class"]["kind"]]=classes.get(item["class"]["kind"],0)+1
    result={"phase":args.phase,"samples":len(first),"classes":classes,
            "p50_ms":pct(timings,.5),"p95_ms":pct(timings,.95),
            "trials_ms":timings,"sql":sql,"raw_output":raw_first,
            "all_same_as_original":True}
    pathlib.Path(args.output).write_text(json.dumps(result,indent=2)+"\n")
    print("IEEE_BOUNDARY_PHASE",json.dumps({k:v for k,v in result.items()
         if k not in ("raw_output","sql")},separators=(",",":")),flush=True)

if __name__=="__main__":main()
