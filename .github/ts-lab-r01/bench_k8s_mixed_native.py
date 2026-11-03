#!/usr/bin/env python3
"""R01: matched stock O1 native TimeSeries LC vs RAW UUID, mixed-cadence Kubernetes rows.

Same frozen 400-slot/3h churn fixture, 60s/300s/15s gauges, 3d windows,
source and binary, exact logical ID checks, native PromQL output parity.
Read-only A/B/B/A with warmups, full QueryFinish CPU/physical memory, RSS
and representative processor/CPU stacks. No ClickHouse recompilation.
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
    ".github/ts-lab-r01", ".github/ts-lab-perf",
    ".github/ts-lab-bench", ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, inner, streamed_insert, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import pct, post
from bench_filtered_selector_v4_sql import sql_string
from bench_cpu_knobs import capture_profiles
from bench_native_multiclient_stock_20261103 import (
    physical, server_status_pid, RSSMonitor, complete_logs, sha256, save
)
from generate_k8s_churn_fixture import expected_counts, LIFE, PER_DAY, BASE

SOURCE_SHA = "52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA = "f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
LC = "tslab_r01_k8s_lc"
RAW = "tslab_r01_k8s_raw"
HOST = "http://127.0.0.1:18191"
DAY = 86400
WARMUPS = 2
MEASURED = 6
CASES = {
    "node_broad": ("tslab_node_cpu_ratio", "", "project", 8),
    "pod_namespace": ("tslab_pod_cpu_ratio", '{namespace="team-000"}', "namespace", 1),
    "pod_broad": ("tslab_pod_cpu_ratio", "", "namespace", 16),
    "burst15": ("tslab_pod_cpu_ratio", '{scrape="15s"}', "namespace", 4),
}


def query(table, case, begin, end):
    metric, matcher, group, _ = CASES[case]
    expr = f"avg by({group})(avg_over_time({metric}{matcher}[1d]))"
    return (
        f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
        f"{begin},{end},86400) SETTINGS max_threads=4,"
        "max_memory_usage=3500000000,log_queries=1,"
        "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )


def jsonrows(text):
    return [json.loads(v) for v in sql(text + " FORMAT JSONEachRow").splitlines() if v.strip()]


def ddl():
    sql(f"CREATE TABLE {LC} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
    sql(
        f"CREATE TABLE {RAW} ENGINE=TimeSeries "
        "SETTINGS recent_samples_ttl_seconds=0 "
        "SAMPLES INNER COLUMNS (id Tuple(UInt64, UUID)) "
        "TAGS INNER COLUMNS ("
        "id Tuple(UInt64, UUID) DEFAULT tuple(sipHash64(metric_name),"
        "reinterpretAsUUID(sipHash128(tags))),"
        "metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
        "GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64, UUID))"
    )


def profile_rows(ids):
    try:
        return capture_profiles(ids)
    except Exception as e:
        return {"profile_error": str(e)[:1500]}


def run_phase(case, days, end, table, order, pid, reference, variant_label=None):
    begin = end - (days - 1) * DAY
    tag = variant_label if variant_label is not None else ("lc" if table == LC else "raw")
    prefix = f"r01k8s_{case}_{tag}_{order}_{os.getenv('GITHUB_RUN_ID','local')}_"
    statement = query(table, case, begin, end)
    phase = {"case": case, "variant": tag, "phase": order, "trials": [],
             "query": statement}
    with RSSMonitor(pid) as observer:
        for repeat in range(WARMUPS + MEASURED):
            qid = f"{prefix}{repeat}"
            start = time.perf_counter()
            client_ms, raw = post(HOST, statement, qid)
            result = normalize_any(raw)
            assert_same(reference, result, case, f"{tag}_phase{order}_trial{repeat}")
            phase["trials"].append({
                "qid": qid,
                "client_ms": client_ms,
                "warmup": repeat < WARMUPS,
                "client_elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            })
    phase["rss"] = observer.report()
    if phase["rss"]["observer_overhead_fraction"] > 0.02:
        raise AssertionError("RSS sampling overhead invalid")
    qids = {item["qid"] for item in phase["trials"]}
    logs = complete_logs(prefix, qids)
    for trial in phase["trials"]:
        row = logs[trial["qid"]]
        ev = row.get("ProfileEvents", {})
        trial.update({
            "server_ms": row["query_duration_ms"],
            "cpu_ms": round(ev.get("OSCPUVirtualTimeMicroseconds", 0) / 1000, 3),
            "runnable_ms": round(ev.get("OSCPUWaitMicroseconds", 0) / 1000, 3),
            "read_rows": row["read_rows"],
            "read_bytes": row["read_bytes"],
            "selected_marks": ev.get("SelectedMarks"),
            "compressed_read_bytes": ev.get("ReadCompressedBytes"),
            "query_peak_memory_bytes": row["memory_usage"],
        })
    chosen = phase["trials"][WARMUPS:]
    phase["summary"] = {
        "p50_ms": pct([t["client_ms"] for t in chosen], .50),
        "p95_ms": pct([t["client_ms"] for t in chosen], .95),
        "cpu_ms": round(statistics.median(t["cpu_ms"] for t in chosen), 3),
        "runnable_ms": round(statistics.median(t["runnable_ms"] for t in chosen), 3),
        "median_read_rows": statistics.median(t["read_rows"] for t in chosen),
        "median_read_bytes": statistics.median(t["read_bytes"] for t in chosen),
        "median_compressed_bytes": statistics.median(
            t["compressed_read_bytes"] or 0 for t in chosen
        ),
        "query_memory_max_bytes": max(t["query_peak_memory_bytes"] for t in chosen),
        "peak_observed_rss_bytes": phase["rss"]["rss_observed_max_bytes"],
        "observer_overhead": phase["rss"]["observer_overhead_fraction"],
    }
    phase["profile_qid"] = phase["trials"][WARMUPS]["qid"]
    print("R01_NATIVE_PHASE", json.dumps({
        "case": case, "phase": order, "variant": tag,
        "summary": phase["summary"],
    }, separators=(",", ":")), flush=True)
    return phase


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--pid-file", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    p.add_argument("--expected-source-binary-sha", default=BINARY_SHA)
    args = p.parse_args()
    out = {
        "question_id": "R01-k8s-mixed-v1", "source_sha": SOURCE_SHA,
        "expected_binary_sha": args.expected_source_binary_sha,
        "cases": {}, "errors": [],
        "schema": {"baseline": LC, "candidate": RAW},
    }
    save(args.output, out)
    try:
        manifest = json.loads(pathlib.Path(args.manifest).read_text())
        days = int(manifest["days"])
        slots = int(manifest["node_slots"])
        expected_rows, expected_samples = expected_counts(days, slots)
        if (manifest["schema_version"] != 1 or manifest["base"] != BASE or
            manifest["rows_by_tier"] != expected_rows or
            manifest["samples_by_tier"] != expected_samples or
            manifest["series"] != sum(expected_rows.values()) or
            manifest["samples"] != sum(expected_samples.values()) or
            days != 3 or slots != 400 or manifest["sha256"] != sha256(args.fixture)):
            raise AssertionError("R01 frozen fixture or distribution identity mismatch")
        out["fixture_manifest"] = manifest
        pid = server_status_pid(args.pid_file)
        out["server_pid"] = pid
        ddl()
        out["ingest"] = {}
        for table in (LC, RAW):
            out["ingest"][table] = streamed_insert(table, args.fixture)
            if out["ingest"][table]["rows"] != manifest["series"]:
                raise AssertionError((table, "Incomplete ingest", out["ingest"][table]))
        out["optimize"] = {t: optimize_table(t) for t in (LC, RAW)}
        for t, result in out["optimize"].items():
            if any(isinstance(value, dict) for value in result.values()):
                raise AssertionError((t, "OPTIMIZE did not complete", result))
        wait_no_merges()
        out["physical_before"] = {t: physical(t) for t in (LC, RAW)}
        out["logical_identity"] = {}
        for table in (LC, RAW):
            phys = out["physical_before"][table]
            id_name = ("LowCardinality(UUID)" if table == LC else "Tuple(UInt64, UUID)")
            for layer in ("tags", "samples", "timeranges"):
                if id_name not in phys[layer]["id_column"]:
                    raise AssertionError((table, layer, "Unexpected ID type", phys[layer]["id_column"]))
            if int(phys["tags"]["parts"]["rows"]) != manifest["series"]:
                raise AssertionError((table, "Tag series count mismatch", phys["tags"]))
            # v11 SAMPLES uses an hourly Array(Tuple(timestamp,value)) bucket.
            # A physical row is a bucket, NOT an individual sample. Compare
            # total sample elements and physical bucket rows separately.
            sample_table = inner(table, "samples")
            counted = jsonrows(
                "SELECT count() AS bucket_rows,sum(length(samples)) AS total_samples "
                "FROM " + sample_table
            )[0]
            out.setdefault("sample_point_audit", {})[table] = counted
            if int(counted["total_samples"]) != manifest["samples"]:
                raise AssertionError((table, "Wrong sample array element count", counted))
            if int(counted["bucket_rows"]) != int(phys["samples"]["parts"]["rows"]):
                raise AssertionError((table, "Physical bucket row count does not match", counted))
            tag = inner(table, "tags")
            out["logical_identity"][table] = jsonrows(
                "SELECT count() AS n,sum(sipHash64(toString(id))) AS digest,"
                "countIf(metric_name='tslab_node_cpu_ratio') AS nodes,"
                "countIf(metric_name='tslab_pod_cpu_ratio') AS pods FROM " + tag
            )[0]
            if int(out["logical_identity"][table]["nodes"]) != expected_rows["node60"]:
                raise AssertionError((table, "Wrong node metric population"))
        if out["logical_identity"][LC] != out["logical_identity"][RAW]:
            raise AssertionError(("Logical series IDs mismatch", out["logical_identity"]))
        save(args.output, out)
        end = int(manifest["end"])
        begin = end - (days - 1) * DAY
        expected_ticks = {begin + i * DAY for i in range(days)}
        for case in args.cases:
            expected_points = days * CASES[case][3]
            ref_id = f"r01oracle_{case}_{os.getenv('GITHUB_RUN_ID','local')}"
            _, raw = post(HOST, query(LC, case, begin, end), ref_id)
            reference = normalize_any(raw)
            if (len(reference) != expected_points or
                {timestamp for _, timestamp in reference} != expected_ticks):
                raise AssertionError((case, "wrong output cardinality/grid", len(reference),
                                      expected_points, expected_ticks))
            parts = []
            out["cases"][case] = {"phases": parts, "expected_points": expected_points}
            for order, table in enumerate((LC, RAW, RAW, LC)):
                phase = run_phase(case, days, end, table, order, pid, reference)
                parts.append(phase)
                save(args.output, out)
            left = [x["summary"] for x in parts if x["variant"] == "lc"]
            right = [x["summary"] for x in parts if x["variant"] == "raw"]
            avg = lambda xs, name: statistics.mean(x[name] for x in xs)
            baseline = avg(left, "p50_ms")
            cand = avg(right, "p50_ms")
            drift = abs(left[0]["p50_ms"] - left[1]["p50_ms"]) / baseline
            summary = {
                "case": case,
                "lc_p50_ms": round(baseline, 3),
                "raw_p50_ms": round(cand, 3),
                "raw_vs_lc_speedup": round(baseline / cand, 4),
                "lc_p95_ms": round(avg(left, "p95_ms"), 3),
                "raw_p95_ms": round(avg(right, "p95_ms"), 3),
                "lc_cpu_ms": round(avg(left, "cpu_ms"), 3),
                "raw_cpu_ms": round(avg(right, "cpu_ms"), 3),
                "lc_median_read_rows": round(avg(left, "median_read_rows"), 1),
                "raw_median_read_rows": round(avg(right, "median_read_rows"), 1),
                "control_drift": round(drift, 5),
                "valid_control": drift <= 0.25,
            }
            out["cases"][case]["summary"] = summary
            out["cases"][case]["profiles"] = profile_rows([
                parts[0]["profile_qid"], parts[1]["profile_qid"]
            ])
            if not summary["valid_control"]:
                out["errors"].append({"case": case, "control_drift": drift})
            print("R01_NATIVE_COMPARE", json.dumps(summary, separators=(",", ":")), flush=True)
            save(args.output, out)
        out["physical_after"] = {t: physical(t) for t in (LC, RAW)}
        if out["physical_before"] != out["physical_after"]:
            out["errors"].append({"physical_parts_changed": True})
        out["passed"] = not out["errors"]
    except Exception as exc:
        out["passed"] = False
        out["errors"].append({
            "fatal": str(exc)[:2500],
            "traceback": traceback.format_exc()[-4500:],
        })
    finally:
        save(args.output, out)
        compact = {
            "question_id": out["question_id"],
            "passed": out["passed"], "errors": out["errors"],
            "fixture": out.get("fixture_manifest"),
            "physical_before": out.get("physical_before"),
            "physical_after": out.get("physical_after"),
            "logical_identity": out.get("logical_identity"),
            "cases": {
                k: v.get("summary") for k, v in out["cases"].items()
            },
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")), compact)
        print("R01_NATIVE_DONE", json.dumps({
            "passed": out["passed"],
            "errors": out["errors"], "cases": compact["cases"]
        }, separators=(",", ":")), flush=True)
    if not out["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
