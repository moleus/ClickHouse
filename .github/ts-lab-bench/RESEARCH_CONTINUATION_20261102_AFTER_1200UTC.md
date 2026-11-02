# Research continuation — 2026-11-02 after 12:00 UTC

All changes confined to fork `moleus/ClickHouse`, branch `lab/timeseries-fastpath-ci`; upstream source `52529f0c711566d696917678bf15e7068772fdde` remains pinned. No production writes, no cancelled CI jobs, no fork-master changes.

## 1. New production-relevance finding: v3 does NOT optimize filtered Grafana queries

`.github/ts-lab-pending/apply_selector_native_one_point_v3_cpp.py` requires exactly one `__name__` equality matcher. Thus all queries with branch/job/stage matchers use the original converter. The successful 22%-faster broad Jenkins query did not prove that actual Grafana variable filters benefit.

Proof-first plan `FILTERED_SELECTOR_V4_PROOF_20261102.md` commit b6ee3822. SQL script `bench_filtered_selector_v4_sql.py` commits fc089833/dcccf836. **41444592786 SUCCESS**: unchanged pinned baseline binary, same fixture 67200 series + unrelated Jenkins metrics. Native PromQL vs translated native one-point SQL with full selector passed **20/20 exact output comparisons** (labels/time/value) across 10 selectors and grid E=3/40. Cases included exact/regex/negative branch, exact/regex stage, 3 simultaneous matchers, build regex, absent branch, and regex containing an apostrophe escaped at the SQL layer. Runtime p50 examples (native recheck vs candidate): E3 whole 336.840/277.535ms; branch eq 411.475/321.270; branch regex 423.539/331.069; E40 whole 380.233/277.273; stage regex 291.967/246.790; three matchers 109.161/96.944. Narrow build regex E40 regressed 70.432→72.984ms; empty E40 36.924→37.083ms. Filter selectivity matters. This is **SQL-only**, not yet compiled C++ v4.

Lab C++ v4 patch `apply_selector_native_one_point_v4_filtered_cpp.py` commits 92828a3e, 7988163e: use standard inner selector `toString`, `quoteString` to escape SQL literals and preserve user regex, still require an exact `__name__` matcher and strict existing AST checks. Adds **explicit session setting `promql_enable_native_one_point_lab` default FALSE**, avoids v3's automatic enabled-by-Float64 behavior. Verified patch apply / Python syntax / git diff with **41446061019 SUCCESS** after a shell-variable escaping error in early 41445905244; preflight workflow `ts-lab-v4-filtered-preflight-20261102.yml`. Build of v4 plus streaming KBN **41446272488** launched; check status, compile errors, artifact and default-off/explicit-on smoke before correctness tests.

## 2. Grouped average accuracy: native TimeSeries KBN reusable at stage level

SQL proof `GROUPED_AVG_NATIVE_KBN_PROOF_20261102.md` commit 316c59d9. The native `timeSeriesAvgToGrid` can average *cross-ID* values if assigned synthetic distinct negative millisecond timestamps in its `(-86400s,0]` window. This retains `AggregateFunctionTimeseriesCompensatedSum`'s Kahan-Babuska-Neumaier arithmetic but must materialize all per-stage values in `groupArray`; therefore it is a **numerical oracle/prototype**, not production SQL.

Run **41444146788 FAILED** due derived SQL missing GROUP BY; repaired and **41444399417 SUCCESS**. All **7 extreme cross-ID cancellation stage groups returned mathematical oracle `math.fsum(values)/len(values)` in all 15 repeats**, with zero observed error, including:
- `[1e18,1,-1e18]` exact 1/3; original ordinary avg median 0; native KBN stage exactly 1/3.
- `[1e18] + [1]*127 + [-1e18]` exact 0.984496124; original median 0.550387597; KBN exactly 0.984496124.
- `[1e18]*50+[1]*101+[-1e18]*50` exact 0.502487562; original median 0.004975124; KBN exactly 0.502487562.
- `[1e18,1,-1e18,1]*60` exact 0.5; original median 0; KBN exactly 0.5.
- explicit descending-absolute sorting of values did not improve KBN results.
p50 E2 tiny test: original recheck 14.9346ms, KBN stage SQL 16.6460ms. SQL variant has extra memory/array work, not a production cost comparison.

## 3. Streaming C++ aggregate `avgKBNLab(Float64)`

Proof `STAGE_AVG_KBN_STREAMING_CPP_PROOF_20261102.md` commit c5c26918; patch script `apply_avg_kbn_streaming_cpp.py` commit 7f451d60 + registration declaration fix fec4b48f; retains native TimeSeries Summary add/merge arithmetic and count in 24-byte state, uses IAggregateFunctionDataHelper and standard serialize/deserialize. First build **41444971609 failed solely on -Wmissing-prototypes** for registration declaration; saved 1.34GB compiler cache (not cancelled). Fixed build **41445568892 SUCCESS**, pinned LLVM22 O1 plus v3 and avgKBNLab, compiled binary artifact `tslab-native-one-point-kbn-lab-binary-8ef8492b8a30bf0348bb3f820313f4232e4725bb` artifact ID 13142392014. KBN smoke `SELECT avgKBNLab(...) FROM [1e18,1,-1e18]` returned 0.3333333333333333. All 13 PromQL smoke tests passed, partial compiler cache retained. **41446233352** streaming state/merge and oracle test started, check results. Benchmark script `bench_streaming_avg_kbn_cpp.py` commit cef9beb7 tests 7 groups, max_threads 1/4/8, forced `avgKBNLabState`/`avgKBNLabMerge` partial states, 15 repeats, NaN/Inf/NULL/overflow separately.

## 4. Open decisions

- **General numerical semantics**: Cross-ID original ClickHouse `avgForEach` unstable/inaccurate on extreme cancellation (41439318904). Prometheus `promql/engine.go` uses `kahansum.Inc` and overflow fallback; native KBN grouped stage improves finite cancellation accuracy in SQL prototype but is not yet proven compatible with all Prometheus overflow/NaN/Inf cases or distributed merges.
- **Cost model**: v3 C++ boosts broad Jenkins ~22% and 67200-series long grids ~38.8% relative initial baseline (41439396625 and 41439588519), but small N=210 E3 regresses 9.5%. Synchronous COUNT gate is too expensive and not time-range exact (41437764684). v4 has explicit default-off setting, not an automatic cost policy. Filtered stage/build query often has small selected N and benefit is negligible.
- **Upcoming**: (a) check KBN merge test, (b) check v4+KBN C++ compile 41446272488 and opt-in smoke, (c) original/v4_on/v4_off/original A/B/A filtered selectors using `bench_filtered_selector_v4_cpp_aba.py`, (d) benchmark streaming compensated stage cost at 67200 series and E=3/40, (e) special values/time boundaries, ACL and O2/O3 before production.
