#!/usr/bin/env python3
"""Experimental ClickHouse v11 native grouped-avg fastpath reading owned TS targets.

Strict guard: own samples+tags, no recents/time_ranges, exact metric
selector only, grouped by stage, avg_over_time with nonoverlapping
windows and DateTime64(3)/Float64. Regular metrics and every unsupported
expression fall back to the original C++ converter. Lab experiment only:
direct-table permission inheritance needs explicit security review.
"""
from pathlib import Path

def exact(path,old,new):
    p=Path(path);s=p.read_text();c=s.count(old)
    if c!=1:raise RuntimeError(f"{path}: expected one matching region got {c}: {old[:120]!r}")
    p.write_text(s.replace(old,new))

settings="src/Storages/TimeSeries/PrometheusQueryEvaluationSettings.h"
exact(settings,
"""    UInt64 time_series_version = TimeSeriesVersion::LATEST;

    /// Data type""",
"""    UInt64 time_series_version = TimeSeriesVersion::LATEST;

    /// Lab-only native inner sample/tag target fastpath. Both must be set
    /// by StoragePrometheusQuery AFTER checking version, target ownership,
    /// TTL configuration and input Float64 timestamp precision.
    StorageID lab_samples_target_id = StorageID::createEmpty();
    StorageID lab_tags_target_id = StorageID::createEmpty();

    /// Data type""")

storage="src/Storages/StoragePrometheusQuery.cpp"
exact(storage,
"""    evaluation_settings.push_down_label_matchers = context->getSettingsRef()[Setting::promql_push_down_label_matchers];
    PrometheusQueryToSQL::Converter converter""",
"""    evaluation_settings.push_down_label_matchers = context->getSettingsRef()[Setting::promql_push_down_label_matchers];

    /// Experimental only: access control over the inner target tables
    /// needs explicit review before production enablement. Restrict to
    /// owner-managed targets with no recent/temporal index target.
    if (time_series_storage->getVersion() == 11
        && time_series_storage->isInnerTable(ViewTarget::Samples)
        && time_series_storage->isInnerTable(ViewTarget::Tags)
        && !time_series_storage->hasTarget(ViewTarget::RecentSamples)
        && !time_series_storage->hasTarget(ViewTarget::TimeRanges))
    {
        auto meta = time_series_storage->getInMemoryMetadataPtr(context, false);
        const auto [ts_type, val_type] = splitTimeSeriesType(
            meta->columns.get(TimeSeriesColumnNames::getOuterSamples(11)).type);
        if (val_type->getName() == "Float64")
        {
            evaluation_settings.lab_samples_target_id = time_series_storage->getTargetTableID(ViewTarget::Samples, context);
            evaluation_settings.lab_tags_target_id = time_series_storage->getTargetTableID(ViewTarget::Tags, context);
        }
    }
    PrometheusQueryToSQL::Converter converter""")

converter="src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp"
p=Path(converter);s=p.read_text()
old="#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>"
if s.count(old)!=1:raise RuntimeError("converter include")
s=s.replace(old,
"""#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>
#include <Parsers/ParserSelectWithUnionQuery.h>
#include <Parsers/parseQuery.h>
#include <DataTypes/DataTypeDateTime64.h>
#include <Common/typeid_cast.h>
#include <fmt/format.h>""",1)
old="""namespace
{
    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)"""
