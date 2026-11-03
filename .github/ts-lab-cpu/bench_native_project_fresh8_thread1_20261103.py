#!/usr/bin/env python3
"""Fresh-process project14d 8-client max_threads=1 capacity control.

Same pinned stock O1, SAME frozen LC and RAW on-disk inner data. Restart the
server A/B/B/A, one fresh process per phase. Count OOM as observed capacity
failure, save both successful and failed variants. 2 warmup and 8 measured
waves per phase, exact full native TimeSeries PromQL equality on successes.
Do not label glibc retention as a leak without allocator/malloc evidence.
"""
import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import traceback

sys.path[:0] = [".github/ts-lab-perf", ".github/ts-lab-bench", ".github/ts-lab-cpu"]
from bench_granularity_v11 import inner, sql, wait_no_merges
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import post
from bench_native_multiclient_stock_20261103 import (
    LC, RAW, FIXTURE_SHA, SOURCE_SHA, HOST, physical, run_phase,
    save, server_status_pid, sha256, statement
)

EXPECTED = 266
CASE = "project"
CLIENTS = 8

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--binary", required=True)
    p.add_argument("--restart-script", required=True)
    p.add_argument("--pid-file", required=True)
    p.add_argument("--fixture", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    import bench_native_multiclient_stock_20261103 as stock
    stock.MAX_THREADS_PER_QUERY = 1
    assert 'max_threads=1' in stock.statement(LC, CASE, 1234567890)
    report = {
        "case": CASE, "clients": CLIENTS, "max_threads_per_query": 1,
        "source_sha": SOURCE_SHA,
        "fixture_sha": FIXTURE_SHA, "comparison": "fresh process each phase",
        "phases": [], "errors": [], "valid_complete_abba": False,
    }
    save(args.output, report)
    try:
        manifest = json.loads(pathlib.Path(args.manifest).read_text())
        if manifest["sha256"] != FIXTURE_SHA or manifest["series"] != 1330000:
            raise AssertionError("Fixture identity mismatch")
        if sha256(args.fixture) != FIXTURE_SHA:
            raise AssertionError("Fixture bytes SHA mismatch")
        end = int(manifest["end"])
        ticks = {end - i * 86400 for i in range(14)}
        expected_query = statement(LC, CASE, end)
        report["native_promql_lc"] = expected_query
        report["physical_before"] = {t: physical(t) for t in (LC, RAW)}
        report["inner_id_types"] = {
            t: {kind: val["id_column"] for kind, val in report["physical_before"][t].items()}
            for t in (LC, RAW)
        }
        ref = None
        for phase_i, variant in enumerate(("lc", "raw", "raw", "lc")):
            phase = {
                "order": phase_i, "variant": variant,
                "outcome": "not_started",
            }
            report["phases"].append(phase)
            save(args.output, report)
            try:
                subprocess.run(
                    [args.restart_script, args.binary, f"fresh8_t1_{phase_i}_{variant}"],
                    check=True, timeout=240
                )
                pid = server_status_pid(args.pid_file)
                phase["server_pid"] = pid
                wait_no_merges()  # stop merges again after restart
                if {t: physical(t) for t in (LC, RAW)} != report["physical_before"]:
                    raise AssertionError("Physical inner part layout changed during restart")
                table = LC if variant == "lc" else RAW
                # Equal preflight oracle/query warming for every fresh process.
                _, output = post(
                    HOST, statement(table, CASE, end),
                    f"clpar_fresh_oracle_{phase_i}_{variant}_{os.getenv('GITHUB_RUN_ID','local')}"
                )
                actual = normalize_any(output)
                if len(actual) != EXPECTED or {t for _, t in actual} != ticks:
                    raise AssertionError(f"Unexpected native PromQL points: {len(actual)}")
                if ref is None:
                    ref = actual
                else:
                    assert_same(ref, actual, CASE, "fresh-process-oracle")
                block = run_phase(
                    CLIENTS, variant, phase_i, CASE, end, pid,
                    ref, EXPECTED, ticks
                )
                phase.update({
                    "outcome": "pass",
                    "metrics": block["metrics"],
                    "rss": block["rss"],
                    "profile_qid": block["profile_qid"],
                    "waves": block["waves"],
                    "query": block["query"],
                })
                print("FRESH8_PHASE", json.dumps({
                    "phase": phase_i, "variant": variant, "outcome": "pass",
                    "metrics": block["metrics"]
                }, separators=(",", ":")), flush=True)
            except Exception as e:
                phase.update({
                    "outcome": "failed",
                    "error": str(e)[:3000],
                    "traceback": traceback.format_exc()[-3500:],
                })
                report["errors"].append({
                    "phase": phase_i, "variant": variant,
                    "error": str(e)[:800]
                })
                print("FRESH8_PHASE", json.dumps({
                    "phase": phase_i, "variant": variant,
                    "outcome": "failed", "error": phase["error"]
                }, separators=(",", ":")), flush=True)
            save(args.output, report)
        successes = [p for p in report["phases"] if p["outcome"] == "pass"]
        report["valid_complete_abba"] = len(successes) == 4
        report["success_variants"] = [p["variant"] for p in successes]
        if len(successes) == 4:
            lc = [x["metrics"] for x in successes if x["variant"] == "lc"]
            raw = [x["metrics"] for x in successes if x["variant"] == "raw"]
            median = lambda array: statistics.mean(x["p50_client_ms"] for x in array)
            drift = abs(lc[0]["p50_client_ms"] - lc[1]["p50_client_ms"]) / median(lc)
            report["summary"] = {
                "lc_p50_ms": round(median(lc), 3),
                "raw_p50_ms": round(median(raw), 3),
                "raw_vs_lc_speedup": round(median(lc) / median(raw), 4),
                "lc_p95_ms": round(statistics.mean(x["p95_client_ms"] for x in lc), 3),
                "raw_p95_ms": round(statistics.mean(x["p95_client_ms"] for x in raw), 3),
                "lc_cpu_ms": round(statistics.mean(x["mean_cpu_per_request_ms"] for x in lc), 3),
                "raw_cpu_ms": round(statistics.mean(x["mean_cpu_per_request_ms"] for x in raw), 3),
                "lc_rss_max_bytes": max(x["rss_max_observed_bytes"] for x in lc),
                "raw_rss_max_bytes": max(x["rss_max_observed_bytes"] for x in raw),
                "control_drift": round(drift, 5),
                "valid": drift <= 0.25,
            }
            if drift > 0.25:
                report["errors"].append({"control_drift": drift})
            report["physical_after"] = {t: physical(t) for t in (LC, RAW)}
            if report["physical_after"] != report["physical_before"]:
                report["errors"].append({"physical_parts_changed": True})
        report["passed"] = report["valid_complete_abba"] and not report["errors"]
    except Exception as e:
        report["errors"].append({
            "fatal": str(e)[:3000], "traceback": traceback.format_exc()[-3500:]
        })
        report["passed"] = False
    finally:
        save(args.output, report)
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")), {
            "case": CASE,
            "passed": report["passed"],
            "valid_complete_abba": report["valid_complete_abba"],
            "summary": report.get("summary"),
            "phases": [{
                "order": p["order"], "variant": p["variant"],
                "outcome": p["outcome"],
                "metrics": p.get("metrics"),
                "error": p.get("error"),
                "rss": p.get("rss"),
            } for p in report["phases"]],
            "errors": report["errors"],
        })
        print("FRESH8_DONE", json.dumps({
            "passed": report["passed"],
            "summary": report.get("summary"),
            "errors": report["errors"],
        }, separators=(",", ":")), flush=True)
    if not report["passed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
