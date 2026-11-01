#!/usr/bin/env python3
"""Measure raw TimeSeries v11 sample aggregation without an ANTL4 dependency.

This isolates dense-grid rate costs. It is NOT a substitute for label-selector
and full PromQL benchmarks. Both engines receive the same generated samples.
"""
import argparse
import json
import pathlib
import random
import statistics
import time
import urllib.parse
import urllib.error
import urllib.request


def sql(host, query, timeout=180):
    req = urllib.request.Request(host.rstrip("/") + "/?wait_end_of_query=1",
                                 data=query.encode("utf-8"), method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse SQL HTTP {exc.code}: {message[:4000]}; query: {query[:2000]}") from exc
    return (time.perf_counter() - t0) * 1000, text


def pctl(a, n):
    s = sorted(a)
    k = (len(s) - 1) * n / 100
    lo = int(k)
    return round(s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (k - lo), 3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--ch", default="http://127.0.0.1:18191")
    p.add_argument("--vm", default="http://127.0.0.1:8430")
    p.add_argument("--table", required=True)
    p.add_argument("--repeats", type=int, default=12)
    p.add_argument("--warmups", type=int, default=4)
    p.add_argument("--output", required=True)
    cfg = p.parse_args()
    m = json.loads(pathlib.Path(cfg.manifest).read_text())
    _, name_text = sql(cfg.ch, "SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw")
    names = [x for x in name_text.splitlines() if x]
    if len(names) != 1:
        raise RuntimeError("expected one TimeSeries samples inner table; got: " + repr(names))
    quote = chr(96)
    tab = "default." + quote + names[0].replace(quote, quote + quote) + quote
    _, stored = sql(cfg.ch, "SELECT sum(length(samples)) FROM " + tab)
    if int(stored) != m["samples_total"]:
        raise AssertionError(("stored samples differ", int(stored), m["samples_total"]))
    _, series = sql(cfg.ch, "SELECT uniqExact(id) FROM " + tab)
    if int(series) != m["series"]:
        raise AssertionError(("stored series differ", int(series), m["series"]))
    start = m["eval_start_epoch"] * 1000
    end = m["eval_end_epoch"] * 1000
    window = 300
    step = m["eval_step_seconds"]
    rate = ("timeSeriesRateToGrid(fromUnixTimestamp64Milli(" + str(start) +
            "),fromUnixTimestamp64Milli(" + str(end) +
            "),toIntervalSecond(" + str(step) +
            "),toIntervalSecond(" + str(window) + "))(samples)")
    where = (" WHERE min_time <= fromUnixTimestamp64Milli(" + str(end) +
             ") AND max_time >= fromUnixTimestamp64Milli(" + str(start - 1000 * window) + ")")
    queries = {}
    for group in ("id", "tupleElement(id,1), tupleElement(id,2)"):
        for threads in (1, 4):
            key = ("id" if group == "id" else "pair") + "_threads_" + str(threads)
            queries[key] = ("SELECT sumForEach(grid) AS values FROM (SELECT " +
                            rate + " AS grid FROM " + tab + where + " GROUP BY " +
                            group + ") SETTINGS max_threads=" + str(threads) +
                            " FORMAT JSONEachRow")
    stage_queries = {
        "scan_rows": "SELECT count() FROM " + tab + where,
        "decode_samples": "SELECT sum(length(samples)) FROM " + tab + where,
        "hash_ids": "SELECT uniqExact(id) FROM " + tab + where,
        "group_ids": "SELECT count() FROM (SELECT id FROM " + tab + where + " GROUP BY id)",
    }
    stage_results = {}
    for key, query in stage_queries.items():
        _, raw = sql(cfg.ch, query)
        stage_results[key] = int(raw)
    if stage_results["hash_ids"] != m["series"] or stage_results["group_ids"] != m["series"]:
        raise AssertionError(("grouped IDs mismatch", stage_results, m["series"]))
    stage_times = {k: [] for k in stage_queries}
    for query in stage_queries.values():
        for _ in range(cfg.warmups):
            sql(cfg.ch, query)
    stage_rng = random.Random(8102345)
    for _ in range(cfg.repeats):
        stage_keys = list(stage_queries)
        stage_rng.shuffle(stage_keys)
        for key in stage_keys:
            elapsed, _ = sql(cfg.ch, stage_queries[key])
            stage_times[key].append(round(elapsed, 3))
    stage_stats = {key: {"p50_ms": pctl(vals, 50), "p95_ms": pctl(vals, 95),
                         "p99_ms": pctl(vals, 99), "trials_ms": vals,
                         "result": stage_results[key]}
                   for key, vals in stage_times.items()}
    values = {}
    for key, query in queries.items():
        _, response = sql(cfg.ch, query)
        rows = [json.loads(x) for x in response.splitlines()]
        if len(rows) != 1 or len(rows[0]["values"]) != m["eval_end_epoch"] // step - m["eval_start_epoch"] // step + 1:
            raise AssertionError((key, "unexpected grid", response[:500]))
        values[key] = rows[0]["values"]
    ref = values["id_threads_1"]
    deltas = {}
    for key, arr in values.items():
        deltas[key] = max(abs(float(a or 0) - float(b or 0)) for a, b in zip(ref, arr))
        if deltas[key] > 1e-9 * max(1, max(abs(float(x or 0)) for x in ref)):
            raise AssertionError((key, deltas[key]))
    vmparams = urllib.parse.urlencode(dict(
        query="sum(rate(bench_metric[5m]))",
        start=m["eval_start_epoch"], end=m["eval_end_epoch"],
        step=step, nocache="1"))
    vm_url = cfg.vm.rstrip("/") + "/api/v1/query_range?" + vmparams
    with urllib.request.urlopen(vm_url, timeout=180) as resp:
        vm = json.load(resp)
    vm_series = vm.get("data", {}).get("result", [])
    if vm.get("status") != "success" or len(vm_series) != 1:
        raise AssertionError(("bad VM query", vm))
    vm_points = vm_series[0]["values"]
    if len(vm_points) != len(ref):
        raise AssertionError(("VM points differ", len(vm_points), len(ref)))
    diffs = []
    for i, ((ts, value), ch) in enumerate(zip(vm_points, ref)):
        expected_ts = m["eval_start_epoch"] + i * step
        if int(ts) != expected_ts:
            raise AssertionError(("time mismatch", ts, expected_ts))
        fv, fc = float(value), float(ch or 0)
        diffs.append(abs(fc - fv) / max(1e-12, abs(fv)))
    times = {x: [] for x in queries}
    vm_times = []
    def vm_request():
        start_t = time.perf_counter()
        with urllib.request.urlopen(vm_url, timeout=180) as response:
            response.read()
        return (time.perf_counter() - start_t) * 1000
    for key, query in queries.items():
        for _ in range(cfg.warmups):
            sql(cfg.ch, query)
    for _ in range(cfg.warmups):
        vm_request()
    rng = random.Random(20261101)
    for repetition in range(cfg.repeats):
        choices = list(queries)
        rng.shuffle(choices)
        if repetition % 2 == 0:
            vm_times.append(round(vm_request(), 3))
        for key in choices:
            elapsed, _ = sql(cfg.ch, queries[key])
            times[key].append(round(elapsed, 3))
        if repetition % 2 != 0:
            vm_times.append(round(vm_request(), 3))
    stats = {key: {"p50_ms": pctl(vals, 50), "p95_ms": pctl(vals, 95),
                   "p99_ms": pctl(vals, 99), "trials_ms": vals}
             for key, vals in times.items()}
    vm_stats = {"p50_ms": pctl(vm_times, 50), "p95_ms": pctl(vm_times, 95),
                "p99_ms": pctl(vm_times, 99), "trials_ms": vm_times}
    result = {
        "source": "direct inner samples table (no ANTL4)",
        "table": cfg.table, "inner_table": names[0], "manifest": m,
        "stored_samples": int(stored), "stored_series": int(series),
        "warmups": cfg.warmups, "repeats": cfg.repeats,
        "comparison": "VM MetricsQL rate() vs CH PromQL-style rate(); numerical semantics may differ",
        "vm_vs_ch_max_relative_diff": max(diffs),
        "vm_vs_ch_median_relative_diff": statistics.median(diffs),
        "variant_max_absolute_diff": deltas, "stats": stats,
        "stage_stats": stage_stats, "stage_queries": stage_queries,
        "vm_stats": vm_stats,
        "ch_to_vm_p50_ratios": {name: round(values["p50_ms"] / vm_stats["p50_ms"], 3)
                                for name, values in stats.items()},
        "queries": queries,
    }
    dest = pathlib.Path(cfg.output)
    dest.write_text(json.dumps(result, indent=2) + "\n")
    for key in ("id_threads_1", "pair_threads_4"):
        qry = queries[key].replace(" FORMAT JSONEachRow", "")
        try:
            _, plan = sql(cfg.ch, "EXPLAIN PIPELINE " + qry, timeout=30)
            dest.with_name(dest.stem + "-" + key + ".pipeline.txt").write_text(plan[:100000])
        except Exception as exc:
            dest.with_name(dest.stem + "-" + key + ".pipeline-error.txt").write_text(repr(exc))
    print(json.dumps({"table":cfg.table, "samples":int(stored), "series":int(series),
                      "vm_ch_max_relative_diff":max(diffs), "vm":vm_stats,
                      "ch_to_vm_ratios":result["ch_to_vm_p50_ratios"], "variants":stats,
                      "stages":stage_stats}, indent=2))


if __name__ == "__main__":
    main()
