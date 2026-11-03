#!/usr/bin/env python3
"""R19: retention TTL zero-row part lifecycle under repeated late inserts.

Do NOT mistake empty old partition parts for live expired data. Test whether
natural merges and explicit OPTIMIZE FINAL remove/consolidate zero-row parts.
Reuses pinned 30d TTL definition and separate exact native mean oracle.
No production data, concurrency, latency or release recommendation.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r19_ttl_resurrection_semantics as ttl

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
TABLE='tslab_r19_empty_part_lifecycle'
LATE_INSERTS=16
TABLE_EXPERIMENT_DIR='R19'


def old_statistics(endpoint,inner):
    parts=ttl.partitions(endpoint,inner)
    old=[p for p in parts if p['partition_id']=='20260810']
    live=[p for p in parts if p['partition_id']=='20261020']
    return {'old_rows':sum(int(p['rows']) for p in old),
            'old_active_part_count':sum(int(p['parts']) for p in old),
            'old_bytes':sum(int(p['bytes']) for p in old),
            'live_rows':sum(int(p['rows']) for p in live),
            'live_part_count':sum(int(p['parts']) for p in live),
            'partitions':parts}


def assert_native(endpoint,kind):
    # The metric and table names of r19_ttl_resurrection_semantics are reused
    # unchanged. Old expired writes must not affect the Oct20 native oracle.
    result=ttl.native(endpoint,kind,False)
    if result['points']!=1:
        raise AssertionError('Expired data made old series visible')
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    p.add_argument('--out',required=True)
    a=p.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    if not (dt.datetime(2026,11,1,tzinfo=dt.timezone.utc) <= now <
            dt.datetime(2026,11,18,tzinfo=dt.timezone.utc)):
        raise AssertionError('Static 30d TTL fixture is valid only Nov 1-17 2026 UTC')
    ttl.TABLE=TABLE
    out=pathlib.Path(a.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
        'subexperiment':'expired_zero_row_SAMPLES_parts_lifecycle_after_16_late_inserts',
        'source_sha':SOURCE,'experiment_started':now.isoformat(),
        'requested_late_writes':LATE_INSERTS,
        'input_sha256':{},'observations':[], 'checks':{}, 'errors':[],
        'passed':False,'scientific_result':'INCONCLUSIVE'}
    try:
        ddl=(f'CREATE TABLE {TABLE} ENGINE=TimeSeries '
             'SETTINGS recent_samples_ttl_seconds=0 '
             'SAMPLES INNER ENGINE = AggregatingMergeTree '
             "PARTITION BY toDate(bucket,'UTC') ORDER BY (id,bucket) "
             "TTL toDateTime(bucket,'UTC') + INTERVAL 30 DAY "
             'SETTINGS ttl_only_drop_parts=1')
        report['ddl_sha256']=hashlib.sha256(ddl.encode()).hexdigest()
        ttl.request(a.endpoint,ddl)
        inner=ttl.table_inner(a.endpoint,'samples')
        show=ttl.request(a.endpoint,'SHOW CREATE TABLE '+ttl.named(inner)+' FORMAT TSV')
        if 'TTL' not in show or 'ttl_only_drop_parts' not in show:
            raise AssertionError('TimeSeries inner TTL not in effective DDL')
        report['inner_ddl_sha256']=hashlib.sha256(show.encode()).hexdigest()
        old=ttl.row('old',0,ttl.OLD,7.0)
        live=''.join(ttl.row('live',i,ttl.NEW,100+i/10) for i in range(ttl.LIVE))
        report['input_sha256']['old']=hashlib.sha256(old.encode()).hexdigest()
        report['input_sha256']['live']=hashlib.sha256(live.encode()).hexdigest()
        ttl.request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+old)
        ttl.request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+live)
        ttl.materialize(a.endpoint,inner)
        first=old_statistics(a.endpoint,inner)
        if first['old_rows']!=0 or first['live_rows']!=ttl.LIVE:
            raise AssertionError('Failed to remove old samples while keeping live data')
        report['checks']['initial']={'physical':first,
                   'tags_rows':ttl.counts(a.endpoint,'tags'),
                   'timeranges_rows':ttl.counts(a.endpoint,'timeranges'),
                   'native':assert_native(a.endpoint,'initial')}
        if report['checks']['initial']['tags_rows']!=ttl.LIVE+1 or \
           report['checks']['initial']['timeranges_rows']!=ttl.LIVE+1:
            raise AssertionError('Initial metadata identity mismatch')
        report['observations'].append({'batch':0,**first})
        late_sha=hashlib.sha256()
        for n in range(1,LATE_INSERTS+1):
            late=ttl.row('old',0,ttl.OLD+n*60,float(n+7))
            late_sha.update(late.encode())
            ttl.request(a.endpoint,
                f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+late)
            ttl.materialize(a.endpoint,inner)
            snap=old_statistics(a.endpoint,inner)
            report['observations'].append({'batch':n,**snap})
            if snap['old_rows']!=0 or snap['live_rows']!=ttl.LIVE:
                raise AssertionError(f'Expired samples survived batch {n}: {snap}')
            if n in (1,2,4,8,16):
                assert_native(a.endpoint,'late_'+str(n))
        report['input_sha256']['late_sequence']=late_sha.hexdigest()
        report['checks']['after_late_writes']={
            'physical':report['observations'][-1],
            'tags_rows':ttl.counts(a.endpoint,'tags'),
            'timeranges_rows':ttl.counts(a.endpoint,'timeranges'),
            'native':assert_native(a.endpoint,'late_final')}
        # Let natural merges run. Observing a stable part count is an
        # observation, not proof that the parts will remain indefinitely.
        for delay in (1.0,3.0):
            time.sleep(delay)
            snap=old_statistics(a.endpoint,inner)
            report['observations'].append({'natural_wait_s':delay,**snap})
            if snap['old_rows']!=0 or snap['live_rows']!=ttl.LIVE:
                raise AssertionError('Old sample came back during natural merge')
        before_opt=old_statistics(a.endpoint,inner)
        try:
            stmt=f'OPTIMIZE TABLE {ttl.named(inner)} PARTITION ID \x2720260810\x27 FINAL ' \
                 f'SETTINGS optimize_throw_if_noop=0'
            report['checks']['optimize_sql_sha256']=hashlib.sha256(stmt.encode()).hexdigest()
            ttl.request(a.endpoint,stmt)
            report['checks']['optimize_status']='succeeded'
        except Exception as exc:
            # A dropped/empty partition can reject OPTIMIZE; capture exact
            # engine response without calling it a TTL correctness failure.
            report['checks']['optimize_status']='not_applicable_or_failed'
            report['checks']['optimize_error']=str(exc)[:1400]
        after_opt=old_statistics(a.endpoint,inner)
        report['checks']['optimize']={'before':before_opt,'after':after_opt}
        if after_opt['old_rows']!=0 or after_opt['live_rows']!=ttl.LIVE:
            raise AssertionError('OPTIMIZE changed live/expired sample validity')
        final_native=assert_native(a.endpoint,'post_optimize')
        # AggregateTree TimeRanges can have multiple physical rows per ID
        # until merge. Match semantics with R19 prior resurrection test.
        for kind in ('tags','timeranges'):
            ttl.request(a.endpoint,
                'OPTIMIZE TABLE '+ttl.named(ttl.table_inner(a.endpoint,kind))+
                ' FINAL SETTINGS optimize_throw_if_noop=0')
        report['checks']['final']={
            'native':final_native,
            'samples_rows':ttl.counts(a.endpoint,'samples'),
            'tags_rows':ttl.counts(a.endpoint,'tags'),
            'timeranges_rows':ttl.counts(a.endpoint,'timeranges')}
        if report['checks']['final']['tags_rows']!=ttl.LIVE+1 or \
           report['checks']['final']['timeranges_rows']!=ttl.LIVE+1:
            raise AssertionError('Late writes unexpectedly changed stable TAGS/TimeRanges identity')
        report['passed']=True
        observed=[x for x in report['observations'] if 'batch' in x]
        if observed[-1]['old_active_part_count']>observed[0]['old_active_part_count']:
            report['scientific_result']='EMPTY_PART_GROWTH_OBSERVED'
        else:
            report['scientific_result']='NO_MONOTONIC_EMPTY_PART_GROWTH'
        report['checks']['empty_part_growth']={
            'initial':observed[0]['old_active_part_count'],
            'after_16':observed[-1]['old_active_part_count'],
            'after_natural_wait':before_opt['old_active_part_count'],
            'after_manual_final':after_opt['old_active_part_count'],
            'initial_bytes':observed[0]['old_bytes'],
            'after_16_bytes':observed[-1]['old_bytes'],
            'after_natural_wait_bytes':before_opt['old_bytes'],
            'after_manual_final_bytes':after_opt['old_bytes']}
    except Exception as exc:
        report['errors'].append(str(exc)[:2200])
    finally:
        out.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
        print('R19_EMPTY_PARTS_DONE',json.dumps({
            'passed':report['passed'],'decision':report['scientific_result'],
            'growth':report['checks'].get('empty_part_growth'),
            'optimize_status':report['checks'].get('optimize_status'),
            'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
