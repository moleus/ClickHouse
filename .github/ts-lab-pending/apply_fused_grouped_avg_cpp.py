#!/usr/bin/env python3
"""Experimental C++ PromQL planner fused grouped avg for disjoint windows.

Only root avg [by|without] (avg_over_time(range-selector[window])) with
DateTime64(3) input/output, no offsets/subqueries/overlap. Keep the old
converter in every other case. No per-series dense nullable grid.
This guarded fastpath remains lab-only until cross-engine correctness,
stale-NaN, duplicate timestamp, and fractional boundary tests pass.
"""
from pathlib import Path

p=Path("src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp")
s=p.read_text()
old="#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>"
replacement="""#include <Storages/TimeSeries/PrometheusQueryToSQL/ConverterContext.h>
#include <Storages/TimeSeries/PrometheusQueryToSQL/SelectQueryBuilder.h>
#include <Storages/TimeSeries/PrometheusQueryToSQL/transformGroupASTForAggregationOperator.h>
#include <DataTypes/DataTypeDateTime64.h>
#include <Common/typeid_cast.h>
#include <Parsers/ASTFunction.h>
#include <Parsers/ASTIdentifier.h>
#include <Parsers/ASTLiteral.h>"""
if s.count(old)!=1:raise AssertionError(("missing include",s.count(old)))
s=s.replace(old,replacement,1)
needle="""namespace
{
    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)"""
