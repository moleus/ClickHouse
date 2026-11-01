#!/usr/bin/env python3
"""EXPERIMENTAL ONLY: restrict time-range bypass to lab identity-like label keys.

Apply to ClickHouse source SHA 52529f0c711566d696917678bf15e7068772fdde.
Never deploy by default; instance/host are NOT universally unique or safe\ncardinality guards. This is a test-only mitigation of the observed project label\nregression, not a general optimizer or semantic guarantee.
"""
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument(
    "--allow-unverified-instance-bypass", action="store_true",
    help="RESEARCH ONLY: bypass time-ranges for instance/host even when the label has high cardinality"
)
args = parser.parse_args()
if not args.allow_unverified_instance_bypass:
    print("SAFE DEFAULT: no selector patch applied. A/B/A run 41342350563 showed "
          "unverified instance/host equality can increase memory and latency. "
          "Use --allow-unverified-instance-bypass only for isolated regressions.")
    raise SystemExit(0)

path = Path("src/Storages/StorageTimeSeriesSelector.cpp")
data = path.read_text()
start = data.index("void StorageTimeSeriesSelector::readImpl(")
prefix, body = data[:start], data[start:]
old = """    const StorageID time_ranges_table_id = time_series_storage->hasTarget(ViewTarget::TimeRanges)
        ? time_series_storage->getTargetTableID(ViewTarget::TimeRanges, context)
        : StorageID::createEmpty();
    const TableTimeRange time_range_to_filter_ids = getTimeRangeToFilterIDs(table_time_range, time_ranges_table_id, *time_series_settings);"""
new = """    // Experimental high-selectivity lab guard: bypass time ranges only when
    // an exact nonempty instance/host identity-like matcher is present.
    // This deliberately excludes broad project/region/team equality labels.
    // IMPORTANT: instance and host need not be unique in production. Do NOT
    // enable this heuristic in the generic v11 engine without cardinality proof.
    // The sample-table query still checks the time range and drops empty
    // samples, and makeSelectIDsQuery() retains its original behavior.
    // For sparse label matchers this avoids a full_sorting_merge LEFT JOIN
    // over time ranges. This known-case whitelist is a temporary LAB gate,
    // not a generic solution to selectivity estimation.
    const bool skip_time_ranges_for_exact_tag = time_series_settings->hasBucketedSamples()
        && std::any_of(matchers.begin(), matchers.end(), [](const auto & matcher)
        {
            return matcher.matcher_type == PrometheusQueryTree::MatcherType::EQ
                && (matcher.label_name == "instance" || matcher.label_name == "host")
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
print("Applied experimental instance/host-only JOIN bypass in readImpl; broad project labels keep join")
