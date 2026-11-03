#!/usr/bin/env python3
"""R02: native PromQL under simultaneous small-batch writes and natural merges.

Four fresh ClickHouse servers LC/RAW/RAW/LC on one GitHub runner. A fixed native
PromQL query reads the *immutable first twelve batches*. Only subsequent
timestamps are inserted while the reader runs, so exact expected query values
stay invariant. Do not confuse this with standalone ingest or R03 CH-vs-VM.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import statistics
import sys
import threading
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_process_cpu_abba as process
base=process.base

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
ROWS_PER_BATCH=4000
SLOTS=2000
BATCHES=96
PREFIX=12
TABLE='tslab_r02_live'
PHASES=(('A0','lc'),('B1','raw'),('B2','raw'),('A3','lc'))
PINNED_FIXTURE='dd2dd55ee8fcf3c11c1fef94ebdd54ac8b3a6a53cea6deaae991b3b94c88b931'


def percentile(numbers, quantile):
    seq=sorted(numbers)
    if not seq:
        return None
    p=(len(seq)-1)*quantile
    left=int(math.floor(p))
    right=int(math.ceil(p))
    return round(seq[left]+(seq[right]-seq[left])*(p-left),4)


def query_text():
    end=base.BASE+(PREFIX-1)*60
    expr=f'avg by(scope)(avg_over_time({base.METRIC}[30m]))'
    q=(f'SELECT * FROM prometheusQueryRange({TABLE},{base.q(expr)},'
       f'{end},{end},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    return q


def check_result(body):
    expected=base.expected(SLOTS,PREFIX)
    rows=base.normalize(body)
    end=base.BASE+(PREFIX-1)*60
    observed={}
    for (scope,time_string),value in rows.items():
        epoch=int(base.dt.datetime.fromisoformat(time_string.replace(' ','T'))
                  .replace(tzinfo=base.dt.timezone.utc).timestamp())
        if epoch!=end or scope in observed:
            raise AssertionError('Wrong native time or duplicate scope')
        observed[scope]=value
    if set(observed)!=set(expected):
        raise AssertionError(f'Native label mismatch, expected {expected}, got {observed}')
    for key in expected:
        if not math.isclose(observed[key],expected[key],rel_tol=1e-11,abs_tol=1e-8):
            raise AssertionError(f'Native numeric mismatch {key}: {observed[key]} vs {expected[key]}')
    return observed


def reader_thread(endpoint,phase,stop,started,records,errors,progress):
    q=query_text()
    count=0
    started.set()
    while not stop.is_set() and count<3000:
        qid=f'r02live_{phase}_{count:04d}'
        completed_at_start=progress['completed_batches']
        t0=time.monotonic()
        try:
            _,body=base.request(endpoint,q,qid)
            result=check_result(body)
            t1=time.monotonic()
            records.append({'id':qid,'start_monotonic':t0,'end_monotonic':t1,
                            'completed_batches_at_start':completed_at_start,
                            'wall_ms':round((t1-t0)*1000,4),'native':result})
        except Exception as exc:
            errors.append({'query_id':qid,'error':str(exc)[:1600]})
            stop.set()
            break
        count+=1


def query_logs(endpoint, ids):
    base.sql(endpoint,'SYSTEM FLUSH LOGS')
    where="query_id IN ("+','.join(base.q(i) for i in ids)+")"
    for attempt in range(8):
        data=base.jsonrows(endpoint,
            "SELECT query_id,query_duration_ms,ProfileEvents,read_rows,read_bytes,"
            "memory_usage,exception_code FROM system.query_log "
            "WHERE type='QueryFinish' AND "+where)
        found={x['query_id']:x for x in data}
        if all(k in found for k in ids):
            return {k:found[k] for k in ids}
        time.sleep(.2*(attempt+1))
    raise AssertionError('Missing QueryFinish: '+repr(sorted(set(ids)-set(found))[:5]))


def one_phase(binary,root,phase,mode,blocks,digests,port):
    phase_dir=root / f'phase-{phase}-{mode}'
    phase_dir.mkdir(parents=True,exist_ok=False)
    proc,output,endpoint=None,None,None
    info={'phase':phase,'mode':mode,'source_sha':SOURCE,
          'errors':[],'passed':False,'insert_records':[],
          'reader_samples':[],'reader_errors':[],'metrics':{}}
    thread=None
    stop=threading.Event()
    try:
        proc,output,endpoint=process.boot(binary,phase_dir,port)
        info['parent_pid']=proc.pid
        ddls=base.tables_ddl(TABLE,TABLE)
        base.sql(endpoint,ddls[0] if mode=='lc' else ddls[1])
        # Natural background merges are ENABLED. Do not use STOP MERGES.
        for i in range(PREFIX):
            base.sql(endpoint,f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+blocks[i],
                     'r02live_'+phase+'_prefix_'+str(i))
        warmups=[]
        for i in range(2):
            before=time.monotonic()
            warmups.append(check_result(base.sql(endpoint,query_text(),
                                                 'r02live_'+phase+'_warm_'+str(i))))
            if time.monotonic()-before>10:
                raise AssertionError('Excessive warmup query latency')
        info['oracle_expected']=base.expected(SLOTS,PREFIX)
        info['query_sha256']=hashlib.sha256(query_text().encode()).hexdigest()
        info['warmups']=len(warmups)
        started=threading.Event()
        progress={'completed_batches':PREFIX}
        thread=threading.Thread(target=reader_thread,args=(endpoint,phase,stop,started,
                                     info['reader_samples'],info['reader_errors'],progress),daemon=True)
        before=process.measure(proc.pid)
        thread.start()
        if not started.wait(10):
            raise AssertionError('Reader thread did not start')
        writer_start=time.monotonic()
        for i in range(PREFIX,BATCHES):
            qid=f'r02live_{phase}_insert_{i:03d}'
            ms,_=base.request(endpoint,
                f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+blocks[i],qid)
            info['insert_records'].append({'batch':i,'query_id':qid,
                                          'wall_ms':round(ms,3),'sha256':digests[i]})
            progress['completed_batches']=i+1
            if info['reader_errors']:
                raise RuntimeError('Reader correctness failed during inserts')
        writer_end=time.monotonic()
        after=process.measure(proc.pid)
        stop.set()
        thread.join(timeout=30)
        if thread.is_alive():
            raise AssertionError('Native reader thread did not stop')
        if info['reader_errors']:
            raise AssertionError('Reader error: '+repr(info['reader_errors'][:2]))
        info['metrics']['mixed_read_write_server_process']=process.difference(before,after)
        valid_reads=[r for r in info['reader_samples'] if
                     r['start_monotonic']>=writer_start and
                     r['end_monotonic']<=writer_end]
        info['valid_reader_samples']=valid_reads
        if len(valid_reads)<12:
            raise AssertionError(f'Too few complete overlapping reads: {len(valid_reads)}')
        ids=[r['id'] for r in valid_reads]
        logs=query_logs(endpoint,ids)
        for r in valid_reads:
            log=logs[r['id']]
            if log['exception_code']!=0:
                raise AssertionError('Native QueryFinish failed '+r['id'])
            events=log['ProfileEvents']
            r['server_duration_ms']=log['query_duration_ms']
            r['cpu_ms']=events.get('OSCPUVirtualTimeMicroseconds',0)/1000
            r['read_rows']=log['read_rows']
            r['read_bytes']=log['read_bytes']
            r['peak_query_bytes']=log['memory_usage']
        p95=percentile([r['wall_ms'] for r in valid_reads],.95)
        p99=percentile([r['wall_ms'] for r in valid_reads],.99)
        # Reader samples are not uniformly distributed across ingestion progress.
        # Preserve progress-stratified latency for fair comparisons.
        quarters={}
        for quarter in range(4):
            lower=PREFIX+quarter*(BATCHES-PREFIX)/4
            upper=PREFIX+(quarter+1)*(BATCHES-PREFIX)/4
            included=[r['wall_ms'] for r in valid_reads if
                      lower <= r['completed_batches_at_start'] < upper]
            quarters[str(quarter)]={'count':len(included),
                'p50_ms':percentile(included,.50),'p95_ms':percentile(included,.95)}
        info['read_progress_quartiles']=quarters
        info['metrics']['native_read']={
            'count':len(valid_reads),
            'p50_ms':percentile([r['wall_ms'] for r in valid_reads],.50),
            'p95_ms':p95,'p99_ms':p99,
            'cpu_ms_p50':percentile([r['cpu_ms'] for r in valid_reads],.50),
            'cpu_ms_p95':percentile([r['cpu_ms'] for r in valid_reads],.95),
            'read_rows_p50':percentile([r['read_rows'] for r in valid_reads],.50)}
        final=base.physical(endpoint,TABLE)
        if final['physical_sample_points']!=BATCHES*ROWS_PER_BATCH:
            raise AssertionError('Physical points lost during concurrent read/write')
        for kind in ('tags','samples','timeranges'):
            if ('LowCardinality' in final[kind]['id_type']) != (mode=='lc'):
                raise AssertionError('Wrong physical ID type in '+mode+' '+kind)
        info['physical']=final
        info['part_log']=base.part_log(endpoint,[base.table_uuid(endpoint,TABLE)])
        if not info['part_log']['available']:
            raise AssertionError('Missing system.part_log under natural merges')
        info['input_sha256']=digests
        info['passed']=True
        print('R02_LIVE_PHASE',json.dumps({'phase':phase,'mode':mode,
             'read_count':len(valid_reads),'read_p50_ms':info['metrics']['native_read']['p50_ms'],
             'read_p95_ms':p95,'read_p99_ms':p99,
             'mixed_cpu_s':info['metrics']['mixed_read_write_server_process']['cpu_s'],
             'writer_wall_s':round(writer_end-writer_start,4),
             'sample_points':final['physical_sample_points'],
             'active_samples_parts':final['samples']['parts'],
             'passed':True},separators=(',',':')),flush=True)
    except Exception as exc:
        info['errors'].append(str(exc)[:2500])
        print('R02_LIVE_PHASE_ERROR',json.dumps({'phase':phase,'mode':mode,
            'errors':info['errors']},separators=(',',':')),flush=True)
    finally:
        stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=5)
        (phase_dir/'report.json').write_text(json.dumps(info,indent=2,sort_keys=True)+'\n')
        process.shutdown(proc,output)
    if not info['passed']:
        raise RuntimeError(f'Phase {phase} failed: {info["errors"]}')
    return info


def reduction(phases):
    result={'method_valid':True,'decision':'INCONCLUSIVE',
            'metrics':{},'errors':[],'stop_conditions':[]}
    for key,label in [('native_read','p95_ms'),
                      ('mixed_read_write_server_process','cpu_s'),
                      ('mixed_read_write_server_process','wall_s')]:
        lc=[phases[x]['metrics'][key][label] for x in ('A0','A3')]
        raw=[phases[x]['metrics'][key][label] for x in ('B1','B2')]
        a=statistics.mean(lc);b=statistics.mean(raw)
        drift=abs(lc[0]-lc[1])/a if a else math.inf
        result['metrics'][key+'.'+label]={'lc_mean':round(a,5),'raw_mean':round(b,5),
            'raw_over_lc':round(b/a,5) if a else None,
            'lc_control_drift':round(drift,5),
            'raw_phase_values':raw,'lc_phase_values':lc}
        if drift>.25 or a<=0 or b<=0:
            result['method_valid']=False
            result['errors'].append(key+'.'+label+' drift/invalid baseline')
    if not result['method_valid']:
        result['decision']='INVALID_METHOD'
    else:
        p95=result['metrics']['native_read.p95_ms']
        cpu=result['metrics']['mixed_read_write_server_process.cpu_s']
        if abs(p95['raw_over_lc']-1)>max(.05,p95['lc_control_drift']) or \
           abs(cpu['raw_over_lc']-1)>max(.05,cpu['lc_control_drift']):
            result['decision']='INCONCLUSIVE_REPLICATION_NEEDED'
        else:
            result['decision']='NO_EFFECT'
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--port',type=int,default=18191)
    a=p.parse_args()
    root=pathlib.Path(a.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks=[base.fixture_batch(b,SLOTS) for b in range(BATCHES)]
    digests=[hashlib.sha256(b.encode()).hexdigest() for b in blocks]
    combined=hashlib.sha256(''.join(blocks).encode()).hexdigest()
    if combined!=PINNED_FIXTURE:
        raise AssertionError('Fixture generator changed since pinned 96-batch R02')
    report={'schema_version':1,'question_id':'R02-small-batch-raw-lc-write-v1',
       'subexperiment':'natural-merges-concurrent-native-ABBA-v1','source_sha':SOURCE,
       'fixture_sha256':combined,'phases':{},'errors':[], 'passed':False}
    try:
        for phase,mode in PHASES:
            report['phases'][phase]=one_phase(a.binary,root,phase,mode,blocks,digests,a.port)
            (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        if any(tuple(x['input_sha256'])!=tuple(digests) for x in report['phases'].values()):
            raise AssertionError('Input differs across ABBA phases')
        report['comparison']=reduction(report['phases'])
        report['passed']=report['comparison']['method_valid']
        if not report['passed']:
            report['errors'].extend(report['comparison']['errors'])
    except Exception as exc:
        report['errors'].append(str(exc)[:2500])
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print('R02_LIVE_ABBA_DONE',json.dumps({'passed':report['passed'],
              'decision':report.get('comparison',{}).get('decision'),
              'metrics':report.get('comparison',{}).get('metrics'),
              'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
