#!/usr/bin/env python3
"""TimeSeries long sparse PromQL: exact HTTP output and bounded-query memory.

Runs on a disposable local GitHub-hosted runner. All metrics are emitted as JSON.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def parser():
    a=argparse.ArgumentParser()
    a.add_argument('--ch',default='http://127.0.0.1:18191')
    a.add_argument('--vm',default='http://127.0.0.1:8430')
    a.add_argument('--ch-pid',type=int)
    a.add_argument('--vm-pid',type=int)
    a.add_argument('--manifest',required=True)
    a.add_argument('--table',default='long_ts')
    a.add_argument('--output',required=True)
    a.add_argument('--label',required=True)
    a.add_argument('--steps',default='900,300,60')
    a.add_argument('--repeats',type=int,default=2)
    a.add_argument('--ch-query-memory',type=int,default=1200000000)
    return a.parse_args()


def sample_rss(pid):
    if not pid:return None
    try:
        for line in pathlib.Path('/proc',str(pid),'status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])*1024
    except (OSError,ValueError):
        pass
    return None


def clock_cpu(pid):
    if not pid:return None
    try:
        data=pathlib.Path('/proc',str(pid),'stat').read_text()
        fields=data[data.rfind(')')+2:].split()
        return (int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
    except (OSError,ValueError,IndexError):
        return None


def request(url,payload=None,pid=None):
    headers={'Accept':'application/json'}
    if payload is not None:
        headers['Content-Type']='text/plain; charset=utf-8'
    req=urllib.request.Request(url,data=payload,headers=headers)
    stop=threading.Event()
    peak=[sample_rss(pid)]
    def watcher():
        while not stop.wait(0.05):
            rss=sample_rss(pid)
            if rss is not None:peak[0]=max(peak[0] or 0,rss)
    t=threading.Thread(target=watcher,daemon=True)
    t.start()
    cpu0=clock_cpu(pid)
    start=time.perf_counter()
    status=None
    result=b''
    try:
        with urllib.request.urlopen(req,timeout=370) as rsp:
            status=rsp.status
            result=rsp.read()
    except urllib.error.HTTPError as e:
        status=e.code
        result=e.read()
    except Exception as e:
        status=0
        result=str(e).encode()
    seconds=time.perf_counter()-start
    cpu1=clock_cpu(pid)
    stop.set();t.join(timeout=1)
    result_obj={'http_status':status,'wall_ms':round(seconds*1000,3),
                'cpu_seconds':round(cpu1-cpu0,3) if cpu0 is not None and cpu1 is not None else None,
                'rss_peak_bytes':peak[0],'payload_sha256':hashlib.sha256(result).hexdigest(),
                'response_bytes':len(result)}
    return result_obj,result


def from_ch(response):
    rows=[json.loads(s) for s in response.splitlines() if s.strip()]
    if len(rows)!=1:raise ValueError('Expected one ungrouped sum series: '+str(len(rows)))
    if rows[0].get('tags')!=[]:raise ValueError('Expected no labels: '+str(rows[0].get('tags')))
    vals={}
    for timestamp,value in rows[0].get('samples',[]):
        if isinstance(timestamp,(int,float)): epoch=int(timestamp)
        else:epoch=int(dt.datetime.fromisoformat(timestamp.replace(' ','T')).replace(tzinfo=dt.timezone.utc).timestamp())
        vals[epoch]=float(value)
    return vals


def from_vm(response):
    payload=json.loads(response)
    if payload.get('status')!='success':raise ValueError(payload)
    data=payload.get('data',{}).get('result',[])
    if len(data)!=1:raise ValueError('Expected one VM ungrouped sum series: '+str(len(data)))
    return {int(x):float(y) for x,y in data[0].get('values',[])}


def summary_numbers(vals):
    if not vals:return {'points':0}
    return {'points':len(vals),'first_epoch':min(vals),'last_epoch':max(vals),
            'min_value':min(vals.values()),'max_value':max(vals.values()),
            'value_sum':sum(vals.values())}


def main():
    args=parser()
    manifest=json.load(open(args.manifest))
    out=pathlib.Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    expression='sum(rate('+manifest['metric']+'[5m]))'
    metrics={'label':args.label,'manifest':manifest,'expr':expression,
             'ch_query_mem_limit':args.ch_query_memory,'repeats':args.repeats,'cases':[]}
    for step in [int(x) for x in args.steps.split(',')]:
        start=manifest['query_start_epoch'];end=manifest['query_end_epoch']
        query='SELECT * FROM prometheusQueryRange('+args.table+', '+json.dumps(expression)+', '+str(start)+', '+str(end)+', '+str(step)+') FORMAT JSONEachRow'
        url_ch=args.ch+'/?'+urllib.parse.urlencode({
            'allow_experimental_time_series_table':1,
            'max_memory_usage':args.ch_query_memory,
            'max_bytes_before_external_group_by':250000000,
            'max_threads':4,'max_execution_time':350})
        params=urllib.parse.urlencode({'query':expression,'start':start,'end':end,'step':step,'nocache':1})
        url_vm=args.vm+'/api/v1/query_range?'+params
        output={'step_seconds':step,'expected_output_points':((end-start)//step)+1,'runs':[],'comparison':None}
        for n in range(args.repeats+1):
            versions={}
            for engine,url,payload,pid in [('victoria',url_vm,None,args.vm_pid),('clickhouse',url_ch,query.encode(),args.ch_pid)]:
                result,data=request(url,payload,pid)
                if result['http_status']==200:
                    try:
                        parsed=from_vm(data) if engine=='victoria' else from_ch(data)
                        result['output']=summary_numbers(parsed)
                        versions[engine]=parsed
                    except Exception as ex:
                        result['parser_error']=str(ex)
                        result['response_start']=data[:900].decode('utf-8','replace')
                else:
                    result['response_start']=data[:1700].decode('utf-8','replace')
                if n>0:
                    output['runs'].append({'engine':engine,**result})
                elif n==0:
                    output.setdefault('warmup',{})[engine]=result
                if n==args.repeats and step==60:
                    with gzip.open(out/(args.label+'-'+engine+'-1m-response.gz'),'wb') as f:f.write(data)
            if n==args.repeats and len(versions)==2:
                c=versions['clickhouse'];v=versions['victoria']
                both=sorted(set(c)&set(v))
                delta=[(abs(c[t]-v[t]),abs(c[t]-v[t])/max(abs(v[t]),1e-12)) for t in both]
                output['comparison']={'common_points':len(both),'ch_points':len(c),'vm_points':len(v),
                         'matching_timestamps':len(c)==len(v)==len(both),
                         'max_abs_error':max((x[0] for x in delta),default=None),
                         'max_rel_error':max((x[1] for x in delta),default=None)}
            print(json.dumps({'step':step,'run':n,'status':[(x['engine'],x['http_status'],x['wall_ms']) for x in output['runs'][-2:]]}),flush=True)
        for engine in ['victoria','clickhouse']:
            values=sorted(x['wall_ms'] for x in output['runs'] if x['engine']==engine)
            output[engine+'_p50_ms']=round(statistics.median(values),3) if values else None
            output[engine+'_max_ms']=max(values,default=None)
        metrics['cases'].append(output)
        (out/'summary.json').write_text(json.dumps(metrics,indent=2)+'\n')
    print(json.dumps({'label':args.label,'cases':[{k:v for k,v in q.items() if k not in ('runs','warmup')} for q in metrics['cases']]},indent=2),flush=True)

if __name__=='__main__':
    main()
