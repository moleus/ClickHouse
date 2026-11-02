#!/usr/bin/env python3
"""Test streaming avgKBNLab C++ aggregate under single and merged partial states."""
import argparse
import json
import math
import pathlib
import statistics
from bench_native_one_point_sql import post
from bench_group_avg_order_distribution import input_groups

def execute(sql):
    return post(sql)

def values(sql):
    elapsed, raw = execute(sql)
    observed = {}
    for line in raw.splitlines():
        if line.strip():
            row = json.loads(line)
            observed[row["stage"]] = float(row["mean"])
    return elapsed, observed

def build_fixture():
    execute("CREATE TABLE group_kbn_lab (stage String, seq UInt32, value Float64) "
            "ENGINE=MergeTree ORDER BY (stage,seq) SETTINGS index_granularity=16")
    rows = []
    for stage, data in input_groups().items():
        for seq, value in enumerate(data):
            rows.append({"stage":stage,"seq":seq,"value":value})
    # Several source parts exercise parallel group aggregation and state merges.
    for base in range(0,len(rows),45):
        data = "\n".join(json.dumps(x,separators=(",",":")) for x in rows[base:base+45])
        execute("INSERT INTO group_kbn_lab FORMAT JSONEachRow\n"+data+"\n")
    _, raw = execute("SELECT count() FORMAT TSV")
    assert raw.strip() == b"1"
    _, raw = execute("SELECT count() FROM group_kbn_lab FORMAT TSV")
    assert int(raw.strip()) == len(rows)

def compare(oracle, actual):
    if set(oracle) != set(actual):
        raise AssertionError(("different group labels",set(oracle)^set(actual)))
    return {stage: abs(actual[stage]-oracle[stage]) for stage in oracle}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    ap.add_argument("--repeats",type=int,default=15)
    args=ap.parse_args()
    build_fixture()
    oracle={stage:math.fsum(vals)/len(vals) for stage,vals in input_groups().items()}
    summaries=[]
    kinds=[
        ("sql_avg","avg(value)"),
        ("kbn_stream","avgKBNLab(value)"),
        ("kbn_merged","avgKBNLabMerge(state)"),
    ]
    for threads in (1,4,8):
        for kind,fn in kinds:
            if kind=="kbn_merged":
                sql=("SELECT stage,avgKBNLabMerge(state) AS mean "
                     "FROM (SELECT stage,seq%8 AS part,avgKBNLabState(value) AS state "
                     "FROM group_kbn_lab GROUP BY stage,part) "
                     "GROUP BY stage ORDER BY stage "
                     f"SETTINGS max_threads={threads} FORMAT JSONEachRow")
            else:
                sql=(f"SELECT stage,{fn} AS mean FROM group_kbn_lab "
                     "GROUP BY stage ORDER BY stage "
                     f"SETTINGS max_threads={threads} FORMAT JSONEachRow")
            times=[]
            results={stage:[] for stage in oracle}
            for i in range(args.repeats+2):
                elapsed,res=values(sql)
                errors=compare(oracle,res)
                if i>=2:
                    times.append(elapsed)
                    for stage,value in res.items(): results[stage].append(value)
                if kind.startswith("kbn") and any(
                    not math.isclose(res[k],oracle[k],rel_tol=1e-10,abs_tol=1e-9)
                    for k in oracle):
                    raise AssertionError((kind,threads,i,"KBN changed",errors,res))
            result={"threads":threads,"phase":kind,"p50_ms":statistics.median(times),
                    "max_abs_error":max(max(abs(v-oracle[s]) for v in vals)
                                        for s,vals in results.items()),
                    "stages":{stage:{"oracle":oracle[stage],"min":min(vals),"max":max(vals),
                                     "unique":len(set(vals)),
                                     "median":statistics.median(vals)}
                              for stage,vals in results.items()}}
            summaries.append(result)
            print("STREAMING_KBN_CPP_PHASE",json.dumps({
                "threads":threads,"phase":kind,"p50_ms":result["p50_ms"],
                "max_abs_error":result["max_abs_error"],
                "distinct_counts":{stage:v["unique"] for stage,v in result["stages"].items()}
            },separators=(",",":")),flush=True)
    specials={}
    for name,sql in {
        "empty":"SELECT avgKBNLab(x) FROM (SELECT toFloat64(1) AS x WHERE 0) FORMAT TSV",
        "plus_inf":"SELECT avgKBNLab(x) FROM (SELECT arrayJoin([toFloat64(5),"
                   "reinterpretAsFloat64(toUInt64(9218868437227405312))]) AS x) FORMAT TSV",
        "mixed_inf":"SELECT avgKBNLab(x) FROM (SELECT arrayJoin(["
                    "reinterpretAsFloat64(toUInt64(9218868437227405312)),"
                    "reinterpretAsFloat64(toUInt64(18442240474082181120))]) AS x) FORMAT TSV",
        "overflow":"SELECT avgKBNLab(x) FROM (SELECT arrayJoin([toFloat64(1e308),"
                   "toFloat64(1e308)]) AS x) FORMAT TSV",
        "null_skip":"SELECT avgKBNLab(x) FROM "
                    "(SELECT if(number=0,toNullable(toFloat64(4)),CAST(NULL,'Nullable(Float64)')) AS x "
                    "FROM numbers(2)) FORMAT TSV",
    }.items():
        _,raw=execute(sql)
        specials[name]=raw.decode().strip()
        print("STREAMING_KBN_SPECIAL",json.dumps({"case":name,"value":specials[name]}),flush=True)
    assert math.isclose(float(specials["null_skip"]),4.0)
    assert specials["empty"].lower()=="nan",specials
    assert specials["plus_inf"].lower() in ("inf","+inf"),specials
    assert specials["mixed_inf"].lower()=="nan",specials
    # Overflow is deliberately a diagnostic: Prometheus uses incremental mean fallback.
    pathlib.Path(args.output).write_text(json.dumps({
        "exact_on_finite_cases":True,"cases":summaries,"oracle":oracle,"specials":specials
    },indent=2)+"\n")
    print("STREAMING_KBN_CPP_SUCCESS",json.dumps({"finite_cases":len(summaries),
          "groups":len(oracle),"specials":specials}),flush=True)

if __name__=="__main__":
    main()
