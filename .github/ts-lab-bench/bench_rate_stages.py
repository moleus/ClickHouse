#!/usr/bin/env python3
"""Measure TimeSeries selector and rate() stages using HTTP. Read-only workload."""
import argparse
import json
import pathlib
import random
import time
import urllib.parse
import urllib.request


def percentile(numbers, p):
    arr = sorted(numbers)
    idx = (len(arr) - 1) * p / 100
    lo = int(idx)
    return round(arr[lo] + (arr[min(lo + 1, len(arr) - 1)] - arr[lo]) * (idx - lo), 3)


def request(host, sql):
    params = urllib.parse.urlencode({
        'query': sql,
        'allow_experimental_time_series_table': '1',
        'wait_end_of_query': '1',
    })
    req = urllib.request.Request(host.rstrip('/') + '/?' + params, data=b'')
    started = time.perf_counter_ns()
    with urllib.request.urlopen(req, timeout=180) as response:
        result = response.read()
    return round((time.perf_counter_ns() - started) / 1e6, 3), result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--ch', default='http://127.0.0.1:18191')
    parser.add_argument('--table', default='bench_ts_50k')
    parser.add_argument('--database', default='default')
    parser.add_argument('--metric', default='bench_metric')
    parser.add_argument('--window', type=int, default=300)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=8)
    parser.add_argument('--output', required=True)
    opts = parser.parse_args()
    if not (opts.table.replace('_', '').isalnum() and opts.database.replace('_', '').isalnum()):
        parser.error('Only simple SQL database/table names are accepted')
    manifest = json.loads(pathlib.Path(opts.manifest).read_text())
    start = manifest['eval_start_epoch'] * 1000
    end = manifest['eval_end_epoch'] * 1000
    step = manifest['eval_step_seconds']
    source = 'timeSeriesSelector(' + ','.join([
        "'" + opts.database + "'", "'" + opts.table + "'", "'" + opts.metric.replace("'", "''") + "'",
        f'fromUnixTimestamp64Milli({start - opts.window * 1000 + 1})',
        f'fromUnixTimestamp64Milli({end})', "'samples'",
    ]) + ')'
    rate = ('timeSeriesRateToGrid('
            f'fromUnixTimestamp64Milli({start}),fromUnixTimestamp64Milli({end}),'
            f'toIntervalSecond({step}),toIntervalSecond({opts.window}))(samples)')
    sql = {
        'selector_count': f'SELECT count() FROM {source}',
        'selector_sample_lengths': f'SELECT sum(length(samples)) FROM {source}',
        'selector_uniq_ids': f'SELECT uniqExact(id) FROM {source}',
        'rate_group_tags': f'SELECT sumForEach(values) AS values FROM (SELECT {rate} AS values FROM {source} GROUP BY timeSeriesIdToGroup(id)) FORMAT JSONEachRow',
        'rate_group_id': f'SELECT sumForEach(values) AS values FROM (SELECT {rate} AS values FROM {source} GROUP BY id) FORMAT JSONEachRow',
    }
    _, values1 = request(opts.ch, sql['rate_group_tags'])
    _, values2 = request(opts.ch, sql['rate_group_id'])
    x = json.loads(values1)['values']
    y = json.loads(values2)['values']
    if len(x) != len(y):
        raise RuntimeError('Different number of output steps')
    max_absolute_delta = max(abs(a - b) for a, b in zip(x, y))
    if max_absolute_delta > 1e-9 * max(1, *(abs(a) for a in x)):
        raise RuntimeError(f'Grouping by ID changes results: {max_absolute_delta}')
    for name, query in sql.items():
        for _ in range(opts.warmups):
            request(opts.ch, query)
    times = {k: [] for k in sql}
    randomizer = random.Random(20261101)
    for _ in range(opts.repeats):
        names = list(sql)
        randomizer.shuffle(names)
        for name in names:
            elapsed, _ = request(opts.ch, sql[name])
            times[name].append(elapsed)
    summary = {key: {
        'p50_ms': percentile(series, 50),
        'p95_ms': percentile(series, 95),
        'min_ms': min(series),
        'max_ms': max(series),
    } for key, series in times.items()}
    result = {
        'engine': request(opts.ch, 'SELECT version()')[1].decode().strip(),
        'manifest': manifest,
        'dataset': {'database': opts.database, 'table': opts.table, 'metric': opts.metric},
        'window_seconds': opts.window, 'warmups': opts.warmups, 'repeats': opts.repeats,
        'groups_equal': True, 'max_absolute_delta': max_absolute_delta,
        'queries': sql, 'measurements_ms': times, 'summary_ms': summary,
        'limitations': 'Sequential HTTP wall clock. Synthetic data. Shared runtime. Selector count has less work than rate aggregation.',
    }
    pathlib.Path(opts.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'summary_ms': summary, 'groups_equal': True, 'max_delta': max_absolute_delta}, indent=2))


if __name__ == '__main__':
    main()
