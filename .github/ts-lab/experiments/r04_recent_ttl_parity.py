#!/usr/bin/env python3
"""R04 native TimeSeries RecentSamples TTL routing and numeric oracle.

Synthetic data relative to the recorded UTC minute avoids expiring before CI
starts. Only correctness and physical read-target selection, not speedup.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import time
import urllib.error
import urllib.request

METRIC = 'tslab_r04_ttl'
TTL_SECONDS = 3600
CASES = [
    ('hot5m', -30, 300, True),
    ('hot30m', -30, 1800, True),
    ('straddle2h', -30, 7200, False),
    ('older5m', -3500, 300, False),
    ('cold10m', -6900, 600, False),
    ('boundary5m', -3400, 300, False),
]
INITIAL = [
    ('hot', [(-240, 2.), (-90, 3.)]),
    ('mixed', [(-7050, 5.), (-210, 7.)]),
    ('old', [(-7110, 13.)]),
    ('boundary', [(-3610, 17.), (-3490, 19.)]),
]
LATE = [
    ('old', [(-7010, 29.)]),
    ('mixed', [(-100, 23.), (-7120, 31.)]),
]


def quote(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


def sql(endpoint, statement):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(endpoint, data=statement.encode(), method='POST'), timeout=60
        ) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f'HTTP {exc.code}: {exc.read().decode(errors="replace")[:1400]} {statement[:170]}'
        ) from exc


def stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000')


def serialize(records, base):
    return ''.join(
        json.dumps(
            {'metric_name': METRIC, 'tags': {'case_id': name},
             'samples': [[stamp(base + seconds), value] for seconds, value in samples]},
            sort_keys=True, separators=(',', ':'),
        ) + '\n'
        for name, samples in records
    )


def native(endpoint, table, base, case, prefer):
    name, offset, window, _ = case
    end = base + offset
    expression = f'avg by(case_id)(avg_over_time({METRIC}[{window}s]))'
    command = (
        f'SELECT * FROM prometheusQueryRange({table},{quote(expression)},{end},{end},60) '
        f'SETTINGS time_series_prefer_recent_samples_table={prefer},max_threads=2 FORMAT JSONEachRow'
    )
    actual = {}
    for line in sql(endpoint, command).splitlines():
        item = json.loads(line)
        labels = dict(item['tags'])
        if set(labels) != {'case_id'} or len(item['samples']) != 1:
            raise AssertionError(f'Unexpected native output {item}')
        when, value = item['samples'][0]
        epoch = int(dt.datetime.fromisoformat(when.replace(' ', 'T'))
                    .replace(tzinfo=dt.timezone.utc).timestamp())
        key = (labels['case_id'], epoch)
        if key in actual:
            raise AssertionError(f'Duplicate TimeSeries output {key}')
        actual[key] = float(value)
    # The translated native plan, not only a guessed selector setting,
    # determines whether ClickHouse actually selected RecentSamples.
    raw = sql(endpoint, 'EXPLAIN PLAN ' + command)
    plan = '\n'.join(json.loads(line)['explain'] for line in raw.splitlines() if line.strip())
    recent = '.inner_id.recentsamples.' in plan
    main = '.inner_id.samples.' in plan
    if recent == main:
        raise AssertionError('Exactly one main/recent physical sample target required')
    return {'values': actual, 'recent': recent, 'main': main,
            'explain_sha256': hashlib.sha256(plan.encode()).hexdigest(),
            'explain_excerpt': plan[:7500]}


def independent_oracle(base, canonical, case):
    _, offset, window, _ = case
    end = base + offset
    result = {}
    for series_id, samples in canonical.items():
        values = [value for t, value in samples if end - window < t <= end]
        if values:
            result[(series_id, end)] = sum(values) / len(values)
    return result


def part_statistics(endpoint, table_uuid):
    result = {}
    for kind in ('samples', 'recentsamples', 'tags', 'timeranges'):
        table = f'.inner_id.{kind}.{table_uuid}'
        statement = (
            f'SELECT count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes '
            f'FROM system.parts WHERE active AND database={quote("default")} '
            f'AND table={quote(table)} FORMAT JSONEachRow'
        )
        result[kind] = json.loads(sql(endpoint, statement).strip())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint', default='http://127.0.0.1:18191/')
    parser.add_argument('--table', default='tslab_r04_recent_ttl')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    base = int(time.time() // 60 * 60)
    canonical = {}
    input_blocks = []
    report = {'schema_version': 1, 'source_sha': '52529f0c711566d696917678bf15e7068772fdde',
              'question_id': 'R04-recent-ttl-parity-v1', 'base_epoch': base,
              'ttl_seconds': TTL_SECONDS, 'cases': {}, 'errors': [], 'inputs': [],
              'physical': {}, 'scope': 'native full PromQL target routing and independent avg oracle'}
    try:
        sql(args.endpoint, f'CREATE TABLE {args.table} ENGINE=TimeSeries '
                           f'SETTINGS recent_samples_ttl_seconds={TTL_SECONDS}')
        table_uuid = sql(args.endpoint,
            f'SELECT toString(uuid) FROM system.tables WHERE database={quote("default")} '
            f'AND name={quote(args.table)} FORMAT TSV').strip()
        if len(table_uuid) != 36:
            raise AssertionError('Missing TimeSeries UUID')
        for phase in ('initial', 'late_insert', 'after_merge'):
            if phase in ('initial', 'late_insert'):
                records = INITIAL if phase == 'initial' else LATE
                for name, samples in records:
                    canonical.setdefault(name, []).extend((base + seconds, value) for seconds, value in samples)
                block = serialize(records, base)
                sql(args.endpoint, f'INSERT INTO {args.table}(metric_name,tags,samples) '
                                   f'FORMAT JSONEachRow\n' + block)
                input_blocks.append(block)
            if phase == 'after_merge':
                for kind in ('tags', 'samples', 'recentsamples', 'timeranges'):
                    inner = f'default.' + chr(96) + f'.inner_id.{kind}.{table_uuid}' + chr(96)
                    sql(args.endpoint, f'OPTIMIZE TABLE {inner} FINAL '
                                       f'SETTINGS optimize_throw_if_noop=0')
            report['physical'][phase] = part_statistics(args.endpoint, table_uuid)
            for case in CASES:
                name, _, _, should_recent = case
                off = native(args.endpoint, args.table, base, case, 0)
                on = native(args.endpoint, args.table, base, case, 1)
                expected = independent_oracle(base, canonical, case)
                for preference, actual in ((0, off), (1, on)):
                    if set(actual['values']) != set(expected):
                        raise AssertionError(
                            f'Key mismatch {phase}/{name}/prefer={preference}: '
                            f'{set(actual["values"]) ^ set(expected)}')
                    if any(not math.isclose(expected[k], actual['values'][k],
                                            rel_tol=1e-10, abs_tol=1e-8) for k in expected):
                        raise AssertionError(f'Value mismatch {phase}/{name}/prefer={preference}')
                if off['recent'] or not off['main']:
                    raise AssertionError(f'Preference OFF selected recent table {phase}/{name}')
                if on['recent'] != should_recent or on['main'] == should_recent:
                    raise AssertionError(f'Wrong physical route {phase}/{name}: {on}')
                record = {'phase': phase, 'case': name, 'actual_points': len(expected),
                          'read_target_prefer_off': 'main',
                          'read_target_prefer_on': 'recent' if on['recent'] else 'main',
                          'plan_sha256_off': off['explain_sha256'],
                          'plan_sha256_on': on['explain_sha256'],
                          'expected_values': {str(k): v for k, v in expected.items()}}
                report['cases'][phase + '/' + name] = record
                print('R04_TTL_CASE', json.dumps(
                    {'phase': phase, 'case': name, 'points': len(expected),
                     'selected': record['read_target_prefer_on'], 'passed': True},
                    separators=(',', ':')), flush=True)
        report['passed'] = len(report['cases']) == len(CASES) * 3
    except Exception as exc:
        report['errors'].append(str(exc)[:2600])
        report['passed'] = False
    finally:
        report['inputs'] = [{'bytes': len(block),
                             'sha256': hashlib.sha256(block.encode()).hexdigest()}
                            for block in input_blocks]
        target = pathlib.Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print('R04_TTL_DONE', json.dumps({'passed': report['passed'],
              'cases': len(report['cases']), 'errors': report['errors']},
              separators=(',', ':')), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
