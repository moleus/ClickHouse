#!/usr/bin/env python3
"""R19: TimeRanges-only retirement as a safer candidate than deleting TAGS.

No new access path or sidecar. Use the unpatched TimeSeries v11 schema; retain
TAGS and exact metric IDs, and delete only TimeRanges rows of sample-expired
series. Test that native PromQL still sees active samples and a previously
expired series becomes visible after fresh INSERT (TimeSeriesSink always
writes TimeRanges even if TAGS dedup cache skips metadata). Deliberate direct
inner TimeRanges mutation is an isolated lab candidate, not production safe.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r19_ttl_resurrection_semantics as ttl

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
TABLE='tslab_r19_timeranges_retirement'
CUTOFF='2026-09-01 00:00:00'
OLD_COUNT=128
LIVE_COUNT=16


def state(endpoint,table,stage):
    samples=ttl.table_inner(endpoint,'samples')
    tags=ttl.table_inner(endpoint,'tags')
    timeranges=ttl.table_inner(endpoint,'timeranges')
    return {'stage':stage,'samples_rows':ttl.counts(endpoint,'samples'),
            'tags_rows':ttl.counts(endpoint,'tags'),
            'timeranges_rows':ttl.counts(endpoint,'timeranges'),
            'sample_partitions':ttl.partitions(endpoint,samples),
            'tags_distinct_id':int(ttl.request(endpoint,'SELECT uniqExact(id) FROM '+ttl.named(tags)+' FORMAT TSV').strip()),
            'timeranges_distinct_id':int(ttl.request(endpoint,'SELECT uniqExact(id) FROM '+ttl.named(timeranges)+' FORMAT TSV').strip())}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    p.add_argument('--out',required=True)
    a=p.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    if not (dt.datetime(2026,11,1,tzinfo=dt.timezone.utc) <= now <
            dt.datetime(2026,11,18,tzinfo=dt.timezone.utc)):
        raise AssertionError('Pinned 30d TTL fixture only valid 1-17 Nov 2026 UTC')
    ttl.TABLE=TABLE
    output=pathlib.Path(a.out)
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,
        'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
        'subexperiment':'TimeRanges-only-historical-metadata-retirement-with-resurrection',
        'source_sha':SOURCE,
        'created_utc':now.isoformat(),
        'cutoff_utc':CUTOFF,
        'old_series':OLD_COUNT,'live_series':LIVE_COUNT,
        'inputs_sha256':{},'steps':{},'errors':[], 'passed':False,
        'scientific_decision':'INCONCLUSIVE'}
    def save():
        output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    try:
        ddl=(f'CREATE TABLE {TABLE} ENGINE=TimeSeries '
              'SETTINGS recent_samples_ttl_seconds=0 '
              'SAMPLES INNER ENGINE = AggregatingMergeTree '
              "PARTITION BY toDate(bucket,'UTC') ORDER BY (id,bucket) "
              "TTL toDateTime(bucket,'UTC') + INTERVAL 30 DAY "
              'SETTINGS ttl_only_drop_parts=1')
        report['ddl_sha256']=hashlib.sha256(ddl.encode()).hexdigest()
        ttl.request(a.endpoint,ddl)
        old=''.join(ttl.row('old',i,ttl.OLD,1+i/100) for i in range(OLD_COUNT))
        live=''.join(ttl.row('live',i,ttl.NEW,100+i/10) for i in range(LIVE_COUNT))
        report['inputs_sha256']['old']=hashlib.sha256(old.encode()).hexdigest()
        report['inputs_sha256']['live']=hashlib.sha256(live.encode()).hexdigest()
        for body in (old,live):
            ttl.request(a.endpoint,
                f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+body)
        samples=ttl.table_inner(a.endpoint,'samples')
        ranges=ttl.table_inner(a.endpoint,'timeranges')
        ttl.materialize(a.endpoint,samples)
        p=state(a.endpoint,TABLE,'after_sample_ttl')
        report['steps']['after_sample_ttl']=p
        if (p['samples_rows'],p['tags_distinct_id'],p['timeranges_distinct_id'])!= \
                (LIVE_COUNT,OLD_COUNT+LIVE_COUNT,OLD_COUNT+LIVE_COUNT):
            raise AssertionError('Original TAGS/TimeRanges metadata or live samples unexpectedly missing: '+repr(p))
        report['steps']['native_before']=ttl.native(a.endpoint,'before_range_retirement',False)
        save()
        mutation=('ALTER TABLE '+ttl.named(ranges)+
            f" DELETE WHERE max_time < toDateTime64('{CUTOFF}',3,'UTC') "
            'SETTINGS mutations_sync=2')
        report['mutation_sha256']=hashlib.sha256(mutation.encode()).hexdigest()
        ttl.request(a.endpoint,mutation)
        p=state(a.endpoint,TABLE,'after_ranges_retirement')
        report['steps']['after_ranges_retirement']=p
        if (p['samples_rows'],p['tags_distinct_id'],p['timeranges_distinct_id'])!= \
                (LIVE_COUNT,OLD_COUNT+LIVE_COUNT,LIVE_COUNT):
            raise AssertionError('TimeRanges only removal failed the expected 128 retired ID count')
        report['steps']['native_after_retire']=ttl.native(a.endpoint,'after_range_retirement',False)
        if report['steps']['native_before']['values']!=report['steps']['native_after_retire']['values']:
            raise AssertionError('TimeRanges-only cleanup changed the active native oracle')
        save()
        # Resume expired ID0 at a current timestamp. TAGS is kept and its
        # in-memory cache may still be warm. New TimeRanges is always written.
        resume=ttl.row('old',0,ttl.NEW+120,7.0)
        report['inputs_sha256']['resume']=hashlib.sha256(resume.encode()).hexdigest()
        ttl.request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+resume)
        p=state(a.endpoint,TABLE,'after_old_series_resumed')
        report['steps']['after_old_series_resumed']=p
        if (p['samples_rows'],p['tags_distinct_id'],p['timeranges_distinct_id'])!= \
                (LIVE_COUNT+1,OLD_COUNT+LIVE_COUNT,LIVE_COUNT+1):
            raise AssertionError('Resumed old ID did not reinsert TimeRanges and sample')
        report['steps']['native_after_resume']=ttl.native(a.endpoint,'after_resume',True)
        save()
        # Late historical data after current resurrection must expire while
        # preserving the fresh current data and the original ID.
        late=ttl.row('old',0,ttl.OLD+120,91.0)
        report['inputs_sha256']['late']=hashlib.sha256(late.encode()).hexdigest()
        ttl.request(a.endpoint,
            f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n'+late)
        ttl.materialize(a.endpoint,samples)
        for kind in ('tags','timeranges'):
            ttl.request(a.endpoint,'OPTIMIZE TABLE '+
                ttl.named(ttl.table_inner(a.endpoint,kind))+
                ' FINAL SETTINGS optimize_throw_if_noop=0')
        p=state(a.endpoint,TABLE,'after_late_and_final')
        report['steps']['after_late_and_final']=p
        if (p['samples_rows'],p['tags_distinct_id'],p['timeranges_distinct_id'])!= \
                (LIVE_COUNT+1,OLD_COUNT+LIVE_COUNT,LIVE_COUNT+1):
            raise AssertionError('Late history changed physical row count/series identity after FINAL')
        report['steps']['native_final']=ttl.native(a.endpoint,'after_late',True)
        if report['steps']['native_after_resume']['values']!=report['steps']['native_final']['values']:
            raise AssertionError('Late old sample changed active native oracle')
        report['passed']=True
        report['scientific_decision']='SEMANTIC_LAB_PROOF_ONLY'
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        report['scientific_decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R19_RANGES_RETIRE_DONE',json.dumps({'passed':report['passed'],
             'decision':report['scientific_decision'],
             'physical':{k:v for k,v in report['steps'].items() if 'timeranges_rows' in v},
             'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
