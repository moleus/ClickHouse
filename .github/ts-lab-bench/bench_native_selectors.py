#!/usr/bin/env python3
"""Benchmark metric-name, regex and tag selectors plus grouped rate under same synthetic input.

The two engines have different rate() semantics, so p50 comparisons are performance-only.
Correctness checks compare ClickHouse exact vs regex fallback; output counts are recorded.
"""
import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

QUERIES = (
    ("sum_rate_exact", "sum(rate(bench_metric[5m]))"),
    ("sum_rate_regex", 'sum(rate({__name__=~"bench_metric"}[5m]))'),
    ("sum_region_east", 'sum(rate(bench_metric{region="east"}[5m]))'),
    ("sum_team_t00", 'sum(rate(bench_metric{team="t00"}[5m]))'),
    ("sum_host_h0001", 'sum(rate(bench_metric{host="h0001"}[5m]))'),
    ("sum_rate_by_region", "sum by (region) (rate(bench_metric[5m]))"),
    ("sum_rate_by_team", "sum by (team) (rate(bench_metric[5m]))"),
)

def request(url, data=None):
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=180) as response:
        return response.read()

def ch_sql(base, sql, body=None):
    params=urllib.parse.urlencode({"query":sql,"allow_experimental_time_series_table":"1","wait_end_of_query":"1"})
    return request(base+"/?"+params,b"" if body is None else body)

def ch_load(base, table, path):
    assert table.replace("_","").isalnum(), "Unsafe SQL identifier"
    ch_sql(base, f"DROP TABLE IF EXISTS {table} SYNC")
    ch_sql(base, f"CREATE TABLE {table} ENGINE=TimeSeries")
    t0=time.perf_counter()
    ch_sql(base, f"INSERT INTO {table} (metric_name, tags, samples) FORMAT JSONEachRow", path.read_bytes())
    print(f"ClickHouse ingestion: {time.perf_counter()-t0:.3f} s",flush=True)

def evaluate(engine, base, table, expr, start, end, step):
    t0=time.perf_counter_ns()
    if engine=="ch":
        sql=f"SELECT * FROM prometheusQueryRange({table}, '{expr.replace(chr(39),chr(39)*2)}', {start}, {end}, {step}) FORMAT JSONEachRow"
        data=request(base+"/?"+urllib.parse.urlencode({"query":sql, "allow_experimental_time_series_table":"1"}), b"")
        rows=[json.loads(x) for x in data.splitlines() if x]
        points=sum(len(r.get("samples",[])) for r in rows)
    else:
        params=urllib.parse.urlencode({"query":expr,"start":start,"end":end,"step":step,"nocache":"1"})
        data=request(base+"/api/v1/query_range?"+params)
        payload=json.loads(data)
        if payload["status"]!="success": raise RuntimeError(payload)
        rows=payload["data"]["result"]
        points=sum(len(r.get("values",[])) for r in rows)
    duration=(time.perf_counter_ns()-t0)/1e6
    return dict(elapsed_ms=duration,rows=rows,series=len(rows),points=points,response_bytes=len(data))

def pctl(items,p):
    values=sorted(items)
    pos=(len(values)-1)*p/100
    idx=int(pos)
    return round(values[idx]+(values[min(idx+1,len(values)-1)]-values[idx])*(pos-idx),3)

def identical_ch_results(a,b):
    if len(a)!=len(b): return False
    sorter=lambda x:json.dumps(x.get("tags",[]))
    for left,right in zip(sorted(a,key=sorter),sorted(b,key=sorter)):
        if left.get("tags")!=right.get("tags") or len(left["samples"])!=len(right["samples"]): return False
        for one,two in zip(left["samples"],right["samples"]):
            if one[0]!=two[0] or abs(float(one[1])-float(two[1]))>1e-9*max(1,abs(float(one[1])),abs(float(two[1]))): return False
    return True

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data",default="tslab-data")
    p.add_argument("--clickhouse",default="")
    p.add_argument("--victoria",default="")
    p.add_argument("--table",default="tslab_benchmark")
    p.add_argument("--load-clickhouse",action="store_true")
    p.add_argument("--warmups",type=int,default=4)
    p.add_argument("--repetitions",type=int,default=20)
    a=p.parse_args()
    root=Path(a.data)
    manifest=json.loads((root/"manifest.json").read_text())
    start,end,step=(manifest[k] for k in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    if a.clickhouse and a.load_clickhouse: ch_load(a.clickhouse,a.table,root/"ch-import.jsonl")
    results={"manifest":manifest,"warmups":a.warmups,"repetitions":a.repetitions,"cases":[]}
    reference=None
    for name,expr in QUERIES:
        for engine,base in (("ch",a.clickhouse),("vm",a.victoria)):
            if not base: continue
            def run(): return evaluate(engine,base,a.table,expr,start,end,step)
            for _ in range(a.warmups): run()
            samples=[run() for _ in range(a.repetitions)]
            last=samples[-1]
            t=[s["elapsed_ms"] for s in samples]
            item=dict(name=name,engine=engine,p50_ms=pctl(t,50),p95_ms=pctl(t,95),mean_ms=round(statistics.mean(t),3),
                      min_ms=round(min(t),3),max_ms=round(max(t),3),
                      series=last["series"],points=last["points"],response_bytes=last["response_bytes"])
            if engine=="ch" and name=="sum_rate_exact": reference=last["rows"]
            if engine=="ch" and name=="sum_rate_regex" and reference is not None:
                item["fastpath_matches_regex_fallback"]=identical_ch_results(reference,last["rows"])
                if not item["fastpath_matches_regex_fallback"]: raise AssertionError("ClickHouse fastpath differs from regex fallback")
            print(json.dumps(item),flush=True)
            results["cases"].append(item)
    (root/"benchmark-results.json").write_text(json.dumps(results,indent=2)+"\n")

if __name__=="__main__":
    main()
