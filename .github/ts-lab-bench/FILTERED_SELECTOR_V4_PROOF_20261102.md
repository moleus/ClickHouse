# Filtered TimeSeries selector fastpath: source proof and A/B/A plan

2026-11-02, written before SQL-only experiment. All changes lab branch only.

## Observed production relevance

The v3 C++ candidate only matches `RangeSelector` whose inner instant selector has precisely one matcher `__name__ = metric`. Source: `.github/ts-lab-pending/apply_selector_native_one_point_v3_cpp.py`:
`if (matchers.size() != 1 || matchers[0].label_name != "__name__" || matchers[0].matcher_type != EQ ... ) return {};`

The actual Grafana Jenkins panel uses variable job/branch filters, frequently a many-branch regex. The tested 282036-series broad Jenkins query has no filters, so the 22% latency gain does not establish performance for the actual filtered dashboard.

## Permitted extension hypothesis

The normal converter's `src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp` serializes the entire inner selector via `range_selector_node->getInstantSelector()->toString(*context.promql_tree)` and passes that text to `timeSeriesSelector`. The query execution path handles equality, regex, negative matchers and empty-value behavior. The v3 lowering can reuse that exact serialization and the normal `timeSeriesSelector` function without direct access to inner TAGS/SAMPLES tables.

**Security requirement:** Do not interpolate raw selector text inside single-quoted SQL. Build a quoted string with ClickHouse `quoteString` or ASTLiteral. For the SQL-only Python prototype, escape `\\` and `'` in the ClickHouse string literal. Test a regex containing a literal single quote to prove the handling.

**Eligibility rule:** For now still require an explicit `__name__=<nonempty metric>` exact matcher, with any number of additional standard matchers; reject selectors without an exact metric, offsets, `@`, subquery, non-v11 and non-1d windows. The outer query uses `avg by(stage)(avg_over_time(selector[1d]))` only. Future product-grade implementation should use AST construction over re-parsing raw SQL.

## Experiment contract

1. One pinned v11 Float64 mixed TimeSeries table with 67200 selected event series and unrelated Jenkins series, unchanged baseline binary, exact A/B/A SQL.
2. Test exact branch, branch regex, negative matcher, exact stage, stage regex, combined branch+stage+project, regex containing apostrophe, build-prefix regex, absent branch (empty result), and whole metric. Output grid E=3 and E=40.
3. Use full-range `prometheusQueryRange` baseline vs `timeSeriesSelector`-based translated native one-point SQL (without redundant SQL timestamp dedup) with identical selector. Compare exact output labels, timestamps and Float64 values for each case; empty outputs must match. Two warmups plus 5 measured timings per phase, same data, and query-log rows/bytes/memory.
4. If SQL equivalent for all tested selectors, introduce C++ v4 patch using normal inner selector `toString` and safe SQL quoting, then rerun original/v4/original numerical and filtered Jenkins benchmarks. Do not infer production eligibility from this success alone.
5. New C++ must provide a real opt-in/allowlist and a small-series fallback before production. V3's schema-based internal lab flag currently auto-enables the path for eligible queries; it is NOT a general end-user opt-in.
6. No master writes, no production changes, no cancellations or upstream CI.
