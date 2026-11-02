#!/usr/bin/env python3
"""A/B/A on a tiny metric embedded in a large TimeSeries table.

The v2 guard checks TOTAL SAMPLES rows, not rows selected by a metric. This
benchmark tests the expected false-positive: 210 short-lived series of
lab_small_metric embedded next to 282036 series of an unrelated metric.
Values/labels/timestamps must match original native PromQL. The original
baseline is restored after the candidate phase.
"""
import argparse
import json
import os
from pathlib import Path

from bench_bucket_cxx_abca import assert_same, normalize_any, pct, post


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", required=True,
                   choices=["base_before", "selector_scalar", "base_after"])
    p.add_argument("--jenkins-manifest", required=True)
    p.add_argument("--canonical", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--repeats", type=int, default=4)
    p.add_argument("--warmups", type=int, default=1)
    a = p.parse_args()
    manifest = json.loads(Path(a.jenkins_manifest).read_text())

    _, count_raw = post("http://127.0.0.1:18191",
        "SELECT count() FROM jenkins_ts "
        "WHERE metric_name = 'lab_small_metric' FORMAT TSV")
    count = int(count_raw.decode().strip())
    if count < 150 or count > 210:
        raise AssertionError(("small metric cardinality invalid", count))

    _, total_raw = post("http://127.0.0.1:18191",
        "SELECT sum(rows) FROM system.parts WHERE active=1 "
        "AND database='default' AND startsWith(table,'.inner_id.jenkins_ts.samples.') FORMAT TSV")
    total = int(total_raw.decode().strip() or 0)
    if total < 32768:
        raise AssertionError(("large table guard does not apply",total))

    sql = ("SELECT * FROM prometheusQueryRange(jenkins_ts,"
           "'avg by(stage)(avg_over_time(lab_small_metric[1d]))',"
           f"{manifest['query_start_epoch']},"
           f"{manifest['query_end_epoch']},"
           f"{manifest['step_seconds']}) FORMAT JSONEachRow")
    canonical_path = Path(a.canonical)
    if a.phase == "base_before":
        if canonical_path.exists():
            raise AssertionError(("unexpected existing canonical",str(canonical_path)))
        canonical = None
    else:
        canonical = json.loads(canonical_path.read_text())
    qprefix = "tslab_smallmetric_"+os.environ.get("GITHUB_RUN_ID","local")
    timings=[]
    observed_first=None
    qids=[]
    for i in range(a.warmups+a.repeats):
        qid=f"{qprefix}_{a.phase}_{i}"
        elapsed,raw=post("http://127.0.0.1:18191",sql,qid)
        output=normalize_any(raw)
        if not output:
            raise AssertionError(("empty small metric result",a.phase,i))
        if observed_first is None:
            observed_first=output
        else:
            assert_same(observed_first,output,"small_metric","same_phase")
        if canonical is not None:
            expected={((tuple(tuple(kv) for kv in tags)),int(ts)):float(v)
                      for tags,ts,v in canonical["rows"]}
            assert_same(expected,output,"small_metric",a.phase)
        if i >= a.warmups:
            timings.append(elapsed)
            qids.append(qid)
    if a.phase == "base_before":
        rows=[[list([k,v] for k,v in label),ts,value]
              for (label,ts),value in sorted(observed_first.items())]
        canonical={"rows":rows}
        canonical_path.write_text(json.dumps(canonical)+"\n")

    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{qprefix}_{a.phase}_') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip()
          for x in [json.loads(line)]}
    selected=[logs[qid] for qid in qids]
    report={
        "phase":a.phase,"selected_metric_series":count,
        "total_samples_table_rows":total,
        "input_metric":"lab_small_metric",
        "points":len(observed_first),
        "all_exact":True,"p50_ms":pct(timings,.5),
        "p95_ms":pct(timings,.95),"trials_ms":timings,
        "read_rows_p50":pct([x["read_rows"] for x in selected],.5),
        "read_bytes_p50":pct([x["read_bytes"] for x in selected],.5),
        "tracked_memory_p95_bytes":pct([x["memory_usage"] for x in selected],.95),
    }
    Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("TSLAB_SMALL_METRIC_ABA",json.dumps(report,sort_keys=True),flush=True)


if __name__ == "__main__":
    main()
