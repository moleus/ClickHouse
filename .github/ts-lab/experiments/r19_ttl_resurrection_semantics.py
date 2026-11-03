#!/usr/bin/env python3
"""R19 actual SAMPLES TTL materialization, late old data, resumed historical ID.

Unlike r19_expired_metadata_native.py this enables TTL on a real inner
AggregatingMergeTree. No performance/timing claim; native semantic oracle only.
The fixture dates are valid for executions before 2026-11-18T00:00:00 UTC.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
TABLE='tslab_r19_ttl_actual'
METRIC='tslab_r19_ttl_semantics'
OLD=int(dt.datetime(2026,8,10,12,tzinfo=dt.timezone.utc).timestamp())
NEW=int(dt.datetime(2026,10,20,12,tzinfo=dt.timezone.utc).timestamp())
RETIRE=128
LIVE=16


def row(scope,id_,epoch,value):
    return json.dumps({'metric_name':METRIC,
      'tags':{'scope':scope,'series':f'{id_:04d}','project':'project-a'},
      'samples':[[base.stamp(epoch),float(value)]]},
       sort_keys=True,separators=(',',':'))+'\n'


def request(endpoint,query):
    return base.sql(endpoint,query)


def table_inner(endpoint,kind):
    uuid=base.table_uuid(endpoint,TABLE)
    return '.inner_id.'+kind+'.'+uuid


def named(inner):
    return 'default.'+chr(96)+inner+chr(96)


def partitions(endpoint,inner):
    return base.jsonrows(endpoint,
       "SELECT partition_id,count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes "
       "FROM system.parts WHERE active AND database='default' "
       f"AND table={base.q(inner)} GROUP BY partition_id ORDER BY partition_id")


def counts(endpoint,kind):
    return int(request(endpoint,
        'SELECT count() FROM '+named(table_inner(endpoint,kind))+' FORMAT TSV').strip())


def native(endpoint,phase,expect_old):
    end=NEW+180
    expr=f'avg by(scope)(avg_over_time({METRIC}[1d]))'
    sql=(f'SELECT * FROM prometheusQueryRange({TABLE},{base.q(expr)},'
         f'{end},{end},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    raw=request(endpoint,sql)
    got={}
    for line in raw.splitlines():
        if not line.strip():
            continue
        x=json.loads(line)
        tags=dict(x['tags'])
        if set(tags)!={'scope'} or len(x['samples'])!=1:
            raise AssertionError('Invalid native output shape '+str(x)[:250])
        timestamp,value=x['samples'][0]
        t=int(dt.datetime.fromisoformat(timestamp.replace(' ','T'))
              .replace(tzinfo=dt.timezone.utc).timestamp())
        if t!=end or tags['scope'] in got:
            raise AssertionError('Unexpected TimeSeries native grid/labels')
        got[tags['scope']]=float(value)
    expected_live=sum(100+i/10 for i in range(LIVE))/LIVE
    expected={'live':expected_live}
    if expect_old:
        expected['old']=7.0
    if set(got)!=set(expected):
        raise AssertionError(f'{phase}: wrong labels {got} expected {expected}')
    for k,val in expected.items():
        if not math.isclose(got[k],val,abs_tol=1e-8,rel_tol=1e-11):
            raise AssertionError(f'{phase}: wrong value for {k}, got {got[k]} expected {val}')
    return {'query_sha256':hashlib.sha256(sql.encode()).hexdigest(),
            'points':len(got),'values':got,'epoch':end}


def materialize(endpoint,samples):
    # Synchronous mutation, not a direct DELETE/DROP PARTITION.
    cmd='ALTER TABLE '+named(samples)+' MATERIALIZE TTL SETTINGS mutations_sync=2'
    request(endpoint,cmd)
    return hashlib.sha256(cmd.encode()).hexdigest()


def await_old_retired(endpoint,samples):
    for n in range(30):
        parts=partitions(endpoint,samples)
        if not any(x['partition_id']=='20260810' for x in parts):
            return parts
        time.sleep(.2)
    raise AssertionError('An expired old sample partition remained after MATERIALIZE TTL')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    p.add_argument('--out',required=True)
    a=p.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    if not dt.datetime(2026,11,1,tzinfo=dt.timezone.utc) <= now < dt.datetime(2026,11,18,tzinfo=dt.timezone.utc):
        raise AssertionError('Pinned TTL fixture window is only valid 1-17 Nov 2026 UTC')
    dest=pathlib.Path(a.out)
    dest.parent.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
          'subexperiment':'actual-SAMPLES-inner-TTL-and-resumed-old-ID-semantics',
          'source_sha':SOURCE,'created_utc':now.isoformat(),'ttl_days':30,
          'old_epoch':OLD,'live_epoch':NEW,'fixture_sha256':{},'checkpoints':{},
          'errors':[],'passed':False}
    try:
        ddl=(f'CREATE TABLE {TABLE} ENGINE=TimeSeries '
             'SETTINGS recent_samples_ttl_seconds=0 '
             'SAMPLES INNER ENGINE = AggregatingMergeTree '
             "PARTITION BY toDate(bucket,'UTC') ORDER BY (id,bucket) "
             "TTL toDateTime(bucket,'UTC') + INTERVAL 30 DAY "
             'SETTINGS ttl_only_drop_parts=1')
        report['ddl_sha256']=hashlib.sha256(ddl.encode()).hexdigest()
        request(a.endpoint,ddl)
        sample_inner=table_inner(a.endpoint,'samples')
        create=request(a.endpoint,'SHOW CREATE TABLE '+named(sample_inner)+' FORMAT TSV')
        report['samples_ddl_excerpt']=create[:9000]
        if 'TTL' not in create or 'ttl_only_drop_parts' not in create:
            raise AssertionError('Configured SAMPLES TTL is not visible in SHOW CREATE')
        old=''.join(row('old',i,OLD,1+i/100) for i in range(RETIRE))
        live=''.join(row('live',i,NEW,100+i/10) for i in range(LIVE))
        report['fixture_sha256']={'old':hashlib.sha256(old.encode()).hexdigest(),
                                  'live':hashlib.sha256(live.encode()).hexdigest()}
        request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+old)
        request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+live)
        report['checkpoints']['post_ingest']={
           'sample_partitions':partitions(a.endpoint,sample_inner),
           'tags_rows':counts(a.endpoint,'tags'),
           'timeranges_rows':counts(a.endpoint,'timeranges')}
        report['materialize_ttl_sql_sha256_first']=materialize(a.endpoint,sample_inner)
        after=await_old_retired(a.endpoint,sample_inner)
        if not any(x['partition_id']=='20261020' for x in after):
            raise AssertionError('Active live partition was incorrectly deleted')
        if counts(a.endpoint,'samples')!=LIVE:
            raise AssertionError('Real TTL must leave exactly 16 live sample points')
        first=native(a.endpoint,'post_expire',False)
        if counts(a.endpoint,'tags')!=RETIRE+LIVE or counts(a.endpoint,'timeranges')!=RETIRE+LIVE:
            raise AssertionError('TAGS/TimeRanges unexpectedly deleted during TTL')
        report['checkpoints']['post_ttl']={
            'sample_partitions':after,'samples_rows':counts(a.endpoint,'samples'),
            'tags_rows':counts(a.endpoint,'tags'),'timeranges_rows':counts(a.endpoint,'timeranges'),
            'native':first}
        # A late historical sample cannot become visible after TTL expiry.
        late=row('old',0,OLD+120,9.0)
        report['fixture_sha256']['late']=hashlib.sha256(late.encode()).hexdigest()
        request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+late)
        report['materialize_ttl_sql_sha256_second']=materialize(a.endpoint,sample_inner)
        await_old_retired(a.endpoint,sample_inner)
        after_late=native(a.endpoint,'after_late_old',False)
        if counts(a.endpoint,'samples')!=LIVE:
            raise AssertionError('Expired late sample survived actual TTL')
        report['checkpoints']['post_late']={
            'samples_rows':counts(a.endpoint,'samples'),'native':after_late}
        # Resume the exact old tag identity in a current bucket.
        resume=row('old',0,NEW+120,7.0)
        report['fixture_sha256']['resume']=hashlib.sha256(resume.encode()).hexdigest()
        request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+resume)
        after_resume=native(a.endpoint,'after_resume',True)
        if counts(a.endpoint,'samples')!=LIVE+1:
            raise AssertionError('Resumed old series did not insert one fresh sample')
        for kind in ('tags','timeranges'):
            request(a.endpoint,
                'OPTIMIZE TABLE '+named(table_inner(a.endpoint,kind))+
                ' FINAL SETTINGS optimize_throw_if_noop=0')
        if counts(a.endpoint,'tags')!=RETIRE+LIVE or \
           counts(a.endpoint,'timeranges')!=RETIRE+LIVE:
            raise AssertionError('Resumed series changed tag/TimeRanges stable identity count')
        report['checkpoints']['post_resume']={
            'samples_rows':counts(a.endpoint,'samples'),'native':after_resume,
            'tags_rows':counts(a.endpoint,'tags'),'timeranges_rows':counts(a.endpoint,'timeranges'),
            'sample_partitions':partitions(a.endpoint,sample_inner)}
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
    finally:
        dest.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print('R19_TTL_SEMANTICS',json.dumps({'passed':report['passed'],
             'checks':list(report['checkpoints']),'errors':report['errors']},
             separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
