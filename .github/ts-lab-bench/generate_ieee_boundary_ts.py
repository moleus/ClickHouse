#!/usr/bin/env python3
"""Make nonfinite and window-boundary TimeSeries fixture using public SQL INSERT."""
import argparse, datetime, json, pathlib
from bench_bucket_cxx_abca import post

METRIC="tslab_ieee_edge"
DAY=86400000
T0=int(datetime.datetime(2026,11,1,tzinfo=datetime.timezone.utc).timestamp()*1000)
MILLI={
    "quiet_nan": 9221120237041090560,       # 0x7ff8000000000000
    "stale_nan": 9218868437227405314,       # 0x7ff0000000000002
    "positive_inf": 9218868437227405312,    # 0x7ff0000000000000
    "negative_inf": 18442240474082181120,  # 0xfff0000000000000
    "negative_zero": 9223372036854775808,  # 0x8000000000000000
}
def number(kind):
    return f"reinterpretAsFloat64(toUInt64({MILLI[kind]}))"

def ins(stage,build,items):
    samples="["+",".join(
       f"tuple(fromUnixTimestamp64Milli({ms}),{value})"
       for ms,value in items)+"]"
    query=(f"INSERT INTO precision_ts (metric_name,tags,samples) "
           f"SELECT '{METRIC}', map('stage','{stage}','build','{build}'), {samples}")
    post("http://127.0.0.1:18191",query)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    args=p.parse_args()
    time=T0
    fixtures=[
       ("quiet_nan_and_finite",[(time-7200000,number("quiet_nan")),(time-3600000,"toFloat64(5)")]),
       ("stale_only",[(time-3600000,number("stale_nan"))]),
       ("stale_and_finite",[(time-7200000,number("stale_nan")),(time-3600000,"toFloat64(7)")]),
       ("positive_inf",[(time-3600000,number("positive_inf")),(time-1800000,"toFloat64(1)")]),
       ("negative_inf",[(time-3600000,number("negative_inf")),(time-1800000,"toFloat64(-1)")]),
       ("both_inf",[(time-3600000,number("positive_inf")),(time-1800000,number("negative_inf"))]),
       ("duplicate_nan",[(time-3600000,"toFloat64(5)"),(time-1200000,"toFloat64(7)")]),
       ("boundary_left_exact",[(time-DAY,"toFloat64(11)")]),
       ("boundary_left_plus_1ms",[(time-DAY+1,"toFloat64(13)")]),
       ("boundary_right_exact",[(time,"toFloat64(17)")]),
       ("boundary_next_plus_1ms",[(time+1,"toFloat64(19)")]),
       ("boundary_next_right_exact",[(time+DAY,"toFloat64(23)")]),
       ("signed_zero",[(time-3600000,number("negative_zero")),(time-1800000,"toFloat64(0)")]),
    ]
    for stage,samples in fixtures:
        ins(stage,"build-"+stage,samples)
    # Second physical part, same ID/timestamp as a finite value, now ordinary NaN.
    ins("duplicate_nan","build-duplicate_nan",[(time-3600000,number("quiet_nan"))])
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,rows=post("http://127.0.0.1:18191",
        f"SELECT count() FROM precision_ts WHERE metric_name='{METRIC}' FORMAT TSV")
    n=int(rows.decode().strip())
    assert n==len(fixtures),(n,len(fixtures))
    manifest={"metric":METRIC,"start":T0//1000,"end":T0//1000+2*86400,
              "step":86400,"unique_stages":n,"input_cases":len(fixtures),
              "payload_bits":MILLI}
    pathlib.Path(args.manifest).write_text(json.dumps(manifest,indent=2)+"\n")
    print("IEEE_EDGE_FIXTURE",json.dumps(manifest,separators=(",",":")),flush=True)
if __name__=="__main__":
    main()
