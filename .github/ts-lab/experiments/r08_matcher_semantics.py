#!/usr/bin/env python3
"""R08 native PromQL absent-label and anchored-regex semantic oracle.

Uses a pinned small synthetic fixture. This is a correctness gate, not
a speed comparison or a staleness/counter correctness test.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import urllib.error
import urllib.request

BASE = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.timezone.utc)
EPOCH = int(BASE.timestamp()) + 180
METRIC = 'tslab_r08_matcher_labels'
ROWS = [
    ('absent', None, None),
    ('explicit_empty', '', ''),
    ('a', 'a', 'prod'),
    ('ba', 'ba', 'prod'),
    ('abc', 'abc', 'stage'),
    ('a_dash', 'a-1', 'test'),
    ('b', 'b', 'prod'),
    ('x', 'x', None),
    ('zz', 'zz', ''),
    ('a_pipe_b', 'a|b', 'stage'),
    ('a_dot', 'a.b', 'prod'),
    ('upper_A', 'A', 'prod'),
]
MATCHERS = [
    ('eq_empty', [('probe', '=', '')]),
    ('eq_a', [('probe', '=', 'a')]),
    ('neq_a', [('probe', '!=', 'a')]),
    ('neq_empty', [('probe', '!=', '')]),
    ('regex_star', [('probe', '=~', '.*')]),
    ('regex_plus', [('probe', '=~', '.+')]),
    ('regex_a_anchored', [('probe', '=~', 'a')]),
    ('regex_a_prefix', [('probe', '=~', 'a.*')]),
    ('regex_a_anywhere', [('probe', '=~', '.*a.*')]),
    ('nregex_a', [('probe', '!~', 'a')]),
    ('nregex_star', [('probe', '!~', '.*')]),
    ('nregex_plus', [('probe', '!~', '.+')]),
    ('literal_metachar', [('probe', '=~', r'a\.b')]),
    ('literal_pipe', [('probe', '=', 'a|b')]),
    ('regex_alternation', [('probe', '=~', 'a|b')]),
    ('prod_or_stage', [('env', '=~', 'prod|stage')]),
    ('not_prod', [('env', '!=', 'prod')]),
    ('empty_env', [('env', '=', '')]),
    ('mixed_filter', [('probe', '!=', ''), ('env', '=~', 'prod|stage')]),
    ('mixed_negative', [('probe', '!~', 'a.*'), ('env', '!=', '')]),
    ('no_match', [('probe', '=', 'none')]),
]


def timestamp(seconds):
    return dt.datetime.fromtimestamp(int(BASE.timestamp()) + seconds, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000')


def input_bytes():
    records = []
    for i, (name, probe, env) in enumerate(ROWS):
        tags = {'case_id': name}
        if probe is not None:
            tags['probe'] = probe
        if env is not None:
            tags['env'] = env
        records.append({'metric_name': METRIC, 'tags': tags,
                        'samples': [[timestamp(0), float(i + 1)], [timestamp(120), float(i + 101)]]})
    return ''.join(json.dumps(x, sort_keys=True, separators=(',', ':')) + '\n' for x in records)


def sql_quote(s):
    return "'" + s.replace('\\', '\\\\').replace("'", "\\'") + "'"


def sql(endpoint, statement):
    try:
        with urllib.request.urlopen(urllib.request.Request(endpoint, data=statement.encode(), method='POST'), timeout=40) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'ClickHouse HTTP {e.code}: {e.read().decode(errors="replace")[:1300]}') from e


def match(op, pattern, label):
    if op == '=':
        return label == pattern
    if op == '!=':
        return label != pattern
    if op == '=~':
        return re.fullmatch(pattern, label) is not None
    if op == '!~':
        return re.fullmatch(pattern, label) is None
    raise AssertionError(op)


def expected(matchers):
    result = {}
    for i, (name, probe, env) in enumerate(ROWS):
        labels = {'case_id': name}
        if probe is not None:
            labels['probe'] = probe
        if env is not None:
            labels['env'] = env
        if all(match(op, pattern, labels.get(key, '')) for key, op, pattern in matchers):
            result[(name, EPOCH)] = float(i + 101)
    return result


def expression(matchers):
    inside = ','.join(k + op + json.dumps(v, ensure_ascii=False) for k, op, v in matchers)
    return f'sum by(case_id)(last_over_time({METRIC}{{{inside}}}[5m]))'


def normalize(response):
    values = {}
    for line in response.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels = dict(row.get('tags', []))
        if set(labels) != {'case_id'}:
            raise AssertionError('Unexpected labels: ' + repr(labels))
        if len(row.get('samples', [])) != 1:
            raise AssertionError('Unexpected sample count: ' + repr(row))
        sample = row['samples'][0]
        when = int(dt.datetime.fromisoformat(sample[0].replace(' ', 'T')).replace(tzinfo=dt.timezone.utc).timestamp())
        key = (labels['case_id'], when)
        if key in values:
            raise AssertionError('Duplicate output: ' + repr(key))
        values[key] = float(sample[1])
    return values


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--endpoint', default='http://127.0.0.1:18191/')
    p.add_argument('--table', default='tslab_r08_matchers')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    report = {'schema_version': 1, 'question_id': 'R08-matcher-absence-regex-v1',
              'source_sha': '52529f0c711566d696917678bf15e7068772fdde',
              'scope': 'full native PromQL independent matcher oracle; not a speed or counter/staleness test',
              'epoch': EPOCH, 'input_sha256': hashlib.sha256(input_bytes().encode()).hexdigest(),
              'cases': {}, 'errors': [], 'input_count': len(ROWS)}
    outfile = pathlib.Path(a.output)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        sql(a.endpoint, f'CREATE TABLE {a.table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0')
        sql(a.endpoint, 'SYSTEM STOP MERGES')
        records = input_bytes().splitlines()
        for batch in (records[:6], records[6:]):
            sql(a.endpoint, f'INSERT INTO {a.table}(metric_name,tags,samples) FORMAT JSONEachRow\n' + '\n'.join(batch) + '\n')
        stored = int(sql(a.endpoint, f'SELECT count() FROM {a.table} FORMAT TSV').strip())
        if stored != len(ROWS):
            raise AssertionError(f'Stored {stored} != {len(ROWS)}')
        table_uuid = sql(a.endpoint, f"SELECT toString(uuid) FROM system.tables WHERE name={sql_quote(a.table)} AND database='default' FORMAT TSV").strip()
        if len(table_uuid) != 36:
            raise AssertionError('Missing source TimeSeries UUID')
        def parts():
            result = {}
            for kind in ('tags', 'samples', 'timeranges'):
                physical = f'.inner_id.{kind}.{table_uuid}'
                cmd = (f"SELECT count() AS parts,sum(rows) AS rows FROM system.parts "
                       f"WHERE database='default' AND active AND table={sql_quote(physical)} FORMAT JSONEachRow")
                result[kind] = json.loads(sql(a.endpoint, cmd).strip())
            return result
        report['parts_before'] = parts()
        if any(item['parts'] != 2 for item in report['parts_before'].values()):
            raise AssertionError('Expected 2 physical parts before merge: ' + repr(report['parts_before']))
        for phase in ('before_merge', 'after_merge'):
            if phase == 'after_merge':
                sql(a.endpoint, 'SYSTEM START MERGES')
                for kind in ('tags', 'samples', 'timeranges'):
                    physical = f'default.`.inner_id.{kind}.{table_uuid}`'
                    sql(a.endpoint, f'OPTIMIZE TABLE {physical} FINAL SETTINGS optimize_throw_if_noop=0')
                report['parts_after'] = parts()
                if any(item['parts'] != 1 for item in report['parts_after'].values()):
                    raise AssertionError('Physical merge did not reduce parts to one: ' + repr(report['parts_after']))
            for name, matchers in MATCHERS:
                expr = expression(matchers)
                query = (f'SELECT * FROM prometheusQueryRange({a.table},{sql_quote(expr)},'
                         f'{EPOCH},{EPOCH},60) SETTINGS max_threads=2 FORMAT JSONEachRow')
                want = expected(matchers)
                got = normalize(sql(a.endpoint, query))
                missing = set(want) - set(got)
                extra = set(got) - set(want)
                wrong = {str(k): (want[k], got[k]) for k in want.keys() & got.keys() if want[k] != got[k]}
                record = {'phase': phase, 'expression': expr, 'expected_count': len(want),
                          'actual_count': len(got), 'expected_ids': sorted(k[0] for k in want),
                          'observed_ids': sorted(k[0] for k in got),
                          'missing': sorted(str(x) for x in missing), 'extra': sorted(str(x) for x in extra),
                          'wrong_values': wrong, 'passed': not (missing or extra or wrong)}
                report['cases'][f'{phase}/{name}'] = record
                print('R08_MATCHER_CASE', json.dumps({'phase': phase, 'case': name,
                     'expected': len(want), 'got': len(got), 'passed': record['passed']},
                     separators=(',', ':')), flush=True)
                if not record['passed']:
                    report['errors'].append(f'Mismatch {phase}/{name}: {record}')
        report['passed'] = not report['errors'] and len(report['cases']) == 2 * len(MATCHERS)
    except Exception as e:
        report['errors'].append(str(e)[:2600])
        report['passed'] = False
    finally:
        try:
            sql(a.endpoint, 'SYSTEM START MERGES')
        except Exception:
            pass
        outfile.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print('R08_MATCHER_DONE', json.dumps({'passed': report['passed'],
              'cases': len(report['cases']), 'errors': report['errors'][:4],
              'input_sha256': report['input_sha256']}, separators=(',', ':')), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
