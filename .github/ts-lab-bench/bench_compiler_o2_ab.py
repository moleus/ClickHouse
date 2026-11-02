#!/usr/bin/env python3
"""Fresh-process O1 vs O2 C++ build benchmark, strict VM oracle, no rebuild."""
import argparse,json,os,pathlib,statistics,time
from bench_stage_cpp_vs_original_vm_95k import (HOST,METRIC,DAY,SELECTORS,monitored,server_restart,wait_vm_complete,expected_points,save,post,sql_string,pct,vm_get,vm_normalized,assert_same)
p=argparse.ArgumentParser()
for k in ('manifest','o1-bin','o2-bin','restart-helper','output'):p.add_argument('--'+k,required=True)
a=p.parse_args()
m=json.loads(pathlib.Path(a.manifest).read_text())
assert m['series']==1330000 and m['days']==14
r={'source_sha256':m['sha256'],'run_id':os.getenv('GITHUB_RUN_ID','local'),'cases':{},'errors':[],'note':'O1 vs O2, fresh process per trial, 4 threads, 5GB cap; no benchmark query warmup; output parity vs VM; same physical data and timestamps. Build sources/patches verified separately.'}
save(a.output,r)
r['vm_readiness']=wait_vm_complete(m,str(pathlib.Path(a.output).with_name('vm-readiness.json')))
save(a.output,r)
run=r['run_id']
for sel in ('whole','stage','project','regex'):
    days=14
    name=sel+'_e14'
    end=int(m['end']);start=end-(days-1)*DAY
    expr=f'avg by(stage)(avg_over_time({METRIC}{SELECTORS[sel]}[1d]))'
    expected=expected_points(sel,days)
    vm_ref=None;vm_times=[]
    for i in range(6):
        ms,payload=vm_get({'query':expr,'start':start,'end':end,'step':DAY})
        got=vm_normalized(payload)
        assert len(got)==expected,('VM missing points',name,len(got),expected)
        if vm_ref is None:vm_ref=got
        else:assert_same(vm_ref,got,name,'VM repeated')
        if i>=2:vm_times.append(ms)
    case={'vm_p50_ms':pct(vm_times,.5),'vm_times_ms':vm_times,'points':expected,'trials':[]}
    r['cases'][name]=case;save(a.output,r)
    # Exactly four independent process starts for each code path, alternated.
    order=('o1','o2','o2','o1')*2
    for i,variant in enumerate(order):
        binary=a.o1_bin if variant=='o1' else a.o2_bin
        query_id=f'compiler_ab_{run}_{name}_{variant}_{i}'
        pid=server_restart(a.restart_helper,binary,query_id)
        rss_initial_kib=__import__('bench_stage_cpp_vs_original_vm_95k',fromlist=['rss']).rss(pid)
        query=(f'SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},{start},{end},{DAY}) '
               'SETTINGS promql_enable_native_one_point_lab=1,max_threads=4 FORMAT JSONEachRow')
        out=monitored(query,query_id,pid)
        out['variant']=variant;out['trial_index']=i;out['rss_initial_kib']=rss_initial_kib
        if out['ok']:
            try:assert_same(vm_ref,out.pop('points'),name,variant)
            except Exception as exc:out['ok']=False;out['error']='numerical mismatch: '+str(exc)
        try:
            post(HOST,'SYSTEM FLUSH LOGS')
            _,raw=post(HOST,('SELECT query_id,type,memory_usage,read_rows,read_bytes,ProfileEvents,exception_code '
                     'FROM system.query_log WHERE query_id='+sql_string(query_id)+' FORMAT JSONEachRow'))
            entries=[json.loads(v) for v in raw.splitlines() if v.strip()]
            found=next((v for v in reversed(entries) if v['type'] in ('QueryFinish','ExceptionWhileProcessing')),None)
            if found:
                out['tracked_query_memory_bytes']=found['memory_usage']
                out['read_rows']=found['read_rows'];out['read_bytes']=found['read_bytes']
                out['cpu_us']=found['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)
                out['exception_code']=found['exception_code']
        except Exception as exc:
            out['log_error']=str(exc)
        if not out['ok']:r['errors'].append({'case':name,'variant':variant,'trial':i,'error':out.get('error','')})
        case['trials'].append(out);save(a.output,r)
        print('COMPILER_AB_TRIAL',json.dumps({'case':name,**out},default=str),flush=True)
    by_variant={k:[x for x in case['trials'] if x['variant']==k and x['ok']] for k in ('o1','o2')}
    case['summary']={k:{'success':len(v),'p50_ms':pct([x['duration_ms'] for x in v],.5) if v else None,
                            'rss_peak_p50_kib':pct([x['rss_peak_kib'] for x in v],.5) if v else None,
                            'rss_delta_p50_kib':pct([x['rss_peak_kib']-x['rss_initial_kib'] for x in v],.5) if v else None,
                            'cpu_us_p50':pct([x.get('cpu_us',0) for x in v],.5) if v else None,
                            'query_mem_p50_bytes':pct([x.get('tracked_query_memory_bytes',0) for x in v],.5) if v else None,
                            'query_log_available':all('tracked_query_memory_bytes' in x for x in v)}
                      for k,v in by_variant.items()}
    save(a.output,r)
    print('COMPILER_AB_CASE',json.dumps({'case':name,'summary':case['summary'],'vm_p50_ms':case['vm_p50_ms']}),flush=True)
r['passed']=not r['errors'] and all(all(v['success']==4 for v in x['summary'].values()) for x in (z for z in r['cases'].values()))
save(a.output,r)
print('COMPILER_AB_DONE',json.dumps({'passed':r['passed'],'failures':r['errors']}),flush=True)
if not r['passed']:raise SystemExit(1)
