#!/usr/bin/env python3
"""Prototype secure grouped avg: original TimeSeries selector, scalar per-ID rollup.

This is a SQL experiment, not a production C++ rewrite. It uses the public
timeSeriesSelector table function, which keeps the normal TAGS lookup,
time filters, series ID set and query context's stored tag collector.
It avoids direct references to owned internal tables, and skips the dense
per-series nullable arrays of the normal PromQL avg-over-time path.

Guarded workload: avg by(stage)(avg_over_time(metric[1d])) with
disjoint daily windows, no offset, no explicit @, no binary operators.
Correctness is checked point by point against original native ClickHouse.
"""
import argparse
import json
import os
import pathlib
import random

from bench_bucket_cxx_abca import assert_same, normalize_any, pct, post


def fast_query(table, selector, start_ms, end_ms, step_ms, window_ms):
    if window_ms > step_ms or step_ms <= 0:
        raise ValueError("Overlapping windows are not supported")
    points = (end_ms - start_ms) // step_ms + 1
    last_ms = start_ms + (points - 1) * step_ms
    quote_selector = selector.replace("'", "''")
    return f"""WITH
        src AS (
            SELECT id AS series_id, arrayJoin(samples) AS sample
            FROM timeSeriesSelector(
                'default','{table}','{quote_selector}',
                fromUnixTimestamp64Milli({start_ms - window_ms + 1}),
                fromUnixTimestamp64Milli({last_ms}),
                'samples')
        ),
        flat AS (
            SELECT series_id,
                   toUnixTimestamp64Milli(tupleElement(sample, 1)) AS sample_ms,
                   tupleElement(sample, 2) AS sample_v
            FROM src
        ),
        dedup AS (
            SELECT series_id, sample_ms,
                   argMax(sample_v,
                     tuple(toUInt8(not isNaN(sample_v)),
                           if(isNaN(sample_v), 0, sample_v))) AS value
            FROM flat
            WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
            GROUP BY series_id,sample_ms
        ),
        indexed AS (
            SELECT series_id,sample_ms,value,
                   intDiv(sample_ms - {start_ms} + {step_ms} - 1,
                          {step_ms}) AS gi
            FROM dedup
        ),
        per_series AS (
            SELECT series_id, gi, avg(value) AS series_avg
            FROM indexed
            WHERE gi >= 0 AND gi < {points}
              AND sample_ms > {start_ms} + gi * {step_ms} - {window_ms}
              AND sample_ms <= {start_ms} + gi * {step_ms}
            GROUP BY series_id,gi
        ),
        per_stage AS (
            SELECT timeSeriesRemoveAllTagsExcept(
                       timeSeriesIdToGroup(series_id),
                       ['stage']) AS stage_group,
                   gi, avg(series_avg) AS stage_avg
            FROM per_series
            GROUP BY stage_group,gi
        )
    SELECT timeSeriesGroupToTags(stage_group) AS tags,
           CAST(timeSeriesGroupArray(
               fromUnixTimestamp64Milli({start_ms} + gi * {step_ms}),
               stage_avg),
               'Array(Tuple(DateTime64(3), Float64))') AS samples
    FROM per_stage
    GROUP BY stage_group
    FORMAT JSONEachRow"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--warmups", type=int, default=1)
    p.add_argument("--repeats", type=int, default=5)
    a = p.parse_args()

    m = json.loads(pathlib.Path(a.manifest).read_text())
    start = m["query_start_epoch"]
    end = m["query_end_epoch"]
    step = m["step_seconds"]
    cases = {
        "all": "",
        "branch_main": '{branch="main"}',
        "project00": '{project="project-00"}',
        "stage_exact": '{stage="stage-0007"}',
        "stage_regex": '{stage=~"stage-000[0-9]"}',
    }
    out = {"manifest":m, "all_exact":True, "cases":{},
           "notes":"Selector SQL retains tags ID resolver; correctness via native CH; independent access-policy verification still required"}
    qprefix="tslab_selector_scalar_"+os.environ.get("GITHUB_RUN_ID","local")
    for case,match in cases.items():
        selector=m["metric"]+match
        expr=f"avg by(stage)(avg_over_time({selector}[1d]))"
        original=(f"SELECT * FROM prometheusQueryRange(jenkins_ts, "
                  f"'{expr.replace(chr(39), chr(39)*2)}', "
                  f"{start},{end},{step}) FORMAT JSONEachRow")
        candidate=fast_query("jenkins_ts",selector,start*1000,end*1000,step*1000,86400000)
        variants={"native_before":original,"selector_scalar":candidate,
                  "native_after":original}
        reference=None
        res={}
        for variant,sql in variants.items():
            elapsed=[]
            for trial in range(a.warmups+a.repeats):
                query_id=f"{qprefix}_{case}_{variant}_{trial}"
                ms,raw=post("http://127.0.0.1:18191",sql,query_id)
                actual=normalize_any(raw)
                if reference is None:
                    reference=actual
                    assert reference,("empty original reference",case)
                exact=assert_same(reference,actual,case,variant)
                if trial >= a.warmups:
                    elapsed.append(ms)
            res[variant]={"p50_ms":pct(elapsed,.5),
                          "p95_ms":pct(elapsed,.95),
                          "trials_ms":elapsed, "points":exact["points"]}
        out["cases"][case]={"selector":selector,"variants":res,"all_exact":True,
                            "SQL":candidate}
        print("TSLAB_SELECTOR_SCALAR_CASE",json.dumps({
            "case":case,"points":len(reference),
            "p50_ms":{x:y["p50_ms"] for x,y in res.items()},
            "all_exact":True
        },sort_keys=True),flush=True)

    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
         "SELECT query_id,read_rows,read_bytes,memory_usage FROM system.query_log "
         "WHERE type='QueryFinish' AND startsWith(query_id,'"+qprefix+"') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip()
          for x in [json.loads(line)]}
    for case in cases:
        for variant in ("native_before","selector_scalar","native_after"):
            rows=[logs[f"{qprefix}_{case}_{variant}_{i}"]
                  for i in range(a.warmups,a.warmups+a.repeats)]
            out["cases"][case]["variants"][variant].update({
                "mem_p95":pct([x["memory_usage"] for x in rows],.95),
                "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5)})
    pathlib.Path(a.output).write_text(json.dumps(out,indent=2)+"\n")
    print("TSLAB_SELECTOR_SCALAR_DONE",len(cases),flush=True)


if __name__=="__main__":
    main()
