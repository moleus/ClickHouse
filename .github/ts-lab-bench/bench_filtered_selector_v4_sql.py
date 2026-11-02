#!/usr/bin/env python3
"""A/B/A filtered PromQL selectors: native converter versus native one-point SQL."""
import argparse
import json
import os
import pathlib

from bench_bucket_cxx_abca import assert_same, normalize_any, pct, post
from bench_native_one_point_sql import translated_query_no_sql_dedup

METRIC = "tslab_curve_67200"
STEPS = (3, 40)
CASES = [
    ("whole_metric", METRIC),
    ("branch_eq", METRIC + '{branch="lab"}'),
    ("branch_regex", METRIC + '{branch=~"l.*"}'),
    ("branch_negative", METRIC + '{branch!="other"}'),
    ("stage_exact", METRIC + '{stage="stage-0007"}'),
    ("stage_regex", METRIC + '{stage=~"stage-00(0[0-9]|1[0-9])"}'),
    ("many_matchers", METRIC + '{branch="lab",stage=~"stage-000[7-9]",project="curve"}'),
    ("build_regex", METRIC + '{build=~"curve-67200-0000(00[0-9][0-9]|01[0-9][0-9])"}'),
    ("empty", METRIC + '{branch="not-existing"}'),
    ("sql_quote", METRIC + """{branch=~"lab'|lab"}"""),
]
PHASES = ("native_before", "one_point", "native_after")


def sql_string(value):
    # ClickHouse's SQL single-quoted string escaping; NEVER use raw regex as SQL.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def translated_filtered(start, end, selector):
    q = translated_query_no_sql_dedup(start, end, 86400, table="jenkins_ts", metric=METRIC)
    old = f"FROM timeSeriesSelector('default','jenkins_ts','{METRIC}',"
    new = f"FROM timeSeriesSelector('default','jenkins_ts',{sql_string(selector)},"
    assert q.count(old) == 1
    return q.replace(old, new)


def native_filtered(start, end, selector):
    expression = f"avg by(stage)(avg_over_time({selector}[1d]))"
    return (
        f"SELECT * FROM prometheusQueryRange(jenkins_ts,{sql_string(expression)},"
        f"{start},{end},86400) FORMAT JSONEachRow"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    meta = json.loads(pathlib.Path(args.manifest).read_text())
    end = int(meta["query_end_epoch"])
    prefix = "filterv4_" + os.environ.get("GITHUB_RUN_ID", "local")
    output = []
    for steps in STEPS:
        start = end - (steps - 1) * 86400
        for name, selector in CASES:
            reference = None
            reference_set = False
            phases = {}
            for phase in PHASES:
                sql = (native_filtered(start, end, selector)
                       if phase != "one_point" else translated_filtered(start, end, selector))
                measurements = []
                qids = []
                for trial in range(7):
                    qid = f"{prefix}_{steps}_{name}_{phase}_{trial}"
                    elapsed, raw = post("http://127.0.0.1:18191", sql, qid)
                    observed = normalize_any(raw)
                    if not reference_set:
                        reference = observed
                        reference_set = True
                    else:
                        assert_same(reference, observed, f"{name}_{steps}", phase)
                    if trial >= 2:
                        measurements.append(elapsed)
                        qids.append(qid)
                phases[phase] = {
                    "p50_ms": pct(measurements, .5),
                    "p95_ms": pct(measurements, .95),
                    "trials_ms": measurements,
                    "query_ids": qids,
                    "points": len(reference),
                }
            record = {"case": name, "selector": selector, "steps": steps, "phases": phases}
            output.append(record)
            print("FILTERED_SQL_ABA", json.dumps({
                "case": name,
                "steps": steps,
                "points": len(reference),
                "before_ms": phases["native_before"]["p50_ms"],
                "candidate_ms": phases["one_point"]["p50_ms"],
                "after_ms": phases["native_after"]["p50_ms"],
                "all_equal": True,
            }, separators=(",", ":")), flush=True)

    post("http://127.0.0.1:18191", "SYSTEM FLUSH LOGS")
    _, raw = post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,{sql_string(prefix)}) FORMAT JSONEachRow")
    logs = {item["query_id"]: item for line in raw.splitlines() if line.strip()
            for item in [json.loads(line)]}
    for record in output:
        for ph in record["phases"].values():
            rows = [logs[q] for q in ph.pop("query_ids")]
            ph.update({
                "server_ms_p50": pct([x["query_duration_ms"] for x in rows], .5),
                "read_rows_p50": pct([x["read_rows"] for x in rows], .5),
                "read_bytes_p50": pct([x["read_bytes"] for x in rows], .5),
                "memory_p95_bytes": pct([x["memory_usage"] for x in rows], .95),
            })
    pathlib.Path(args.output).write_text(json.dumps(
        {"all_equal": True, "cases": output}, indent=2) + "\n")
    print("FILTERED_SQL_SUMMARY",
          json.dumps({"all_equal": True, "cases": len(output)}, separators=(",", ":")),
          flush=True)


if __name__ == "__main__":
    main()
