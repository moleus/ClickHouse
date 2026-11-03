#!/usr/bin/env python3
"""R02 fixed-cadence native TimeSeries reader during ongoing ingestion.

Fresh server A/B/B/A with the SAME 96-batch fixture and stock O1 binary.
Fix INSERT starts to 160ms apart; schedule one native PromQL read every two
batches, exactly 40 equal-progress reads per phase. Reject queueing/timing
violations, cache hits, incorrect outputs, incomplete samples or A/A drift.
"""
import argparse,concurrent.futures,hashlib,json,math,pathlib,statistics,sys,time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_process_cpu_abba as process
import r02_live_read_write_abba as live
base=process.base

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
FIXTURE='dd2dd55ee8fcf3c11c1fef94ebdd54ac8b3a6a53cea6deaae991b3b94c88b931'
PHASES=(('A0','lc'),('B1','raw'),('B2','raw'),('A3','lc'))
SLOTS=2000
BATCHES=96
PREFIX=12
TABLE='tslab_r02_live'
CADENCE_S=.16
TOTAL_READER_REQUESTS=40
SCHEDULE_LAG_LIMIT_S=.08


def quantile(vals,p):
    v=sorted(vals)
    if not v:
        raise AssertionError('No valid latency samples')
    q=(len(v)-1)*p
    lo=int(math.floor(q));hi=int(math.ceil(q))
    return round(v[lo]+(v[hi]-v[lo])*(q-lo),5)


def read_one(endpoint,qtext,phase,progress,planned):
    before=time.monotonic()
    qid=f'r02paced_{phase}_read_{progress:02d}'
    _,body=base.request(endpoint,qtext,qid)
    after=time.monotonic()
    result=live.check_result(body)
    return {'id':qid,'progress':progress,'scheduled_s':planned,
            'started_s':before,'ended_s':after,
            'start_lag_s':round(before-planned,5),
            'wall_ms':round((after-before)*1000,5),'native':result}


