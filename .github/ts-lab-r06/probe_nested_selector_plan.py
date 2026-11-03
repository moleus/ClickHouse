#!/usr/bin/env python3
"""Capture native TimeSeries generated SQL and EXPLAIN on an isolated server.

Plan-only investigation. Never interpret any single query timing as a gain.
Requires ClickHouse at localhost:18191 with DEBUG console logging.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

URL = 'http://127.0.0.1:18191'
ANCHOR = 'StorageTimeSeriesSelector: Will execute query:'
STAMP = re.compile(r'^\d{4}[.\-]\d\d[.\-]\d\d(?:\s|T)')
MODES = ('off_merge', 'auto_merge', 'off_hash', 'auto_hash')
MATCHERS = {'stage': '{stage="stage-0007"}', 'project': '{project="project-00"}'}
START = int(dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc).timestamp())
END = START + 6 * 86400


def request(query, query_id=None, timeout=180):
    params = {'wait_end_of_query': 1}
    if query_id: params['query_id'] = query_id
    url = URL + '/?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=query.encode(), method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode()


def quoted(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


def parse_selector_sql(chunk):
    at = chunk.rfind(ANCHOR)
    if at < 0: raise ValueError('Missing StorageTimeSeriesSelector debug SQL')
    remainder = chunk[at + len(ANCHOR):].lstrip(' \r\n')
    lines = []
    for line in remainder.splitlines():
        if STAMP.match(line): break
        lines.append(line)
    stmt = '\n'.join(lines).strip()
    if not stmt.startswith('SELECT '): raise ValueError('Captured selector SQL is not SELECT')
    if 'timeSeriesStoreTags(' not in stmt:
        raise ValueError('Missing stateful TAGS registration in SQL')
    if len(stmt) > 100000: raise ValueError('Unexpected generated SQL length')
    return stmt


def capture(logfile, start_offset):
    for _ in range(40):
        with pathlib.Path(logfile).open('rb') as stream:
            stream.seek(start_offset)
            chunk = stream.read().decode(errors='replace')
        try: return parse_selector_sql(chunk)
        except ValueError: time.sleep(.15)
    raise ValueError('Could not obtain generated selector SQL from DEBUG log')


def fixture(path, series):
    h = hashlib.sha256()
    with pathlib.Path(path).open('wb') as output:
        for i in range(series):
            begin = dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc) + dt.timedelta(days=i % 7)
            samples = [(begin + dt.timedelta(minutes=15 * j)).strftime('%Y-%m-%d %H:%M:%S.000') for j in range(4)]
            row = {'metric_name': 'r06_cpu_seconds_total',
                   'tags': {'stage': f'stage-{i % 38:04d}', 'project': f'project-{i % 20:02d}',
                            'node': f'node-{i:08d}'},
                   'samples': [[ts, float((i % 101) + j)] for j, ts in enumerate(samples)]}
            line = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
            output.write(line)
            h.update(line)
    return {'sha256': h.hexdigest(), 'series': series, 'samples': 4 * series,
            'size_bytes': pathlib.Path(path).stat().st_size, 'fixture_family': 'R06-only 7d synthetic'}


def insert_fixture(path):
    statement = 'INSERT INTO r06_ts (metric_name,tags,samples) FORMAT JSONEachRow'
    url = URL + '/?' + urllib.parse.urlencode({'wait_end_of_query': 1, 'query': statement})
    req = urllib.request.Request(url, data=pathlib.Path(path).read_bytes(), method='POST')
    with urllib.request.urlopen(req, timeout=180) as response: response.read()


def timeranges():
    q = "SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.timeranges.') FORMAT JSONEachRow"
    names = [json.loads(line)['name'] for line in request(q).splitlines() if line.strip()]
    if len(names) != 1: raise ValueError(f'Expected one TimeRanges inner table, found {names}')
    return chr(96) + names[0].replace(chr(96), chr(96) * 2) + chr(96)


def settings_for(mode):
    setting = 'max_threads=4,optimize_use_projections=' + ('1' if mode.startswith('auto') else '0')
    if mode.endswith('hash'): setting += ",join_algorithm='hash',optimize_read_in_order=0"
    return setting


def explain(stmt, settings):
    result = {}
    for kind in ('PLAN indexes=1,projections=1', 'PIPELINE'):
        field = kind.split()[0].lower()
        try: result[field] = request('EXPLAIN ' + kind + ' ' + stmt + ' SETTINGS ' + settings)[:100000]
        except Exception as exc: result[field + '_error'] = str(exc)[:1600]
    return result


def main():
    import sys
    sys.path.insert(0, '.github/ts-lab-bench')
    from bench_bucket_cxx_abca import normalize_any, assert_same
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--series', type=int, default=8192)
    args = parser.parse_args()
    if not (100 <= args.series <= 20000): raise ValueError('Series count must be 100..20000')
    folder = pathlib.Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    report = {'kind': 'R06_NESTED_PLAN_ONLY', 'status': 'INCONCLUSIVE',
              'source_sha': '52529f0c711566d696917678bf15e7068772fdde',
              'cases': {}, 'errors': [], 'no_performance_claim': True}
    try:
        data = folder / 'fixture.jsonl'
        report['fixture'] = fixture(data, args.series)
        request('CREATE TABLE r06_ts ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0')
        insert_fixture(data)
        target = timeranges()
        report['timeranges_target'] = target
        for query in (f"ALTER TABLE {target} MODIFY SETTING deduplicate_merge_projection_mode='rebuild'",
                      f'ALTER TABLE {target} ADD INDEX idx_time_max max_time TYPE minmax GRANULARITY 1',
                      f'ALTER TABLE {target} MATERIALIZE INDEX idx_time_max SETTINGS mutations_sync=2',
                      f'ALTER TABLE {target} ADD PROJECTION time_by_max (SELECT id,min_time,max_time ORDER BY max_time,id)',
                      f'ALTER TABLE {target} MATERIALIZE PROJECTION time_by_max SETTINGS mutations_sync=2'):
            request(query)
        report['timeranges_show_create'] = request('SHOW CREATE TABLE ' + target)
        request('SYSTEM STOP MERGES')
        for case, matcher in MATCHERS.items():
            report['cases'][case] = {}
            expr = f'avg by(stage)(avg_over_time(r06_cpu_seconds_total{matcher}[1d]))'
            reference = None
            for mode in MODES:
                qid = f'r06_nested_{case}_{mode}_{os.getenv("GITHUB_RUN_ID", "local")}'
                settings = settings_for(mode)
                native = (f'SELECT * FROM prometheusQueryRange(r06_ts,{quoted(expr)},'
                          f'{START},{END},86400) SETTINGS {settings} FORMAT JSONEachRow')
                start_offset = pathlib.Path(args.log).stat().st_size
                rows = normalize_any(request(native, qid))
                if not rows: raise ValueError('Empty native result for ' + case + '/' + mode)
                parity = {'max_abs_error': 0}
                if reference is None: reference = rows
                else: parity = assert_same(reference, rows, case, mode)
                nested = capture(args.log, start_offset)
                item = {'query_id': qid, 'native_sql': native, 'points': len(rows),
                        'max_abs_error': parity['max_abs_error'], 'requested_settings': settings,
                        'generated_sql_sha256': hashlib.sha256(nested.encode()).hexdigest(),
                        'generated_selector_sql': nested, 'explain': explain(nested, settings)}
                report['cases'][case][mode] = item
                (folder / f'{case}-{mode}-selector.sql').write_text(nested + '\n')
                (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
                print('R06_CAPTURE', case, mode, 'points', len(rows),
                      'plan', bool(item['explain'].get('plan')), 'pipeline', bool(item['explain'].get('pipeline')), flush=True)
        report['status'] = 'PASS' if all(not any(k.endswith('_error') for k in item['explain'])
            for matrix in report['cases'].values() for item in matrix.values()) else 'INCONCLUSIVE'
    except Exception as exc:
        report['errors'].append(str(exc))
        report['status'] = 'INCONCLUSIVE'
    finally:
        (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print('R06_DONE', report['status'], report['errors'], flush=True)
    if report['status'] != 'PASS': raise SystemExit(1)


if __name__ == '__main__': main()
