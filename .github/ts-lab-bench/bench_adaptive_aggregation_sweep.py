#!/usr/bin/env python3
"""A/B/C/D/A adaptive GROUP BY settings on one pinned ClickHouse TimeSeries.

An exact-output experiment to determine whether adaptive bucket merging
is the primary cost of high-cardinality grouped AVG and dense rate.
All variants use the same on-disk samples. This is not a default setting
recommendation; source-level plan changes require an independent C++ test.
"""
import argparse
import json
import os
import pathlib

from bench_bucket_cxx_abca import assert_same, normalize_any, post, pct


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--jenkins-manifest", required=True)
    p.add_argument("--dense-manifest", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--warmups", type=int, default=1)
    p.add_argument("--repeats", type=int, default=5)
    a = p.parse_args()

    j = json.loads(pathlib.Path(a.jenkins_manifest).read_text())
    d = json.loads(pathlib.Path(a.dense_manifest).read_text())
    cases = {
        "jenkins_all": {
            "table": "jenkins_ts",
            "expr": f"avg by(stage)(avg_over_time({j['metric']}[1d]))",
            "start": j["query_start_epoch"], "end": j["query_end_epoch"],
            "step": j["step_seconds"],
        },
        "jenkins_stage_exact": {
            "table": "jenkins_ts",
            "expr": f"avg by(stage)(avg_over_time({j['metric']}{{stage=\"stage-0007\"}}[1d]))",
            "start": j["query_start_epoch"], "end": j["query_end_epoch"],
            "step": j["step_seconds"],
        },
        "dense_rate_sum": {
            "table": "dense_ts", "expr": "sum(rate(bench_metric[5m]))",
            "start": d["eval_start_epoch"], "end": d["eval_end_epoch"],
            "step": d["eval_step_seconds"],
        },
        "dense_rate_team": {
            "table": "dense_ts", "expr": "sum by(team)(rate(bench_metric[5m]))",
            "start": d["eval_start_epoch"], "end": d["eval_end_epoch"],
            "step": d["eval_step_seconds"],
        },
        "dense_rate_region": {
            "table": "dense_ts", "expr": "sum by(region)(rate(bench_metric[5m]))",
            "start": d["eval_start_epoch"], "end": d["eval_end_epoch"],
            "step": d["eval_step_seconds"],
        },
        "dense_host_exact": {
            "table": "dense_ts", "expr": 'rate(bench_metric{host="h0001"}[5m])',
            "start": d["eval_start_epoch"], "end": d["eval_end_epoch"],
            "step": d["eval_step_seconds"],
        },
    }
    settings = {
        "default_before": "enable_adaptive_aggregator=1,adaptive_aggregator_freeze_threshold=16384,adaptive_aggregator_freeze_threshold_bytes=4194304",
        "disabled": "enable_adaptive_aggregator=0",
        "early_freeze": "enable_adaptive_aggregator=1,adaptive_aggregator_freeze_threshold=512,adaptive_aggregator_freeze_threshold_bytes=1048576",
        "late_freeze": "enable_adaptive_aggregator=1,adaptive_aggregator_freeze_threshold=65536,adaptive_aggregator_freeze_threshold_bytes=0",
        "default_after": "enable_adaptive_aggregator=1,adaptive_aggregator_freeze_threshold=16384,adaptive_aggregator_freeze_threshold_bytes=4194304",
    }
    prefix = "tslab_adap_" + os.environ.get("GITHUB_RUN_ID", "local")
    result = {"jenkins": j, "dense": d, "settings": settings,
              "warmups": a.warmups, "repeats": a.repeats,
              "all_exact": True, "cases": {}}
    for case, v in cases.items():
        original = None
        per_variant = {}
        for variant, knobs in settings.items():
            query = ("SELECT * FROM prometheusQueryRange("
                     f"{v['table']},'{v['expr'].replace(chr(39), chr(39)*2)}',"
                     f"{v['start']},{v['end']},{v['step']}) "
                     "SETTINGS max_threads=4," + knobs + " FORMAT JSONEachRow")
            times = []
            for i in range(a.warmups + a.repeats):
                qid = f"{prefix}_{case}_{variant}_{i}"
                ms, raw = post("http://127.0.0.1:18191", query, qid)
                observed = normalize_any(raw)
                if original is None:
                    original = observed
                    assert len(original) > 0, ("empty reference", case)
                exactness = assert_same(original, observed, case, variant)
                if i >= a.warmups:
                    times.append(ms)
            per_variant[variant] = {
                "p50_ms": pct(times, .5), "p95_ms": pct(times, .95),
                "trials_ms": times, "points": exactness["points"],
                "exact": True,
            }
        result["cases"][case] = per_variant
        print("TSLAB_ADAPTIVE_SWEEP_CASE", json.dumps({
            "case": case, "p50_ms": {k: v["p50_ms"]
                                     for k, v in per_variant.items()},
            "points": len(original), "all_exact": True
        }, sort_keys=True), flush=True)

    post("http://127.0.0.1:18191", "SYSTEM FLUSH LOGS")
    _, raw = post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms, "
        "ProfileEvents['AdaptiveAggregationDrainedRecords'] AS drained "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    logs = {x["query_id"]: x for l in raw.splitlines() if l.strip()
            for x in [json.loads(l)]}
    for case in cases:
        for variant in settings:
            rows = [logs[f"{prefix}_{case}_{variant}_{i}"]
                    for i in range(a.warmups, a.warmups + a.repeats)]
            result["cases"][case][variant].update({
                "query_mem_p95": pct([x["memory_usage"] for x in rows], .95),
                "read_rows_p50": pct([x["read_rows"] for x in rows], .5),
                "read_bytes_p50": pct([x["read_bytes"] for x in rows], .5),
                "adaptive_drained_records_p50": pct([x["drained"] for x in rows], .5),
            })
    pathlib.Path(a.output).write_text(json.dumps(result, indent=2) + "\n")
    print("TSLAB_ADAPTIVE_SWEEP_DONE", json.dumps({
        "cases": len(cases), "variants": len(settings), "all_exact": True
    }), flush=True)


if __name__ == "__main__":
    main()
