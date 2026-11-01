#!/usr/bin/env python3
"""Capture every PromQL selector response and check exact-match CXX patch correctness.

Run once per A/B/A phase against the same persisted samples table.
"""
import argparse
import datetime as dt
import json
import pathlib
import urllib.parse
import urllib.request

QUERIES = (
    ('sum_rate_exact', 'sum(rate(bench_metric[5m]))'),
    ('sum_rate_regex', 'sum(rate({__name__=~"bench_metric"}[5m]))'),
    ('sum_region_east', 'sum(rate(bench_metric{region="east"}[5m]))'),
    ('sum_team_t00', 'sum(rate(bench_metric{team="t00"}[5m]))'),
    ('sum_host_h0001', 'sum(rate(bench_metric{host="h0001"}[5m]))'),
    ('sum_rate_by_region', 'sum by (region) (rate(bench_metric[5m]))'),
    ('sum_rate_by_team', 'sum by (team) (rate(bench_metric[5m]))'),
)

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--manifest',required=True)
    a.add_argument('--ch',default='http://127.0.0.1:18191')
    a.add_argument('--table',required=True)
    a.add_argument('--phase',required=True)
    a.add_argument('--output',required=True)
    z=a.parse_args()
    m=json.loads(pathlib.Path(z.manifest).read_text())
    start,end,step=(m[k] for k in ('eval_start_epoch','eval_end_epoch','eval_step_seconds'))
    result={'phase':z.phase,'input_series':m['series'],'samples':m['samples_total'],'cases':{}}
    for name,expr in QUERIES:
        sql=("SELECT * FROM prometheusQueryRange("+z.table+", '"+expr.replace("'","''")+
             "', "+str(start)+", "+str(end)+", "+str(step)+") FORMAT JSONEachRow")
        q=urllib.parse.urlencode({'allow_experimental_time_series_table':1,'wait_end_of_query':1,'log_comment':'tslab_selector_'+z.phase+'_'+name})
        req=urllib.request.Request(z.ch.rstrip('/')+'/?'+q,sql.encode())
        with urllib.request.urlopen(req,timeout=180) as rsp:
            raw=rsp.read()
        rows=[json.loads(s) for s in raw.splitlines() if s.strip()]
        def convert(r):
            labels=tuple(tuple(v) for v in r['tags'])
            samples=tuple((str(ts),float(val)) for ts,val in r['samples'])
            return {'labels':labels,'samples':samples}
        normalized=sorted((convert(row) for row in rows),key=lambda obj:obj['labels'])
        result['cases'][name]=normalized
        print('CAPTURE',z.phase,name,'series',len(normalized),'points',sum(len(row['samples']) for row in normalized),flush=True)
    pathlib.Path(z.output).write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':
    main()