if s.count(needle)!=1:raise AssertionError("missing namespace visitor")
helper=r'''
namespace
{
    /// Fastpath: collapse samples into a per-series/per-window average, then
    /// group averages, without creating an Array(Nullable(Float64)) for
    /// every original series. Only DISJOINT grids are handled; everything
    /// else still uses timeSeriesAvgToGrid and avgForEach below.
    ///
    /// This intentionally does not assume one sample per time series.
    /// The two GROUP BY stages are necessary for correct PromQL weighting.
    ASTPtr tryGroupedDisjointAvgFastpath(const Node * root, ConverterContext & context, const PrometheusQueryEvaluationSettings & settings)
    {
        if (context.result_type != ResultType::RANGE_VECTOR
            || root->node_type != NodeType::AggregationOperator
            || context.result_timestamp_scale != 3)
            return {};

        /// The input timestamp must be exactly milliseconds: converting
        /// nanosecond timestamps to millis would change window membership.
        const auto * timestamp_type = typeid_cast<const DataTypeDateTime64 *>(settings.table_timestamp_type.get());
        if (!timestamp_type || timestamp_type->getScale() != 3)
            return {};

        const auto * aggregation = static_cast<const PrometheusQueryTree::AggregationOperator *>(root);
        if (aggregation->operator_name != "avg" || aggregation->getArguments().size() != 1)
            return {};

        const auto * function_node = aggregation->getArguments()[0];
        if (function_node->node_type != NodeType::Function)
            return {};
        const auto * function = static_cast<const PrometheusQueryTree::Function *>(function_node);
        if (function->function_name != "avg_over_time" || function->getArguments().size() != 1)
            return {};
        const auto * selector_node = function->getArguments()[0];
        if (selector_node->node_type != NodeType::RangeSelector)
            return {};
        const auto * selector = static_cast<const PrometheusQueryTree::RangeSelector *>(selector_node);
        const auto & range = context.node_range_getter.get(function_node);
        if (range.empty() || range.step.value <= 0 || range.window.value <= 0
            || range.window.value > range.step.value)
            return {};

        /// All time quantities are integer milliseconds at scale 3.
        /// The underlying PromQL window is (evaluation_time - window, evaluation_time].
        /// With window <= step, each sample belongs to at most one window.
        const Int64 start_ms = range.start_time.value;
        const Int64 step_ms = range.step.value;
        const Int64 window_ms = range.window.value;
        const Int64 points = 1 + (range.end_time.value - start_ms) / step_ms;
        if (points <= 0)
            return {};

        SQLQueryPiece input = fromSelector(selector, context);
        if (input.store_method != StoreMethod::SAMPLES || !input.select_query)
            return {};

        auto column = [](const char * name) -> ASTPtr { return make_intrusive<ASTIdentifier>(name); };
        auto integer = [](Int64 n) -> ASTPtr { return make_intrusive<ASTLiteral>(n); };
        auto addStage = [&context](ASTPtr query) -> String
        {
            context.subqueries.emplace_back(context.subqueries.size(), std::move(query), SQLSubqueryType::TABLE);
            return context.subqueries.back().name;
        };

        /// Unfold the bucketed Array(Tuple(timestamp,value)) into one row
        /// per sample while preserving its original series group.
        SelectQueryBuilder expand;
        expand.from_table = addStage(std::move(input.select_query));
        expand.select_list.push_back(column(ColumnNames::Group));
        expand.select_list.back()->setAlias("lab_series_group");
        expand.select_list.push_back(makeASTFunction("arrayJoin", column(ColumnNames::Samples)));
        expand.select_list.back()->setAlias("lab_sample");

        SelectQueryBuilder extract;
        extract.from_table = addStage(expand.getSelectQuery());
        extract.select_list.push_back(column("lab_series_group"));
        extract.select_list.push_back(makeASTFunction(
            "toUnixTimestamp64Milli",
            makeASTFunction("tupleElement", column("lab_sample"), integer(1))));
        extract.select_list.back()->setAlias("lab_sample_ms");
        extract.select_list.push_back(makeASTFunction("tupleElement", column("lab_sample"), integer(2)));
        extract.select_list.back()->setAlias("lab_sample_value");

        /// ceil((sample_ms-start_ms)/step_ms) determines the only possible
        /// evaluation index. The explicit WHERE condition enforces both
        /// open-left and closed-right PromQL window boundaries and gaps
        /// if the step is larger than the window.
        SelectQueryBuilder assign;
        assign.from_table = addStage(extract.getSelectQuery());
        assign.select_list.push_back(column("lab_series_group"));
        assign.select_list.push_back(column("lab_sample_ms"));
        assign.select_list.push_back(column("lab_sample_value"));
        assign.select_list.push_back(makeASTFunction(
            "intDiv",
            makeASTFunction("plus",
                makeASTFunction("minus", column("lab_sample_ms"), integer(start_ms)),
                integer(step_ms - 1)),
            integer(step_ms)));
        assign.select_list.back()->setAlias("lab_grid_idx");

        auto end_ms = [&column,&integer,start_ms,step_ms]() -> ASTPtr
        {
            return makeASTFunction(
                "plus", integer(start_ms),
                makeASTFunction("multiply", column("lab_grid_idx"), integer(step_ms)));
        };

        SelectQueryBuilder per_series;
        per_series.from_table = addStage(assign.getSelectQuery());
        per_series.select_list.push_back(column("lab_series_group"));
        per_series.select_list.push_back(column("lab_grid_idx"));
        per_series.select_list.push_back(makeASTFunction("avg", column("lab_sample_value")));
        per_series.select_list.back()->setAlias("lab_series_avg");
        per_series.group_by.push_back(column("lab_series_group"));
        per_series.group_by.push_back(column("lab_grid_idx"));
        per_series.where = makeASTFunction(
            "and",
            makeASTFunction("greaterOrEquals", column("lab_grid_idx"), integer(0)),
            makeASTFunction("less", column("lab_grid_idx"), integer(points)),
            makeASTFunction("greater", column("lab_sample_ms"),
                makeASTFunction("minus", end_ms(), integer(window_ms))),
            makeASTFunction("lessOrEquals", column("lab_sample_ms"), end_ms()));

        /// The __name__ label is dropped by avg_over_time(). The same
        /// grouping transform is used by the ordinary PromQL converter.
        bool metric_name_dropped = true;
        ASTPtr unname = makeASTFunction(
            "timeSeriesRemoveTags",
            column("lab_series_group"),
            make_intrusive<ASTLiteral>(Array{String{kMetricName}}));
        ASTPtr new_group = transformGroupASTForAggregationOperator(
            aggregation, std::move(unname), /*drop_metric_name=*/ true, metric_name_dropped);

        SelectQueryBuilder grouped;
        grouped.from_table = addStage(per_series.getSelectQuery());
        grouped.select_list.push_back(std::move(new_group));
        grouped.select_list.back()->setAlias("lab_result_group");
        grouped.select_list.push_back(column("lab_grid_idx"));
        grouped.select_list.push_back(makeASTFunction("avg", column("lab_series_avg")));
        grouped.select_list.back()->setAlias("lab_group_avg");
        grouped.group_by.push_back(column("lab_result_group"));
        grouped.group_by.push_back(column("lab_grid_idx"));

        /// The output is one sample array per FINAL group, not one nullable
        /// grid per raw time series. timeSeriesGroupArray keeps timestamps
        /// sorted and returns the same required data type as finalizeSQL().
        SelectQueryBuilder output;
        output.from_table = addStage(grouped.getSelectQuery());
        output.select_list.push_back(makeASTFunction("timeSeriesGroupToTags", column("lab_result_group")));
        output.select_list.back()->setAlias(ColumnNames::Tags);
        ASTPtr eval_time = makeASTFunction(
            "CAST",
            makeASTFunction("fromUnixTimestamp64Milli", end_ms()),
            make_intrusive<ASTLiteral>(context.result_timestamp_type->getName()));
        ASTPtr samples = makeASTFunction(
            "timeSeriesGroupArray", std::move(eval_time), column("lab_group_avg"));
        samples = makeASTFunction(
            "CAST", std::move(samples),
            make_intrusive<ASTLiteral>(fmt::format("Array(Tuple({}, Float64))",context.result_timestamp_type->getName())));
        output.select_list.push_back(std::move(samples));
        output.select_list.back()->setAlias(ColumnNames::getOuterSamples(context.time_series_version));
        output.group_by.push_back(column("lab_result_group"));
        output.with = std::move(context.subqueries);
        return output.getSelectQuery();
    }

    SQLQueryPiece visitNode(const Node * node, ConverterContext & context)'''
if s.count(needle)!=1:raise AssertionError("missing visitor")
s=s.replace(needle,helper,1)
s=s.replace("#include <Parsers/ASTLiteral.h>", "#include <Parsers/ASTLiteral.h>\n#include <fmt/format.h>",1)
old="""    ConverterContext context{promql_tree_to_convert, settings};
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
new="""    ConverterContext context{promql_tree_to_convert, settings};
    if (auto fast = tryGroupedDisjointAvgFastpath(promql_tree_to_convert->getRoot(), context, settings))
        return fast;
    auto query_piece = visitNode(promql_tree_to_convert->getRoot(), context);"""
if s.count(old)!=1:raise AssertionError("missing getSQL body")
s=s.replace(old,new,1)
p.write_text(s)
print("LAB_GROUPED_DISJOINT_AVG_CXX_APPLIED",p,"new_bytes",len(s))
