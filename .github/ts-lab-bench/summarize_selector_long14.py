#!/usr/bin/env python3
"""Summarize 14-day churn selector A/B/A, including broad-matcher risks."""
import json
import math
import os
import pathlib
import urllib.request

def percentile(xs, q):
    a=sorted(xs)
    p=(len(a)-1)*q
    i=int(p)
    return round(a[i]+(a[min(i+1,len(a)-1)]-a[i])*(p-i),3)

def main():
    root=pathlib.Path('evidence')
    phases=['baseline','candidate','recheck']
    outputs={p:json.loads((root/('churn-'+p+'.json')).read_text()) for p in phases}
    run=os.environ['GITHUB_RUN_ID']
    query="SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'tslabedge_"+run+"_') FORMAT JSONEachRow"
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18191/',data=b'SYSTEM FLUSH LOGS'),timeout=90) as _: pass
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18191/',data=query.encode()),timeout=90) as r: data=r.read().decode()
    rows={r['query_id']:r for line in data.splitlines() if line for r in [json.loads(line)]}
    summary=json.loads((root/'churn-exact-validation.json').read_text())
    results={}
    for case in summary['cases']:
        results[case]={}
        for phase in phases:
            timing=outputs[phase]['timings'][case]
            n=timing['repetitions']
            w=timing['warmups']
            prefix=timing['query_id_prefix']
            observed=[rows[prefix+str(i)] for i in range(w,w+n) if prefix+str(i) in rows]
            if len(observed)!=n: raise AssertionError(('query_log missing',case,phase,len(observed),n))
            if any(r['exception_code'] for r in observed): raise AssertionError(('query error',case,phase))
            results[case][phase]={
               'p50_ms':timing['p50_ms'],'p95_ms':timing['p95_ms'],
               'read_rows_p50':percentile([r['read_rows'] for r in observed],.5),
               'read_bytes_p50':percentile([r['read_bytes'] for r in observed],.5),
               'query_memory_p95_bytes':percentile([r['memory_usage'] for r in observed],.95),
               'peak_memory_bytes':max(r['memory_usage'] for r in observed),
               'query_duration_p50_ms':percentile([r['query_duration_ms'] for r in observed],.5)}
    summary['performance']=results
    summary['query_count']=len(rows)
    (root/'churn-exact-validation.json').write_text(json.dumps(summary,indent=2)+'\n')
    (root/'edge-querylog.jsonl').write_text(data)
    print('LONG14_SELECTOR_ABA',json.dumps(results,sort_keys=True),flush=True)

if __name__=='__main__':
    main()
