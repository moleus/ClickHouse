#!/usr/bin/env python3
"""Deterministic TimeSeries PromQL tests. No external services required."""
import json
import math
import subprocess
import sys

clickhouse = sys.argv[1]
setup = """
SET allow_experimental_time_series_table = 1;
SET session_timezone = 'UTC';
CREATE TABLE t ENGINE = TimeSeries;
INSERT INTO t (metric_name, tags, samples) VALUES
 ('foo', map('job','a'), [(toDateTime64(100,3), 1.), (toDateTime64(160,3), 3.), (toDateTime64(220,3), 7.), (toDateTime64(280,3), 9.)]),
 ('foo', map('job','b'), [(toDateTime64(100,3), 2.), (toDateTime64(160,3), 4.), (toDateTime64(220,3), 6.), (toDateTime64(280,3), 8.)]),
 ('bar', map('job','a'), [(toDateTime64(100,3), 2.), (toDateTime64(160,3), 4.), (toDateTime64(220,3), 6.), (toDateTime64(280,3), 8.)]);
"""

def evaluate(expr):
    query = f"SELECT * FROM prometheusQueryRange(t, '{expr}', 160, 280, 60) ORDER BY ALL FORMAT JSONEachRow;"
    p = subprocess.run([clickhouse, "local", "--multiquery"], input=setup+query+"\n",
                       text=True, capture_output=True, timeout=90)
    if p.returncode:
        return {"error": p.stderr.strip(), "exit_code": p.returncode}
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]

def samples(rows):
    assert isinstance(rows, list) and len(rows) == 1, rows
    assert rows[0]["tags"] == [], rows
    return [float(v) for _, v in rows[0]["samples"]]

values = samples(evaluate("sum(rate(foo[2m]))"))
expected = [7.0/120.0, 11.0/120.0, 8.0/120.0]
assert len(values) == len(expected)
assert all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12) for x,y in zip(values,expected)), (values,expected)
print("PASS sum(rate(foo[2m]))")

braced = samples(evaluate('sum(rate({__name__="foo"}[2m]))'))
assert all(math.isclose(x,y,rel_tol=1e-12,abs_tol=1e-12) for x,y in zip(braced,values)), (braced,values)
print("PASS exact __name__ matcher")

one = samples(evaluate('sum(rate(foo{job="a"}[2m]))'))
assert len(one) == 3 and all(x >= 0 for x in one), one
print("PASS metric with label filter")

grouped = evaluate("sum by (job) (rate(foo[2m]))")
assert isinstance(grouped,list) and len(grouped)==2,grouped
assert {tuple(tuple(p) for p in r["tags"]) for r in grouped} == {(('job','a'),),(('job','b'),)},grouped
print("PASS grouped sum on normal path")

regex = evaluate('sum(rate({__name__=~"foo|bar"}[2m]))')
assert isinstance(regex,dict) and regex.get("exit_code") and "Multiple series have the same tags" in regex.get("error",""),regex
print("PASS regex duplicate-series rejection")
print("RESULT: 5 PromQL regression cases passed")
