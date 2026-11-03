#!/usr/bin/env python3
"""Stock-O1 native TimeSeries: LC UUID vs raw UUID with 1/4/8 concurrent clients.

Uses the same frozen high-churn input and same executable for both v11 tables.
A/B/B/A per client load, 2 warmup waves, 8 measured waves, native result parity.
Query CPU, scheduler runnable wait, thread-pool locks, physical reads, request
tails, throughput and process RSS are separate measurements. No /proc/TID poll.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import sys
import threading
import time
import traceback

sys.path[:0] = [".github/ts-lab-perf", ".github/ts-lab-bench", ".github/ts-lab-cpu"]
from bench_granularity_v11 import (
    inner, optimize_table, querylogs, sql, streamed_insert, wait_no_merges
)
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import pct, post
from bench_filtered_selector_v4_sql import sql_string
from bench_cpu_knobs import capture_profiles

FIXTURE_SHA = "8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SOURCE_SHA = "52529f0c711566d696917678bf15e7068772fdde"
RAW = "churn_multiclient_rawid"
LC = "churn_ts"
HOST = "http://127.0.0.1:18191"
SELS = {
    "stage": ('{stage="stage-0007"}', 14, 1),
    "project": ('{project="project-00"}', 14, 19),
    "whole": ("", 1, 38),
}
EVENTS = (
    "OSCPUVirtualTimeMicroseconds", "OSCPUWaitMicroseconds",
    "GlobalThreadPoolLockWaitMicroseconds", "LocalThreadPoolLockWaitMicroseconds",
    "ContextLockWaitMicroseconds", "PartsLockWaitMicroseconds",
    "SelectedMarks", "ReadCompressedBytes",
)
WAVES = 10
WARMUP_WAVES = 2
MAX_THREADS_PER_QUERY = 2


def save(file, value):
    p = pathlib.Path(file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def jsonrows(query):
    return [json.loads(x) for x in sql(query + " FORMAT JSONEachRow").splitlines() if x.strip()]


def sha256(file):
    digest = hashlib.sha256()
    with open(file, "rb") as stream:
        for block in iter(lambda: stream.read(2 ** 20), b""):
            digest.update(block)
    return digest.hexdigest()


def physical(table):
    result = {}
    for layer in ("tags", "timeranges", "samples"):
        name = inner(table, layer).split(chr(96))[1]
        result[layer] = {
            "name": name,
            "id_column": jsonrows(
                "SELECT type FROM system.columns WHERE database='default' "
                "AND table=" + sql_string(name) + " AND name='id'"
            )[0]["type"],
            "parts": jsonrows(
                "SELECT count() AS part_count,sum(rows) AS rows,"
                "sum(marks) AS marks,sum(bytes_on_disk) AS bytes "
                "FROM system.parts WHERE active AND database='default' "
                "AND table=" + sql_string(name)
            )[0],
        }
    return result


def create_raw():
    sql(
        "CREATE TABLE " + RAW + " ENGINE=TimeSeries "
        "SETTINGS recent_samples_ttl_seconds=0 "
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


def statement(table, case, finish):
    selector, days, _ = SELS[case]
    start = finish - (days - 1) * 86400
    expr = f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{selector}[1d]))"
    return (
        f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
        f"{start},{finish},86400) SETTINGS max_threads={MAX_THREADS_PER_QUERY},"
        "max_memory_usage=1800000000,log_queries=1,"
        "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )


def server_status_pid(parent_file):
    """Resolve ClickHouse watchdog + server; select the largest resident process."""
    root = int(pathlib.Path(parent_file).read_text().strip())
    seen, queue = set(), [root]
    while queue and len(seen) < 32:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            children = pathlib.Path(f"/proc/{pid}/task/{pid}/children").read_text()
            queue.extend(int(x) for x in children.split())
        except OSError:
            pass
    found = []
    for pid in seen:
        try:
            body = pathlib.Path(f"/proc/{pid}/status").read_text()
            rss = int(next(x.split()[1] for x in body.splitlines() if x.startswith("VmRSS:")))
            found.append((rss, pid))
        except (OSError, StopIteration, ValueError):
            pass
    if not found:
        raise RuntimeError(f"Cannot resolve ClickHouse server process from {root}")
    return max(found)[1]


def rss_bytes(pid):
    try:
        body = pathlib.Path(f"/proc/{pid}/status").read_text()
        values = {}
        for line in body.splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                values[line.split(":")[0]] = int(line.split()[1]) * 1024
        return values
    except OSError:
        return {"unavailable": True}


class RSSMonitor:
    """Single process /proc/PID/status at 4 Hz; quantify instrumentation cost."""
    def __init__(self, pid):
        self.pid = pid
        self.stop = threading.Event()
        self.samples = []
        self.overhead_s = 0.0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            t = time.perf_counter()
            self.samples.append(rss_bytes(self.pid))
            self.overhead_s += time.perf_counter() - t
            self.stop.wait(0.25)

    def __enter__(self):
        self.start = time.perf_counter()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(2)
        self.window_s = time.perf_counter() - self.start

    def report(self):
        live = [v for v in self.samples if "VmRSS" in v]
        return {
            "server_pid": self.pid, "samples": len(live),
            "rss_start_bytes": live[0]["VmRSS"] if live else None,
            "rss_end_bytes": live[-1]["VmRSS"] if live else None,
            "rss_observed_max_bytes": max((x["VmRSS"] for x in live), default=None),
            "vmhwm_end_bytes": live[-1].get("VmHWM") if live else None,
            "observer_overhead_fraction": round(self.overhead_s / max(self.window_s, 1e-6), 6),
        }


def run_wave(executor, n, query, case, prefix, wave, reference, expected, ticks):
    barrier = threading.Barrier(n + 1, timeout=30)

    def client(i):
        barrier.wait()
        begin = time.perf_counter()
        qid = f"{prefix}_{wave}_{i}"
        measured_ms, raw = post(HOST, query, qid)
        end = time.perf_counter()
        points = normalize_any(raw)
        if len(points) != expected or {t for _, t in points} != ticks:
            raise AssertionError((case, "wrong output grid", len(points), expected))
        assert_same(reference, points, case, "multiclient")
        return {
            "qid": qid, "client_ms": measured_ms,
            "started_s": begin, "ended_s": end, "wave": wave,
            "warmup": wave < WARMUP_WAVES,
        }

    futures = [executor.submit(client, i) for i in range(n)]
    barrier.wait()
    trials = [f.result(timeout=380) for f in futures]
    span = max(x["ended_s"] for x in trials) - min(x["started_s"] for x in trials)
    return {
        "wave": wave, "warmup": wave < WARMUP_WAVES,
        "duration_s": round(span, 6),
        "trials": trials,
    }


def complete_logs(prefix, expected_ids):
    missing = set(expected_ids)
    records = {}
    for attempt in range(9):
        for row in querylogs(prefix):
            if row.get("type") == "QueryFinish" and row["query_id"] in expected_ids:
                records[row["query_id"]] = row
        missing = expected_ids - set(records)
        if not missing:
            return records
        time.sleep(min(0.35 * (attempt + 1), 1.8))
    raise RuntimeError(f"Missing {len(missing)} QueryFinish rows: {sorted(missing)[:8]}")


def run_phase(n, variant, order, case, finish, pid, reference, expected, ticks):
    table = LC if variant == "lc" else RAW
    phase_prefix = (
        f"clpar_{case}_{n}_{order}_{variant}_{os.getenv('GITHUB_RUN_ID', 'local')}"
    )
    query = statement(table, case, finish)
    phase = {
        "clients": n, "variant": variant, "order": order,
        "query": query, "waves": [], "trials": [],
    }
    with RSSMonitor(pid) as observer:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            for wave in range(WAVES):
                item = run_wave(
                    executor, n, query, case, phase_prefix,
                    wave, reference, expected, ticks
                )
                phase["waves"].append(item)
                phase["trials"].extend(item["trials"])
                print(
                    "MULTICLIENT_WAVE",
                    json.dumps({
                        "case": case, "n": n, "variant": variant,
                        "phase": order, "wave": wave,
                        "span_s": item["duration_s"],
                    }, separators=(",", ":")),
                    flush=True,
                )
    phase["rss"] = observer.report()
    if phase["rss"]["observer_overhead_fraction"] > 0.02:
        raise AssertionError("RSS observer exceeds 2% of phase window")
    all_ids = {x["qid"] for x in phase["trials"]}
    logs = complete_logs(phase_prefix, all_ids)
    for trial in phase["trials"]:
        row = logs[trial["qid"]]
        events = row.get("ProfileEvents", {})
        trial["server_ms"] = row["query_duration_ms"]
        trial["query_peak_memory_bytes"] = row["memory_usage"]
        trial["read_rows"] = row["read_rows"]
        trial["read_bytes"] = row["read_bytes"]
        trial["events"] = {event: int(events.get(event, 0)) for event in EVENTS}
    measured = [x for x in phase["trials"] if not x["warmup"]]
    waves = [x for x in phase["waves"] if not x["warmup"]]
    ms = [x["client_ms"] for x in measured]
    phase["metrics"] = {
        "requests": len(measured),
        "waves": len(waves),
        "p50_client_ms": pct(ms, 0.5),
        "p95_client_ms": pct(ms, 0.95),
        "p99_client_ms": pct(ms, 0.99),
        "throughput_rps": round(len(measured) / sum(w["duration_s"] for w in waves), 4),
        "mean_cpu_per_request_ms": round(
            statistics.mean(x["events"]["OSCPUVirtualTimeMicroseconds"] for x in measured) / 1000, 3
        ),
        "mean_runnable_wait_per_request_ms": round(
            statistics.mean(x["events"]["OSCPUWaitMicroseconds"] for x in measured) / 1000, 3
        ),
        "mean_pool_lock_wait_per_request_ms": round(
            statistics.mean(
                x["events"]["GlobalThreadPoolLockWaitMicroseconds"] +
                x["events"]["LocalThreadPoolLockWaitMicroseconds"]
                for x in measured
            ) / 1000, 3
        ),
        "max_query_peak_memory_bytes": max(x["query_peak_memory_bytes"] for x in measured),
        "mean_read_rows": round(statistics.mean(x["read_rows"] for x in measured), 2),
        "mean_selected_marks": round(statistics.mean(x["events"]["SelectedMarks"] for x in measured), 2),
        "mean_compressed_read_bytes": round(
            statistics.mean(x["events"]["ReadCompressedBytes"] for x in measured), 2
        ),
        "rss_max_observed_bytes": phase["rss"]["rss_observed_max_bytes"],
    }
    phase["profile_qid"] = next(
        x["qid"] for x in measured if x["wave"] == WARMUP_WAVES
    )
    return phase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=SELS, required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--pid-file", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    report = {
        "case": args.case, "fixture_sha256": FIXTURE_SHA,
        "source_sha": SOURCE_SHA, "max_threads_per_query": MAX_THREADS_PER_QUERY,
        "warmup_waves": WARMUP_WAVES, "measured_waves": WAVES - WARMUP_WAVES,
        "phases": [], "comparisons": {}, "errors": [],
    }
    save(args.output, report)
    try:
        m = json.loads(pathlib.Path(args.manifest).read_text())
        if (
            m.get("sha256") != FIXTURE_SHA or m.get("series") != 1330000 or
            sha256(args.fixture) != FIXTURE_SHA
        ):
            raise AssertionError("Fixture SHA or series count mismatch")
        selector, days, groups = SELS[args.case]
        finish = int(m["end"])
        ticks = {finish - (days - 1 - i) * 86400 for i in range(days)}
        expected = groups * days
        pid = server_status_pid(args.pid_file)
        report["server_pid"] = pid
        report["logical_workload"] = {
            "selector": selector, "days": days, "groups": groups,
            "expected_points": expected, "timestamps": sorted(ticks),
        }
        create_raw()
        report["raw_ingest"] = streamed_insert(RAW, args.fixture)
        if report["raw_ingest"]["rows"] != 1330000:
            raise AssertionError("Incomplete raw ID import")
        report["optimized"] = {t: optimize_table(t) for t in (LC, RAW)}
        wait_no_merges()
        report["physical_before"] = {t: physical(t) for t in (LC, RAW)}
        digests = {}
        for table in (LC, RAW):
            parts = report["physical_before"][table]
            expected_id = (
                "LowCardinality(UUID)" if table == LC else "Tuple(UInt64, UUID)"
            )
            for layer, observed in parts.items():
                if expected_id not in observed["id_column"]:
                    raise AssertionError((table, layer, "ID type", observed["id_column"]))
            tags = inner(table, "tags")
            digests[table] = jsonrows(
                "SELECT count() AS n, countIf(tags['stage']='stage-0007') AS stage_n,"
                "sum(sipHash64(toString(id))) AS id_digest FROM " + tags
            )[0]
            if digests[table]["n"] != 1330000 or digests[table]["stage_n"] != 35000:
                raise AssertionError(("Incorrect dataset", table, digests[table]))
        if digests[LC] != digests[RAW]:
            raise AssertionError(("IDs differ across schemas", digests))
        report["id_digests"] = digests
        base_query = statement(LC, args.case, finish)
        _, sample = post(HOST, base_query, f"clpar_oracle_{args.case}_{os.getenv('GITHUB_RUN_ID','local')}")
        reference = normalize_any(sample)
        if len(reference) != expected or {t for _, t in reference} != ticks:
            raise AssertionError(("Incorrect reference grid", len(reference), expected))
        save(args.output, report)
        for n in (1, 4, 8):
            blocks = []
            report["comparisons"][str(n)] = {"phases": blocks}
            for order, variant in enumerate(("lc", "raw", "raw", "lc")):
                phase = run_phase(
                    n, variant, order, args.case, finish, pid, reference, expected, ticks
                )
                blocks.append(phase)
                report["phases"].append({
                    "clients": n, "variant": variant, "order": order,
                    "metrics": phase["metrics"],
                })
                save(args.output, report)
                print(
                    "MULTICLIENT_PHASE", json.dumps({
                        "case": args.case, "clients": n, "variant": variant,
                        "order": order, "metrics": phase["metrics"],
                    }, separators=(",", ":")), flush=True
                )
            base = [p["metrics"] for p in blocks if p["variant"] == "lc"]
            alt = [p["metrics"] for p in blocks if p["variant"] == "raw"]
            average = lambda xs, key: statistics.mean(x[key] for x in xs)
            base_p50 = average(base, "p50_client_ms")
            raw_p50 = average(alt, "p50_client_ms")
            drift = abs(base[0]["p50_client_ms"] - base[1]["p50_client_ms"]) / base_p50
            result = {
                "clients": n,
                "lc_p50_ms": round(base_p50, 3),
                "raw_p50_ms": round(raw_p50, 3),
                "raw_vs_lc_p50_speedup": round(base_p50 / raw_p50, 4),
                "lc_p95_ms": round(average(base, "p95_client_ms"), 3),
                "raw_p95_ms": round(average(alt, "p95_client_ms"), 3),
                "lc_throughput_rps": round(average(base, "throughput_rps"), 3),
                "raw_throughput_rps": round(average(alt, "throughput_rps"), 3),
                "lc_cpu_request_ms": round(average(base, "mean_cpu_per_request_ms"), 3),
                "raw_cpu_request_ms": round(average(alt, "mean_cpu_per_request_ms"), 3),
                "lc_runnable_wait_ms": round(average(base, "mean_runnable_wait_per_request_ms"), 3),
                "raw_runnable_wait_ms": round(average(alt, "mean_runnable_wait_per_request_ms"), 3),
                "lc_rss_peak_bytes": max(x["rss_max_observed_bytes"] for x in base),
                "raw_rss_peak_bytes": max(x["rss_max_observed_bytes"] for x in alt),
                "control_drift": round(drift, 4),
                "valid": drift <= 0.25,
            }
            report["comparisons"][str(n)]["summary"] = result
            if n in (1, 4, 8):
                # Diagnostic traces are collected AFTER timing, never within a wave.
                qids = [blocks[0]["profile_qid"], blocks[1]["profile_qid"]]
                try:
                    report["comparisons"][str(n)]["profiles"] = capture_profiles(qids)
                except Exception as exc:
                    report["comparisons"][str(n)]["profile_error"] = str(exc)[:1600]
            if not result["valid"]:
                report["errors"].append({"clients": n, "control_drift": drift})
            save(args.output, report)
            print("MULTICLIENT_COMPARE", json.dumps({"case": args.case, **result}), flush=True)
        report["physical_after"] = {t: physical(t) for t in (LC, RAW)}
        if report["physical_before"] != report["physical_after"]:
            raise AssertionError("Physical part layout changed during read-only experiment")
        report["passed"] = not report["errors"]
    except Exception as exc:
        report["errors"].append({"fatal": str(exc)[:1800], "traceback": traceback.format_exc()[-3500:]})
        report["passed"] = False
    finally:
        save(args.output, report)
        compact = {
            "case": args.case, "passed": report["passed"],
            "errors": report["errors"],
            "comparisons": {k: v.get("summary") for k, v in report["comparisons"].items()},
            "physical_before": report.get("physical_before"),
            "physical_after": report.get("physical_after"),
            "id_digests": report.get("id_digests"),
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")), compact)
        print("MULTICLIENT_DONE", json.dumps({
            "case": args.case, "passed": report["passed"],
            "errors": report["errors"],
            "comparisons": compact["comparisons"],
        }, separators=(",", ":")), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
