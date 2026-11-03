#!/usr/bin/env python3
"""Memory retention A/B: exact O1 binary/1.33m fixture under glibc malloc tunables.

Two factors are controlled only through process startup environment:
MALLOC_ARENA_MAX=2 and MALLOC_TRIM_THRESHOLD_=131072. Both the binary and
fixture are identical. Capture RSS, smaps_rollup, glibc environment and
per-query memory/latency; a 14d OOM is an observation, not an invalid run.
"""
import argparse,json,os,pathlib,sys
sys.path.insert(0,'.github/ts-lab-cbo')
from probe_native_rss_lifetime import one,save,rss,FIXTURE
from bench_granularity_v11 import sql
EXPECTED={
 'default':{},
 'trim':{'MALLOC_TRIM_THRESHOLD_':'131072'},
 'arena2':{'MALLOC_ARENA_MAX':'2'},
 'trim_arena2':{'MALLOC_TRIM_THRESHOLD_':'131072','MALLOC_ARENA_MAX':'2'}
}
def process():
    parent=int((pathlib.Path(os.environ['RUNNER_TEMP'])/'ch.pid').read_text())
    children=(pathlib.Path(f'/proc/{parent}/task/{parent}/children').read_text().split())
    for pid in children:
        status=pathlib.Path(f'/proc/{pid}/status').read_text()
        if any(line.startswith('Name:') and 'clickhouse' in line.lower() for line in status.splitlines()):
            return int(pid)
    raise RuntimeError('ClickHouse server process not found in watchdog children')
def snap():
    pid=process()
    status=pathlib.Path(f'/proc/{pid}/status').read_text()
    fields={}
    for line in status.splitlines():
        if line.startswith(('VmRSS:','VmHWM:','VmSize:','VmData:','RssAnon:','RssFile:','Threads:')):
            key,val=line.split(':',1);fields[key]=val.strip()
    rollup=pathlib.Path(f'/proc/{pid}/smaps_rollup')
    smaps={}
    if rollup.exists():
        for line in rollup.read_text().splitlines():
            if line.startswith(('Rss:','Pss:','Pss_Anon:','Pss_File:','Private_Clean:','Private_Dirty:','Anonymous:','Swap:')):
                key,val=line.split(':',1);smaps[key]=val.strip()
    env=pathlib.Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    mallocenv={}
    for var in env:
        if var.startswith((b'MALLOC_',b'GLIBC_TUNABLES=')):
            key,val=var.split(b'=',1);mallocenv[key.decode()]=val.decode(errors='replace')
    out={'pid':pid,'status':fields,'smaps_rollup':smaps,'allocator_env':mallocenv}
    for qname,query in {
      'current_memory':"SELECT metric,value FROM system.metrics WHERE metric LIKE 'Memory%' OR metric LIKE '%Cache%' ORDER BY metric FORMAT JSONEachRow",
      'async_memory':"SELECT metric,value FROM system.asynchronous_metrics WHERE metric LIKE 'jemalloc%' OR metric LIKE 'Memory%' ORDER BY metric FORMAT JSONEachRow",
      'build_allocator':"SELECT name,value FROM system.build_options WHERE name='USE_JEMALLOC' FORMAT JSONEachRow",
    }.items():
        try:out[qname]=[json.loads(x) for x in sql(query).splitlines() if x.strip()]
        except Exception as e:out[qname+'_error']=str(e)[:500]
    return out
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--mode',required=True,choices=tuple(EXPECTED))
    p.add_argument('--warm-repeats',type=int,default=12)
    p.add_argument('--phase',default='single',help='unique within a runner for query_id evidence')
    p.add_argument('--table',default='churn_ts')
    a=p.parse_args()
    if a.warm_repeats<1 or a.warm_repeats>14:raise AssertionError('Invalid repeat count')
    if not a.phase.replace('_','').replace('-','').isalnum():raise AssertionError('Invalid phase identifier')
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert (m['sha256'],m['series'],m['days'])==(FIXTURE,1330000,14)
    output=pathlib.Path(a.out)/'report.json'
    report={'mode':a.mode,'phase':a.phase,'expected_allocator_env':EXPECTED[a.mode],
            'fixture_sha256':m['sha256'],'series':m['series'],
            'scope':'glibc allocator 2x2 factorial; native full PromQL, not direct SQL',
            'warm_repeats':a.warm_repeats,'steps':[],'diagnostics':[],
            'errors':[],'completed':False,'passed':False}
    save(output,report)
    try:
        report['server_start']=snap()
        actual=report['server_start']['allocator_env']
        for key,value in EXPECTED[a.mode].items():
            if actual.get(key)!=value:
                raise AssertionError(f'Wrong server allocator env: {key}={actual.get(key)}, expected {value}')
        for key in ('MALLOC_TRIM_THRESHOLD_','MALLOC_ARENA_MAX'):
            if key not in EXPECTED[a.mode] and key in actual:
                raise AssertionError(f'Unwanted allocator environment: {key}')
        report['build_use_jemalloc']=report['server_start']['build_allocator']
        for n in range(a.warm_repeats):
            item=one(a.table,m,7,f'glibc_{a.mode}_{a.phase}_warm_{n}',report)
            try:item['process_memory_after']=snap()
            except Exception as e:report['errors'].append({'snapshot':str(e)[:700]})
            save(output,report)
            vmrss=item.get('process_memory_after',{}).get('status',{}).get('VmRSS')
            print('GLIBC_MEMORY_STEP',json.dumps({'mode':a.mode,'i':n+1,'ok':item['ok'],
                 'rss':vmrss,'query_peak_bytes':item.get('querylog',{}).get('peak_query_bytes'),
                 'wall_ms':item.get('querylog',{}).get('duration_ms')},separators=(',',':')),flush=True)
            if not item['ok']:
                report['errors'].append({'warmup_failed_at':n,'message':item.get('error','')[:450]})
                break
        # This final query has higher peak memory; it can correctly fail after RSS retention.
        if len([x for x in report['steps'] if x['days']==7 and x['ok']])==a.warm_repeats:
            item=one(a.table,m,14,f'glibc_{a.mode}_{a.phase}_probe14',report)
            try:item['process_memory_after']=snap()
            except Exception as e:report['errors'].append({'snapshot':str(e)[:700]})
            report['probe14_ok']=item['ok']
            save(output,report)
        report['passed']=not report['errors'] and report.get('probe14_ok',False)
    except Exception as e:
        report['errors'].append({'infrastructure_error':str(e)[:1800]})
    finally:
        report['completed']=True
        save(output,report)
        print('GLIBC_MEMORY_DONE',json.dumps({'mode':a.mode,'passed':report['passed'],
            'probe14_ok':report.get('probe14_ok'),
            'completed_7d':len([x for x in report['steps'] if x['days']==7 and x['ok']]),
            'errors':report['errors']},separators=(',',':')),flush=True)
    if any('infrastructure_error' in x for x in report['errors']):raise SystemExit(1)
if __name__=='__main__':main()
