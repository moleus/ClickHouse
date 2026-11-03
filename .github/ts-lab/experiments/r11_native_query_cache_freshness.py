#!/usr/bin/env python3
"""R11: Does an opted-in QueryResultCache serve stale native PromQL after INSERT?

The pinned QueryResultCache.h explicitly states that INSERT/DELETE do not
invalidate result cache. This test determines whether native
prometheusQueryRange is a cacheable path, with an independent input oracle.
Never use this server for production data or make production cache changes.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

SRC = '52529f0c711566d696917678bf15e7068772fdde'
T0 = int(dt.datetime(2026, 8, 10, 12, tzinfo=dt.timezone.utc).timestamp())
T1 = T0 + 120
METRIC = 'tslab_r11_query_result_cache'


def quote(s):
    return "'" + s.replace('\\', '\\\\').replace("'", "\\'") + "'"


def sql(endpoint, statement, query_id=None):
    url = endpoint
    if query_id is not None:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode({'query_id': query_id})
    try:
        request = urllib.request.Request(url, data=statement.encode('utf-8'), method='POST')
        with urllib.request.urlopen(request, timeout=35) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'HTTP {exc.code}: {exc.read().decode(errors="replace")[:1200]}') from exc


def sample_row(t, value):
    return json.dumps({'metric_name': METRIC, 'tags': {'case_id': 'same-series'},
                       'samples': [[dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000'),
                                    float(value)]]},
                      sort_keys=True, separators=(',', ':')) + '\n'


def event_counters(endpoint):
    statement = ("SELECT event,value FROM system.events "
                 "WHERE event IN ('QueryCacheHits','QueryCacheMisses','QueryCacheWrittenRows') "
                 "FORMAT JSONEachRow")
    return {x['event']: x['value'] for x in
            (json.loads(line) for line in sql(endpoint, statement).splitlines() if line)}


def physical_point_count(endpoint, table):
    uuid = sql(endpoint, "SELECT toString(uuid) FROM system.tables "
               "WHERE database='default' AND name=" + quote(table) + " FORMAT TSV").strip()
    if len(uuid) != 36:
        raise AssertionError(f'Bad TimeSeries UUID: {uuid}')
    inner = 'default.' + chr(96) + '.inner_id.samples.' + uuid + chr(96)
    return int(sql(endpoint, 'SELECT sum(length(samples)) FROM ' + inner + ' FORMAT TSV').strip())


def native(endpoint, table, use_cache, qid):
    expr = f'avg by(case_id)(avg_over_time({METRIC}[5m]))'
    stmt = (f'SELECT * FROM prometheusQueryRange({table},{quote(expr)},{T1},{T1},60) '
            f'SETTINGS use_query_cache={int(use_cache)},'
            f'query_cache_min_query_runs=0,query_cache_ttl=120,max_threads=2 '
            f'FORMAT JSONEachRow')
    rows = [json.loads(line) for line in sql(endpoint, stmt, qid).splitlines() if line]
    if len(rows) != 1:
        raise AssertionError(f'Unexpected full native TimeSeries rows: {rows}')
    labels = dict(rows[0]['tags'])
    if labels != {'case_id': 'same-series'} or len(rows[0]['samples']) != 1:
        raise AssertionError(f'Unexpected native labels/sample shape: {rows}')
    t, value = rows[0]['samples'][0]
    parsed = int(dt.datetime.fromisoformat(t.replace(' ', 'T'))
                 .replace(tzinfo=dt.timezone.utc).timestamp())
    if parsed != T1:
        raise AssertionError(f'Wrong sample timestamp: {parsed} != {T1}')
    return {'value': float(value), 'query_sha256': hashlib.sha256(stmt.encode()).hexdigest(),
            'case_id': labels['case_id'], 'epoch': parsed,
            'query_id': qid}


def count_entries(endpoint):
    try:
        return int(sql(endpoint, "SELECT count() FROM system.query_cache FORMAT TSV").strip())
    except Exception as exc:
        return {'unavailable': str(exc)[:350]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--endpoint', default='http://127.0.0.1:18191/')
    p.add_argument('--table', default='tslab_r11_cache')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = sample_row(T0, 1)
    second = sample_row(T1, 3)
    report = {'schema_version': 1, 'question_id': 'R11-native-promql-query-cache-consistency-v1',
              'source_sha': SRC, 'metric': METRIC, 'table': a.table,
              'input_sha256': [hashlib.sha256(x.encode()).hexdigest() for x in (first, second)],
              'oracle': {'before_insert': 1., 'after_insert': 2.},
              'query_time_epoch': T1, 'cache_ttl_s': 120,
              'cached': {}, 'uncached': {}, 'event_counters': {},
              'errors': [], 'passed': False, 'scientific_result': 'INCONCLUSIVE'}
    try:
        sql(a.endpoint, 'SYSTEM DROP QUERY CACHE')
        sql(a.endpoint, 'CREATE TABLE ' + a.table +
                        ' ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0')
        sql(a.endpoint, 'INSERT INTO ' + a.table +
                        '(metric_name,tags,samples) FORMAT JSONEachRow\n' + first)
        if physical_point_count(a.endpoint, a.table) != 1:
            raise AssertionError('Initial input should contain exactly one sample point')
        report['event_counters']['before'] = event_counters(a.endpoint)
        report['cached']['before'] = native(a.endpoint, a.table, True, 'r11_cache_before')
        report['cached']['hit_check'] = native(a.endpoint, a.table, True, 'r11_cache_hit')
        report['event_counters']['after_hit'] = event_counters(a.endpoint)
        report['cache_entries_after_hit'] = count_entries(a.endpoint)
        assert report['cached']['before']['value'] == 1.0
        assert report['cached']['hit_check']['value'] == 1.0
        assert report['cached']['before']['query_sha256'] == report['cached']['hit_check']['query_sha256']
        before = report['event_counters']['before'].get('QueryCacheHits', 0)
        after = report['event_counters']['after_hit'].get('QueryCacheHits', 0)
        if after <= before:
            raise AssertionError('No proven native query cache hit: '+repr(report['event_counters']))
        sql(a.endpoint, 'INSERT INTO ' + a.table +
                        '(metric_name,tags,samples) FORMAT JSONEachRow\n' + second)
        if physical_point_count(a.endpoint, a.table) != 2:
            raise AssertionError('Second INSERT did not persist a new sample')
        report['uncached']['after_insert'] = native(a.endpoint, a.table, False, 'r11_uncached_after_insert')
        assert report['uncached']['after_insert']['value'] == 2.0
        report['cached']['after_insert'] = native(a.endpoint, a.table, True, 'r11_cached_after_insert')
        report['event_counters']['after_insert'] = event_counters(a.endpoint)
        hit_after = report['event_counters']['after_insert'].get('QueryCacheHits', 0)
        if hit_after <= after:
            raise AssertionError('No second native cache hit after INSERT; cannot attribute result to query cache')
        assert report['cached']['before']['query_sha256'] == report['cached']['after_insert']['query_sha256']
        if report['cached']['after_insert']['value'] == 1.0:
            report['scientific_result'] = 'CACHE_STALE_AFTER_INSERT'
        elif report['cached']['after_insert']['value'] == 2.0:
            report['scientific_result'] = 'CACHE_REFRESHED_AFTER_INSERT'
        else:
            raise AssertionError('Unexpected post-INSERT cached result')
        sql(a.endpoint, 'SYSTEM DROP QUERY CACHE')
        report['cached']['after_drop'] = native(a.endpoint, a.table, True, 'r11_cache_after_drop')
        if report['cached']['after_drop']['value'] != 2.0:
            raise AssertionError('Fresh query after cache clear must see current two points')
        report['passed'] = True
    except Exception as exc:
        report['errors'].append(str(exc)[:2200])
        report['scientific_result'] = 'INCONCLUSIVE_OR_INFRA_FAIL'
    finally:
        out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
        print('R11_CACHE_RESULT', json.dumps(
            {'passed': report['passed'], 'scientific_result': report['scientific_result'],
             'uncached_after_insert': report['uncached'].get('after_insert', {}).get('value'),
             'cached_after_insert': report['cached'].get('after_insert', {}).get('value'),
             'events': report['event_counters'], 'errors': report['errors']},
            separators=(',', ':')), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