def one_phase(binary,root,phase,mode,blocks,hashes,port):
    folder=root/f'phase-{phase}-{mode}'
    folder.mkdir(parents=True,exist_ok=False)
    proc=log=endpoint=None
    report={'phase':phase,'mode':mode,'errors':[],'passed':False,
            'subexperiment':'fixed-160ms-cadence-one-read-per-two-batches',
            'source_sha':SOURCE,'fixture_sha256':FIXTURE,
            'scheduled_reads':TOTAL_READER_REQUESTS,
            'query_schedule_lag_s':[],'writer_start_lag_s':[],
            'read_samples':[],'insert_records':[],'metrics':{},
            'run_schedule':[]}
    try:
        proc,log,endpoint=process.boot(binary,folder,port)
        report['parent_pid']=proc.pid
        ddls=base.tables_ddl(TABLE,TABLE)
        base.sql(endpoint,ddls[0] if mode=='lc' else ddls[1])
        for batch in range(PREFIX):
            base.sql(endpoint,f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+blocks[batch],
                     f'r02paced_{phase}_prefix_{batch}')
        query=live.query_text()
        report['native_query_sha256']=hashlib.sha256(query.encode()).hexdigest()
        for warm in range(2):
            live.check_result(base.sql(endpoint,query,f'r02paced_{phase}_warm{warm}'))

        # No STOP MERGES: background MergeParts occur during both reads/writes.
        # Schedule query submission by fixed batch index and time, not previous
        # query completion. One outstanding worker means no client concurrency
        # growth when a candidate is faster or slower.
        cpu_before=process.measure(proc.pid)
        futures=[]
        writer_start=time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for ix in range(PREFIX,BATCHES):
                index=ix-PREFIX
                target=writer_start+CADENCE_S*index
                remaining=target-time.monotonic()
                if remaining>0:
                    time.sleep(remaining)
                started=time.monotonic()
                lateness=max(0,started-target)
                report['writer_start_lag_s'].append(round(lateness,6))
                if lateness>SCHEDULE_LAG_LIMIT_S:
                    raise AssertionError(f'Writer cadence missed {ix}: {lateness:.4f}s')

                # Exactly 40 progress-aligned read requests at progress
                # 12,14,...,90; six trailing batches let all reads finish
                # before the writer closes. Each quartile has 10 requests.
                if index%2==0 and index<80:
                    # If a previous request is still active, this phase cannot
                    # maintain fixed QPS. Fail instead of silently queueing.
                    if futures and not futures[-1].done():
                        raise AssertionError(f'Reader backlog at batch {ix}')
                    futures.append(pool.submit(read_one,endpoint,query,phase,ix,target))

                qid=f'r02paced_{phase}_insert_{ix:02d}'
                ms,_=base.request(endpoint,
                    f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+blocks[ix],
                    qid)
                report['insert_records'].append({'batch':ix,'wall_ms':round(ms,4),
                    'query_id':qid,'sha256':hashes[ix]})
            writer_end=time.monotonic()
            for future in futures:
                report['read_samples'].append(future.result(timeout=30))
        cpu_after=process.measure(proc.pid)
        report['metrics']['server_cpu_and_io']=process.difference(cpu_before,cpu_after)
        report['metrics']['writer_active_wall_s']=round(writer_end-writer_start,5)
        if len(futures)!=TOTAL_READER_REQUESTS or len(report['read_samples'])!=TOTAL_READER_REQUESTS:
            raise AssertionError('Missing fixed schedule queries')
        if len(report['insert_records'])!=BATCHES-PREFIX:
            raise AssertionError('Missing insert records')
        if report['read_samples'][-1]['ended_s']>writer_end:
            raise AssertionError('Last scheduled read finished after writer; not fully overlapping')
        for row in report['read_samples']:
            if row['started_s']<writer_start or row['ended_s']>writer_end:
                raise AssertionError('Native read fell outside writer interval')
            if row['start_lag_s']<-.01 or row['start_lag_s']>SCHEDULE_LAG_LIMIT_S:
                raise AssertionError('Native read missed fixed arrival schedule')
            report['query_schedule_lag_s'].append(row['start_lag_s'])
        if sorted(row['progress'] for row in report['read_samples'])!=list(range(12,92,2)):
            raise AssertionError('Wrong progress labels')
        # QueryFinish verification AFTER whole-process CPU sample avoids
        # counting system.query_log collection as part of the write workload.
        ids=[row['id'] for row in report['read_samples']]
        logs=live.query_logs(endpoint,ids)
        for row in report['read_samples']:
            meta=logs[row['id']]
            if meta['exception_code']!=0:
                raise AssertionError('Native SQL query log exception')
            row['cpu_ms']=meta['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)/1000
            row['read_rows']=meta['read_rows']
            row['peak_bytes']=meta['memory_usage']
            if row['cpu_ms']<=0:
                raise AssertionError('Missing native SQL CPU')
        times=[row['wall_ms'] for row in report['read_samples']]
        report['metrics']['native_fixed_reads']={
            'count':len(times),'p50_ms':quantile(times,.5),
            'p95_ms':quantile(times,.95),'p99_ms':quantile(times,.99),
            'cpu_p50_ms':quantile([x['cpu_ms'] for x in report['read_samples']],.5),
            'cpu_p95_ms':quantile([x['cpu_ms'] for x in report['read_samples']],.95),
            'read_rows_p50':quantile([x['read_rows'] for x in report['read_samples']],.5)}
        quarters={}
        for q in range(4):
            vals=[x['wall_ms'] for x in report['read_samples']
                  if 20*q<=x['progress']-12<20*(q+1)]
            if len(vals)!=10:
                raise AssertionError('Progress-stratified query count is not ten')
            quarters[str(q)]={'count':10,'p50_ms':quantile(vals,.5),
                              'p95_ms':quantile(vals,.95)}
        report['progress_quartiles']=quarters
        physical=base.physical(endpoint,TABLE)
        if physical['physical_sample_points']!=384000:
            raise AssertionError('Wrong physical sample count after live inserts')
        for kind in ('tags','samples','timeranges'):
            if ('LowCardinality' in physical[kind]['id_type'])!=(mode=='lc'):
                raise AssertionError('Wrong physical ID encoding '+kind)
        report['physical']=physical
        plog=base.part_log(endpoint,[base.table_uuid(endpoint,TABLE)])
        if not plog.get('available') or not plog.get('events'):
            raise AssertionError('No natural background merge records')
        report['part_log']=plog
        report['passed']=True
        print('R02_PACED_PHASE',json.dumps({'phase':phase,'mode':mode,
            'p95_ms':report['metrics']['native_fixed_reads']['p95_ms'],
            'cpu_s':report['metrics']['server_cpu_and_io']['cpu_s'],
            'reader_count':len(times),'writer_lag_p95_ms':quantile(
                  [x*1000 for x in report['writer_start_lag_s']],.95),
            'reader_lag_p95_ms':quantile([x*1000 for x in report['query_schedule_lag_s']],.95),
            'points':physical['physical_sample_points']},separators=(',',':')),flush=True)
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        print('R02_PACED_PHASE_ERROR',json.dumps({'phase':phase,'mode':mode,
            'errors':report['errors']},separators=(',',':')),flush=True)
    finally:
        (folder/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        process.shutdown(proc,log)
    if not report['passed']:
        raise RuntimeError(f'Phase {phase} invalid: {report["errors"]}')
    return report


def reduce(phases):
    comp={'valid':True,'decision':'INCONCLUSIVE','metrics':{},'errors':[]}
    for path in ('native_fixed_reads.p95_ms','native_fixed_reads.p50_ms',
                 'server_cpu_and_io.cpu_s','writer_active_wall_s'):
        pieces=path.split('.')
        def val(p):
            if path=='writer_active_wall_s':
                return phases[p]['metrics'][path]
            return phases[p]['metrics'][pieces[0]][pieces[1]]
        a=[val(x) for x in ('A0','A3')]
        b=[val(x) for x in ('B1','B2')]
        a_mean=statistics.mean(a)
        b_mean=statistics.mean(b)
        drift=abs(a[0]-a[1])/a_mean if a_mean else math.inf
        if drift>.25 or a_mean<=0 or b_mean<=0:
            comp['valid']=False
            comp['errors'].append(path+' drift/invalid')
        comp['metrics'][path]={
            'lc_mean':round(a_mean,5),'raw_mean':round(b_mean,5),
            'raw_over_lc':round(b_mean/a_mean,5),
            'lc_control_drift':round(drift,5)}
    if not comp['valid']:
        comp['decision']='INVALID_METHOD'
    else:
        p95=comp['metrics']['native_fixed_reads.p95_ms']
        ratio=p95['raw_over_lc']
        if abs(ratio-1)>max(.05,p95['lc_control_drift']):
            comp['decision']='INCONCLUSIVE_REPLICATION_NEEDED'
        else:
            comp['decision']='NO_EFFECT'
    return comp


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--port',type=int,default=18191)
    a=p.parse_args()
    root=pathlib.Path(a.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks=[base.fixture_batch(i,SLOTS) for i in range(BATCHES)]
    hashes=[hashlib.sha256(block.encode()).hexdigest() for block in blocks]
    fixture=hashlib.sha256(''.join(blocks).encode()).hexdigest()
    if fixture!=FIXTURE:
        raise AssertionError('Frozen source fixture SHA mismatch')
    report={'schema_version':1,'question_id':'R02-small-batch-raw-lc-write-v1',
            'subexperiment':'fixed-160ms-writer-cadence-40-native-reads-per-phase',
            'source_sha':SOURCE,'fixture_sha256':fixture,'cadence_s':CADENCE_S,
            'phases':{},'errors':[],'passed':False}
    try:
        for name,mode in PHASES:
            report['phases'][name]=one_phase(a.binary,root,name,mode,blocks,hashes,a.port)
            (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        report['comparison']=reduce(report['phases'])
        report['passed']=report['comparison']['valid']
        if not report['passed']:
            report['errors'].extend(report['comparison']['errors'])
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print('R02_PACED_ABBA_DONE',json.dumps({'passed':report['passed'],
          'decision':report.get('comparison',{}).get('decision'),
          'comparison':report.get('comparison',{}).get('metrics'),
          'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
