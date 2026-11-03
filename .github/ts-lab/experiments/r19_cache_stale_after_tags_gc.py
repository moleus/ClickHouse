#!/usr/bin/env python3
"""R19: test metadata-deletion safety under TimeSeries TAGS dedup cache.

Source hypothesis: TimeSeriesSink filters TAGS by recently-written-ID cache
before writing samples. Direct DELETE from the inner TAGS table does not
invalidate outer TimeSeries dedup cache. A resumed series can then gain new
SAMPLES with no TAGS label row, making native PromQL temporarily invisible.
Compare cache enabled versus disabled, then clear cache before reinsert.

This deliberately uses destructive ALTER ONLY on local synthetic tables.
Such direct inner-target edits are not a proposed production cleanup.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
METRIC='tslab_r19_tags_gc_cache_safety'
EPOCH=int(dt.datetime(2026,10,20,12,tzinfo=dt.timezone.utc).timestamp())
END=EPOCH+180
MODE={'enabled':104857600,'disabled':0}
VALUE_SEQUENCE=(10.,17.,19.)
OFFSET_SEQUENCE=(0,60,120)


def inner(endpoint,table,kind):
    return f".inner_id.{kind}.{base.table_uuid(endpoint,table)}"


def quote(inner_table):
    return 'default.'+chr(96)+inner_table+chr(96)


def input_line(epoch,value):
    return json.dumps({'metric_name':METRIC,
        'tags':{'scope':'victim','project':'safe-test','series':'same-id'},
        'samples':[[base.stamp(epoch),value]]},
        sort_keys=True,separators=(',',':'))+'\n'


def select_native(endpoint,table):
    expr=f'avg by(scope)(avg_over_time({METRIC}[10m]))'
    stmt=(f'SELECT * FROM prometheusQueryRange({table},{base.q(expr)},'
          f'{END},{END},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    body=base.sql(endpoint,stmt)
    result={}
    for line in body.splitlines():
        if not line.strip():
            continue
        row=json.loads(line)
        tags=dict(row['tags'])
        if tags!={'scope':'victim'} or len(row['samples'])!=1:
            raise AssertionError('Unexpected native tags/point count: '+line[:400])
        timestamp,value=row['samples'][0]
        ts=int(dt.datetime.fromisoformat(timestamp.replace(' ','T'))
               .replace(tzinfo=dt.timezone.utc).timestamp())
        if ts!=END or 'victim' in result:
            raise AssertionError('Wrong timestamp or duplicate group')
        result['victim']=float(value)
    return {'value':result,'result_count':len(result),
            'sql_sha256':hashlib.sha256(stmt.encode()).hexdigest()}


def check_native(r,expected,stage):
    if expected is None:
        if r['result_count']!=0:
            raise AssertionError(stage+': expected zero visible series but got '+repr(r))
    else:
        if r['result_count']!=1 or not math.isclose(r['value'].get('victim',float('nan')),
                expected,abs_tol=1e-8,rel_tol=1e-11):
            raise AssertionError(stage+': native value mismatch '+repr((expected,r)))


def count(endpoint,inner_table):
    return int(base.sql(endpoint,'SELECT count() FROM '+quote(inner_table)+' FORMAT TSV').strip())


def stage_snapshot(endpoint,table,step):
    return {'step':step,'tags_rows':count(endpoint,inner(endpoint,table,'tags')),
            'timeranges_rows':count(endpoint,inner(endpoint,table,'timeranges')),
            'physical_sample_points':base.physical(endpoint,table)['physical_sample_points'],
            'native':select_native(endpoint,table)}


def query_logs(endpoint,ids):
    base.sql(endpoint,'SYSTEM FLUSH LOGS')
    arr=base.jsonrows(endpoint,
        "SELECT query_id,ProfileEvents,exception_code FROM system.query_log "
        "WHERE type='QueryFinish' AND startsWith(query_id,'r19cache_')")
    d={x['query_id']:x for x in arr}
    if not all(i in d for i in ids):
        raise AssertionError('QueryFinish did not include all tagged inserts')
    return {k:d[k] for k in ids}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    p.add_argument('--out',required=True)
    a=p.parse_args()
    dest=pathlib.Path(a.out)
    dest.parent.mkdir(parents=True,exist_ok=True)
    out={'schema_version':1,
         'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
         'subexperiment':'direct-inner-TAGS-DELETE-dedup-cache-invalidation-safety',
         'source_sha':SOURCE,'metric':METRIC,'cache_expiration_seconds':3600,
         'modes':{},'inputs_sha256':{},'errors':[],
         'passed':False,'scientific_decision':'INCONCLUSIVE'}
    def save():
        dest.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    try:
        for mode,bytes_ in MODE.items():
            table='tslab_r19_gc_cache_'+mode
            ddl=f'CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0, tags_deduplication_cache_size_bytes={bytes_}, tags_deduplication_cache_expiration_seconds=3600'
            out['modes'][mode]={'table':table,'ddl_sha256':hashlib.sha256(ddl.encode()).hexdigest(),
                                'configured_cache_bytes':bytes_,
                                'steps':[],'query_log':{}}
            base.sql(a.endpoint,ddl)
            t=inner(a.endpoint,table,'tags')
            s=base.sql(a.endpoint,'SHOW CREATE TABLE '+table+' FORMAT TSV')
            out['modes'][mode]['outer_show_create_sha256']=hashlib.sha256(s.encode()).hexdigest()
            for key,i in enumerate((0,1,2)):
                if i==1:
                    # Direct inner target mutation, intentionally NOT a safe
                    # production strategy and not an outer TimeSeries operation.
                    mutation=('ALTER TABLE '+quote(t)+
                         f' DELETE WHERE metric_name={base.q(METRIC)} SETTINGS mutations_sync=2')
                    base.sql(a.endpoint,mutation)
                    post_delete=stage_snapshot(a.endpoint,table,'post_tags_delete')
                    if post_delete['tags_rows']!=0:
                        raise AssertionError('Synthetic TAGS DELETE mutation did not remove row')
                    check_native(post_delete['native'],None,mode+'_post_delete')
                    out['modes'][mode]['steps'].append(post_delete)
                if i==2:
                    # Explicitly invalidate the outer time-series caches
                    # before the third reinsert; the cache-enabled table must
                    # then regain its metadata.
                    base.sql(a.endpoint,'SYSTEM DROP TIME SERIES CACHES '+table)
                    out['modes'][mode]['cache_invalidation_statement']='SYSTEM DROP TIME SERIES CACHES '+table
                payload=input_line(EPOCH+OFFSET_SEQUENCE[i],VALUE_SEQUENCE[i])
                out['inputs_sha256'][f'read_input_{i}']=hashlib.sha256(payload.encode()).hexdigest()
                qid=f'r19cache_{mode}_insert_{i}'
                base.sql(a.endpoint,
                    f'INSERT INTO {table}(metric_name,tags,samples) FORMAT JSONEachRow\n'+payload,qid)
                snap=stage_snapshot(a.endpoint,table,'insert_'+str(i))
                out['modes'][mode]['steps'].append(snap)
                if snap['physical_sample_points']!=i+1:
                    raise AssertionError(f'{mode}: wrong physical sample count after insert {i}')
                if i==0:
                    if snap['tags_rows']!=1:
                        raise AssertionError('TAGS were not created by first insert')
                    check_native(snap['native'],VALUE_SEQUENCE[0],mode+'_initial')
                if i==1:
                    if bytes_ and snap['tags_rows']!=0:
                        raise AssertionError('Hypothesis falsified: cache-enabled TAGS restored after direct DELETE')
                    if not bytes_ and snap['tags_rows']!=1:
                        raise AssertionError('Cache-disabled TAGS were not rewritten after direct DELETE')
                    check_native(snap['native'],None if bytes_ else sum(VALUE_SEQUENCE[:2])/2,
                                 mode+'_second_insert')
                if i==2:
                    if snap['tags_rows']!=1:
                        raise AssertionError('Cache invalidation did not restore tags on next insert')
                    check_native(snap['native'],sum(VALUE_SEQUENCE)/3,mode+'_restored')
                save()
            qids=[f'r19cache_{mode}_insert_{i}' for i in range(3)]
            logs=query_logs(a.endpoint,qids)
            out['modes'][mode]['query_log']={k:{
                'exception_code':logs[k]['exception_code'],
                'dedup_cache_hits':logs[k]['ProfileEvents'].get('TimeSeriesTagsDeduplicationCacheHits'),
                'dedup_cache_misses':logs[k]['ProfileEvents'].get('TimeSeriesTagsDeduplicationCacheMisses')}
                for k in qids}
            if any(x['exception_code']!=0 for x in out['modes'][mode]['query_log'].values()):
                raise AssertionError('QueryFinish recorded a failed insert')
        on=out['modes']['enabled']['steps']
        off=out['modes']['disabled']['steps']
        after_on=next(x for x in on if x['step']=='insert_1')
        after_off=next(x for x in off if x['step']=='insert_1')
        if after_on['tags_rows']!=0 or after_on['native']['result_count']!=0 or \
           after_on['physical_sample_points']!=2:
            raise AssertionError('Cache-enabled orphan hazard not replicated')
        if after_off['tags_rows']!=1 or after_off['native']['result_count']!=1 or \
           after_off['physical_sample_points']!=2:
            raise AssertionError('No-cache control did not recover metadata')
        out['passed']=True
        out['scientific_decision']='SAFETY_HAZARD_REPRODUCED'
    except Exception as exc:
        out['errors'].append(str(exc)[:2600])
        out['scientific_decision']='INVALID_METHOD_OR_FALSIFIED'
    finally:
        save()
        print('R19_CACHE_SAFETY_DONE',json.dumps({
            'passed':out['passed'],'decision':out['scientific_decision'],
            'modes':{k:[{'step':x['step'],'tags':x['tags_rows'],
                  'physical_points':x['physical_sample_points'],
                  'native_count':x['native']['result_count']}
                 for x in v['steps']] for k,v in out['modes'].items()},
            'errors':out['errors']},separators=(',',':')),flush=True)
    if not out['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
