#!/usr/bin/env python3
"""Lab C++ PromQL lowering: scalar per-ID grouped avg via timeSeriesSelector.

Strict guard: explicit lab opt-in, v11, Float64, DateTime64 scale 3, UTC,
exact metric name with optional label matchers, avg by(stage) of
avg_over_time(metric[1d]) with one-day step and query-range mode.
Other queries keep the unchanged native converter. This is NOT a
production optimization until access/security and adversarial tests pass.

Unlike direct-inner SQL, this path uses timeSeriesSelector and the normal
ID-to-tags query collector, so it keeps the engine's regular label lookup.
"""
from pathlib import Path


def replace_once(path, old, new):
    p=Path(path)
    s=p.read_text()
    n=s.count(old)
    if n != 1:
        raise RuntimeError(f"{path}: expected one source anchor; got {n}: {old[:110]!r}")
    p.write_text(s.replace(old,new,1))


core_settings = "src/Core/Settings.cpp"
replace_once(core_settings,
    '    DECLARE(String, promql_database, "", R"(',
    '''    DECLARE(Bool, promql_enable_native_one_point_lab, false, R"(
Explicit laboratory opt-in for native one-point grouped AVG.
This is not a production setting. Without opt-in the original PromQL plan is used.
)", PRIVATE_PREVIEW, \
        {"26.11", false, false, "Laboratory-only native one-point grouped AVG."}) \
    DECLARE(String, promql_database, "", R"(''')

settings="src/Storages/TimeSeries/PrometheusQueryEvaluationSettings.h"
replace_once(settings,
"""    UInt64 time_series_version = TimeSeriesVersion::LATEST;

    /// Data type""",
"""    UInt64 time_series_version = TimeSeriesVersion::LATEST;

    /// Lab-only native compensated grouped average via the normal TimeSeries selector.
    /// StoragePrometheusQuery sets this only after checking that the v11
    /// stored samples contain Float64 values. Never assume that type.
    bool lab_selector_scalar_avg_float64 = false;

    /// Data type""")

storage="src/Storages/StoragePrometheusQuery.cpp"
replace_once(storage,
"""    extern const SettingsBool promql_push_down_label_matchers;
}""",
"""    extern const SettingsBool promql_push_down_label_matchers;
    extern const SettingsBool promql_enable_native_one_point_lab;
}""")
replace_once(storage,
"#include <Storages/TimeSeries/PrometheusQueryToSQL/Converter.h>",
"""#include <Storages/TimeSeries/PrometheusQueryToSQL/Converter.h>
#include <Storages/TimeSeries/TimeSeriesColumnNames.h>
#include <Storages/TimeSeries/splitTimeSeriesType.h>""")

replace_once(storage,
"""    evaluation_settings.push_down_label_matchers = context->getSettingsRef()[Setting::promql_push_down_label_matchers];
    PrometheusQueryToSQL::Converter converter""",
"""    evaluation_settings.push_down_label_matchers = context->getSettingsRef()[Setting::promql_push_down_label_matchers];

    /// The experimental lowering assumes Float64 sample values.
    /// The actual TimeSeries v11 schema determines whether it can run.
    if (time_series_storage->getVersion() == 11)
    {
        const auto meta = time_series_storage->getInMemoryMetadataPtr(context, false);
        const auto [timestamp_type, value_type] = splitTimeSeriesType(
            meta->columns.get(TimeSeriesColumnNames::getOuterSamples(11)).type);
        evaluation_settings.lab_selector_scalar_avg_float64 = value_type->getName() == "Float64"\n            && context->getSettingsRef()[Setting::promql_enable_native_one_point_lab];
    }
    PrometheusQueryToSQL::Converter converter""")

converter="src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp"
replace_once(converter,
"#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>",
"""#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>
#include <Parsers/ParserSelectWithUnionQuery.h>
#include <Parsers/parseQuery.h>
#include <DataTypes/DataTypeDateTime64.h>
#include <Common/typeid_cast.h>
#include <fmt/format.h>\n#include <Common/quoteString.h>""")

s=Path(converter).read_text()
anchor="""namespace
{
    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)"""
