#!/usr/bin/env python3
"""R27: VM v1.134.0 global/per-day read path on canonical 95k/day Jenkins.

- Frozen 1.33m one-sample series / 14d, reused from canonical R01-like Jenkins.
- VM source binary, indexed import, result cache and resource limits unchanged.
- Four post-import cloned data processes, GLOBAL/DAY/DAY/GLOBAL ABBA.
- Independent analytical oracle: each ID has one Float64 value; group by stage
  should equal arithmetic average of the generated IDs within lookbehind.
- No ClickHouse server, no new binary compile.
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
    SOURCE_SHA, VM_ARCHIVE_SHA, HOST,
    sha256, fingerprint_tree, metrics, vm_process_cpu, vm_mem, assert_equal,
    trace_snippets, stop_vm, percentile, pct_drift,
)

FIXTURE_SHA = "8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
METRIC = "jenkins_ci_95k_daily"
PER_DAY = 95000
DAYS = 14
DAY = 86400
PHASES = ["global", "day", "day", "global"]
WARMUPS = 4
MEASURED = 24
# A registered series has exactly one value; source generator defines 38 stages.
CASES = {
    "broad1d": (1, "all"),
    "broad3d": (3, "all"),
    "broad7d": (7, "all"),
    "broad14d": (14, "all"),
    "stage1d": (1, "stage"),
    "stage14d": (14, "stage"),
    "project1d": (1, "project"),
    "project14d": (14, "project"),
    "regex14d": (14, "regex"),
}
FILTERS = {
    "all": "",
    "stage": '{stage="stage-0007"}',
    "project": '{project="project-00"}',
    "regex": '{branch=~"feature-.*"}',
}


def query_expr(case):
    ndays, matcher = CASES[case]
    return f"avg by(stage)(avg_over_time({METRIC}{FILTERS[matcher]}[{ndays}d]))"


def expected_output(case, end):
    ndays, selector = CASES[case]
    sums = [0] * 38
    counts = [0] * 38
    for day in range(DAYS - ndays, DAYS):
        for i in range(PER_DAY):
            stage = i % 38
            if selector == "stage" and stage != 7:
                continue
            if selector == "project" and i % 8 != 0:
                continue
            if selector == "regex" and i % 7 == 0:
                continue
            # Exact integer source value in milliunits.
            value_milliunits = ((i % 1900 + 1) * 100) + day
            sums[stage] += value_milliunits
            counts[stage] += 1
    expected = []
    for stage, n in enumerate(counts):
        if n:
            expected.append(((("stage", f"stage-{stage:04d}"),), ((end, sums[stage] / n / 1000.0),)))
    # project="project-00" selects only even i; gcd(8,38)=2 -> 19 stages.
    expected_groups = {"all": 38, "stage": 1, "project": 19, "regex": 38}[selector]
    if len(expected) != expected_groups:
        raise AssertionError(("incorrect oracle groups", case, len(expected), expected_groups))
    return tuple(expected)


def request(case, end, trace=False):
    params = {
        "query": query_expr(case),
        "start": end, "end": end, "step": DAY, "nocache": "1",
    }
    if trace:
        params["trace"] = "1"
    url = HOST + "/api/v1/query_range?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter_ns()
    with urllib.request.urlopen(url, timeout=200) as response:
        payload = json.loads(response.read())
    elapsed = (time.perf_counter_ns() - t0) / 1e6
    if payload.get("status") != "success":
        raise AssertionError(("VM query status", case, payload))
    normalized = []
    for row in payload.get("data", {}).get("result", []):
        labels = tuple(sorted(row["metric"].items()))
        values = tuple((int(float(t)), float(x)) for t, x in row["values"])
        if len(values) != 1 or values[0][0] != end or not math.isfinite(values[0][1]):
            raise AssertionError(("Unexpected VM point", case, labels, values))
        normalized.append((labels, values))
    normalized.sort()
    return elapsed, tuple(normalized), payload.get("trace")


def expect(actual, oracle, case):
    if len(actual) != len(oracle):
        raise AssertionError(("wrong group count", case, len(actual), len(oracle)))
    assert_equal(actual, oracle, case)


def start_vm(binary, data, mode, logfile):
    cmd = [
        str(binary), "-storageDataPath=" + str(data),
        "-httpListenAddr=127.0.0.1:8430", "-retentionPeriod=6",
        "-search.maxUniqueTimeseries=2000000", "-search.disableCache",
        "-memory.allowedBytes=4294967296",
        "-storage.cacheSizeIndexDBTagFilters=1",
    ]
    if mode == "global":
        cmd.append("-disablePerDayIndex")
    with open(logfile, "w") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    for _ in range(160):
        if proc.poll() is not None:
            raise RuntimeError(("VM startup failed", proc.returncode, str(logfile)))
        try:
            with urllib.request.urlopen(HOST + "/health", timeout=1) as response:
                if response.status == 200:
                    return proc, cmd
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    proc.wait(timeout=20)
    raise RuntimeError("VM readiness timeout")


def summarize(report):
    out = {}
    primary_valid = 0
    for case in CASES:
        low = [r["cases"][case] for r in report["phases"] if r["mode"] == "global"]
        high = [r["cases"][case] for r in report["phases"] if r["mode"] == "day"]
        if len(low) != 2 or len(high) != 2:
            raise AssertionError("Missing global/day ABBA case")
        avg = lambda xs, key: statistics.mean(x[key] for x in xs)
        g = avg(low, "p50_ms")
        d = avg(high, "p50_ms")
        cg = avg(low, "cpu_ms_per_request")
        cd = avg(high, "cpu_ms_per_request")
        drift = {
            "global_wall": pct_drift(low[0]["p50_ms"], low[1]["p50_ms"]),
            "day_wall": pct_drift(high[0]["p50_ms"], high[1]["p50_ms"]),
            "global_cpu": pct_drift(low[0]["cpu_ms_per_request"], low[1]["cpu_ms_per_request"]),
            "day_cpu": pct_drift(high[0]["cpu_ms_per_request"], high[1]["cpu_ms_per_request"]),
        }
        stable = max(drift.values()) <= 0.25
        path_verified = all(x["path_verified"] for x in low + high)
        valid = stable and path_verified and all(x["cache_resets"] == 0 for x in low + high)
        primary = case.startswith("broad")
        if valid and primary:
            primary_valid += 1
        out[case] = {
            "global_p50_ms": round(g, 4),
            "perday_p50_ms": round(d, 4),
            "global_div_perday_p50": round(g / d, 5),
            "global_cpu_ms_per_request": round(cg, 4),
            "perday_cpu_ms_per_request": round(cd, 4),
            "global_div_perday_cpu": round(cg / cd, 5) if cd else None,
            "controls": drift,
            "path_verified": path_verified,
            "valid": valid,
            "primary": primary,
        }
    report["comparison"] = out
    report["validity"]["primary_valid"] = primary_valid
    report["validity"]["primary_required"] = 4
    # All four broad cases answer the primary time-window crossover question.
    report["decision"] = (
        "ACCEPT_DIAGNOSTIC_JENKINS_INDEX_CROSSOVER"
        if primary_valid == 4
        else "PARTIAL_OR_INVALID_PRIMARY_WINDOW_CONTROLS"
    )
    return primary_valid == 4


def main():
    a = argparse.ArgumentParser()
    for key in ("binary", "base-data", "manifest", "workdir", "output"):
        a.add_argument("--" + key, required=True)
    o = a.parse_args()
    binary = Path(o.binary).resolve()
    base = Path(o.base_data).resolve()
    work = Path(o.workdir).resolve()
    output = Path(o.output)
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(o.manifest).read_text())
    if manifest.get("sha256") != FIXTURE_SHA or manifest.get("series") != 1330000:
        raise AssertionError("R27 not canonical fixture")
    end = int(manifest["end"])
    oracle = {case: expected_output(case, end) for case in CASES}
    if any(len(v) < 1 for v in oracle.values()):
        raise AssertionError("empty analytic oracle")
    base_hash = fingerprint_tree(base)
    report = {
        "schema_version": 1, "question_id": "R27-vm-perday-global-jenkins14d-v1",
        "source_sha": SOURCE_SHA,
        "vm_binary_sha256": sha256(binary),
        "vm_archive_sha256": VM_ARCHIVE_SHA,
        "fixture_sha256": FIXTURE_SHA,
        "fixture_series": 1330000,
        "fixture_samples": 1330000,
        "base_storage_fingerprint": base_hash,
        "phases_order": PHASES,
        "measured_requests_per_case_per_phase": MEASURED,
        "warmups": WARMUPS,
        "cases": {case: {"query": query_expr(case),
                         "expected_groups": len(oracle[case]),
                         "expected_output_digest": hashlib.sha256(repr(oracle[case]).encode()).hexdigest()}
                  for case in CASES},
        "only_changed_flag": "-disablePerDayIndex",
        "shared_flags": [
            "-storage.cacheSizeIndexDBTagFilters=1", "-search.disableCache",
            "-memory.allowedBytes=4294967296",
            "-search.maxUniqueTimeseries=2000000",
        ],
        "warnings": ["VM series limit 2m is a benchmark override, not production policy of 165k"],
        "run_id": os.getenv("GITHUB_RUN_ID"), "phases": [],
        "validity": {"all_clones_equal": True, "all_native_oracle_matches": True},
        "decision": "UNFINISHED",
    }
    try:
        for ix, mode in enumerate(PHASES):
            phase_data = work / f"vm-{ix}-{mode}"
            if phase_data.exists():
                raise AssertionError("Working directory not fresh")
            shutil.copytree(base, phase_data)
            if fingerprint_tree(phase_data) != base_hash:
                raise AssertionError("Physical source differs before phase")
            logfile = output.parent / f"vm-{ix}-{mode}.log"
            proc, args = start_vm(binary, phase_data, mode, logfile)
            phase = {
                "phase": ix, "mode": mode, "command": args,
                "vm_pid": proc.pid, "cloned_storage_fingerprint": base_hash,
                "results": "See cases map", "cases": {},
                "start_ns": time.time_ns(), "log": str(logfile),
            }
            report["phases"].append(phase)
            try:
                for case in CASES:
                    for _ in range(WARMUPS):
                        _, value, _ = request(case, end)
                        expect(value, oracle[case], case)
                    before = metrics()
                    cpu_start = vm_process_cpu(proc.pid)
                    durations = []
                    for _ in range(MEASURED):
                        t, value, _ = request(case, end)
                        expect(value, oracle[case], case)
                        durations.append(t)
                    cpu_end = vm_process_cpu(proc.pid)
                    after = metrics()
                    _, value, trace = request(case, end, trace=True)
                    expect(value, oracle[case], case)
                    texts = trace_snippets(trace)
                    target = "search metric ids in the " + ("global" if mode == "global" else "per-day") + " index"
                    verified = any(target in v.lower() for v in texts)
                    resets = int(after["vm_cache_resets_total"] - before["vm_cache_resets_total"])
                    if resets < 0:
                        raise AssertionError("cache resets counter regressed")
                    entry = {
                        "p50_ms": round(percentile(durations, 50), 5),
                        "p95_ms": round(percentile(durations, 95), 5),
                        "p99_ms": round(percentile(durations, 99), 5),
                        "cpu_ms_per_request": round((cpu_end - cpu_start) * 1000 / MEASURED, 5),
                        "cache_requests": int(after["vm_cache_requests_total"] - before["vm_cache_requests_total"]),
                        "cache_misses": int(after["vm_cache_misses_total"] - before["vm_cache_misses_total"]),
                        "cache_resets": resets,
                        "trace_snippets": texts,
                        "path_verified": verified,
                    }
                    phase["cases"][case] = entry
                    print("R27_CASE", json.dumps({
                        "phase": ix, "mode": mode, "case": case, "p50": entry["p50_ms"],
                        "cpu_ms": entry["cpu_ms_per_request"], "path_verified": verified,
                        "requests": entry["cache_requests"], "misses": entry["cache_misses"],
                    }, sort_keys=True), flush=True)
                phase["process_memory_end"] = vm_mem(proc.pid)
            finally:
                stop_vm(proc)
                phase["ended_ns"] = time.time_ns()
                phase["server_exit_code"] = proc.returncode
                output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        valid = summarize(report)
        print("R27_SUMMARY", json.dumps(report["comparison"], sort_keys=True), flush=True)
        if not valid:
            raise AssertionError("R27 invalid broad 1/3/7/14d A/A or path controls")
    except Exception as e:
        if report["decision"] == "UNFINISHED":
            report["decision"] = "CORRECTNESS_OR_METHOD_FAILURE"
        report["failure"] = repr(e)
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print("R27_DECISION", report["decision"], flush=True)


if __name__ == "__main__":
    main()
