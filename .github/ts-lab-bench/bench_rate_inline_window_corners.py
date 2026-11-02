#!/usr/bin/env python3
"""Exact-output A/B/A cases for the inline/deque sliding rate window.

Each phase uses the same TimeSeries stored data and restores the original
binary in the last phase. Equality covers labels, timestamps and numeric
values; VictoriaMetrics MetricsQL rate is not used as an exact-value oracle.
"""
import argparse
import json
import pathlib

from bench_bucket_cxx_abca import (
    assert_same, from_json_rows, normalize_any, post, pct, to_json_rows
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--canonical", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--repeats", type=int, default=2)
    a = p.parse_args()

    manifest = json.loads(pathlib.Path(a.manifest).read_text())
    start = manifest["eval_start_epoch"]
    end = manifest["eval_end_epoch"]
    cases = {
        "rate_inline_60s": ("sum(rate(bench_metric[5m]))", 60),
        "rate_grid_15s": ("sum(rate(bench_metric[5m]))", 15),
        "rate_overflow_30m": ("sum(rate(bench_metric[30m]))", 60),
        "rate_overflow_1h": ("sum(rate(bench_metric[1h]))", 300),
        "increase_5m": ("sum(increase(bench_metric[5m]))", 60),
        "delta_5m": ("sum(delta(bench_metric[5m]))", 60),
        "rate_grouped_team": ("sum by(team)(rate(bench_metric[5m]))", 60),
        "rate_grouped_region": ("sum by(region)(rate(bench_metric[5m]))", 60),
    }
    canonical_path = pathlib.Path(a.canonical)
    if canonical_path.exists():
        canonical = json.loads(canonical_path.read_text())
    else:
        if a.phase != "base_before":
            raise RuntimeError("Baseline canonical missing")
        canonical = {}

    all_results = {}
    for case, (expr, step) in cases.items():
        query = ("SELECT * FROM prometheusQueryRange("
                 "dense_ts,'" + expr.replace("'", "''") + "',"
                 f"{start},{end},{step}) SETTINGS max_threads=4 "
                 "FORMAT JSONEachRow")
        trials = []
        observed = None
        for iteration in range(a.repeats + 1):
            qid = f"tslab_rateinline_{a.phase}_{case}_{iteration}"
            ms, raw = post("http://127.0.0.1:18191", query, qid)
            value = normalize_any(raw)
            if observed is None:
                observed = value
            else:
                assert_same(observed, value, case, "same_binary")
            if iteration:
                trials.append(ms)
        if case not in canonical:
            canonical[case] = to_json_rows(observed)
        expected = from_json_rows(canonical[case])
        exactness = assert_same(expected, observed, case, a.phase)
        all_results[case] = {
            "expr": expr, "step": step, "p50_ms": pct(trials, .5),
            "p95_ms": pct(trials, .95), "trials_ms": trials,
            "points": exactness["points"],
            "max_abs_error": exactness["max_abs_error"],
            "matches_original_CH": True,
        }
        print("RATE_INLINE_CORNER", json.dumps({
            "phase": a.phase, "case": case, **all_results[case]
        }, sort_keys=True), flush=True)

    if a.phase == "base_before":
        canonical_path.write_text(json.dumps(canonical, sort_keys=True) + "\n")
    output_path = pathlib.Path(a.output)
    output_path.write_text(json.dumps({
        "phase": a.phase, "manifest": manifest,
        "all_exact": True, "cases": all_results
    }, indent=2) + "\n")
    print("RATE_INLINE_CORNERS_DONE", a.phase, len(all_results), flush=True)


if __name__ == "__main__":
    main()
