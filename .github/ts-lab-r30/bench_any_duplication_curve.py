#!/usr/bin/env python3
"""R30 follow-up: ALL/ANY TimeRanges duplicate-density curve on frozen tables.

No compilation, no native C++ rewrite. Each level compares the same data
within one source-pinned process using A/B/B/A. Across levels the physical
right-hand row count varies, so absolute cross-level wall times are NOT paired.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, ".github/ts-lab-r30")
sys.path.insert(0, ".github/ts-lab-r06")
from bench_timeranges_any_sql import (
    request, quoted, write_fixture, rewritten, explicit_id_alias,
    checked_pair, plans, abba, END, DAY, METRIC, SETTINGS, save
)
from probe_nested_selector_plan import capture, extract_ids_subquery

LEVELS=(1,2,4,8,16)


def table_name(level):
    if level not in LEVELS:
        raise ValueError("Unsupported duplicate-density level")
    return f"r30_curve_{level}"


def target(table):
    result=request("SELECT uuid FROM system.tables WHERE database='default' "
                   f"AND name={quoted(table)} FORMAT JSONEachRow")
    values=[json.loads(line)["uuid"] for line in result.splitlines() if line.strip()]
    if len(values)!=1: raise ValueError(f"Cannot resolve TimeRanges UUID for {table}: {values}")
    return "default." + chr(96) + ".inner_id.timeranges." + values[0] + chr(96)


def census(table, series, level):
    tr=target(table)
    result=request(f"SELECT count() AS total,uniqExact(id) AS ids "
                   f"FROM {tr} FORMAT JSONEachRow")
    item=json.loads(result.strip())
    total, ids=int(item["total"]),int(item["ids"])
    if ids!=series or total!=series*level:
        raise ValueError(f"Unexpected TimeRanges state {table} rows={total} ids={ids}")
    parts=request("SELECT count() AS n,sum(rows) AS rows FROM system.parts "
                  "WHERE database='default' AND active AND "
                  f"table={quoted(tr.split(chr(96))[1])} FORMAT JSONEachRow")
    return {"physical_rows":total,"distinct_ids":ids,"multiplicity":total/ids,
            "parts":json.loads(parts.strip()),"time_ranges_target":tr}


def insert(table, fixture_lines, series, level):
    import urllib.request
    import urllib.parse
    endpoint="http://127.0.0.1:18191/?"+urllib.parse.urlencode({
        "wait_end_of_query":1,
        "query":f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow"
    })
    for i in range(level):
        data=b"".join(fixture_lines[i*series:(i+1)*series])
        if len(data)==0: raise ValueError("Empty insert batch")
        req=urllib.request.Request(endpoint,data=data,method="POST")
        with urllib.request.urlopen(req,timeout=180) as response:
            response.read()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",required=True)
    p.add_argument("--log",required=True)
    p.add_argument("--series",type=int,default=2048)
    args=p.parse_args()
    if not (512 <= args.series <= 8192): raise ValueError("Series must be in 512..8192")
    folder=pathlib.Path(args.out)
    folder.mkdir(parents=True,exist_ok=True)
    fixture_path=folder/"fixture.jsonl"
    fixture=write_fixture(SimpleNamespace(series=args.series,updates=max(LEVELS)),fixture_path)
    # The last two cold cohorts in this reusable fixture are intentionally NOT
    # inserted. Record the SHA of only the inserted rows as a separate identity.
    input_lines=fixture_path.read_bytes().splitlines(keepends=True)
    inserted=input_lines[:args.series*max(LEVELS)]
    sha=hashlib.sha256(b"".join(inserted)).hexdigest()
    report={"question":"R30","test":"MULTIPLICITY_CURVE_SQL_ONLY",
            "fixture_meta":fixture,"inserted_sha256":sha,"series":args.series,
            "levels":{},"errors":[],"status":"INCONCLUSIVE",
            "source_sha":"52529f0c711566d696917678bf15e7068772fdde",
            "native_cpp_modified":False,
            "caveat":"Between-level absolute times are not paired. A/A drift is validated within each frozen level."}
    save(folder/"report.json",report)
    try:
        # Global stop is active before any table creation; it guarantees one
        # physical TimeRanges row per insert for each stable ID in the fixture.
        request("SYSTEM STOP MERGES")
        runid=os.getenv("GITHUB_RUN_ID","local")
        for level in LEVELS:
            table=table_name(level)
            request(f"CREATE TABLE {table} ENGINE=TimeSeries "
                    "SETTINGS recent_samples_ttl_seconds=0")
            insert(table,inserted,args.series,level)
            record={"level":level,"table":table,"physical":census(table,args.series,level)}
            report["levels"][str(level)]=record
            save(folder/"report.json",report)
            expr=f'avg by(stage)(avg_over_time({METRIC}{{project="project-01"}}[1d]))'
            native=(f"SELECT * FROM prometheusQueryRange({table},{quoted(expr)},"
                    f"{END},{END},{DAY}) SETTINGS {SETTINGS} FORMAT JSONEachRow")
            offset=pathlib.Path(args.log).stat().st_size
            body=request(native,f"r30_curve_native_{level}_{runid}")
            if not body.strip(): raise ValueError(f"Empty native output at level {level}")
            generated=capture(args.log,offset)
            ids_all=extract_ids_subquery(generated)
            ids_any=rewritten(ids_all)
            record["native_query"]=native
            record["native_output_sha256"]=hashlib.sha256(body.encode()).hexdigest()
            record["sql_sha256"]=hashlib.sha256(generated.encode()).hexdigest()
            record["plan"]=plans(ids_all,ids_any)
            record["parity"]=checked_pair(ids_all,ids_any,generated,rewritten(generated),
                                           f"project_k{level}",runid,expect_reduction=(level>1))
            if abs(record["parity"]["right_multiplicity_reduction"]-level) > 1e-9:
                raise ValueError(f"Observed multiplicity not {level}: {record['parity']}")
            record["abba"]=abba(explicit_id_alias(ids_all),explicit_id_alias(ids_any),
                                 f"project_k{level}",runid)
            record["valid_for_timing"]=record["abba"]["passed"]
            print("R30_CURVE_LEVEL",json.dumps({
                "level":level,
                "physical":record["physical"]["physical_rows"],
                "reduction":record["parity"]["right_multiplicity_reduction"],
                "all":record["abba"]["summary"]["all"],
                "any":record["abba"]["summary"]["any"],
                "valid_timing":record["valid_for_timing"]}),flush=True)
            save(folder/"report.json",report)
        report["status"]="PASS" if all(x["valid_for_timing"] for x in report["levels"].values()) else "INCONCLUSIVE"
    except Exception as error:
        report["errors"].append(str(error))
        report["status"]="INVALID_METHOD_OR_INCONCLUSIVE"
    finally:
        save(folder/"report.json",report)
        print("R30_CURVE_DONE",json.dumps({"status":report["status"],"errors":report["errors"],
            "timing_valid":{k:r.get("valid_for_timing") for k,r in report["levels"].items()}},
            separators=(",",":")),flush=True)
    if report["status"]!="PASS":
        raise SystemExit(1)


if __name__=="__main__":
    main()
