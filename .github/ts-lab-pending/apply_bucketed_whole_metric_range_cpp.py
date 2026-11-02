#!/usr/bin/env python3
"""Research: reuse existing safe whole-metric ID range in large bucketed samples.

The existing tryMakeWholeMetricIDRangeConditions performs a real tags/time
counterexample probe, validates physical ID type, keeps exact id IN <set>
for row-level correctness and uses indexHint only for pruning. The default
source enables this on non-bucketed TimeSeries data. This experiment allows
it on a large bucketed samples table, using a rough metadata rows threshold.
A probe failure or a filtered metric still uses the unchanged selector.

Lab only: 65536 rows is NOT a validated cost-estimation threshold.
No outer permission, tags storage, label index or wire format changes.
"""
from pathlib import Path

p = Path("src/Storages/StorageTimeSeriesSelector.cpp")
s = p.read_text()
old = """    ASTPtr whole_metric_id_range_condition;
    if (!time_series_settings->hasBucketedSamples())
    {
        whole_metric_id_range_condition = tryMakeWholeMetricIDRangeConditions("""
new = """    ASTPtr whole_metric_id_range_condition;

    /// Lab experiment: a bucketed TimeSeries table with many single-sample
    /// series has nearly one SAMPLES row per series. Probe whether a verified
    /// whole-metric ID range can prune its primary key more cheaply than a
    /// large set of all matching IDs. The probe and exact row filter are the
    /// same as the existing non-bucketed path. Small bucketed tables retain
    /// the original plan. The row threshold is only an A/B test guard.
    const auto samples_rows = time_series_storage->getTargetTable(samples_table_kind, context)->totalRows(context);
    const bool try_bucketed_metric_id_range = time_series_settings->hasBucketedSamples()
        && samples_rows && *samples_rows >= 65536;
    if (!time_series_settings->hasBucketedSamples() || try_bucketed_metric_id_range)
    {
        whole_metric_id_range_condition = tryMakeWholeMetricIDRangeConditions("""
if s.count(old) != 1:
    raise RuntimeError(f"Expected one native guard, found {s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s)
print("LAB_BUCKETED_WHOLE_METRIC_RANGE_CPP_APPLIED", p)
