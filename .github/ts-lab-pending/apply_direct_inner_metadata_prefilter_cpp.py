#!/usr/bin/env python3
"""Avoid TimeSeries inner target metadata work except potential eligible grouped AVG.

Apply AFTER apply_direct_inner_grouped_cpp.py, optionally after
apply_direct_inner_grouped_array_merge_cpp.py. Most ordinary PromQL and
all regular rate() calls will never use this native direct fastpath;
therefore skip getInMemoryMetadataPtr() and getTargetTableID entirely
for them, keeping the original query setup and read path untouched.
"""
from pathlib import Path
p=Path("src/Storages/StoragePrometheusQuery.cpp")
s=p.read_text()
old=r'''    if (time_series_storage->getVersion() == 11
        && time_series_storage->isInnerTable(ViewTarget::Samples)'''
new=r'''    /// Test the query tree BEFORE even looking up inner target metadata.
    /// This avoids work on every ordinary rate(), instant selector and
    /// selector-filtered query that is not eligible for this optimization.
    bool could_use_direct_grouped_avg = false;
    if (const auto * node = config.promql_tree->getRoot(); node
        && node->node_type == PrometheusQueryTree::NodeType::AggregationOperator)
    {
        const auto * agg = static_cast<const PrometheusQueryTree::AggregationOperator *>(node);
        if (agg->operator_name == "avg" && agg->by && !agg->without
            && agg->labels.size() == 1 && agg->labels.front() == "stage"
            && agg->getArguments().size() == 1)
        {
            const auto * child = agg->getArguments().front();
            if (child->node_type == PrometheusQueryTree::NodeType::Function)
            {
                const auto * f = static_cast<const PrometheusQueryTree::Function *>(child);
                if (f->function_name == "avg_over_time" && f->getArguments().size() == 1
                    && f->getArguments().front()->node_type == PrometheusQueryTree::NodeType::RangeSelector)
                {
                    const auto * range = static_cast<const PrometheusQueryTree::RangeSelector *>(f->getArguments().front());
                    const auto & ms = range->getInstantSelector()->matchers;
                    could_use_direct_grouped_avg = ms.size() == 1
                        && ms[0].label_name == "__name__"
                        && ms[0].matcher_type == PrometheusQueryTree::MatcherType::EQ;
                }
            }
        }
    }

    if (could_use_direct_grouped_avg
        && time_series_storage->getVersion() == 11
        && time_series_storage->isInnerTable(ViewTarget::Samples)'''
if s.count(old)!=1:raise RuntimeError(f"Expected exactly one direct path guard block; got {s.count(old)}")
p.write_text(s.replace(old,new))
print("LAB_DIRECT_INNER_METADATA_PREFILTER_APPLIED",p)
