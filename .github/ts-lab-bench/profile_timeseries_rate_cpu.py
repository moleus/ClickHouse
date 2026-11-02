#!/usr/bin/env python3
"""CPU sampling profile of native ClickHouse TimeSeries rate over dense data.

This is a diagnostic, not an optimization. Uses the same baseline binary
and 20k-series dense dataset as prior A/B/A tests. If trace events are not
available, keeps explicit diagnostic evidence instead of guessing hotspots.
"""
import argparse
import json
import pathlib

from bench_jenkins_querypath import post, pct


def sql(query, qid=None):
    ms, raw = post("http://127.0.0.1:18191", query, qid)
    return ms, raw.decode("utf-8", "replace")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--warmups", type=int, default=3)
    p.add_argument("--repeats", type=int, default=24)
    a = p.parse_args()

    m = json.loads(pathlib.Path(a.manifest).read_text())
    start, end, step = (m[x] for x in
                        ("eval_start_epoch", "eval_end_epoch", "eval_step_seconds"))
    expressions = {
        "rate_sum": "sum(rate(bench_metric[5m]))",
        "rate_team": "sum by(team)(rate(bench_metric[5m]))",
        "rate_exact_host": "rate(bench_metric{host=\"h0001\"}[5m])",
    }

    prefix = "tslab_cpu_"
    result = {"dataset": m, "repeats": a.repeats,
              "sampling_period_ns": 1000000, "cases": {}}
    for case, expression in expressions.items():
        times = []
        query = ("SELECT * FROM prometheusQueryRange("
                 "dense_ts,'" + expression.replace("'", "''") + "',"
                 f"{start},{end},{step}) "
                 "SETTINGS max_threads=4,"
                 "query_profiler_cpu_time_period_ns=1000000,"
                 "query_profiler_real_time_period_ns=0 FORMAT JSONEachRow")
        for i in range(a.warmups + a.repeats):
            qid = f"{prefix}{case}_{i}"
            elapsed, body = sql(query, qid)
            if not body.strip():
                raise AssertionError(("empty PromQL output", case, i))
            if i >= a.warmups:
                times.append(elapsed)
        result["cases"][case] = {
            "expression": expression, "p50_ms": pct(times, .5),
            "p95_ms": pct(times, .95), "trials_ms": times,
        }
        print("TSLAB_CPU_PROFILE_QUERY", json.dumps({
            "case": case, **result["cases"][case]
        }, sort_keys=True), flush=True)

    sql("SYSTEM FLUSH LOGS")
    for case in expressions:
        like = prefix + case + "_%"
        query = (
            "SELECT count() AS samples FROM system.trace_log "
            f"WHERE query_id LIKE '{like}' AND trace_type='CPU'")
        try:
            _, raw_count = sql(query)
            count = int(raw_count.strip())
            result["cases"][case]["trace_samples"] = count
            if not count:
                result["cases"][case]["trace_warning"] = "No CPU trace samples"
                continue

            top = (
                "SELECT demangle(addressToSymbol(frame)) AS symbol, "
                "count() AS samples "
                "FROM (SELECT arrayJoin(trace) AS frame FROM system.trace_log "
                f"WHERE query_id LIKE '{like}' AND trace_type='CPU') "
                "GROUP BY symbol ORDER BY samples DESC LIMIT 60 "
                "SETTINGS allow_introspection_functions=1 FORMAT JSONEachRow")
            _, raw = sql(top)
            result["cases"][case]["top_symbols"] = [
                json.loads(line) for line in raw.splitlines() if line.strip()]
        except Exception as exc:
            result["cases"][case]["trace_error"] = repr(exc)
        print("TSLAB_CPU_PROFILE_SYMBOLS", json.dumps({
            "case": case, "samples": result["cases"][case].get("trace_samples"),
            "symbols": result["cases"][case].get("top_symbols", [])[:12],
            "error": result["cases"][case].get("trace_error")
        }, sort_keys=True), flush=True)

    path = pathlib.Path(a.output)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("TSLAB_CPU_PROFILE_DONE", path, flush=True)


if __name__ == "__main__":
    main()