helper=r'''
namespace
{
    /// Explicitly opted-in compensated one-point grouped average; lab only.
    /// It keeps the normal selector (the tags collector and permissions)
    /// but avoids a nullable grid for every short-lived series, and preserves
    /// native per-ID compensated sums and duplicate timestamp selection.
    ASTPtr trySelectorScalarGroupedAvg(
        const Node * root, const ConverterContext & context, const PrometheusQueryEvaluationSettings & settings)
    {
        if (!settings.lab_selector_scalar_avg_float64
            || settings.time_series_version != 11
            || context.result_type != ResultType::RANGE_VECTOR
            || context.result_timestamp_scale != 3
            || root->node_type != NodeType::AggregationOperator
            || (!settings.time_zone.empty() && settings.time_zone != "UTC"))
            return {};

        const auto * timestamp_type = typeid_cast<const DataTypeDateTime64 *>(settings.table_timestamp_type.get());
        if (!timestamp_type || timestamp_type->getScale() != 3)
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
        const auto * selector = static_cast<const PrometheusQueryTree::RangeSelector *>(rnode);
        const auto & matchers = selector->getInstantSelector()->matchers;
        bool has_exact_metric_name = false;
        for (const auto & matcher : matchers)
        {
            if (matcher.label_name == "__name__"
                && matcher.matcher_type == PrometheusQueryTree::MatcherType::EQ
                && !matcher.label_value.empty())
                has_exact_metric_name = true;
        }
        if (!has_exact_metric_name)
            return {};
        const String selector_text = selector->getInstantSelector()->toString(*context.promql_tree);

        /// Keep identifiers safe; selector SQL literal is escaped by quoteString.
        auto safeName = [](const String & name, bool allow_colon)
        {
            if (name.empty())
                return false;
            for (char c : name)
                if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                    || (c >= '0' && c <= '9') || c == '_' || (allow_colon && c == ':')))
                    return false;
            return true;
        };
        const String & db = settings.time_series_storage_id.getDatabaseName();
        const String & table = settings.time_series_storage_id.getTableName();
        if (!safeName(db, false) || !safeName(table, false))
            return {};

        const auto & range = context.node_range_getter.get(fnode);
        if (range.empty() || range.step.value != 86400000 || range.window.value != 86400000
            || range.end_time.value <= range.start_time.value
            || range.start_time.value <= range.window.value)
            return {};
        const Int64 start = range.start_time.value;
        const Int64 step = range.step.value;
        const Int64 window = range.window.value;
        const Int64 points = 1 + (range.end_time.value - start) / step;
        if (points < 2 || points > 1000000)
            return {};
        const Int64 last = start + (points - 1) * step;

        const String sql = fmt::format(R"TS(
            WITH
                src AS (
                    SELECT id AS series_id, arrayJoin(samples) AS sample
                    FROM timeSeriesSelector(
                        '{}', '{}', {},
                        fromUnixTimestamp64Milli({}),
                        fromUnixTimestamp64Milli({}), 'samples')
                ),
                flat AS (
                    SELECT series_id,
                        toUnixTimestamp64Milli(tupleElement(sample, 1)) AS sample_ms,
                        tupleElement(sample, 2) AS sample_v
                    FROM src
                ),
                indexed AS (
                    SELECT series_id,sample_ms,sample_v AS value,
                        intDiv(sample_ms - {} + {} - 1,{}) AS gi
                    FROM flat
                    WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
                ),
                per_series AS (
                    SELECT series_id,gi,
                        arrayElement(
                            timeSeriesAvgToGrid(
                                fromUnixTimestamp64Milli(0), fromUnixTimestamp64Milli(0),
                                toDecimal64('86400',3), toDecimal64('86400',3)
                            )(
                                fromUnixTimestamp64Milli(sample_ms - ({} + gi * {})),value),
                            1) AS series_avg
                    FROM indexed
                    WHERE gi >= 0 AND gi < {}
                        AND sample_ms > {} + gi * {} - {}
                        AND sample_ms <= {} + gi * {}
                    GROUP BY series_id,gi
                ),
                per_stage AS (
                    SELECT timeSeriesRemoveAllTagsExcept(
                        timeSeriesIdToGroup(series_id),
                        ['stage']) AS stage_group,
                        gi,avg(series_avg) AS stage_avg
                    FROM per_series
                    GROUP BY stage_group,gi
                )
            SELECT timeSeriesGroupToTags(stage_group) AS tags,
                CAST(timeSeriesGroupArray(
                    fromUnixTimestamp64Milli({} + gi * {}),
                    stage_avg),'Array(Tuple(DateTime64(3),Float64))') AS samples
            FROM per_stage
            GROUP BY stage_group
        )TS",
            db, table, quoteString(selector_text), start-window+1, last,
            start,step,step,
            start,step,
            points,start,step,window,start,step,
            start,step);

        ParserSelectWithUnionQuery parser;
        return parseQuery(parser, sql, "TimeSeries native one-point compensated avg via selector (lab)", 0, 1000, 1000000);
    }

    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)'''
if s.count(anchor) != 1:
    raise RuntimeError(f"Converter namespace anchor count {s.count(anchor)}")
s=s.replace(anchor,helper,1)
old="""    ConverterContext context{promql_tree_to_convert, settings};
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
new="""    ConverterContext context{promql_tree_to_convert, settings};
    if (auto direct = trySelectorScalarGroupedAvg(promql_tree_to_convert->getRoot(), context, settings))
        return direct;
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
if s.count(old) != 1:
    raise RuntimeError("Converter getSQL anchor")
Path(converter).write_text(s.replace(old,new,1))
print("LAB_NATIVE_ONE_POINT_AVG_V4_FILTERED_CPP_APPLIED", settings, storage, converter)
