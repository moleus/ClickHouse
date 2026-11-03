#!/usr/bin/env python3
"""Source-pinned native metric-name-removal deferral. No storage or rollup change."""
import argparse
import difflib
import hashlib
from pathlib import Path

PATH = "src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp"
BLOB = "2e13dfbc73d7f9c2a0ce6631c132c88e6133894d"
HELPER = '''    /// A direct selector with a fixed metric name has unique groups even after
    /// removing that name. An immediate ordinary aggregation that discards the
    /// name can perform the label projection itself. Avoid a redundant GROUP BY
    /// and per-series tag copy, but retain the stock path without this proof.
    bool canDeferMetricNameRemoval(const Node * node)
    {
        using QueryTree = PrometheusQueryTree;
        if (!node || node->node_type != QueryTree::NodeType::Function || node->children.size() != 1)
            return false;
        if (!node->parent || node->parent->node_type != QueryTree::NodeType::AggregationOperator)
            return false;

        const auto & parent = typeid_cast<const QueryTree::AggregationOperator &>(*node->parent);
        if (!isOneArgumentAggregationOperator(parent.operator_name) || parent.without
            || parent.children.size() != 1 || parent.children.front() != node)
            return false;
        for (const auto & label : parent.labels)
            if (label == "__name__")
                return false;

        const auto * child = node->children.front();
        if (!child || child->node_type != QueryTree::NodeType::RangeSelector)
            return false;
        const auto & range = typeid_cast<const QueryTree::RangeSelector &>(*child);
        for (const auto & matcher : range.getInstantSelector()->matchers)
        {
            if (matcher.label_name == "__name__" && matcher.matcher_type == QueryTree::MatcherType::EQ
                && !matcher.label_value.empty())
                return true;
        }
        return false;
    }

'''
OLD = '''    else if (drop_metric_name.value_or(impl_info->drop_metric_name))
    {
        res = dropMetricName(std::move(res), context);
    }'''
NEW = '''    else if (drop_metric_name.value_or(impl_info->drop_metric_name))
    {
        /// Keep metric_name_dropped unchanged when deferring: the parent still
        /// receives the original groups and must perform its normal projection.
        if (!canDeferMetricNameRemoval(node))
            res = dropMetricName(std::move(res), context);
    }'''


def patch(raw):
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != BLOB:
        raise ValueError(f"Unexpected source blob {actual}; expected {BLOB}")
    text = raw.decode()
    if text.count(OLD) != 1:
        raise ValueError("Expected exactly one metric-name-removal call site")
    anchor = "namespace\n{\n    /// Checks if the types"
    if text.count(anchor) != 1:
        raise ValueError("Unexpected anonymous namespace")
    text = text.replace(anchor, "namespace\n{\n" + HELPER + "    /// Checks if the types", 1)
    include = "#include <Storages/TimeSeries/PrometheusQueryToSQL/dropMetricName.h>"
    text = text.replace(include, include + "\n#include <Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.h>", 1)
    return text.replace(OLD, NEW, 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=PATH)
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    source = Path(args.source)
    raw = source.read_bytes()
    modified = patch(raw)
    diff = "".join(difflib.unified_diff(raw.decode().splitlines(True), modified.splitlines(True),
                                     fromfile="a/" + PATH, tofile="b/" + PATH))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff)
    if args.apply:
        source.write_text(modified)
    print("R35_PATCH_SHA256", hashlib.sha256(diff.encode()).hexdigest())
    print("R35_SOURCE_SHA256", hashlib.sha256(modified.encode()).hexdigest())


if __name__ == "__main__":
    main()
