#!/usr/bin/env python3
"""Additional PromQL regression tests for ClickHouse TimeSeries sum(rate())."""
import json
import math
import subprocess
import sys

CH = sys.argv[1] if len(sys.argv) > 1 else 'clickhouse'
SETUP = """
SET allow_experimental_time_series_table = 1;
SET session_timezone = 'UTC';
CREATE TABLE t ENGINE = TimeSeries;
INSERT INTO t (metric_name, tags, samples) VALUES
 ('foo', map('job','a'), [(toDateTime64(100,3), 1.), (toDateTime64(160,3), 3.), (toDateTime64(220,3), 7.), (toDateTime64(280,3), 9.)]),
 ('foo', map('job','b'), [(toDateTime64(100,3), 2.), (toDateTime64(160,3), 4.), (toDateTime64(220,3), 6.), (toDateTime64(280,3), 8.)]),
 ('bar', map('job','a'), [(toDateTime64(100,3), 2.), (toDateTime64(160,3), 4.), (toDateTime64(220,3), 6.), (toDateTime64(280,3), 8.)]);
"""


def evaluate(expr):
    sql = f"SELECT * FROM prometheusQueryRange(t, '{expr}', 160, 280, 60) ORDER BY ALL FORMAT JSONEachRow;"
    p = subprocess.run([CH, 'local', '--tmp', '--multiquery'], input=SETUP + sql + '\n',
                       text=True, capture_output=True, timeout=90)
    if p.returncode:
        return {'code': p.returncode, 'stderr': p.stderr}
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def one_series(expr):
    rows = evaluate(expr)
    assert isinstance(rows, list) and len(rows) == 1 and rows[0]['tags'] == [], (expr, rows)
    return rows[0]['samples']


def same(left, right):
    assert len(left) == len(right), (left, right)
    for (time1, value1), (time2, value2) in zip(left, right):
        assert time1 == time2, (time1, time2)
        assert math.isclose(value1, value2, rel_tol=1e-12, abs_tol=1e-12), (value1, value2)


baseline = one_series('sum(rate(foo[2m]))')
assert len(baseline) == 3
for name, expression in {
    'exact metric with label regex': 'sum(rate({__name__="foo",job=~"a|b"}[2m]))',
    'one-metric name regex uses normal path': 'sum(rate({__name__=~"foo"}[2m]))',
    'sum by empty uses normal path': 'sum by () (rate(foo[2m]))',
    'sum without job uses normal path': 'sum without (job) (rate(foo[2m]))',
}.items():
    same(baseline, one_series(expression))
    print('PASS', name, flush=True)

missing = evaluate('sum(rate(does_not_exist[2m]))')
assert missing == [], missing
print('PASS absent metric', flush=True)

offset = one_series('sum(rate(foo[2m] offset 60s))')
same(offset, [[baseline[1][0], baseline[0][1]], [baseline[2][0], baseline[1][1]]])
print('PASS offset uses normal path', flush=True)

at_value = one_series('sum(rate(foo[2m] @ 220))')
assert len(at_value) == 3
assert all(math.isclose(value, baseline[1][1], rel_tol=1e-12) for _, value in at_value)
print('PASS fixed @ modifier', flush=True)

invalid = evaluate('sum(rate({__name__!="bar"}[2m]))')
assert isinstance(invalid, dict) and 'non-empty matcher' in invalid['stderr'], invalid
print('PASS invalid selector is rejected', flush=True)

print('RESULT: 8 additional PromQL cases passed', flush=True)
