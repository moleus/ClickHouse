#!/usr/bin/env python3
"""R30: isolated ALL->ANY TimeRanges membership probe, no ClickHouse source patch.

Only validates generated SQL membership, SAMPLES and direct-query costs.
Full native PromQL under modified C++ remains UNTESTED.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".github/ts-lab-r06")
from probe_nested_selector_plan import (
    request as source_request, quoted, extract_ids_subquery, capture, rows
)

def request(query, query_id=None, timeout=180):
    from urllib.error import HTTPError
    try:
        return source_request(query,query_id,timeout)
    except HTTPError as error:
        raise RuntimeError(f"ClickHouse HTTP {error.code}: {error.read().decode(errors='replace')[:1200]}") from error


METRIC = "r30_cpu_seconds_total"
END = int(dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc).timestamp())
DAY = 86400
CASES = {
    "stage": '{stage="stage-01"}',
    "project": '{project="project-01"}',
    "whole": "",
}
SETTINGS = ("join_algorithm='full_sorting_merge',optimize_read_in_order=1,"
            "optimize_use_projections=0,max_threads=4,max_memory_usage=2000000000")


def save(path, value):
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def rewritten(source):
    if source.count(" ALL LEFT JOIN ") != 1:
        raise ValueError("Expected exactly one ALL LEFT JOIN in generated TimeRanges selector")
    if "timeSeriesStoreTags(" not in source or "time_ranges_subquery" not in source:
        raise ValueError("Missing ID membership/side-effect context")
    return source.replace(" ALL LEFT JOIN ", " ANY LEFT JOIN ", 1)


def explicit_id_alias(ids_source):
    """The generated IN selector returns an unaliased storeTags() expression.

    Standalone SELECT DISTINCT r30_id FROM (...) requires a named output.
    Preserve the function and JOIN; alias only its output column.
    """
    if not ids_source.startswith("SELECT timeSeriesStoreTags("):
        raise ValueError("Unexpected native ID SELECT expression")
    idx=ids_source.find(" FROM ")
    if idx < 0 or " AS r30_id FROM " in ids_source:
        raise ValueError("Missing FROM or already aliased")
    return ids_source[:idx] + " AS r30_id" + ids_source[idx:]


def canonical_result(statement, query_id):
    raw = request(statement + " FORMAT JSONEachRow", query_id=query_id)
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not records:
        raise ValueError("Unexpected empty SQL result for " + query_id)
    canon = "\n".join(sorted(json.dumps(v, sort_keys=True, separators=(",", ":")) for v in records))
    return {"rows": len(records), "sha256": hashlib.sha256(canon.encode()).hexdigest()}


def write_fixture(args, path):
    """Create 16 repeated insert batches plus cold-before and cold-after cohorts."""
    h = hashlib.sha256()
    with pathlib.Path(path).open("wb") as f:
        for batch in range(args.updates + 2):
            for i in range(args.series):
                # The same labels form the same logical ID on every write.
                # The two extra batches are disjoint nonmatching ID cohorts.
                if batch >= args.updates and i >= max(1, args.series // 8):
                    continue
                if batch == args.updates:
                    pod = f"before-{i:07d}"
                    stamp = dt.datetime(2026, 10, 17, 12, tzinfo=dt.timezone.utc)
                elif batch == args.updates + 1:
                    pod = f"after-{i:07d}"
                    stamp = dt.datetime(2026, 10, 23, 12, tzinfo=dt.timezone.utc)
                else:
                    pod = f"pod-{i:07d}"
                    stamp = dt.datetime(2026, 10, 19, 8, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=batch * 25)
                row = {"metric_name": METRIC,
                       "tags": {"stage": f"stage-{i%4:02d}", "project": f"project-{i%2:02d}",
                                "pod": pod},
                       "samples": [[stamp.strftime("%Y-%m-%d %H:%M:%S.000"), float(i%103 + batch)]]}
                encoded = (json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n").encode()
                f.write(encoded)
                h.update(encoded)
    return {"sha256": h.hexdigest(), "series_repeated": args.series, "updates": args.updates,
            "cold_before": max(1,args.series//8), "cold_after": max(1,args.series//8),
            "bytes":pathlib.Path(path).stat().st_size}


def write_batches_from_fixture(fixture_path, args):
    """Insert a batch per request, never merge, preserve repeated raw TimeRanges IDs."""
    endpoint = "http://127.0.0.1:18191/?"+urllib.parse.urlencode({
        "wait_end_of_query": 1,
        "query": "INSERT INTO r30_ts (metric_name,tags,samples) FORMAT JSONEachRow",
    })
    with pathlib.Path(fixture_path).open("rb") as f:
        for batch in range(args.updates + 2):
            lines = [f.readline() for _ in range(args.series if batch < args.updates else max(1,args.series//8))]
            if not lines or any(not line for line in lines):
                raise ValueError(f"Partial fixture batch {batch}")
            req = urllib.request.Request(endpoint, data=b"".join(lines), method="POST")
            with urllib.request.urlopen(req, timeout=180) as response:
                response.read()
            print("R30_INSERT", batch, len(lines), flush=True)
        if f.read(1): raise ValueError("Fixture has unexpected trailing bytes")


def inner_target(kind):
    q = ("SELECT name FROM system.tables WHERE database='default' "
         f"AND startsWith(name,'.inner_id.{kind}.') FORMAT JSONEachRow")
    actual = [json.loads(v)['name'] for v in request(q).splitlines() if v]
    if len(actual) != 1:
        raise ValueError(f"Expected exactly one {kind} inner table, got {actual}")
    return "default." + chr(96) + actual[0].replace(chr(96), chr(96)*2) + chr(96)


def physical_census():
    tags, tr = inner_target("tags"), inner_target("timeranges")
    table = {}
    for name, target in (("tags", tags), ("timeranges", tr)):
        table[name] = rows(f"SELECT count() AS physical_rows,uniqExact(id) AS ids "
                           f"FROM {target}")
        table[name]["target"] = target
        table[name]["parts"] = rows("SELECT count() AS n,sum(rows) AS sum_rows "
                                  "FROM system.parts WHERE active AND database='default' "
                                  f"AND table={quoted(target.split(chr(96))[1])}")
    factor = table["timeranges"]["physical_rows"] / max(1,table["timeranges"]["ids"])
    if factor < 2:
        raise ValueError(f"No duplicated physical TimeRanges rows; factor={factor}")
    table["timeranges"]["physical_rows_per_id"] = factor
    return table


def plans(ids_all, ids_any):
    result = {}
    for name, statement in (("all", ids_all), ("any", ids_any)):
        result[name] = request("EXPLAIN PLAN indexes=1,projections=1 "+statement+" SETTINGS "+SETTINGS)[:80000]
        assert "FullSortingMergeJoin" in result[name], "Unexpected JOIN physical algorithm"
        # FullSortingMergeJoin must retain the requested strictness.
        if f"Strictness: {name}" not in result[name]:
            raise ValueError(f"JOIN changed strictness unexpectedly for {name}")
    return result


def checked_pair(ids_all, ids_any, selector_all, selector_any, case, runid):
    # DISTINCT membership is the actual input used by the subsequent SAMPLES id IN set.
    ids = {}
    for name, statement in (("all", explicit_id_alias(ids_all)), ("any", explicit_id_alias(ids_any))):
        ids[name] = canonical_result(f"SELECT DISTINCT r30_id FROM ({statement}) ORDER BY r30_id SETTINGS {SETTINGS}",
                                     f"r30_id_{case}_{name}_{runid}")
    if ids["all"] != ids["any"]:
        raise ValueError(f"{case}: membership ID set changed ALL->ANY")
    counts = {}
    for name, statement in (("all", explicit_id_alias(ids_all)), ("any", explicit_id_alias(ids_any))):
        counts[name] = rows(f"SELECT count() AS rows,uniqExact(r30_id) AS ids FROM ({statement}) SETTINGS {SETTINGS}")
    if int(counts["all"]["ids"]) != int(counts["any"]["ids"]) or int(counts["all"]["rows"]) <= int(counts["any"]["rows"]):
        raise ValueError(f"{case}: no multiplicity reduction or ID mismatch: {counts}")
    # Keep complete SAMPLES array values, IDs and timestamps; compare byte-independent sorted JSON.
    samples = {}
    for name, statement in (("all", selector_all), ("any", selector_any)):
        samples[name] = canonical_result(f"SELECT id,samples FROM ({statement}) ORDER BY id SETTINGS {SETTINGS}",
                                         f"r30_samples_{case}_{name}_{runid}")
    if samples["all"] != samples["any"]:
        raise ValueError(f"{case}: SAMPLES ID/time/value output changed ALL->ANY")
    return {"membership":ids["all"],"join_counts":counts,"samples":samples["all"],
            "right_multiplicity_reduction":int(counts["all"]["rows"])/int(counts["any"]["rows"])}


def read_query_logs(qids):
    request("SYSTEM FLUSH LOGS")
    by = {}
    for _ in range(8):
        query = ("SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents "
                 "FROM system.query_log WHERE type='QueryFinish' AND query_id IN ("
                 + ",".join(quoted(q) for q in qids)+") FORMAT JSONEachRow")
        by = {a["query_id"]:a for a in (json.loads(line) for line in request(query).splitlines() if line)}
        if all(qid in by for qid in qids): break
        time.sleep(.25)
    if set(by) != set(qids):
        raise ValueError(f"Missing QueryFinish rows for {set(qids)-set(by)}")
    return by


def abba(ids_all,ids_any,case,runid):
    order = ("all","any","any","all")
    phases=[]
    qids=[]
    for i,name in enumerate(order):
        statement = ids_all if name=="all" else ids_any
        trials=[]
        for j in range(7):
            qid=f"r30_abba_{case}_{runid}_{i}_{j}"
            raw = request(f"SELECT count() AS n,uniqExact(r30_id) AS u FROM ({statement}) "
                          f"SETTINGS {SETTINGS} FORMAT JSONEachRow", qid)
            parsed = json.loads(raw)
            if int(parsed['u'])<=0: raise ValueError("Missing distinct IDs")
            trials.append({"qid":qid,"n":int(parsed['n']),"u":int(parsed['u']),"warmup":j<3})
            qids.append(qid)
        phases.append({"index":i,"mode":name,"trials":trials})
    logs=read_query_logs(qids)
    for phase in phases:
        measured=[]
        for trial in phase["trials"]:
            row=logs[trial["qid"]]
            ev=row.get("ProfileEvents") or {}
            trial.update({"wall_ms":row["query_duration_ms"],
                         "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                         "memory_bytes":row["memory_usage"],
                         "read_rows":row["read_rows"],"read_bytes":row["read_bytes"]})
            if not trial["warmup"]: measured.append(trial)
        phase["medians"]={name:statistics.median(t[name] for t in measured)
                          for name in ("wall_ms","cpu_ms","memory_bytes","read_rows","read_bytes")}
    by={}
    for name in ("all","any"):
        p=[x for x in phases if x["mode"]==name]
        by[name]={k:statistics.mean(x["medians"][k] for x in p) for k in p[0]["medians"]}
        by[name]["wall_drift"]=abs(p[0]["medians"]["wall_ms"]-p[1]["medians"]["wall_ms"])/max(1,by[name]["wall_ms"])
    return {"phases":phases,"summary":by,"passed":by["all"]["wall_drift"]<0.25 and by["any"]["wall_drift"]<0.25,
            "kind":"Direct ID membership SQL diagnostic; NOT compiled native PromQL"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--series", type=int, default=4096)
    ap.add_argument("--updates", type=int, default=16)
    ap.add_argument("--postmerge-control", action="store_true",
                    help="After premerge ABBA, merge TimeRanges only and repeat ID parity/ABBA")
    a=ap.parse_args()
    if not (1024<=a.series<=32768 and 2<=a.updates<=32):
        raise ValueError("Out of bounded diagnostic fixture range")
    path=pathlib.Path(a.out)
    path.mkdir(parents=True,exist_ok=True)
    report={"question":"R30","kind":"ALL_TO_ANY_SQL_DIAGNOSTIC",
            "results":{},"errors":[],"status":"INCONCLUSIVE",
            "native_modified_binary":False,"latency_claim":"none"}
    try:
        data=path/"fixture.jsonl"
        report["fixture"]=write_fixture(a,data)
        request("CREATE TABLE r30_ts ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
        request("SYSTEM STOP MERGES")
        write_batches_from_fixture(data,a)
        report["physical"]=physical_census()
        save(path/"report.json",report)
        runid=os.getenv("GITHUB_RUN_ID", "local")
        for case,matcher in CASES.items():
            expr=f'avg by(stage)(avg_over_time({METRIC}{matcher}[1d]))'
            native=(f"SELECT * FROM prometheusQueryRange(r30_ts,{quoted(expr)},"
                    f"{END},{END},86400) SETTINGS {SETTINGS} FORMAT JSONEachRow")
            log_offset=pathlib.Path(a.log).stat().st_size
            native_response=request(native,f"r30_stock_{case}_{runid}")
            if not native_response.strip(): raise ValueError(f"Empty stock native output: {case}")
            generated=capture(a.log,log_offset)
            ids_all=extract_ids_subquery(generated)
            ids_any=rewritten(ids_all)
            selector_any=rewritten(generated)
            report["results"][case]={
                "stock_native_sql":native,"native_output_sha256":hashlib.sha256(native_response.encode()).hexdigest(),
                "generated_sql_sha256":hashlib.sha256(generated.encode()).hexdigest(),
                "generated_selector_sql":generated,
                "ids_all_sql":ids_all,"ids_any_sql":ids_any,
                "plans":plans(ids_all,ids_any),
                "parity":checked_pair(ids_all,ids_any,generated,selector_any,case,runid)}
            print("R30_PARITY",case,report["results"][case]["parity"]["right_multiplicity_reduction"],flush=True)
            save(path/"report.json",report)
        report["benchmark"]=abba(explicit_id_alias(report["results"]["project"]["ids_all_sql"]),
                                 explicit_id_alias(report["results"]["project"]["ids_any_sql"]),"project",runid)
        if a.postmerge_control:
            target=report["physical"]["timeranges"]["target"]
            # Scope START and STOP MERGES to TimeRanges. Do not touch SAMPLES/TAGS.
            request("SYSTEM START MERGES " + target)
            request("OPTIMIZE TABLE " + target + " FINAL")
            request("SYSTEM STOP MERGES " + target)
            merged={"physical":physical_census(),"cases":{}}
            right=merged["physical"]["timeranges"]
            if int(right["physical_rows"]) != int(right["ids"]):
                raise ValueError(f"TimeRanges FINAL failed to collapse duplicates: {right}")
            for case,prior in report["results"].items():
                ids_all,ids_any=prior["ids_all_sql"],prior["ids_any_sql"]
                # Generated source SQL is frozen; same queries on merged right-side parts.
                outer_all=prior["generated_selector_sql"]
                outer_any=rewritten(outer_all)
                result=checked_pair(ids_all,ids_any,outer_all,outer_any,case,runid+"_merged")
                if result["membership"] != prior["parity"]["membership"] or result["samples"] != prior["parity"]["samples"]:
                    raise ValueError(f"{case}: physical merge changed membership or sample values")
                merged["cases"][case]=result
            merged["benchmark"]=abba(explicit_id_alias(report["results"]["project"]["ids_all_sql"]),
                                     explicit_id_alias(report["results"]["project"]["ids_any_sql"]),
                                     "project",runid+"_merged")
            report["postmerge"]=merged
            print("R30_POSTMERGE",
                  json.dumps({"physical":right,
                              "multiplicity":{k:v["right_multiplicity_reduction"] for k,v in merged["cases"].items()},
                              "benchmark":merged["benchmark"]["summary"]}),flush=True)
        report["status"]="PASS" if (report["benchmark"]["passed"]
            and (not a.postmerge_control or report["postmerge"]["benchmark"]["passed"])) else "INCONCLUSIVE"
    except Exception as exc:
        report["errors"].append(str(exc))
        report["status"]="INCONCLUSIVE"
    finally:
        save(path/"report.json",report)
        print("R30_DONE",json.dumps({"status":report["status"],"errors":report["errors"],
                                    "multiplicity":{k:v.get("parity",{}).get("right_multiplicity_reduction")
                                                    for k,v in report["results"].items()},
                                    "bench":report.get("benchmark",{}).get("summary")},default=str),flush=True)
    if report["status"]!="PASS": raise SystemExit(1)


if __name__=="__main__":main()
