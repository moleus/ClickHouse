#!/usr/bin/env python3
"""Compare writer reserve cap vs pinned baseline on one 14d data set.

ClickHouse output labels, timestamps and values must match in all three
phases. VM MetricsQL rate is benchmarked but not treated as PromQL-equivalent.
"""
import gzip
import json
import math
import os
import pathlib
import urllib.request

ROOT=pathlib.Path('evidence')

def percentile(vals,q):
    data=sorted(vals)
    if not data: raise AssertionError('No timing samples')
    k=(len(data)-1)*q
    p=int(k)
    return round(data[p]+(data[min(p+1,len(data)-1)]-data[p])*(k-p),3)

def inner(folder,pattern):
    paths=list((ROOT/folder).glob(pattern))
    if len(paths)!=1: raise AssertionError(('missing response',folder,pattern,paths))
    rows=[json.loads(x) for x in gzip.decompress(paths[0].read_bytes()).decode().splitlines() if x.strip()]
    if len(rows)!=1 or rows[0]['tags']!=[]: raise AssertionError(('unexpected result',folder))
    return rows[0]['samples']

def main():
    run=os.environ['GITHUB_RUN_ID']
    phase_folders={'baseline':'queries','writer-cap':'writer-queries','baseline-recheck':'recheck-queries'}
    summaries={k:json.loads((ROOT/v/'summary.json').read_text()) for k,v in phase_folders.items()}
    snapshots={p:inner(folder,'*clickhouse-1m-response.gz') for p,folder in phase_folders.items()}
    reference=snapshots['baseline']
    if len(reference)!=20155:raise AssertionError(('unexpected reference timestamps',len(reference)))
    for phase,points in snapshots.items():
        if len(points)!=len(reference):raise AssertionError(('length',phase,len(points)))
        for (t,x),(u,y) in zip(reference,points):
            if t!=u or not math.isclose(float(x),float(y),rel_tol=1e-12,abs_tol=1e-9):
                raise AssertionError(('value difference',phase,t,x,u,y))
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18191/',data=b'SYSTEM FLUSH LOGS'),timeout=180) as _:pass
    prefix='tslab_writer_'+run+'_'
    sql=("SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,exception_code "
         "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'"+prefix+"') FORMAT JSONEachRow")
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18191/',data=sql.encode()),timeout=180) as response:
        raw=response.read().decode()
    logs={x['query_id']:x for line in raw.splitlines() if line for x in [json.loads(line)]}
    results={}
    for phase,data in summaries.items():
        results[phase]={}
        for record in data['cases']:
            step=record['step_seconds']
            measured=[k for k in record['runs'] if k['engine']=='clickhouse']
            if len(measured)!=data['repeats']:raise AssertionError(('trial count',phase,step,len(measured)))
            log_ids=[prefix+phase+'_'+str(step)+'_'+str(i) for i in range(1,data['repeats']+1)]
            matched=[logs[x] for x in log_ids if x in logs]
            if len(matched)!=len(log_ids):raise AssertionError(('missing logs',phase,step,len(matched)))
            if any(x['exception_code']!=0 for x in matched):raise AssertionError(('ClickHouse query error',phase,step))
            results[phase][step]={
                'p50_ms':percentile([x['wall_ms'] for x in measured],.5),
                'p95_ms':percentile([x['wall_ms'] for x in measured],.95),
                'query_memory_p50_bytes':percentile([x['memory_usage'] for x in matched],.5),
                'query_memory_p95_bytes':percentile([x['memory_usage'] for x in matched],.95),
                'query_memory_peak_bytes':max(x['memory_usage'] for x in matched),
                'read_rows_p50':percentile([x['read_rows'] for x in matched],.5),
                'read_bytes_p50':percentile([x['read_bytes'] for x in matched],.5),
                'actual_ch_rss_peak_bytes':max((x['rss_peak_bytes'] for x in measured if x.get('rss_peak_bytes') is not None),default=None),
                'actual_ch_cpu_seconds_p50':percentile([x['cpu_seconds'] for x in measured if x.get('cpu_seconds') is not None],.5) if any(x.get('cpu_seconds') is not None for x in measured) else None,
                'vm_metricsql_p50_ms':record['victoria_p50_ms'],
            }
    result={'run_id':run,'candidate_binary':'tslab-writer-cap-binary-88681d67f9821451acdf369efcbc3482bec7adfe',
            'source_sha':'52529f0c711566d696917678bf15e7068772fdde',
            'exact_20155_CH_timestamps_labels_values_across_aba':True,
            'semantics':'CH-CH strict equality tolerance 1e-12; VM MetricsQL rate differs',
            'p50_p95_and_query_log':results,'recorded_finish_events':len(logs)}
    (ROOT/'writer-cap-aba-result.json').write_text(json.dumps(result,indent=2)+'\n')
    (ROOT/'writer-cap-querylog.jsonl').write_text(raw)
    print('WRITER_CAP_ABA',json.dumps(result,sort_keys=True),flush=True)

if __name__=='__main__':
    main()