if s.count(old)!=1:raise RuntimeError("converter namespace marker")
new=r'''
namespace
{
    /// Native disjoint grouped avg: read the SAME TimeSeries v11 owned
    /// samples/tags tables as the original selector, but aggregate sample
    /// scalars per ID/day before grouping by stage. Avoid creating per-ID
    /// nullable grids. This experimental path is narrowly guarded.
    ASTPtr tryDirectInnerGroupedAvg(
        const Node * root, const ConverterContext & context, const PrometheusQueryEvaluationSettings & settings)
    {
        if (settings.lab_samples_target_id.empty() || settings.lab_tags_target_id.empty()
            || context.result_type != ResultType::RANGE_VECTOR
            || root->node_type != NodeType::AggregationOperator
            || context.result_timestamp_scale != 3)
            return {};

        const auto * type = typeid_cast<const DataTypeDateTime64 *>(settings.table_timestamp_type.get());
        if (!type || type->getScale() != 3)
            return {};

        const auto * agg = static_cast<const PrometheusQueryTree::AggregationOperator *>(root);
        if (agg->operator_name != "avg" || !agg->by || agg->without
            || agg->labels.size() != 1 || agg->labels.front() != "stage"
            || agg->getArguments().size() != 1)
            return {};

        const auto * fnode = agg->getArguments().front();
        if (fnode->node_type != NodeType::Function)
            return {};
        const auto * f = static_cast<const PrometheusQueryTree::Function *>(fnode);
        if (f->function_name != "avg_over_time" || f->getArguments().size() != 1)
            return {};
        const auto * rnode = f->getArguments().front();
        if (rnode->node_type != NodeType::RangeSelector)
            return {};
        const auto * sel = static_cast<const PrometheusQueryTree::RangeSelector *>(rnode);
        const auto & matchers = sel->getInstantSelector()->matchers;
        if (matchers.size() != 1
            || matchers[0].label_name != "__name__"
            || matchers[0].matcher_type != PrometheusQueryTree::MatcherType::EQ
            || matchers[0].label_value.empty())
            return {};
        const String & metric = matchers[0].label_value;
        for (char c : metric)
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                  || (c >= '0' && c <= '9') || c == '_' || c == ':'))
                return {};

        const auto & range = context.node_range_getter.get(fnode);
        if (range.empty() || range.step.value <= 0 || range.window.value <= 0
            || range.window.value > range.step.value
            || range.end_time.value <= range.start_time.value)
            return {};
        const Int64 start = range.start_time.value;
        const Int64 step = range.step.value;
        const Int64 window = range.window.value;
        const Int64 points = 1 + (range.end_time.value - start) / step;
        if (points < 2 || points > 1000000)
            return {};
        const Int64 last = start + (points - 1) * step;

        /// Quote database and table identifier components separately.
        auto tableName = [](const StorageID & id) -> String
        {
            auto quote = [](const String & name) -> String
            {
                String out(1,static_cast<char>(96));
                for (char c : name)
                {
                    out += c;
                    if (c == static_cast<char>(96))
                        out += c;
                }
                out += static_cast<char>(96);
                return out;
            };
            return quote(id.getDatabaseName()) + "." + quote(id.getTableName());
        };

        /// Duplicate-timestamp semantics:
        /// timeSeriesGroupArray keeps MAX real sample at a timestamp;
        /// NaN survives only if all duplicates are NaN.
        /// Flatten, de-duplicate per series/timestamp, then per series/grid
        /// AVG, then group AVG. Never use ANY INNER JOIN, which can drop
        /// multiple sample rows after join reordering.
        const String sql = fmt::format(R"TS(
            WITH
                src AS (
                    SELECT s.id AS series_id, t.tags AS series_tags,
                           arrayJoin(s.samples) AS sample
                    FROM {} AS s
                    INNER JOIN {} AS t FINAL ON s.id=t.id
                    WHERE t.metric_name='{}'
                      AND s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                ),
                flattened AS (
                    SELECT series_id,series_tags,
                           toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
                           tupleElement(sample,2) AS sample_v FROM src
                ),
                dedup AS (
                    SELECT series_id,series_tags,sample_ms,
                           argMax(sample_v,
                             tuple(toUInt8(not isNaN(sample_v)),
                                   if(isNaN(sample_v),0,sample_v))) AS value
                    FROM flattened
                    WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
                    GROUP BY series_id,series_tags,sample_ms
                ),
                indexed AS (
                    SELECT series_id,series_tags,sample_ms,value,
                           intDiv(sample_ms - {} + {} - 1,{}) AS gi FROM dedup
                ),
                per_series AS (
                    SELECT series_id,series_tags,gi,avg(value) AS series_avg
                    FROM indexed
                    WHERE gi >= 0 AND gi < {}
                      AND sample_ms > {} + gi * {} - {}
                      AND sample_ms <= {} + gi * {}
                    GROUP BY series_id,series_tags,gi
                ),
                per_stage AS (
                    SELECT timeSeriesTagsToGroup(
                            if(mapContains(series_tags,'stage'),
                              [tuple('stage',series_tags['stage'])],
                              CAST([],'Array(Tuple(String,String))'))) AS new_group,
                           gi,avg(series_avg) AS result_value
                    FROM per_series
                    GROUP BY new_group,gi
                )
            SELECT timeSeriesGroupToTags(new_group) AS tags,
                   CAST(timeSeriesGroupArray(
                     fromUnixTimestamp64Milli({} + gi * {}),result_value),
                     'Array(Tuple(DateTime64(3),Float64))') AS samples
            FROM per_stage GROUP BY new_group
        )TS",
            tableName(settings.lab_samples_target_id), tableName(settings.lab_tags_target_id),metric,
            last,start-window,
            start,step,step,points,
            start,step,window,start,step,
            start,step);
        ParserSelectWithUnionQuery parser;
        return parseQuery(parser,sql,"TimeSeries experimental direct grouped avg",0,1000,1000000);
    }

    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)'''
s=s.replace(old,new,1)
old="""    ConverterContext context{promql_tree_to_convert, settings};
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
replacement="""    ConverterContext context{promql_tree_to_convert, settings};
    if (auto direct = tryDirectInnerGroupedAvg(promql_tree_to_convert->getRoot(), context, settings))
        return direct;
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
if s.count(old)!=1:raise RuntimeError("converter getSQL marker")
s=s.replace(old,replacement,1)
p.write_text(s)
print("LAB_TS_DIRECT_INNER_GROUPED_CPP_APPLIED",settings,storage,converter)
