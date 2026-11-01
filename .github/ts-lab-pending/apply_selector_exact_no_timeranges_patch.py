#!/usr/bin/env python3
"""EXPERIMENTAL ONLY: selective EQ label selector bypasses time-ranges JOIN.

Apply to ClickHouse source SHA 52529f0c711566d696917678bf15e7068772fdde.
Never deploy by default; benchmark and validate history gaps / stale markers.
"""
from pathlib import Path

path = Path("src/Storages/StorageTimeSeriesSelector.cpp")
data = path.read_text()
start = data.index("void StorageTimeSeriesSelector::readImpl(")
prefix, body = data[:start], data[start:]
old = """    const StorageID time_ranges_table_id = time_series_storage->hasTarget(ViewTarget::TimeRanges)
        ? time_series_storage->getTargetTableID(ViewTarget::TimeRanges, context)
        : StorageID::createEmpty();
    const TableTimeRange time_range_to_filter_ids = getTimeRangeToFilterIDs(table_time_range, time_ranges_table_id, *time_series_settings);"""
new = """    // Experimental selective-label fast path: skip the time-ranges table only
    // while resolving IDs for non-empty exact tag matchers in bucketed v11.
    // The sample-table query still checks the time range and drops empty
    // samples, and makeSelectIDsQuery() retains its original behavior.
    // For sparse label matchers this avoids a full_sorting_merge LEFT JOIN
    // over time ranges. For broad labels it can increase the samples scan.
    const bool skip_time_ranges_for_exact_tag = time_series_settings->hasBucketedSamples()
        && std::any_of(matchers.begin(), matchers.end(), [](const auto & matcher)
        {
            return matcher.matcher_type == PrometheusQueryTree::MatcherType::EQ
                && matcher.label_name != TimeSeriesTagNames::MetricName
                && !matcher.label_value.empty();
        });
    const StorageID time_ranges_table_id = (!skip_time_ranges_for_exact_tag && time_series_storage->hasTarget(ViewTarget::TimeRanges))
        ? time_series_storage->getTargetTableID(ViewTarget::TimeRanges, context)
        : StorageID::createEmpty();
    if (skip_time_ranges_for_exact_tag)
        LOG_DEBUG(log, "Experimental exact-label selector: bypassing time-ranges JOIN; samples still filtered by time");
    const TableTimeRange time_range_to_filter_ids = getTimeRangeToFilterIDs(table_time_range, time_ranges_table_id, *time_series_settings);"""
if body.count(old) != 1:
    raise RuntimeError("Unexpected pinned source: missing or extra target block")
body = body.replace(old, new, 1)
path.write_text(prefix + body)
print("Applied exact-label selective TimeSeries JOIN bypass to readImpl only")
