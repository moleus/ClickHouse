#!/usr/bin/env python3
"""R31 generated native TimeSeries ID SQL: ALL vs ANY vs IN membership.

No patched ClickHouse C++. Direct ID SQL and complete SAMPLES SELECT parity
on two tables with the same logical labels but different TimeRanges multiplicity.
Each frozen table uses A/B/C/C/B/A, 3 warmups and 4 measured queries per phase.
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, ".github/ts-lab-r30")
sys.path.insert(0, ".github/ts-lab-r06")
from bench_timeranges_any_sql import (
    request, quoted, write_fixture, rewritten as any_join,
    explicit_id_alias, canonical_result, read_query_logs, END, METRIC, SETTINGS, save
)
from bench_any_duplication_curve import target as ranges_target
from probe_nested_selector_plan import capture, extract_ids_subquery

CASES={"stage":'{stage="stage-01"}', "project":'{project="project-01"}', "whole":""}
LEVELS=(1,16)
MODE_ORDER=("all","any","in","in","any","all")


def quoted_parenthesized(source, index):
    """Get balanced parentheses, never consider brackets inside quoted SQL strings."""
    if source[index] != "(":
        raise ValueError("Expected opening parenthesis")
    depth=0
    quote=None
    escaping=False
    i=index
    while i<len(source):
        ch=source[i]
        if quote:
            if escaping: escaping=False
            elif ch==chr(92): escaping=True
            elif ch==quote:
                if i+1<len(source) and source[i+1]==quote: i+=1
                else: quote=None
        elif ch in (chr(39),chr(34),chr(96)):
            quote=ch
        elif ch=="(": depth+=1
        elif ch==")":
            depth-=1
            if depth==0: return source[index+1:i],i
            if depth<0: raise ValueError("Unbalanced SQL")
        i+=1
    raise ValueError("Unclosed SQL parentheses")


def convert_join_to_in(ids_sql):
    """Remove only the native TimeRanges membership JOIN and reuse its predicates.

    The original exception for IDs outside metric ranges is retained literally.
    This is a strict source-specific diagnostic, not a general SQL rewriter.
    """
    anchor=" ALL LEFT JOIN ("
    if ids_sql.count(anchor)!=1:
        raise ValueError("Expected exactly one native TimeRanges ALL LEFT JOIN")
    if not ids_sql.startswith("SELECT timeSeriesStoreTags("):
        raise ValueError("Expected native stateful ID SELECT")
    begin=ids_sql.index(anchor)
    inside,closing=quoted_parenthesized(ids_sql,begin+len(anchor)-1)
    tail=ids_sql[closing+1:]
    expected=" AS time_ranges_subquery USING (id) WHERE "
    if not tail.startswith(expected):
        raise ValueError("Unexpected TimeRanges alias or JOIN condition")
    prefix=ids_sql[:begin]
    if " FROM default.`.inner_id.tags." not in prefix:
        raise ValueError("Unexpected TAGS source")
    condition=tail[len(expected):]
    needle="equals(time_ranges_subquery.matched, 1)"
    if condition.count(needle)!=1 or "or(" not in condition:
        raise ValueError("Missing exact matched-flag/legacy exception predicate")
    right=inside.strip()
    right_prefix="SELECT id, 1 AS matched FROM "
    if not right.startswith(right_prefix):
        raise ValueError("Expected right-side constant matched=1")
    right_id="SELECT id FROM "+right[len(right_prefix):]
    # Identical right-side time and metric-ID filters, but set membership
    # prevents duplicated TimeRanges rows from expanding left TAGS rows.
    pred=condition.replace(needle,"in(id, ("+right_id+"))",1)
    if "time_ranges_subquery" in pred:
        raise ValueError("Unresolved RHS reference after JOIN removal")
    return prefix+" WHERE "+pred


def diagnostic_alias(ids):
    # Same projection is preserved; output alias is unique so it cannot shadow
    # the ID used by the source join or IN predicate.
    marker=" FROM default.`.inner_id.tags."
    if ids.count(marker)!=1:
        raise ValueError("Unexpected native ID SELECT projection or TAGS target")
    return ids.replace(marker," AS r31_id"+marker,1)


def run_once(statement,qid):
    return json.loads(request(statement+" SETTINGS "+SETTINGS+" FORMAT JSONEachRow",qid))


def insert(table, groups):
    url="http://127.0.0.1:18191/?"+urllib.parse.urlencode({
        "wait_end_of_query":1,
        "query":f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow"
    })
    for group in groups:
        payload=b"".join(group)
        if not payload: raise ValueError("Empty INSERT group")
        with urllib.request.urlopen(urllib.request.Request(url,data=payload,method="POST"),timeout=180) as response:
            response.read()


def physical(table,series,level):
    target=ranges_target(table)
    physical_rows=json.loads(request(f"SELECT count() AS rows,uniqExact(id) AS ids "
                                     f"FROM {target} FORMAT JSONEachRow"))
    expected=series*level+2*(series//8)
    got=int(physical_rows["rows"])
    ids=int(physical_rows["ids"])
    if got!=expected or ids!=series+2*(series//8):
        raise ValueError(f"Unexpected unmerged rows/IDs for {table}: {physical_rows}; wanted {expected}")
    return {"table":table,"right_target":target,
            "rows":got,"distinct_ids":ids,"hot_multiplier":level}


def three_way_parity(generated,ids,case,level,runid):
    variants={"all":ids,"any":any_join(ids),"in":convert_join_to_in(ids)}
    selectors={name:generated.replace(ids,stmt,1) for name,stmt in variants.items()}
    if generated.count(ids)!=1: raise ValueError("Generated ID subquery not unique")
    census={}
    for name,sql in variants.items():
        named=diagnostic_alias(sql)
        result=canonical_result(f"SELECT DISTINCT r31_id FROM ({named}) "
                                f"ORDER BY r31_id SETTINGS {SETTINGS}",
                                f"r31_ids_{case}_{level}_{name}_{runid}")
        count=run_once(f"SELECT count() AS n,uniqExact(r31_id) AS u FROM ({named})",
                       f"r31_count_{case}_{level}_{name}_{runid}")
        sample=canonical_result(f"SELECT id,samples FROM ({selectors[name]}) "
                                f"ORDER BY id SETTINGS {SETTINGS}",
                                f"r31_sample_{case}_{level}_{name}_{runid}")
        plan=request("EXPLAIN PLAN indexes=1,projections=1 "
                     +sql+" SETTINGS "+SETTINGS)[:100000]
        census[name]={"ids":result,"counts":count,"samples":sample,"plan":plan,
                      "ids_sql":sql,"selector_sql":selectors[name]}
    ids_sets={k:x["ids"]["sha256"] for k,x in census.items()}
    samples_sets={k:x["samples"]["sha256"] for k,x in census.items()}
    if len(set(ids_sets.values()))!=1 or len(set(samples_sets.values()))!=1:
        raise ValueError(f"{case}/{level}: ID or SAMPLES output changed by IN replacement")
    if not (census["all"]["counts"]["u"]==census["any"]["counts"]["u"]==
            census["in"]["counts"]["u"]):
        raise ValueError(f"{case}/{level}: COUNT distinct ID mismatch")
    if census["any"]["counts"]["n"]!=census["in"]["counts"]["n"]:
        raise ValueError(f"{case}/{level}: unexpected multiplicity from IN")
    if level>1 and census["all"]["counts"]["n"]<=census["any"]["counts"]["n"]:
        raise ValueError(f"{case}/{level}: unmerged ALL did not duplicate")
    return census


def abccba(sqls,level,runid):
    all_ids=sqls["all"]
    phases=[]
    qids=[]
    for p,mode in enumerate(MODE_ORDER):
        trials=[]
        for j in range(7):
            query_id=f"r31_cmp_k{level}_{mode}_{p}_{j}_{runid}"
            answer=run_once(f"SELECT count() AS n,uniqExact(r31_id) AS u "
                            f"FROM ({diagnostic_alias(sqls[mode])})",query_id)
            if int(answer["u"])<=0: raise ValueError("No series IDs in R31 benchmark")
            trials.append({"query_id":query_id,"warmup":j<3,
                           "rows":int(answer["n"]),"unique":int(answer["u"])})
            qids.append(query_id)
        phases.append({"phase":p,"mode":mode,"trials":trials})
    logs=read_query_logs(qids)
    for phase in phases:
        for trial in phase["trials"]:
            finish=logs[trial["query_id"]]
            trial.update({"wall_ms":finish["query_duration_ms"],
                          "cpu_ms":(finish.get("ProfileEvents") or {}).get("OSCPUVirtualTimeMicroseconds",0)/1000,
                          "query_memory_bytes":finish["memory_usage"],
                          "read_rows":finish["read_rows"],"read_bytes":finish["read_bytes"]})
        samples=[x for x in phase["trials"] if not x["warmup"]]
        phase["median"]={key:statistics.median(x[key] for x in samples)
                         for key in ("wall_ms","cpu_ms","query_memory_bytes","read_rows","read_bytes")}
    summary={}
    for mode in ("all","any","in"):
        pair=[p["median"] for p in phases if p["mode"]==mode]
        mean={key:statistics.mean(x[key] for x in pair) for key in pair[0]}
        mean["wall_a_a_drift"]=abs(pair[0]["wall_ms"]-pair[1]["wall_ms"])/max(1,mean["wall_ms"])
        summary[mode]=mean
    passed=all(x["wall_a_a_drift"]<0.25 for x in summary.values())
    return {"phases":phases,"summary":summary,"drift_passed":passed}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True)
    parser.add_argument("--log",required=True)
    parser.add_argument("--series",type=int,default=2048)
    args=parser.parse_args()
    if not (512<=args.series<=8192 and args.series%8==0):
        raise ValueError("Series outside bounded range or not divisible by 8")
    folder=pathlib.Path(args.out)
    folder.mkdir(parents=True,exist_ok=True)
    data=folder/"fixture.jsonl"
    meta=write_fixture(SimpleNamespace(series=args.series,updates=16),data)
    batches=data.read_bytes().splitlines(keepends=True)
    hot=[batches[i*args.series:(i+1)*args.series] for i in range(16)]
    cold=batches[16*args.series:]
    # two cold cohorts each have exactly one-eighth of the series
    if len(cold)!=args.series//4:
        raise ValueError("Cold fixture split mismatch")
    cohorts=[cold[:args.series//8],cold[args.series//8:]]
    report={"question_id":"R31","status":"INCONCLUSIVE","errors":[],
            "source_sha":"52529f0c711566d696917678bf15e7068772fdde",
            "fixture":meta,"results":{},"new_native_cpp_binary":False}
    save(folder/"report.json",report)
    runid=os.getenv("GITHUB_RUN_ID","local")
    try:
        for level in LEVELS:
            table=f"r31_inset_{level}"
            request(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
            right=ranges_target(table)
            request("SYSTEM STOP MERGES "+right)
            insert(table,hot[:level]+cohorts)
            case={"level":level,"table":table,"physical":physical(table,args.series,level),
                  "selectors":{}}
            report["results"][str(level)]=case
            for name,matcher in CASES.items():
                expr=f'avg by(stage)(avg_over_time({METRIC}{matcher}[1d]))'
                native=f"SELECT * FROM prometheusQueryRange({table},{quoted(expr)},{END},{END},86400) SETTINGS {SETTINGS} FORMAT JSONEachRow"
                offset=pathlib.Path(args.log).stat().st_size
                response=request(native,f"r31_stock_{name}_{level}_{runid}")
                if not response.strip(): raise ValueError("Empty baseline native result: "+name)
                generated=capture(args.log,offset)
                ids=extract_ids_subquery(generated)
                case["selectors"][name]={
                    "native_sql":native,"native_output_sha256":hashlib.sha256(response.encode()).hexdigest(),
                    "generated_sql_sha256":hashlib.sha256(generated.encode()).hexdigest(),
                    "variants":three_way_parity(generated,ids,name,level,runid)}
                print("R31_PARITY",level,name,
                      {key:v["counts"]["n"] for key,v in case["selectors"][name]["variants"].items()},flush=True)
                save(folder/"report.json",report)
            variants=case["selectors"]["project"]["variants"]
            case["benchmark"]=abccba({mode:v["ids_sql"] for mode,v in variants.items()},level,runid)
            print("R31_BENCH",level,json.dumps(case["benchmark"]["summary"],sort_keys=True),flush=True)
            save(folder/"report.json",report)
        report["status"]="PASS" if all(r["benchmark"]["drift_passed"] for r in report["results"].values()) else "INCONCLUSIVE"
    except Exception as e:
        report["status"]="INVALID_METHOD_OR_INCONCLUSIVE"
        report["errors"].append(str(e))
    finally:
        save(folder/"report.json",report)
        print("R31_DONE",json.dumps({"status":report["status"],"errors":report["errors"],
              "levels":list(report["results"])},sort_keys=True),flush=True)
    if report["status"]!="PASS": raise SystemExit(1)


if __name__=="__main__":
    main()
