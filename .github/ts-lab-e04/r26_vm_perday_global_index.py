#!/usr/bin/env python3
"""R26: VictoriaMetrics v1.134.0 per-day vs global index *read path* ABBA.

Same source/binary, same indexed base snapshot cloned into fresh processes.
The base dataset was ingested with the default per-day index enabled;
the global index also exists in this default layout. Only the
-disablePerDayIndex read-path switch varies after ingestion.
Both modes disable response cache and restrict tag-filter result cache to 1 B.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import urllib.parse
import urllib.request

from r25_vm_index_cache_capacity import (
    CASES, SOURCE_SHA, FIXTURE_SHA, VM_ARCHIVE_SHA, DAY,
    HOST, sha256, fingerprint_tree, metrics, vm_process_cpu, vm_mem,
    query_expression, assert_equal, trace_snippets, stop_vm, percentile, pct_drift
)

ORDER = ["global", "day", "day", "global"]
WARMUPS = 8
MEASURED = 48
WINDOWS = ("one_tick", "three_tick")


def query_r26(case, window, end, trace=False):
    if window not in WINDOWS:
        raise AssertionError(window)
    start = end if window == "one_tick" else end - 2 * DAY
    ticks = (end,) if window == "one_tick" else (start, start + DAY, end)
    params = {
        "query": query_expression(case), "start": start,
        "end": end, "step": DAY, "nocache": "1",
    }
    if trace:
        params["trace"] = "1"
    url = HOST + "/api/v1/query_range?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter_ns()
    with urllib.request.urlopen(url, timeout=90) as response:
        data = json.loads(response.read())
    elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
    if data.get("status") != "success":
        raise AssertionError(("VM HTTP native query failed", case, window, data))
    rows = data.get("data", {}).get("result", [])
    if len(rows) != CASES[case][3]:
        raise AssertionError(("missing/extra groups", case, window, len(rows)))
    normalized = []
    for row in rows:
        labels = tuple(sorted(row["metric"].items()))
        values = tuple((int(float(t)), float(value)) for t, value in row["values"])
        if tuple(t for t, _ in values) != ticks or not all(math.isfinite(v) for _, v in values):
            raise AssertionError(("incorrect timestamps or nonfinite", case, window, labels, values))
        normalized.append((labels, values))
    normalized.sort()
    return elapsed_ms, tuple(normalized), data.get("trace")


def start_vm(binary, data, global_index, logfile):
    args = [
        str(binary), "-storageDataPath=" + str(data),
        "-httpListenAddr=127.0.0.1:8430",
        "-retentionPeriod=6", "-search.maxUniqueTimeseries=165000",
        "-search.disableCache", "-memory.allowedBytes=4294967296",
        "-storage.cacheSizeIndexDBTagFilters=1",
    ]
    if global_index:
        args.append("-disablePerDayIndex")
    with open(logfile, "w") as output:
        proc = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError(("VM did not start", proc.returncode, str(logfile)))
        try:
            with urllib.request.urlopen(HOST + "/health", timeout=1) as response:
                if response.status == 200:
                    return proc, args
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    proc.wait(timeout=15)
    raise RuntimeError("VM readiness timeout")


def path_matches(trace, mode):
    s = "\n".join(trace_snippets(trace)).lower()
    term = "search metric ids in the global index" if mode == "global" else "search metric ids in the per-day index"
    return term in s


def summarize(report):
    ret = {}
    primary_ok = 0
    for case in CASES:
        for window in WINDOWS:
            key = case + "_" + window
            g = [p["results"][key] for p in report["phases"] if p["mode"] == "global"]
            d = [p["results"][key] for p in report["phases"] if p["mode"] == "day"]
            if len(g) != 2 or len(d) != 2:
                raise AssertionError("Missing ABBA phases: " + key)
            mean = lambda arr, k: statistics.mean(x[k] for x in arr)
            mg, md = mean(g, "p50_ms"), mean(d, "p50_ms")
            cg, cd = mean(g, "cpu_ms_per_request"), mean(d, "cpu_ms_per_request")
            drift = {
                "global_wall": pct_drift(g[0]["p50_ms"], g[1]["p50_ms"]),
                "day_wall": pct_drift(d[0]["p50_ms"], d[1]["p50_ms"]),
                "global_cpu": pct_drift(g[0]["cpu_ms_per_request"], g[1]["cpu_ms_per_request"]),
                "day_cpu": pct_drift(d[0]["cpu_ms_per_request"], d[1]["cpu_ms_per_request"]),
            }
            trace_ok = all(x["path_verified"] for x in g+d)
            stable = max(drift.values()) <= 0.25
            valid = trace_ok and stable and all(x["cache_resets"] == 0 for x in g+d)
            primary = case in ("node_broad", "pod_broad")
            if primary and valid:
                primary_ok += 1
            ret[key] = {
                "global_p50_ms": round(mg, 5),
                "perday_p50_ms": round(md, 5),
                "global_div_perday_p50": round(mg/md, 5),
                "global_cpu_ms_per_request": round(cg, 5),
                "perday_cpu_ms_per_request": round(cd, 5),
                "global_div_perday_cpu": round(cg/cd, 5),
                "trace_path_verified": trace_ok,
                "control_drifts": drift,
                "valid": valid,
                "primary_case": primary,
            }
    report["comparison"] = ret
    report["validity"]["valid_primary_cases"] = primary_ok
    report["validity"]["total_primary_cases"] = 2 * len(WINDOWS)
    report["decision"] = "ACCEPT_DIAGNOSTIC_PATH_ABBA" if primary_ok == 2 * len(WINDOWS) else "PARTIAL_OR_INVALID_PRIMARY_CONTROLS"
    return primary_ok == 2 * len(WINDOWS)


def main():
    ap = argparse.ArgumentParser()
    for key in ["binary", "base-data", "manifest", "workdir", "output"]:
        ap.add_argument("--" + key, required=True)
    args = ap.parse_args()
    binary = Path(args.binary).resolve()
    base = Path(args.base_data).resolve()
    workdir = Path(args.workdir).resolve()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text())
    if manifest["sha256"] != FIXTURE_SHA or manifest["samples"] != 2488320 or manifest["series"] != 28896:
        raise AssertionError("Different fixture")
    base_fp = fingerprint_tree(base)
    report = {
        "schema_version": 1, "question_id": "R26-vm-perday-vs-global-query-index-path-v1",
        "source_sha": SOURCE_SHA, "fixture_sha256": FIXTURE_SHA,
        "vm_binary_sha256": sha256(binary), "vm_archive_sha256": VM_ARCHIVE_SHA,
        "source_snapshot_fingerprint": base_fp, "order": ORDER,
        "only_changed_flag": "-disablePerDayIndex (only in global mode)",
        "other_flags": {
            "storage.cacheSizeIndexDBTagFilters": 1,
            "search.disableCache": True,
            "memory.allowedBytes": 4294967296,
            "search.maxUniqueTimeseries": 165000,
        },
        "warmups": WARMUPS, "measured_per_case": MEASURED,
        "cases": {case + "_" + window: {
            "expr": query_expression(case), "eval_ticks": window,
        } for case in CASES for window in WINDOWS},
        "phases": [], "validity": {"all_outputs_equal": True, "all_snapshot_clones_equal": True},
        "run_id": os.getenv("GITHUB_RUN_ID"), "decision": "UNFINISHED",
    }
    end = int(manifest["end"])
    oracle = {}
    try:
        for i, mode in enumerate(ORDER):
            data = workdir / f"r26-{i}-{mode}"
            if data.exists():
                raise AssertionError("Run working directory must be fresh")
            shutil.copytree(base, data)
            if fingerprint_tree(data) != base_fp:
                raise AssertionError("Different source storage clone")
            log = output.parent / f"vm-{i}-{mode}.log"
            proc, cmd = start_vm(binary, data, mode == "global", log)
            phase = {
                "index": i, "mode": mode, "cmd": cmd, "pid": proc.pid,
                "data_fingerprint": base_fp, "log": str(log), "results": {},
                "started_ns": time.time_ns(),
            }
            report["phases"].append(phase)
            try:
                for case in CASES:
                    for window in WINDOWS:
                        key = case + "_" + window
                        for _ in range(WARMUPS):
                            _, rows, _ = query_r26(case, window, end)
                            if key not in oracle:
                                oracle[key] = rows
                            else:
                                assert_equal(oracle[key], rows, key)
                        before = metrics()
                        cpu0 = vm_process_cpu(proc.pid)
                        times = []
                        for _ in range(MEASURED):
                            t, rows, _ = query_r26(case, window, end)
                            assert_equal(oracle[key], rows, key)
                            times.append(t)
                        cpu1 = vm_process_cpu(proc.pid)
                        after = metrics()
                        _, rows, trace = query_r26(case, window, end, trace=True)
                        assert_equal(oracle[key], rows, key)
                        path_verified = path_matches(trace, mode)
                        cr = int(after["vm_cache_requests_total"] - before["vm_cache_requests_total"])
                        cm = int(after["vm_cache_misses_total"] - before["vm_cache_misses_total"])
                        resets = int(after["vm_cache_resets_total"] - before["vm_cache_resets_total"])
                        entry = {
                            "p50_ms": round(percentile(times, 50), 5),
                            "p95_ms": round(percentile(times, 95), 5),
                            "p99_ms": round(percentile(times, 99), 5),
                            "cpu_ms_per_request": round((cpu1-cpu0) * 1000 / MEASURED, 5),
                            "cache_requests": cr, "cache_misses": cm, "cache_resets": resets,
                            "cache_bytes": int(after["vm_cache_size_bytes"]),
                            "path_verified": path_verified,
                            "trace_snippets": trace_snippets(trace),
                            "output_digest": hashlib.sha256(repr(oracle[key]).encode()).hexdigest(),
                        }
                        phase["results"][key] = entry
                        print("R26_CASE", json.dumps({
                            "phase": i, "mode": mode, "case": key,
                            "p50": entry["p50_ms"], "cpu_ms": entry["cpu_ms_per_request"],
                            "req": cr, "miss": cm, "path_verified": path_verified,
                        }, sort_keys=True), flush=True)
                phase["memory_at_end"] = vm_mem(proc.pid)
            finally:
                stop_vm(proc)
                phase["end_ns"] = time.time_ns()
                phase["exitcode"] = proc.returncode
                output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        valid = summarize(report)
        print("R26_SUMMARY", json.dumps(report["comparison"], sort_keys=True), flush=True)
        if not valid:
            raise AssertionError("R26 insufficient valid primary A/A controls/path trace")
    except Exception as exc:
        if report["decision"] == "UNFINISHED":
            report["decision"] = "CORRECTNESS_FAIL_OR_INVALID_METHOD"
        report["failure"] = repr(exc)
        raise
    finally:
        output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        print("R26_DECISION", report["decision"], flush=True)


if __name__ == "__main__":
    main()
