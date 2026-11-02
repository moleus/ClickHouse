#!/usr/bin/env python3
"""Falsify the v1 scalar grouped AVG rewrite with compensated sum values."""
import argparse,datetime,json,math,os,pathlib
from bench_bucket_cxx_abca import post

def parse(raw):
    result={}
    for line in raw.splitlines():
        if not line.strip(): continue
        row=json.loads(line)
        tags=row["tags"]
        labels=tuple(sorted(tags.items())) if isinstance(tags,dict) else tuple(sorted(tuple(x) for x in tags))
        for ts,val in row["samples"]:
            key=(labels,str(ts))
            assert key not in result,("duplicate result key",key)
            result[key]=float(val)
    return result

def encode(x):
    return [{"tags":list(k[0]),"timestamp":k[1],"value":v} for k,v in sorted(x.items())]

def decode(rows):
    return {(tuple(tuple(pair) for pair in row["tags"]),row["timestamp"]):float(row["value"]) for row in rows}

def deviations(expected,actual):
    missing=set(expected)-set(actual)
    extra=set(actual)-set(expected)
    diff=[]
    for key in sorted(set(expected)&set(actual)):
        x,y=expected[key],actual[key]
        if math.isnan(x) and math.isnan(y):continue
        if not math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-8):
            diff.append({"tags":list(key[0]),"timestamp":key[1],"original":x,
                         "candidate":y,"absolute_error":abs(x-y)})
    return {"missing":[repr(x) for x in sorted(missing)],"extra":[repr(x) for x in sorted(extra)],
            "changed":diff}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--phase",choices=["base_before","selector_scalar","base_after"],required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    start,end=manifest["start"],manifest["end"]
    expr="avg by(stage)(avg_over_time(tslab_numeric_precision[1d]))"
    q=("SELECT * FROM prometheusQueryRange(precision_ts,'%s',%d,%d,86400) FORMAT JSONEachRow"%(expr,start,end))
    rows=None
    elapsed=[]
    for trial in range(5):
        ms,raw=post("http://127.0.0.1:18191",q,
                    "numeric_%s_%s_%d"%(os.environ.get("GITHUB_RUN_ID","local"),args.phase,trial))
        observed=parse(raw)
        assert observed,("no samples",args.phase)
        if rows is None:
            rows=observed
        else:
            assert not any(deviations(rows,observed).values()),("same-phase numeric output unstable",args.phase,trial)
        if trial>=1:elapsed.append(ms)
    canonical=pathlib.Path(args.canonical)
    if args.phase=="base_before":
        assert not canonical.exists()
        canonical.write_text(json.dumps(encode(rows),indent=2)+"\n")
        expected=rows
    else:
        expected=decode(json.loads(canonical.read_text()))
    differences=deviations(expected,rows)
    if args.phase=="base_after":
        assert not any(differences.values()),("baseline did not reproduce",differences)
    # Diagnose the SQL per-series average on exactly these values as well.
    diagnostic=("SELECT tags['stage'] AS stage, "
                "avg(tupleElement(sample,2)) AS sql_avg, "
                "sumKahan(tupleElement(sample,2)) / count() AS kahan_avg, "
                "sum(tupleElement(sample,2)) AS sql_sum, count() AS n "
                "FROM precision_ts ARRAY JOIN samples AS sample "
                "GROUP BY stage ORDER BY stage FORMAT JSONEachRow")
    _,diag_raw=post("http://127.0.0.1:18191",diagnostic)
    diagnostics=[json.loads(x) for x in diag_raw.splitlines() if x.strip()]
    report={"phase":args.phase,"values":encode(rows),"expected":encode(expected),
            "differences":differences,"different_count":len(differences["changed"]),
            "all_exact":not any(differences.values()),"trials_ms":elapsed,
            "sql_diagnostics":diagnostics}
    pathlib.Path(args.output).write_text(json.dumps(report,indent=2)+"\n")
    print("NUMERIC_PROOF",json.dumps(report,separators=(",",":"),allow_nan=True),flush=True)

if __name__=="__main__":
    main()
