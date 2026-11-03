#!/usr/bin/env python3
"""R28 native TimeSeries query-slot admission capacity ABBA.

Fresh ClickHouse process per phase; unchanged frozen 1.33m Jenkins RAW UUID
table and stock LLVM22 O1 executable. Eight clients each send the exact
14-day native PromQL. Only the admission limit varies (8/2/4/4/2/8).
Every HTTP response is recorded; an expected baseline OOM is *not* silently
converted to a completed/valid throughput observation.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-bench", ".github/ts-lab-perf", ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, querylogs, wait_no_merges
from bench_bucket_cxx_abca import normalize_any, assert_same
from bench_jenkins_querypath import post, pct
from bench_native_multiclient_stock_20261103 import (
    RAW, SOURCE_SHA, FIXTURE_SHA, HOST, physical, server_status_pid,
    rss_bytes, RSSMonitor, save, sha256, statement, complete_logs,
)

ORDER = (8, 2, 4, 4, 2, 8)
CLIENTS = 8
WARMUP_WAVES = 2
MEASURED_WAVES = 8
POINTS = 266
MEM_LIMIT = 4_800_000_000
EXPECTED_BIN = "f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
RUN = os.environ.get("GITHUB_RUN_ID", "local")


def native_query(end):
    # Use the same native selector, evaluation grid, per-query max_threads=2,
    # per-query memory cap and output format as historical fresh8 controls.
    original = statement(RAW, "project", end)
    token = " FORMAT JSONEachRow"
    assert original.count(token) == 1, "Unknown query layout"
    assert "max_threads=2" in original and "max_memory_usage=1800000000" in original
    return original.replace(
        token,
        ",workload='all',workload_admission_timeout_ms=90000" + token,
    )


def process_cpu_s(pid):
    raw = pathlib.Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def classify_error(text):
    if "MEMORY_LIMIT_EXCEEDED" in text or "Code: 241" in text:
        return "OOM"
    if "QUERY_SLOT_ACQUISITION_TIMEOUT" in text:
        return "ADMISSION_TIMEOUT"
    if "Workload limit" in text or "TOO_MANY_SIMULTANEOUS_QUERIES" in text:
        return "ADMISSION_REJECT"
    if "AssertionError" in text or "wrong output" in text:
        return "PARITY_FAIL"
    return "HTTP_OR_QUERY_ERROR"


def execute_wave(pool, query, phase, wave_no, reference, ticks):
    barrier = threading.Barrier(CLIENTS + 1, timeout=15)
    phase_prefix = f"r28_{RUN}_{phase}"
    def client(idx):
        qid = f"{phase_prefix}_{wave_no}_{idx}"
        barrier.wait()
        start = time.perf_counter()
        result = {
            "query_id": qid, "wave": wave_no, "warmup": wave_no < WARMUP_WAVES,
        }
        try:
            _, data = post(HOST, query, qid)
            values = normalize_any(data)
            if len(values) != POINTS or {stamp for _, stamp in values} != ticks:
                raise AssertionError(
                    f"wrong output grid: {len(values)} points, expected {POINTS}"
                )
            assert_same(reference, values, "project", "R28")
            result["status"] = "OK"
            result["points"] = len(values)
        except Exception as exc:
            result["status"] = (
                "PARITY_FAIL" if isinstance(exc, AssertionError)
                else classify_error(str(exc))
            )
            result["error"] = str(exc)[:1200]
        result["latency_ms"] = round(
            1000 * (time.perf_counter() - start), 3
        )
        return result

    futures = [pool.submit(client, idx) for idx in range(CLIENTS)]
    barrier.wait()
    began = time.perf_counter()
    trials = [future.result(timeout=390) for future in futures]
    elapsed = time.perf_counter() - began
    assert len(trials) == CLIENTS
    return {
        "index": wave_no,
        "warmup": wave_no < WARMUP_WAVES,
        "duration_s": round(elapsed, 6),
        "trials": trials,
        "success": sum(x["status"] == "OK" for x in trials),
        "failed": sum(x["status"] != "OK" for x in trials),
        "failure_types": sorted({x["status"] for x in trials if x["status"] != "OK"}),
    }


def require_frozen(expected):
    now = physical(RAW)
    if now != expected:
        raise AssertionError(("Frozen RAW physical parts changed", expected, now))
    return now


def measure_phase(phase_idx, cap, binary, restart_script, pid_file, end,
                  reference, ticks, frozen, report, output):
    phase = {
        "phase": phase_idx,
        "slots": cap,
        "clients": CLIENTS,
        "outcome": "not_started",
        "waves": [],
        "trials": [],
    }
    report["phases"].append(phase)
    save(output, report)
    try:
        subprocess.run(
            [restart_script, binary, f"r28_{phase_idx}_slots{cap}"],
            check=True, timeout=240,
        )
        pid = server_status_pid(pid_file)
        phase["server_pid"] = pid
        wait_no_merges()
        require_frozen(frozen)
        sql("CREATE RESOURCE IF NOT EXISTS query (QUERY)")
        sql("CREATE WORKLOAD IF NOT EXISTS all SETTINGS "
            "max_concurrent_queries=8,max_waiting_queries=16")
        sql("CREATE OR REPLACE WORKLOAD all SETTINGS "
            f"max_concurrent_queries={cap},max_waiting_queries=16")
        resource = sql(
            "SELECT count() FROM system.scheduler "
            "WHERE resource='query' AND type='inflight_limit' "
            f"AND max_requests={cap} FORMAT TSV"
        ).strip()
        if int(resource) < 1:
            raise AssertionError(f"Expected workload resource limit {cap}")
        query = native_query(end)
        phase["query"] = query
        phase["rss_before"] = rss_bytes(pid)
        phase["process_cpu_start_s"] = process_cpu_s(pid)
        timed_cpu_start = None
        timed_cpu_end = None
        with RSSMonitor(pid) as observer:
            with concurrent.futures.ThreadPoolExecutor(max_workers=CLIENTS) as pool:
                for wave_idx in range(WARMUP_WAVES + MEASURED_WAVES):
                    if wave_idx == WARMUP_WAVES:
                        timed_cpu_start = process_cpu_s(pid)
                    wave = execute_wave(
                        pool, query, phase_idx, wave_idx, reference, ticks
                    )
                    phase["waves"].append(wave)
                    phase["trials"].extend(wave["trials"])
                    save(output, report)
                    print("R28_WAVE", json.dumps({
                        "phase": phase_idx, "slots": cap, "wave": wave_idx,
                        "duration_s": wave["duration_s"],
                        "success": wave["success"],
                        "failure_types": wave["failure_types"],
                    }, separators=(",", ":")), flush=True)
                    # Do not continue an OOM-contaminated phase. Other phases
                    # still run after a full fresh process restart.
                    if wave["failed"]:
                        break
                if timed_cpu_start is not None:
                    timed_cpu_end = process_cpu_s(pid)
        phase["rss_observer"] = observer.report()
        phase["rss_after"] = rss_bytes(pid)
        phase["process_cpu_total_s"] = round(
            process_cpu_s(pid) - phase["process_cpu_start_s"], 4
        )
        phase["process_cpu_measured_s"] = (
            round(timed_cpu_end - timed_cpu_start, 4)
            if timed_cpu_start is not None else None
        )
        require_frozen(frozen)
        failures = [x for x in phase["trials"] if x["status"] != "OK"]
        phase["outcome"] = (
            "COMPLETE" if not failures and len(phase["waves"]) ==
            WARMUP_WAVES + MEASURED_WAVES
            else "OOM" if any(x["status"] == "OOM" for x in failures)
            else "PARITY_FAIL" if any(x["status"] == "PARITY_FAIL" for x in failures)
            else "INCOMPLETE"
        )
        if observer.report()["observer_overhead_fraction"] > 0.02:
            phase["outcome"] = "INVALID_OBSERVER_OVERHEAD"
        valid_trials = [x for x in phase["trials"] if x["status"] == "OK"]
        # Log requests are not timed and do not affect the measured latency.
        if valid_trials:
            try:
                logs = complete_logs(
                    f"r28_{RUN}_{phase_idx}", {x["query_id"] for x in valid_trials},
                )
                for trial in valid_trials:
                    row = logs[trial["query_id"]]
                    events = row.get("ProfileEvents", {})
                    trial["query_finish"] = {
                        "server_ms": row["query_duration_ms"],
                        "cpu_us": int(events.get("OSCPUVirtualTimeMicroseconds", 0)),
                        "selected_marks": int(events.get("SelectedMarks", 0)),
                        "read_bytes": row["read_bytes"],
                        "read_rows": row["read_rows"],
                        "memory_usage": row["memory_usage"],
                    }
                phase["queryfinish_complete"] = True
            except Exception as exc:
                phase["queryfinish_complete"] = False
                phase["queryfinish_error"] = str(exc)[:1000]
        measured = [x for x in phase["trials"] if not x["warmup"]]
        good = [x for x in measured if x["status"] == "OK"]
        complete = (
            phase["outcome"] == "COMPLETE" and
            len(good) == CLIENTS * MEASURED_WAVES and
            phase.get("queryfinish_complete", False)
        )
        metrics = {
            "measured_requests": len(measured),
            "measured_completed": len(good),
            "measured_errors": len(measured) - len(good),
            "errors_all_waves": len(failures),
            "full_valid_window": complete,
            "process_rss_peak_observed_bytes":
                phase["rss_observer"]["rss_observed_max_bytes"],
            "process_rss_lifetime_hwm_bytes":
                phase["rss_after"].get("VmHWM"),
            "rss_observer_overhead_fraction":
                phase["rss_observer"]["observer_overhead_fraction"],
            "process_cpu_measured_s": phase["process_cpu_measured_s"],
        }
        if complete:
            latencies = [x["latency_ms"] for x in good]
            times = [x["duration_s"] for x in phase["waves"] if not x["warmup"]]
            cpus = [x["query_finish"]["cpu_us"] for x in good]
            metrics.update({
                "p50_ms": pct(latencies, 0.50),
                "p95_ms": pct(latencies, 0.95),
                "p99_ms": pct(latencies, 0.99),
                "completed_qps": round(len(good)/sum(times), 4),
                "query_cpu_mean_ms": round(statistics.mean(cpus)/1000, 3),
                "process_cpu_ms_per_request": round(
                    1000*phase["process_cpu_measured_s"]/len(good), 3
                ),
                "peak_query_tracker_bytes": max(
                    x["query_finish"]["memory_usage"] for x in good
                ),
            })
        phase["metrics"] = metrics
    except Exception as exc:
        phase["outcome"] = "INFRA_OR_PROTOCOL_FAIL"
        phase["error"] = str(exc)[:1800]
        phase["traceback"] = traceback.format_exc()[-3800:]
        report["protocol_errors"].append({
            "phase": phase_idx, "slots": cap, "error": phase["error"],
        })
    save(output, report)
    print("R28_PHASE", json.dumps({
        "phase": phase_idx, "slots": cap, "outcome": phase["outcome"],
        "metrics": phase.get("metrics", {}),
        "error": phase.get("error"),
    }, separators=(",", ":")), flush=True)


def classify_result(report):
    phases = report["phases"]
    if len(phases) != len(ORDER):
        return "INFRA_FAIL", {"reason": "Missing phases"}
    if report["protocol_errors"] or any(
        p["outcome"] in ("INFRA_OR_PROTOCOL_FAIL", "INVALID_OBSERVER_OVERHEAD")
        for p in phases
    ):
        return "INVALID_METHOD", {"errors": report["protocol_errors"]}
    if any(p["outcome"] == "PARITY_FAIL" for p in phases):
        return "CORRECTNESS_FAIL", {}
    controls = {}
    for cap in (2, 4, 8):
        pair = [p for p in phases if p["slots"] == cap]
        assert len(pair) == 2
        completed = [p for p in pair if p["metrics"]["full_valid_window"]]
        c = {"complete_phases": len(completed),
             "outcomes": [p["outcome"] for p in pair]}
        if len(completed) == 2:
            for field in ("p50_ms", "p95_ms", "completed_qps",
                          "process_cpu_ms_per_request"):
                a, b = [p["metrics"][field] for p in completed]
                c[field] = round((a+b)/2, 4)
                c[field+"_control_drift"] = round(
                    abs(a-b)/max((a+b)/2,1e-12), 5
                )
        controls[str(cap)] = c
    if any(controls[str(cap)].get("p50_ms_control_drift",0) > 0.25
           or controls[str(cap)].get("completed_qps_control_drift",0) > 0.25
           for cap in (2,4,8)):
        return "INCONCLUSIVE", controls
    if (controls["2"]["complete_phases"] == 2 and
        controls["4"]["complete_phases"] == 2 and
        controls["8"]["complete_phases"] < 2):
        return "ACCEPT_FOR_NEXT_GATE", controls
    if controls["2"]["complete_phases"] < 2:
        return "REJECT", controls
    return "INCONCLUSIVE", controls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--restart-script", required=True)
    ap.add_argument("--pid-file", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    report = {
        "question_id": "R28", "claim_id": "R28-native-query-slot-admission-v1",
        "source_sha": SOURCE_SHA, "binary_sha256": EXPECTED_BIN,
        "fixture_sha256": FIXTURE_SHA,
        "intervention": "workload QUERY slots only; 8/2/4/4/2/8",
        "clients": CLIENTS, "warmup_waves": WARMUP_WAVES,
        "measured_waves": MEASURED_WAVES, "query_threads": 2,
        "max_server_memory_bytes": MEM_LIMIT, "phases": [],
        "protocol_errors": [], "passed_protocol": False,
    }
    save(args.output, report)
    try:
        if sha256(args.binary) != EXPECTED_BIN:
            raise AssertionError("Binary SHA mismatch")
        if sha256(args.fixture) != FIXTURE_SHA:
            raise AssertionError("Fixture SHA mismatch")
        manifest = json.loads(pathlib.Path(args.manifest).read_text())
        if manifest["sha256"] != FIXTURE_SHA or manifest["series"] != 1330000:
            raise AssertionError("Invalid 1.33m/14d manifest")
        end = int(manifest["end"])
        ticks = {end-i*86400 for i in range(14)}
        frozen = physical(RAW)
        if any(layer["parts"]["part_count"] != 1 for layer in frozen.values()):
            raise AssertionError("Expected fully merged frozen inner parts")
        if any("Tuple(UInt64, UUID)" not in x["id_column"]
               for x in frozen.values()):
            raise AssertionError("Expected raw UUID on all inner targets")
        # Take a single native semantic oracle before the per-phase restarts.
        _, raw = post(HOST, statement(RAW,"project",end),
                      f"r28_ref_{RUN}")
        reference = normalize_any(raw)
        if len(reference) != POINTS or {t for _,t in reference} != ticks:
            raise AssertionError("Incorrect full-native 266-point oracle")
        report["reference_sha256"] = hashlib.sha256(
            repr(sorted(reference)).encode()
        ).hexdigest()
        report["query"] = native_query(end)
        report["frozen_parts"] = frozen
        save(args.output, report)
        for i, cap in enumerate(ORDER):
            measure_phase(
                i, cap, args.binary, args.restart_script, args.pid_file,
                end, reference, ticks, frozen, report, args.output,
            )
        report["decision"], report["comparison"] = classify_result(report)
        report["passed_protocol"] = (
            report["decision"] not in ("INVALID_METHOD", "CORRECTNESS_FAIL",
                                       "INFRA_FAIL")
        )
        report["limitations"] = [
            "O1/glibc, not a production optimized jemalloc binary",
            "Closed-loop synchronized eight-client waves; not open-loop load",
            "Client p95/p99 include workload queueing; QueryFinish is execution",
            "RSS observer is /proc status, not a direct live-heap allocator probe",
            "No VictoriaMetrics run or external engine speedup claim",
        ]
    except Exception as exc:
        report["decision"] = "INVALID_METHOD"
        report["protocol_errors"].append({
            "fatal": str(exc)[:1600],
            "traceback": traceback.format_exc()[-4000:],
        })
    finally:
        save(args.output, report)
        compact = {
            key: report.get(key) for key in (
                "question_id", "decision", "passed_protocol", "source_sha",
                "binary_sha256", "fixture_sha256", "reference_sha256",
                "comparison", "protocol_errors", "limitations",
            )
        }
        compact["phase_summaries"] = [
            {"phase": p["phase"], "slots": p["slots"],
             "outcome": p["outcome"], "metrics": p.get("metrics"),
             "error": p.get("error")}
            for p in report["phases"]
        ]
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),
             compact)
        print("R28_DONE", json.dumps(compact, separators=(",", ":")),
              flush=True)
    if not report["passed_protocol"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
