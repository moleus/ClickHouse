#!/usr/bin/env python3
"""R21: controlled TAGS dedup cache working-set capacity and ordering A/B/B/A.

Four fresh servers per input ordering (large/tiny/tiny/large), same 4096
stable logical series and 12 INSERTs. The only DDL variable within each
runner is tags_deduplication_cache_size_bytes. Each series/sample is included
in every variant, exactly once per batch. QueryFinish cache hit/miss counters,
physical ReplacingMergeTree TAGS rows, worker+watchdog process CPU and write
IO are measured. A separate CI runner executes rotated row order to test
whether arrival order changes the observed cache effectiveness.

No new index, no C++ build, no production mutation.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import statistics
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base
import r02_process_cpu_abba as proc

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
BINARY_SHA='f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e'
SERIES=4096
BATCHES=12
SMALL_BYTES=32768
LARGE_BYTES=1048576
NOMINAL_CACHE_ENTRY_BYTES=144
FIXTURE_CANONICAL_SHA='052b2058e7adc280a956b083c9af7d2e636eba6ab2607c204e6f676fcc5d1883'
BASE=1792497600
METRIC='tslab_r21_cache_knee'
TABLE='tslab_r21_capacity'
PHASES=(('A0','large'),('B1','small'),('B2','small'),('A3','large'))
WARMUP=3
MEASURED=6


def line(i,batch):
    tags={'project':f'p{i%4}','series':f'series-{i:05d}','rack':f'r{i%32:02d}'}
    record={'metric_name':METRIC,'tags':tags,
            'samples':[[base.stamp(BASE+60*batch),20.0+(i%97)+batch*.25]]}
    return json.dumps(record,sort_keys=True,separators=(',',':'))+'\n'


def fixture(order):
    canonical=hashlib.sha256()
    ordered=hashlib.sha256()
    blocks=[]
    per_batch=[]
    for batch in range(BATCHES):
        original=[line(i,batch) for i in range(SERIES)]
        for value in original:
            canonical.update(value.encode())
        if order=='fixed':
            rows=original
        elif order=='rotate':
            offset=(batch*997)%SERIES
            rows=original[offset:]+original[:offset]
        else:
            raise AssertionError('Invalid input order')
        block=''.join(rows)
        per_batch.append(hashlib.sha256(block.encode()).hexdigest())
        ordered.update(block.encode())
        blocks.append(block)
    if canonical.hexdigest()!=FIXTURE_CANONICAL_SHA:
        raise AssertionError('Input generator no longer matches pinned canonical SHA')
    return blocks,{'canonical_sha256':canonical.hexdigest(),
                   'ordered_sha256':ordered.hexdigest(),'per_batch_sha256':per_batch,
                   'series':SERIES,'batches':BATCHES,'samples':SERIES*BATCHES,
                   'ordering':order}


def native_query():
    end=BASE+(BATCHES-1)*60
    expr=f'avg by(project)(avg_over_time({METRIC}[30m]))'
    sql=(f'SELECT * FROM prometheusQueryRange({TABLE},{base.q(expr)},'
        f'{end},{end},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    return sql


def oracle():
    average_batch=sum(.25*b for b in range(BATCHES))/BATCHES
    data={}
    for i in range(SERIES):
        key=f'p{i%4}'
        data.setdefault(key,[]).append(20.0+(i%97)+average_batch)
    return {key:sum(xs)/len(xs) for key,xs in data.items()}


def verify_native(body):
    expected=oracle()
    values={}
    want_time=BASE+(BATCHES-1)*60
    for line_ in body.splitlines():
        if not line_:
            continue
        obj=json.loads(line_)
        tags=dict(obj['tags'])
        if set(tags)!={'project'} or len(obj['samples'])!=1:
            raise AssertionError('Native label shape or grid width mismatch')
        timestamp,value=obj['samples'][0]
        epoch=int(dt.datetime.fromisoformat(timestamp.replace(' ','T'))
                  .replace(tzinfo=dt.timezone.utc).timestamp())
        if epoch!=want_time or tags['project'] in values:
            raise AssertionError('Bad native timestamp / duplicate project')
        values[tags['project']]=float(value)
    if set(values)!=set(expected):
        raise AssertionError('Native project labels do not match independent oracle')
    for key,value in expected.items():
        if not math.isclose(values[key],value,rel_tol=1e-11,abs_tol=1e-8):
            raise AssertionError(f'Native wrong value {key}: {values[key]}, expected {value}')
    return values


def percent(vals,p):
    seq=sorted(vals)
    if not seq:
        return None
    x=(len(seq)-1)*p
    a,b=int(math.floor(x)),int(math.ceil(x))
    return round(seq[a]+(seq[b]-seq[a])*(x-a),5)


def raw_counts(endpoint,table):
    uuid=base.table_uuid(endpoint,table)
    res={}
    for kind in ('tags','samples','timeranges'):
        inner=f'default.'+chr(96)+f'.inner_id.{kind}.{uuid}'+chr(96)
        count=int(base.sql(endpoint,f'SELECT count() FROM {inner} FORMAT TSV').strip())
        distinct=int(base.sql(endpoint,
            f'SELECT uniqExact(id) FROM {inner} FORMAT TSV').strip())
        parts=base.jsonrows(endpoint,
            f'SELECT sum(bytes_on_disk) AS bytes, count() AS parts, sum(rows) AS rows '
            f'FROM system.parts WHERE active AND database={base.q("default")} '
            f'AND table={base.q(".inner_id."+kind+"."+uuid)}')
        if len(parts)!=1:
            raise AssertionError('Missing physical parts for '+kind)
        res[kind]={'count':count,'distinct_id':distinct,**parts[0]}
    points=base.physical(endpoint,table)['physical_sample_points']
    res['physical_samples_points']=points
    return res


def cache_events(endpoint):
    """Global ProfileEvents include pipeline worker threads, unlike some query logs.

    QueryFinish OSCPUVirtualTime is kept separately. Event counters in
    system.events are not coupled to one query when pipelines run in workers.
    Fresh server per A/B/B/A phase isolates this controlled experiment.
    """
    rows=base.jsonrows(endpoint,
        "SELECT event,value FROM system.events WHERE event IN "
        "('TimeSeriesTagsDeduplicationCacheHits',"
        "'TimeSeriesTagsDeduplicationCacheMisses')")
    return {r['event']:int(r['value']) for r in rows}


def query_log(endpoint,ids):
    base.sql(endpoint,'SYSTEM FLUSH LOGS')
    quoted=','.join(base.q(i) for i in ids)
    for attempt in range(6):
        rows=base.jsonrows(endpoint,
            'SELECT query_id,query_duration_ms,ProfileEvents,read_rows,read_bytes,'
            'memory_usage,exception_code FROM system.query_log '
            "WHERE type='QueryFinish' AND query_id IN ("+quoted+')')
        latest={r['query_id']:r for r in rows}
        if set(ids)<=set(latest):
            return {x:latest[x] for x in ids}
        time.sleep(.2*(attempt+1))
    raise AssertionError('Missing QueryFinish '+repr(sorted(set(ids)-set(latest))[:9]))


def ddl_for(variant,expiration_seconds=3600):
    bytes_=LARGE_BYTES if variant=='large' else SMALL_BYTES
    raw=base.tables_ddl('unused',TABLE)[1]
    match='SETTINGS recent_samples_ttl_seconds=0'
    if match not in raw:
        raise AssertionError('Pinned source v11 RAW UUID DDL changed')
    raw=raw.replace(match,
        f'SETTINGS recent_samples_ttl_seconds=0, tags_deduplication_cache_size_bytes={bytes_}, '
        f'tags_deduplication_cache_expiration_seconds={expiration_seconds}, '
        'metric_families_deduplication_cache_size_bytes=104857600')
    return raw,bytes_


def one_phase(binary,root,phase,variant,blocks,input_proof,port,expiration_seconds=3600,delay_between_batches_seconds=0.0):
    phase_dir=root/f'{phase}-{variant}'
    phase_dir.mkdir(parents=True,exist_ok=False)
    server,log,endpoint=None,None,None
    result={'phase':phase,'variant':variant,'input_sha256':input_proof['ordered_sha256'],
            'source_sha':SOURCE,'passed':False,'errors':[],
            'insert_rows':[],'native_samples':[],'stats':{}}
    try:
        server,log,endpoint=proc.boot(binary,phase_dir,port)
        ddl,cache_bytes=ddl_for(variant,expiration_seconds)
        result['cache_bytes']=cache_bytes
        result['cache_expiration_seconds']=expiration_seconds
        result['delay_between_batches_seconds']=delay_between_batches_seconds
        result['capacity_entries_nominal']=cache_bytes//NOMINAL_CACHE_ENTRY_BYTES
        result['ddl_sha256']=hashlib.sha256(ddl.encode()).hexdigest()
        base.sql(endpoint,ddl)
        uuid=base.table_uuid(endpoint,TABLE)
        for kind in ('tags','samples','timeranges'):
            inner=f'default.'+chr(96)+f'.inner_id.{kind}.{uuid}'+chr(96)
            base.sql(endpoint,'SYSTEM STOP MERGES '+inner)
        qids=[]
        start_cache_events=cache_events(endpoint)
        result['stats']['cache_events_start']=start_cache_events
        start=proc.measure(server.pid)
        for batch,block in enumerate(blocks):
            if batch>0 and delay_between_batches_seconds>0:
                time.sleep(delay_between_batches_seconds)
            qid=f'r21_{phase}_{batch:02d}'
            qids.append(qid)
            ms,_=base.request(endpoint,
                f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+block,
                qid)
            result['insert_rows'].append({'batch':batch,
                     'qid':qid,'wall_ms':round(ms,4),
                     'sha256':input_proof['per_batch_sha256'][batch]})
        end=proc.measure(server.pid)
        end_cache_events=cache_events(endpoint)
        result['stats']['cache_events_end']=end_cache_events
        result['stats']['ingest']=proc.difference(start,end)
        # Account actual TAGS materialization before allowing any merging;
        # ReplacingMergeTree RAW count is not logical uniqExact ID count.
        result['physical']=raw_counts(endpoint,TABLE)
        physics=result['physical']
        if physics['physical_samples_points']!=SERIES*BATCHES or \
           physics['tags']['distinct_id']!=SERIES or \
           physics['timeranges']['distinct_id']!=SERIES or \
           physics['samples']['distinct_id']!=SERIES:
            raise AssertionError('Sample count or stable-ID physical cardinality wrong')
        results=query_log(endpoint,qids)
        total_hit,total_miss,total_cpu=0,0,0.0
        for item in result['insert_rows']:
            row=results[item['qid']]
            if row['exception_code']!=0:
                raise AssertionError('Failed logged INSERT '+item['qid'])
            ev=row['ProfileEvents']
            item['server_ms']=row['query_duration_ms']
            item['cpu_ms']=round(ev.get('OSCPUVirtualTimeMicroseconds',0)/1000,5)
            item['cache_hit']=ev.get('TimeSeriesTagsDeduplicationCacheHits',0)
            item['cache_miss']=ev.get('TimeSeriesTagsDeduplicationCacheMisses',0)
            item['write_bytes']=ev.get('WriteBufferFromFileDescriptorWriteBytes',0)
            item['memory_usage']=row['memory_usage']
            if item['cpu_ms']<=0:
                raise AssertionError('Insert query CPU missing')
            total_cpu+=item['cpu_ms']
            total_hit+=item['cache_hit']
            total_miss+=item['cache_miss']
        result['stats']['querylog_ingest_cpu_ms']=round(total_cpu,3)
        result['stats']['querylog_tag_cache_hits']=total_hit
        result['stats']['querylog_tag_cache_misses']=total_miss
        hit_event='TimeSeriesTagsDeduplicationCacheHits'
        miss_event='TimeSeriesTagsDeduplicationCacheMisses'
        # system.events reports only counters that have been incremented.
        # Fresh servers legitimately omit BOTH keys before the first INSERT.
        if hit_event not in end_cache_events or miss_event not in end_cache_events:
            raise AssertionError('TimeSeries global cache ProfileEvents missing after inserts')
        total_hit=end_cache_events[hit_event]-start_cache_events.get(hit_event,0)
        total_miss=end_cache_events[miss_event]-start_cache_events.get(miss_event,0)
        result['stats']['total_cache_hits']=total_hit
        result['stats']['total_cache_misses']=total_miss
        result['stats']['cache_events_method']='system.events global worker-inclusive delta'
        result['stats']['cache_hit_fraction']=round(total_hit/(total_hit+total_miss),6) if total_hit+total_miss else None
        result['stats']['raw_tags_rows']=physics['tags']['count']
        result['stats']['raw_tags_bytes']=physics['tags']['bytes']
        result['stats']['raw_samples_bytes']=physics['samples']['bytes']
        result['stats']['raw_timeranges_bytes']=physics['timeranges']['bytes']
        # With all metadata merges stopped, TAGS written rows correspond to
        # misses; no-cache has no hit/miss events and always writes all IDs.
        if variant=='large' and expiration_seconds==3600:
            if total_hit!=(BATCHES-1)*SERIES or total_miss!=SERIES:
                raise AssertionError('Large cache did not fit all 4096 stable IDs')
            if physics['tags']['count']!=SERIES:
                raise AssertionError('Large cache did not suppress repeated physical TAGS')
        else:
            if total_hit+total_miss!=SERIES*BATCHES:
                raise AssertionError('Small cache unexpected missing hit/miss counters')
            if physics['tags']['count']!=total_miss:
                raise AssertionError('Physical TAGS rows differ from cached miss count')
        query=native_query()
        result['native_query_sha256']=hashlib.sha256(query.encode()).hexdigest()
        for index in range(WARMUP+MEASURED):
            t0=time.perf_counter()
            qid=f'r21native_{phase}_{index:02d}'
            body=base.sql(endpoint,query,qid)
            wall_ms=1000*(time.perf_counter()-t0)
            labels=verify_native(body)
            result['native_samples'].append({'qid':qid,'warmup':index<WARMUP,
                'wall_ms':round(wall_ms,5),'labels':labels})
        native_logs=query_log(endpoint,[x['qid'] for x in result['native_samples']])
        for x in result['native_samples']:
            row=native_logs[x['qid']]
            if row['exception_code']!=0:
                raise AssertionError('QueryFinish native failed')
            x['cpu_ms']=round(row['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)/1000,5)
            x['read_rows']=row['read_rows']
            x['read_bytes']=row['read_bytes']
            x['peak_bytes']=row['memory_usage']
        measured=[x for x in result['native_samples'] if not x['warmup']]
        result['stats']['native']={'measured_queries':len(measured),
             'wall_p50_ms':percent([x['wall_ms'] for x in measured],.5),
             'wall_p95_ms':percent([x['wall_ms'] for x in measured],.95),
             'cpu_p50_ms':percent([x['cpu_ms'] for x in measured],.5),
             'read_rows_p50':percent([x['read_rows'] for x in measured],.5),
             'read_bytes_p50':percent([x['read_bytes'] for x in measured],.5)}
        result['passed']=True
        print('R21_PHASE',json.dumps({'phase':phase,'variant':variant,
              'total_hits':total_hit,'total_misses':total_miss,
              'raw_tags_rows':physics['tags']['count'],
              'ingest_cpu_s':result['stats']['ingest']['cpu_s'],
              'ingest_write_bytes':result['stats']['ingest']['write_bytes_delta'],
              'native_p50_ms':result['stats']['native']['wall_p50_ms']},
              separators=(',',':')),flush=True)
    except Exception as exc:
        result['errors'].append(str(exc)[:2200])
        print('R21_PHASE_FAIL',json.dumps({'phase':phase,'variant':variant,
             'errors':result['errors']},separators=(',',':')),flush=True)
    finally:
        (phase_dir/'report.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        proc.shutdown(server,log)
    if not result['passed']:
        raise RuntimeError(f'Failed {phase}: {result["errors"]}')
    return result


def compare(all_phases):
    keys={'physical_tags_rows':'raw_tags_rows',
          'physical_tags_bytes':'raw_tags_bytes',
          'process_ingest_cpu':'ingest.cpu_s',
          'process_write_bytes':'ingest.write_bytes_delta',
          'querylog_ingest_cpu_ms':'querylog_ingest_cpu_ms',
          'native_read_p50':'native.wall_p50_ms'}
    out={}
    for kind,key in keys.items():
        def lookup(phase):
            v=all_phases[phase]['stats']
            for path in key.split('.'):
                v=v[path]
            return float(v)
        aa=[lookup('A0'),lookup('A3')]
        bb=[lookup('B1'),lookup('B2')]
        baseline=statistics.mean(aa)
        candidate=statistics.mean(bb)
        drift=abs(aa[0]-aa[1])/baseline if baseline else None
        out[kind]={'large_mean':round(baseline,6),'small_mean':round(candidate,6),
                   'small_over_large':round(candidate/baseline,6) if baseline else None,
                   'large_AA_drift':round(drift,6) if drift is not None else None,
                   'AA':aa,'BB':bb}
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--order',choices=('fixed','rotate'),required=True)
    p.add_argument('--port',type=int,default=18191)
    args=p.parse_args()
    root=pathlib.Path(args.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks,input_proof=fixture(args.order)
    report={'schema_version':1,
       'question_id':'R21-tags-dedup-cache-working-set-knee-v1',
       'subexperiment':'fixed-versus-rotated-repeated-id-input-order-cache-knee',
       'source_sha':SOURCE,'binary_sha256':BINARY_SHA,
       'order':args.order,'fixture':input_proof,'phases':{},
       'comparison':{},'errors':[],'passed':False,'decision':'INCONCLUSIVE'}
    def save():
        (root/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    try:
        for phase,variant in PHASES:
            report['phases'][phase]=one_phase(args.binary,root,phase,variant,
                                          blocks,input_proof,args.port)
            save()
        if len({x['input_sha256'] for x in report['phases'].values()})!=1:
            raise AssertionError('ABBA input SHA mismatch')
        if len({x['native_query_sha256'] for x in report['phases'].values()})!=1:
            raise AssertionError('ABBA native query SQL differs')
        report['comparison']=compare(report['phases'])
        if any(report['comparison'][key]['large_AA_drift']>.25 for key in
              ('process_ingest_cpu','native_read_p50')):
            raise AssertionError('Baseline control drift >25%')
        if not(report['comparison']['physical_tags_rows']['small_over_large']>1.25):
            report['decision']='NO_CAPACITY_KNEE'
        else:
            report['decision']='PHYSICAL_CACHE_WORKING_SET_KNEE'
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2200])
        report['decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R21_CACHE_KNEE_DONE',json.dumps({'passed':report['passed'],
             'decision':report['decision'],'order':args.order,
             'comparison':report['comparison'],'errors':report['errors']},
             separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
