#!/usr/bin/env python3
"""Full IEEE754 bit-level original/v4-on/v4-off/original differential check."""
import argparse, json, os, pathlib, statistics, struct
from bench_bucket_cxx_abca import pct, post

PHASES=("base_before","v4_on","v4_off","base_after")
EXPMASK=0x7ff0000000000000
MANTMASK=0x000fffffffffffff
SIGNMASK=0x8000000000000000
STALE_BITS=0x7ff0000000000002

def category(bits):
    if (bits & EXPMASK)==EXPMASK:
        if (bits & MANTMASK)!=0: return "nan"
        return "negative_inf" if (bits & SIGNMASK) else "positive_inf"
    if (bits & ~SIGNMASK)==0:
        return "negative_zero" if (bits & SIGNMASK) else "positive_zero"
    return "finite"

def to_value(bits):
    return struct.unpack("!d",struct.pack("!Q",bits))[0]

def unpack(raw):
    records={}
    for line in raw.splitlines():
        if not line.strip():continue
        obj=json.loads(line)
        tags=obj["tags"]
        labels=tuple(sorted((str(a),str(b)) for a,b in
                            (tags.items() if isinstance(tags,dict) else tags)))
        for ts,bits_raw in obj["samples"]:
            bits=int(bits_raw)
            key=json.dumps([labels,int(ts)],separators=(",",":"))
            assert key not in records,("duplicate output",key)
            records[key]={"stage":dict(labels).get("stage"),"ts_ms":int(ts),
                          "bits":bits,"kind":category(bits)}
    return records

def equivalent(left,right):
    if set(left)!=set(right):
        return {"missing":list(set(left)-set(right))[:15],
                "extra":list(set(right)-set(left))[:15]},[]
    differences=[]
    nan_payload_differences=[]
    for key,a in left.items():
        b=right[key]
        if a["kind"]!=b["kind"]:
            differences.append({"key":key,"a":a,"b":b,"reason":"IEEE classification"})
        elif a["kind"]=="nan":
            if a["bits"]!=b["bits"]:
                nan_payload_differences.append({"key":key,"a":a["bits"],"b":b["bits"]})
            if b["bits"]==STALE_BITS:
                differences.append({"key":key,"a":a,"b":b,"reason":"stale marker output"})
        elif a["bits"]!=b["bits"]:
            differences.append({"key":key,"a":a,"b":b,"reason":"non-NaN Float64 bits"})
    return {"differences":differences[:20]},nan_payload_differences[:20]

def assert_known_semantics(items,manifest):
    # Capture actual Float64 results before JSON turns NaN and Inf into null.
    stage={r["stage"]:r for r in items.values() if r["stage"] not in {
        "boundary_left_plus_1ms","boundary_right_exact",
        "boundary_next_plus_1ms","boundary_next_right_exact"}}
    assert "stale_only" not in stage,stage
    assert "boundary_left_exact" not in stage,stage
    want={"quiet_nan_and_finite":"nan","stale_and_finite":"finite",
          "positive_inf":"positive_inf","negative_inf":"negative_inf",
          "both_inf":"nan","duplicate_nan":"finite"}
    for name,expected in want.items():
        assert name in stage,(name,"missing")
        assert stage[name]["kind"]==expected,(name,expected,stage[name])
    for name,expected in (("stale_and_finite",7.0),("duplicate_nan",6.0)):
        assert abs(to_value(stage[name]["bits"])-expected)<=1e-12,(name,stage[name])
    start=manifest["start"]*1000
    bounds={(r["stage"],r["ts_ms"]) for r in items.values()
            if r["stage"].startswith("boundary_")}
    expected_bounds={
        ("boundary_left_plus_1ms",start),
        ("boundary_right_exact",start),
        ("boundary_next_plus_1ms",start+86400000),
        ("boundary_next_right_exact",start+86400000)
    }
    assert bounds==expected_bounds,("wrong daily boundary mapping",bounds^expected_bounds)
    assert all(r["bits"]!=STALE_BITS for r in items.values()),"stale NaN leaked"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    expr="avg by(stage)(avg_over_time(tslab_ieee_edge[1d]))"
    sql=("SELECT tags, arrayMap(x -> tuple("
         "toUnixTimestamp64Milli(tupleElement(x,1)), "
         "reinterpretAsUInt64(tupleElement(x,2))), samples) AS samples "
         "FROM prometheusQueryRange(precision_ts,"
         f"'{expr}',{m['start']},{m['end']},{m['step']})")
    if a.phase=="v4_on":
        sql+=" SETTINGS promql_enable_native_one_point_lab=1"
    sql+=" FORMAT JSONEachRow"
    measurements=[]
    reference=None
    nan_diffs=[]
    first_raw=""
    for i in range(7):
        ms,raw=post("http://127.0.0.1:18191",sql,
                    f"ieeebits_{os.environ.get('GITHUB_RUN_ID','local')}_{a.phase}_{i}")
        try:got=unpack(raw)
        except Exception:
            print("IEEE_BITS_RAW_ERROR",json.dumps({"sql":sql,"raw":raw.decode('utf-8','replace')[:5000]}))
            raise
        assert_known_semantics(got,m)
        if reference is None:
            reference=got
            first_raw=raw.decode("utf-8","replace")
        else:
            bad,nandiff=equivalent(reference,got)
            assert not any(bad.values()),("within-phase",a.phase,i,bad)
            nan_diffs.extend(nandiff)
        if i>=2:measurements.append(ms)
    canonical_path=pathlib.Path(a.canonical)
    if a.phase=="base_before":
        assert not canonical_path.exists()
        canonical_path.write_text(json.dumps(reference,indent=2)+"\n")
    else:
        canonical=json.loads(canonical_path.read_text())
        bad,nandiff=equivalent(canonical,reference)
        nan_diffs.extend(nandiff)
        if any(bad.values()):
            print("IEEE_BITS_MISMATCH",json.dumps({"phase":a.phase,"error":bad,
                  "raw":first_raw,"sql":sql}),flush=True)
            raise AssertionError(("cross-phase IEEE bit mismatch",a.phase,bad))
    summary={}
    for row in reference.values():
        summary[row["kind"]]=summary.get(row["kind"],0)+1
    result={"phase":a.phase,"points":len(reference),"classes":summary,
            "p50_ms":pct(measurements,.5),"trials_ms":measurements,
            "nan_payload_differences":nan_diffs,"sample_bits":reference,
            "raw_output":first_raw,"sql":sql}
    pathlib.Path(a.output).write_text(json.dumps(result,indent=2)+"\n")
    print("IEEE_BITS_PHASE",json.dumps({k:v for k,v in result.items() if k not in
          ("sample_bits","raw_output","sql")},separators=(",",":")),flush=True)
if __name__=="__main__":
    main()
