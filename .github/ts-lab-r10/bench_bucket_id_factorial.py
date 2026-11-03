#!/usr/bin/env python3
"""R10 native TimeSeries stock O1: six-table 2x3 bucket-step x UUID-ID factorial.

Same 400-slot, three-hour-churn R01 synthetic fixture and SHA verified O1
binary. No C++ patches, no data re-encoding between variants, one runner.
Independent physical bytes, bucket-row counts, sample-array point count,
logical ID digest and native full PromQL output parity for every table.
Id-type ABBA within each bucket step; bucket-step ABBA within each ID type
on two held-out selectors. No claims of causality for single-profile traces.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-r10", ".github/ts-lab-r01",
    ".github/ts-lab-perf", ".github/ts-lab-bench", ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, inner, streamed_insert, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import post
from bench_native_multiclient_stock_20261103 import physical, sha256, save, server_status_pid
from bench_k8s_mixed_native import (
    CASES, query, jsonrows, run_phase, profile_rows, SOURCE_SHA, BINARY_SHA, DAY
)
from generate_k8s_churn_fixture import BASE, LIFE, expected_counts

FIXTURE_SHA = "ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
STEPS = (900, 3600, 10800)
IDS = ("lc", "raw")
HOST = "http://127.0.0.1:18191"


def table(kind, step):
    return f"tslab_r10_{kind}_b{step}"


def create_table(kind, step):
    name = table(kind, step)
    if kind == "lc":
        sql(
            f"CREATE TABLE {name} ENGINE=TimeSeries "
            f"SETTINGS recent_samples_ttl_seconds=0,"
            f"samples_bucket_step_seconds={step}"
        )
    else:
        sql(
            f"CREATE TABLE {name} ENGINE=TimeSeries "
            f"SETTINGS recent_samples_ttl_seconds=0,"
            f"samples_bucket_step_seconds={step} "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
            "TAGS INNER COLUMNS ("
            "id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),"
            "reinterpretAsUUID(sipHash128(tags))),"
            "metric_name LowCardinality(String),"
            "tags Map(LowCardinality(String),String),"
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
            "GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))"
        )


def physical_audit(name, expected_ids, expected_samples, expected_buckets, kind):
    info = physical(name)
    expected_type = "LowCardinality(UUID)" if kind == "lc" else "Tuple(UInt64, UUID)"
    for layer in ("tags", "samples", "timeranges"):
        if expected_type not in info[layer]["id_column"]:
            raise AssertionError((name, layer, "Unexpected ID type", info[layer]["id_column"]))
        if info[layer]["parts"]["part_count"] != 1:
            raise AssertionError((name, layer, "Active part count not one", info[layer]))
    if int(info["tags"]["parts"]["rows"]) != expected_ids:
        raise AssertionError((name, "Wrong TAGS count", info["tags"]))
    if int(info["timeranges"]["parts"]["rows"]) != expected_ids:
        raise AssertionError((name, "Wrong timeranges count", info["timeranges"]))
    counted = jsonrows(
        "SELECT count() AS bucket_rows,sum(length(samples)) AS sample_elements "
        "FROM " + inner(name, "samples")
    )[0]
    if (int(counted["sample_elements"]) != expected_samples or
        int(counted["bucket_rows"]) != expected_buckets or
        int(info["samples"]["parts"]["rows"]) != expected_buckets):
        raise AssertionError((name, "Wrong bucket/sample element count", counted, info["samples"]))
    tags = jsonrows(
        "SELECT count() AS n,sum(sipHash64(toString(id))) AS digest,"
        "countIf(metric_name='tslab_node_cpu_ratio') AS nodes,"
        "countIf(metric_name='tslab_pod_cpu_ratio') AS pods "
        "FROM " + inner(name, "tags")
    )[0]
    if tags["n"] != expected_ids:
        raise AssertionError((name, "Wrong distinct ID count", tags))
    return {"physical": info, "samples": counted, "identity": tags}


def compare_abba(pair_id, case, first, second, days, end, pid, oracle):
    phases = []
    for order, name in enumerate((first, second, second, first)):
        ph = run_phase(
            case, days, end, name, order, pid, oracle,
            variant_label=f"r10_{pair_id}_{name}"
        )
        phases.append(ph)
    a = [p["summary"] for p in phases if p["variant"].endswith(first)]
    b = [p["summary"] for p in phases if p["variant"].endswith(second)]
    if len(a) != 2 or len(b) != 2:
        raise AssertionError((pair_id, "Bad ABBA variants", [x["variant"] for x in phases]))
    mean = lambda values, field: statistics.mean(x[field] for x in values)
    before = mean(a, "p50_ms")
    after = mean(b, "p50_ms")
    drift = abs(a[0]["p50_ms"] - a[1]["p50_ms"]) / before
    summary = {
        "first": first, "second": second, "case": case,
        "first_p50_ms": round(before, 3), "second_p50_ms": round(after, 3),
        "first_over_second_speedup": round(before / after, 4),
        "first_p95_ms": round(mean(a, "p95_ms"), 3),
        "second_p95_ms": round(mean(b, "p95_ms"), 3),
        "first_cpu_ms": round(mean(a, "cpu_ms"), 3),
        "second_cpu_ms": round(mean(b, "cpu_ms"), 3),
        "first_read_rows": round(mean(a, "median_read_rows"), 2),
        "second_read_rows": round(mean(b, "median_read_rows"), 2),
        "first_compressed_bytes": round(mean(a, "median_compressed_bytes"), 2),
        "second_compressed_bytes": round(mean(b, "median_compressed_bytes"), 2),
        "first_query_memory_bytes": max(x["query_memory_max_bytes"] for x in a),
        "second_query_memory_bytes": max(x["query_memory_max_bytes"] for x in b),
        "control_drift": round(drift, 5),
        "valid_control": drift <= .25,
    }
    result = {
        "summary": summary, "phases": phases,
        "representative_profiles": profile_rows([
            phases[0]["profile_qid"], phases[1]["profile_qid"]
        ]),
    }
    print("R10_FACTORIAL_COMPARE", json.dumps(
        {"pair": pair_id, **summary}, separators=(",", ":")), flush=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--pid-file", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    p.add_argument("--cross-cases", nargs="+", choices=CASES,
                   default=["node_broad", "pod_namespace"])
    args = p.parse_args()
    report = {
        "question_id": "R10-bucket-id-factorial-v1",
        "source_sha": SOURCE_SHA, "binary_sha256": BINARY_SHA,
        "fixture_sha256": FIXTURE_SHA, "steps": STEPS, "id_types": IDS,
        "physical_audits": {}, "id_comparisons": {},
        "bucket_comparisons": {}, "errors": [],
    }
    save(args.output, report)
    try:
        m = json.loads(pathlib.Path(args.manifest).read_text())
        rows, samples = expected_counts(3, 400)
        if (m.get("sha256") != FIXTURE_SHA or sha256(args.fixture) != FIXTURE_SHA or
            m.get("series") != sum(rows.values()) or
            m.get("samples") != sum(samples.values()) or
            m.get("base") != BASE or m.get("days") != 3 or
            m.get("node_slots") != 400):
            raise AssertionError("R10 fixture identity/distribution mismatch")
        report["manifest"] = m
        expected_ids = m["series"]
        expected_samples = m["samples"]
        pid = server_status_pid(args.pid_file)
        report["server_pid"] = pid
        for seconds in STEPS:
            for kind in IDS:
                name = table(kind, seconds)
                create_table(kind, seconds)
                started = time.perf_counter()
                inserted = streamed_insert(name, args.fixture)
                if inserted["rows"] != expected_ids:
                    raise AssertionError((name, "Wrong ingested series", inserted))
                report.setdefault("ingest", {})[name] = {
                    **inserted, "elapsed_s": round(time.perf_counter() - started, 3)
                }
                report.setdefault("optimize", {})[name] = optimize_table(name)
                if any(isinstance(x, dict) for x in report["optimize"][name].values()):
                    raise AssertionError((name, "OPTIMIZE did not fully complete"))
                print("R10_FACTORIAL_INGEST", json.dumps({
                    "table": name, "elapsed_s": report["ingest"][name]["elapsed_s"]
                }, separators=(",", ":")), flush=True)
                save(args.output, report)
        wait_no_merges()
        for seconds in STEPS:
            for kind in IDS:
                name = table(kind, seconds)
                n_buckets = expected_ids * LIFE // seconds
                report["physical_audits"][name] = physical_audit(
                    name, expected_ids, expected_samples, n_buckets, kind
                )
        digests = {v["identity"]["digest"] for v in report["physical_audits"].values()}
        if len(digests) != 1:
            raise AssertionError(("Mismatched logical ID digest", digests))
        report["physical_before"] = {
            name: v["physical"] for name, v in report["physical_audits"].items()
        }
        save(args.output, report)
        end = int(m["end"])
        begin = end - (m["days"] - 1) * DAY
        expected_ticks = {begin + DAY * i for i in range(m["days"])}
        for case in args.cases:
            oracle_id = f"r10oracle_{case}_{os.getenv('GITHUB_RUN_ID','local')}"
            _, body = post(
                HOST, query(table("lc", 3600), case, begin, end), oracle_id
            )
            reference = normalize_any(body)
            expected_points = m["days"] * CASES[case][3]
            if (len(reference) != expected_points or
                {timestamp for _, timestamp in reference} != expected_ticks):
                raise AssertionError((case, "Wrong nonempty oracle cardinality/grid", len(reference)))
            for seconds in STEPS:
                pair = f"idtype_{seconds}_{case}"
                result = compare_abba(
                    pair, case, table("lc", seconds), table("raw", seconds),
                    m["days"], end, pid, reference
                )
                report["id_comparisons"][pair] = result
                if not result["summary"]["valid_control"]:
                    report["errors"].append({"pair": pair, "control_drift":result["summary"]["control_drift"]})
                save(args.output, report)
            if case in args.cross_cases:
                for kind in IDS:
                    for seconds in (900,10800):
                        pair = f"bucket_{kind}_{seconds}_{case}"
                        result = compare_abba(
                            pair, case, table(kind, 3600), table(kind, seconds),
                            m["days"], end, pid, reference
                        )
                        report["bucket_comparisons"][pair] = result
                        if not result["summary"]["valid_control"]:
                            report["errors"].append({"pair": pair, "control_drift":result["summary"]["control_drift"]})
                        save(args.output, report)
        report["physical_after"] = {
            name: physical(name) for name in report["physical_before"]
        }
        if report["physical_before"] != report["physical_after"]:
            raise AssertionError("Physical layouts changed during read-only trials")
        report["passed"] = not report["errors"]
    except Exception as e:
        report["errors"].append({
            "fatal":str(e)[:2500], "traceback":traceback.format_exc()[-4300:]
        })
        report["passed"] = False
    finally:
        save(args.output, report)
        compact = {
            "question_id": report["question_id"], "passed":report["passed"],
            "errors":report["errors"], "fixture":report.get("manifest"),
            "physical":{k:v["physical"] for k,v in report["physical_audits"].items()},
            "sample_counts":{k:v["samples"] for k,v in report["physical_audits"].items()},
            "id_comparisons":{k:v["summary"] for k,v in report["id_comparisons"].items()},
            "bucket_comparisons":{k:v["summary"] for k,v in report["bucket_comparisons"].items()},
            "ingest": report.get("ingest"), "optimize": report.get("optimize"),
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")), compact)
        print("R10_FACTORIAL_DONE", json.dumps({
            "passed":report["passed"],"errors":report["errors"],
            "id_comparisons":compact["id_comparisons"],
            "bucket_comparisons":compact["bucket_comparisons"]
        }, separators=(",", ":")), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
