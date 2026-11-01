#!/usr/bin/env python3
"""Isolate ClickHouse TimeSeries selector planning and reading costs.

All queries use one pinned CXX binary, one runner and one dataset.
HTTP times include Python loopback overhead. Read rows per case are
available via ClickHouse system.query_log and EXPLAIN evidence.
"""
import argparse
import json
import pathlib
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

def run(base,q,name):
    url=base.rstrip("/")+"/?"+urllib.parse.urlencode({
        'wait_end_of_query':1,'allow_experimental_time_series_table':1,
        'log_comment':'tslab_floor_'+name})
    start=time.perf_counter()
    req=urllib.request.Request(url,q.encode(),method='POST')
    try:
        with urllib.request.urlopen(req,timeout=200) as r:body=r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError('HTTP '+str(e.code)+' query '+name+': '+e.read().decode(errors='replace')[:1600]) from e
    return (time.perf_counter()-start)*1000,len(body)

def pct(vals,p):
    a=sorted(vals)
    k=(len(a)-1)*p
    i=int(k)
    return round(a[i]+(a[min(i+1,len(a)-1)]-a[i])*(k-i),3)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--ch",default="http://127.0.0.1:18191")
    ap.add_argument("--table",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--repeats",type=int,default=24)
    ap.add_argument("--warmups",type=int,default=5)
    a=ap.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    def sql(q):
        return run(a.ch,q,"setup")[1]
    def text(q):
        url=a.ch.rstrip("/")+"/?wait_end_of_query=1"
        with urllib.request.urlopen(urllib.request.Request(url,q.encode()),timeout=90) as response:
            return response.read().decode("utf-8").strip()
    names=text("SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.') FORMAT TSVRaw").splitlines()
    def exact(s):
        matches=[x for x in names if x.startswith('.inner_id.'+s+'.')]
        if len(matches)!=1:raise AssertionError((s,matches))
        return "default."+chr(96)+matches[0].replace(chr(96),chr(96)*2)+chr(96)
    tags=exact('tags')
    samples=exact('samples')
    b,e,step=(m[x] for x in ('eval_start_epoch','eval_end_epoch','eval_step_seconds'))
    exp='sum(rate(bench_metric{host="h0001"}[5m]))'
    expr_zero='sum(rate(bench_metric{host="not-present"}[5m]))'
    q_label='metric_name = '+chr(39)+'bench_metric'+chr(39)+' AND tags['+chr(39)+'host'+chr(39)+'] = '+chr(39)+'h0001'+chr(39)
    fn="timeSeriesSelector('default','"+a.table+"','bench_metric{host=\"h0001\"}',fromUnixTimestamp64Milli("+str((b-300)*1000)+"),fromUnixTimestamp64Milli("+str(e*1000)+"),'samples')"
    def promql(ex):
        return "SELECT * FROM prometheusQueryRange("+a.table+", '"+ex.replace("'","''")+"',"+str(b)+","+str(e)+","+str(step)+") FORMAT JSONEachRow"
    cases={
        'select1':"SELECT 1 FORMAT TSVRaw",
        'tags_exact':"SELECT count() FROM "+tags+" WHERE "+q_label+" FORMAT TSVRaw",
        'samples_id_set':"SELECT sum(length(samples)) FROM "+samples+" WHERE id IN (SELECT id FROM "+tags+" WHERE "+q_label+") FORMAT TSVRaw",
        'timeSeriesSelector':"SELECT count() FROM "+fn+" FORMAT TSVRaw",
        'promql_host':promql(exp),
        'promql_missing':promql(expr_zero),
        'promql_all':promql("sum(rate(bench_metric[5m]))"),
    }
    result={"series":m['series'],"input_samples":m['samples_total'],"table":a.table,
            "inner_tags":tags,"inner_samples":samples,"warmups":a.warmups,"repeats":a.repeats,
            "queries":cases,"errors":{},"measurements":{}}
    active={}
    for name,q in cases.items():
        try:
            for _ in range(a.warmups):run(a.ch,q,name)
            active[name]=q
        except Exception as ex:
            result["errors"][name]=str(ex)
            print("QUERY_UNSUPPORTED",name,str(ex)[:600],flush=True)
    timings={name:[] for name in active}
    rng=random.Random(260026)
    for _ in range(a.repeats):
        order=list(active)
        rng.shuffle(order)
        for name in order:
            ms,size=run(a.ch,active[name],name)
            timings[name].append(round(ms,3))
    for name,x in timings.items():
        result['measurements'][name]={"p50_ms":pct(x,.5),"p95_ms":pct(x,.95),
                                      "p99_ms":pct(x,.99),"trials_ms":x}
    output=pathlib.Path(a.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    for name in ['samples_id_set','timeSeriesSelector','promql_host']:
        if name not in active:continue
        for ex_type in ['PIPELINE','indexes=1']:
            try:
                q="EXPLAIN "+ex_type+" "+active[name].replace(" FORMAT JSONEachRow","").replace(" FORMAT TSVRaw","")
                detail=text(q)
                output.with_name(output.stem+'-'+name+'-'+ex_type.replace('=','')+'.txt').write_text(detail)
            except Exception as ex:result['errors']['EXPLAIN_'+name+'_'+ex_type]=str(ex)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print('PLANNER_FLOOR',json.dumps({"series":m['series'],"timings":result["measurements"],"errors":result["errors"]},indent=2),flush=True)

if __name__=="__main__":
    main()
