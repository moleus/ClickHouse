#!/usr/bin/env python3
"""R25: same-binary VictoriaMetrics tag-filter index cache capacity ABBA.

Source: VM 1.134.0 tag eaf7a68c92c8069a67ae02e601e81453a5e002de.
Fixture: existing R01 3d/400-slot 2,488,320-point SHA-pinned JSONL.
Compare ONLY storage.cacheSizeIndexDBTagFilters=1 vs 67108864.
Fresh process and byte-identical, clean-stopped VM data clone per phase.
No ClickHouse build, no ingestion, no live data, no response cache.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SOURCE_SHA = "eaf7a68c92c8069a67ae02e601e81453a5e002de"
FIXTURE_SHA = "ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
VM_ARCHIVE_SHA = "cd6e109bae85ff7c138d838b32be9a7fe2a6d7a9b3a387a7cf9234f22991da10"
SIZES = {"low": 1, "high": 67108864}
ORDER = ["low", "high", "high", "low"]
WARMUPS = 8
MEASURED = 48
DAY = 86400
HOST = "http://127.0.0.1:8430"
CASES = {
    "node_broad": ("tslab_node_cpu_ratio", "", "project", 8),
    "pod_namespace": ("tslab_pod_cpu_ratio", '{namespace="team-000"}', "namespace", 1),
    "pod_broad": ("tslab_pod_cpu_ratio", "", "namespace", 16),
    "burst15": ("tslab_pod_cpu_ratio", '{scrape="15s"}', "namespace", 4),
}
CACHE_TYPE = 'type="indexdb/tagFiltersToMetricIDs"'
CACHE_NAMES = [
    "vm_cache_requests_total", "vm_cache_misses_total",
    "vm_cache_resets_total", "vm_cache_entries",
    "vm_cache_size_bytes", "vm_cache_size_max_bytes",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fd:
        for b in iter(lambda: fd.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fingerprint_tree(root):
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(bytes.fromhex(sha256(p)))
    return h.hexdigest()


def metrics():
    with urllib.request.urlopen(HOST + "/metrics", timeout=15) as r:
        content = r.read().decode()
    result = {}
    for line in content.splitlines():
        for name in CACHE_NAMES:
            prefix = name + "{" + CACHE_TYPE + "} "
            if line.startswith(prefix):
                result[name] = float(line[len(prefix):])
    if set(result) != set(CACHE_NAMES):
        raise AssertionError("VM tag-filter cache metrics missing: " + str(result))
    return result


def vm_process_cpu(pid):
    s = Path(f"/proc/{pid}/stat").read_text()
    fields = s[s.rfind(")") + 2:].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[11]) + int(fields[12])) / ticks


def vm_mem(pid):
    out = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            key, val = line.split(":", 1)
            out[key] = int(val.strip().split()[0]) * 1024
    return out


def query_expression(case):
    metric, selector, group, _ = CASES[case]
    return f"avg by({group})(avg_over_time({metric}{selector}[1d]))"


def request(case, start, end, trace=False):
    params = {
        "query": query_expression(case),
        "start": start, "end": end, "step": DAY,
        "nocache": "1",
    }
    if trace:
        params["trace"] = "1"
    url = HOST + "/api/v1/query_range?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter_ns()
    with urllib.request.urlopen(url, timeout=90) as resp:
        body = json.loads(resp.read())
    elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
    if body.get("status") != "success":
        raise AssertionError(("VM native query status", case, body))
    raw = body.get("data", {}).get("result", [])
    if not raw:
        raise AssertionError("Empty VM result: " + case)
    normalized = []
    for item in raw:
        labels = tuple(sorted(item["metric"].items()))
        values = tuple((int(float(ts)), float(v)) for ts, v in item["values"])
        if not values or not all(math.isfinite(x) for _, x in values):
            raise AssertionError("Empty/nonfinite VM result: " + case)
        normalized.append((labels, values))
    normalized.sort()
    expected = CASES[case][3]
    if len(normalized) != expected:
        raise AssertionError((case, "group count", len(normalized), expected))
    ticks = (start, start + DAY, end)
    if any(tuple(t for t, _ in values) != ticks for _, values in normalized):
        raise AssertionError((case, "timestamp grid mismatch", ticks))
    return elapsed_ms, tuple(normalized), body.get("trace")


def assert_equal(a, b, case):
    if len(a) != len(b):
        raise AssertionError(("unequal output groups", case))
    for (labels_a, values_a), (labels_b, values_b) in zip(a, b):
        if labels_a != labels_b or len(values_a) != len(values_b):
            raise AssertionError(("unequal labels or values lengths", case))
        for (ta, va), (tb, vb) in zip(values_a, values_b):
            if ta != tb or not math.isclose(va, vb, rel_tol=1e-10, abs_tol=1e-8):
                raise AssertionError(("value/time mismatch", case, ta, tb, va, vb))


def trace_snippets(tree):
    out = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "message" and isinstance(v, str):
                    if any(z in v.lower() for z in ("cache", "search metric", "filter")):
                        out.append(v[:350])
                else:
                    walk(v)
        elif isinstance(x, (list, tuple)):
            for item in x:
                walk(item)
    walk(tree)
    return out[:35]


def stop_vm(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=85)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
            raise RuntimeError("VM clean SIGTERM shutdown timed out")
    if proc.returncode not in (0, -15):
        raise RuntimeError("VM shutdown returned " + str(proc.returncode))


def start_vm(binary, data, cache_size, logfile):
    cmd = [
        str(binary), "-storageDataPath=" + str(data),
        "-httpListenAddr=127.0.0.1:8430",
        "-retentionPeriod=6", "-search.maxUniqueTimeseries=165000",
        "-search.disableCache", "-memory.allowedBytes=4294967296",
        "-storage.cacheSizeIndexDBTagFilters=" + str(cache_size),
    ]
    out = open(logfile, "w")
    try:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    finally:
        out.close()
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(("VM startup failed", proc.returncode, str(logfile)))
        try:
            with urllib.request.urlopen(HOST + "/health", timeout=1) as resp:
                if resp.status == 200:
                    return proc, cmd
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    proc.wait(timeout=15)
    raise RuntimeError("VM startup readiness timed out")


def percentile(data, pct):
    v = sorted(data)
    rank = (len(v) - 1) * pct / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    return v[lo] * (hi - rank) + v[hi] * (rank - lo) if lo != hi else v[lo]


def pct_drift(a, b):
    return abs(a - b) / max(1e-10, (a + b) / 2)


def analyze(report):
    results = {}
    valid = True
    for case in CASES:
        low = [p["cases"][case] for p in report["phases"] if p["mode"] == "low"]
        high = [p["cases"][case] for p in report["phases"] if p["mode"] == "high"]
        if len(low) != 2 or len(high) != 2:
            raise AssertionError("ABBA incomplete")
        mean = statistics.mean
        low_p50 = mean(x["p50_ms"] for x in low)
        high_p50 = mean(x["p50_ms"] for x in high)
        low_cpu = mean(x["cpu_ms_per_request"] for x in low)
        high_cpu = mean(x["cpu_ms_per_request"] for x in high)
        low_misses = sum(x["cache_misses"] for x in low)
        high_misses = sum(x["cache_misses"] for x in high)
        low_reqs = sum(x["cache_requests"] for x in low)
        high_reqs = sum(x["cache_requests"] for x in high)
        lmiss = low_misses / max(1, low_reqs)
        hmiss = high_misses / max(1, high_reqs)
        drifts = {
            "low_wall": pct_drift(low[0]["p50_ms"], low[1]["p50_ms"]),
            "high_wall": pct_drift(high[0]["p50_ms"], high[1]["p50_ms"]),
            "low_cpu": pct_drift(low[0]["cpu_ms_per_request"], low[1]["cpu_ms_per_request"]),
            "high_cpu": pct_drift(high[0]["cpu_ms_per_request"], high[1]["cpu_ms_per_request"]),
        }
        no_cache_queries = low_reqs == 0 and high_reqs == 0
        separated = low_reqs > 0 and high_reqs > 0 and lmiss >= 0.80 and hmiss <= 0.20
        stable = max(drifts.values()) <= 0.25
        passed = (separated or no_cache_queries) and stable and all(x["cache_resets"] == 0 for x in low + high)
        valid = valid and passed
        results[case] = {
            "low_p50_ms": round(low_p50, 4),
            "high_p50_ms": round(high_p50, 4),
            "low_over_high_p50_ratio": round(low_p50 / high_p50, 5),
            "low_cpu_ms_per_request": round(low_cpu, 4),
            "high_cpu_ms_per_request": round(high_cpu, 4),
            "low_over_high_cpu_ratio": round(low_cpu / high_cpu, 5),
            "low_cache_miss_rate": round(lmiss, 6),
            "high_cache_miss_rate": round(hmiss, 6),
            "cache_path": "MEASURED_SEPARATION" if separated else ("NO_CACHE_CALLS" if no_cache_queries else "INDETERMINATE"),
            "cache_requests_low_high": [low_reqs, high_reqs],
            "cache_misses_low_high": [low_misses, high_misses],
            "control_drifts": drifts,
            "valid": passed,
        }
    report["comparison"] = results
    report["validity"]["all_cases_valid"] = valid
    report["decision"] = ("ACCEPT_DIAGNOSTIC_CACHE_CAPACITY" if valid else "INVALID_METHOD_CACHE_SEPARATION_OR_DRIFT")
    return valid


def main():
    pa = argparse.ArgumentParser()
    pa.add_argument("--binary", required=True)
    pa.add_argument("--base-data", required=True)
    pa.add_argument("--manifest", required=True)
    pa.add_argument("--workdir", required=True)
    pa.add_argument("--output", required=True)
    args = pa.parse_args()
    binpath = Path(args.binary).resolve()
    basedir = Path(args.base_data).resolve()
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text())
    if manifest.get("sha256") != FIXTURE_SHA or manifest.get("samples") != 2488320 or manifest.get("series") != 28896:
        raise AssertionError(("wrong R01 fixture", manifest))
    if not binpath.is_file() or not basedir.is_dir():
        raise AssertionError("No executable or base snapshot")
    report = {
        "schema_version": 1, "question_id": "R25-vm-tagfilter-cache-capacity-abba-v1",
        "source_commit": SOURCE_SHA,
        "vm_binary_sha256": sha256(binpath), "vm_release_archive_sha256": VM_ARCHIVE_SHA,
        "fixture_sha256": FIXTURE_SHA, "fixture_series": 28896, "fixture_samples": 2488320,
        "phase_order": ORDER, "cache_sizes": SIZES, "warmups_per_case": WARMUPS,
        "measured_per_case": MEASURED, "query_response_cache": "disabled",
        "vm_memory_allowed_bytes": 4294967296, "max_unique_timeseries": 165000,
        "case_queries": {name: query_expression(name) for name in CASES},
        "host_nproc": os.cpu_count(), "runner": os.getenv("RUNNER_NAME"),
        "workflow_run": os.getenv("GITHUB_RUN_ID"), "phases": [],
        "validity": {"all_outputs_full_match": True, "all_clones_match": True},
        "decision": "UNFINISHED",
    }
    references = {}
    end = int(manifest["end"])
    start = end - 2 * DAY
    base_fp = fingerprint_tree(basedir)
    report["base_storage_fingerprint"] = base_fp

    try:
        for ix, mode in enumerate(ORDER):
            data = workdir / f"vm-phase-{ix}-{mode}"
            if data.exists():
                raise AssertionError("phase data directory already exists: " + str(data))
            shutil.copytree(basedir, data)
            if fingerprint_tree(data) != base_fp:
                raise AssertionError("Storage snapshot clone differs before VM launch")
            log = output.parent / f"vm-phase-{ix}-{mode}.log"
            proc, cmd = start_vm(binpath, data, SIZES[mode], log)
            phase = {
                "index": ix, "mode": mode, "cache_size_bytes": SIZES[mode],
                "command": cmd, "process_pid": proc.pid,
                "start_ns": time.time_ns(), "logfile": str(log),
                "cases": {}, "data_source_fingerprint": base_fp,
            }
            report["phases"].append(phase)
            try:
                for case in CASES:
                    for _ in range(WARMUPS):
                        _, rows, _ = request(case, start, end)
                        if case not in references:
                            references[case] = rows
                        else:
                            assert_equal(references[case], rows, case)
                    before = metrics()
                    cpu_before = vm_process_cpu(proc.pid)
                    durations = []
                    for _ in range(MEASURED):
                        duration, rows, _ = request(case, start, end)
                        assert_equal(references[case], rows, case)
                        durations.append(duration)
                    cpu_after = vm_process_cpu(proc.pid)
                    after = metrics()
                    c_requests = int(after["vm_cache_requests_total"] - before["vm_cache_requests_total"])
                    c_misses = int(after["vm_cache_misses_total"] - before["vm_cache_misses_total"])
                    c_resets = int(after["vm_cache_resets_total"] - before["vm_cache_resets_total"])
                    if c_requests < 0 or c_misses < 0 or c_misses > c_requests:
                        raise AssertionError(("bad cache event accounting", case, before, after))
                    phase["cases"][case] = {
                        "p50_ms": round(percentile(durations, 50), 5),
                        "p95_ms": round(percentile(durations, 95), 5),
                        "p99_ms": round(percentile(durations, 99), 5),
                        "mean_ms": round(statistics.mean(durations), 5),
                        "cpu_ms_per_request": round((cpu_after - cpu_before) * 1000 / MEASURED, 5),
                        "cache_requests": c_requests,
                        "cache_misses": c_misses,
                        "cache_resets": c_resets,
                        "cache_entries": int(after["vm_cache_entries"]),
                        "cache_bytes": int(after["vm_cache_size_bytes"]),
                        "cache_max_bytes": int(after["vm_cache_size_max_bytes"]),
                        "requests": MEASURED,
                        "values_digest": hashlib.sha256(repr(references[case]).encode()).hexdigest(),
                    }
                    # Trace after measured requests. Tracing never affects timed/cpu phases.
                    _, traced_rows, trace = request(case, start, end, trace=True)
                    assert_equal(references[case], traced_rows, case)
                    phase["cases"][case]["trace_returned"] = trace is not None
                    phase["cases"][case]["trace_snippets"] = trace_snippets(trace)
                    print("R25_CASE", json.dumps({
                        "phase": ix, "mode": mode, "case": case, **phase["cases"][case]
                    }, sort_keys=True), flush=True)
                phase["memory_at_end"] = vm_mem(proc.pid)
                phase["cache_metrics_after"] = metrics()
            finally:
                stop_vm(proc)
                phase["server_exit_code"] = proc.returncode
                phase["end_ns"] = time.time_ns()
                report["last_completed_phase"] = ix
                output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        valid = analyze(report)
        print("R25_SUMMARY", json.dumps(report["comparison"], sort_keys=True), flush=True)
        if not valid:
            raise AssertionError("R25 falsifier: cache separation or zero-call controls, A/A drift or cache resets")
    except Exception as exc:
        if report["decision"] == "UNFINISHED":
            report["decision"] = "CORRECTNESS_FAIL_OR_INVALID_METHOD"
        report["failure"] = repr(exc)
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print("R25_DECISION", report["decision"], flush=True)


if __name__ == "__main__":
    main()
