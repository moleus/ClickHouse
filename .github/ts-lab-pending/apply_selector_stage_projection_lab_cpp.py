#!/usr/bin/env python3
"""Guarded C++ lab patch: project stage tags at selector registration only.

Apply AFTER apply_selector_native_one_point_v4_filtered_cpp.py and after
the upstream baseline 52529f0c711566d696917678bf15e7068772fdde.
Default public timeSeriesSelector semantics remain unchanged.
"""
from pathlib import Path

def replace_once(path, old, new):
    f=Path(path)
    text=f.read_text()
    count=text.count(old)
    if count!=1:
        raise RuntimeError(f"{path}: expected one occurrence, got {count}: {old[:100]!r}")
    f.write_text(text.replace(old,new,1))

header="src/Storages/StorageTimeSeriesSelector.h"
replace_once(header,
    "        ReturnColumns return_columns = ReturnColumns::Samples;",
    """        ReturnColumns return_columns = ReturnColumns::Samples;
        /// Only the explicit native one-day avg by(stage) lab plan uses this.
        /// The normal public selector preserves complete original tags.
        bool lab_project_tags_stage = false;""")

src="src/Storages/StorageTimeSeriesSelector.cpp"
replace_once(src,
    """namespace Setting
{
    extern const SettingsBool time_series_prefer_recent_samples_table;
}""",
    """namespace Setting
{
    extern const SettingsBool time_series_prefer_recent_samples_table;
    extern const SettingsBool promql_enable_native_one_point_lab;
}""")
replace_once(src,
    """    String return_columns_str = getStringConstArgument(args[argument_index++], context, "return_columns");
    auto return_columns = parseReturnColumns(return_columns_str, function_name);""",
    """    String return_columns_str = getStringConstArgument(args[argument_index++], context, "return_columns");
    const bool lab_project_tags_stage = return_columns_str == "samples_lab_stage";
    if (lab_project_tags_stage)
    {
        if (!context->getSettingsRef()[Setting::promql_enable_native_one_point_lab])
            throw Exception(ErrorCodes::BAD_ARGUMENTS, "samples_lab_stage requires explicit laboratory opt-in");
        return_columns_str = "samples";
    }
    auto return_columns = parseReturnColumns(return_columns_str, function_name);""")
replace_once(src,
    """    config.return_columns = return_columns;
    config.selector = std::move(selector);""",
    """    config.return_columns = return_columns;
    config.lab_project_tags_stage = lab_project_tags_stage;
    config.selector = std::move(selector);""")
replace_once(src,
    """        const std::optional<TimeSeriesIDRanges> & metric_id_ranges,
        const DataTypePtr & table_timestamp_type)
    {
        auto select_query = make_intrusive<ASTSelectQuery>();

        /// SELECT timeSeriesStoreTags""",
    """        const std::optional<TimeSeriesIDRanges> & metric_id_ranges,
        const DataTypePtr & table_timestamp_type,
        bool lab_project_tags_stage = false)
    {
        auto select_query = make_intrusive<ASTSelectQuery>();

        /// SELECT timeSeriesStoreTags""")
replace_once(src,
    """            args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::ID));
            args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::Tags));
            args.push_back(make_intrusive<ASTLiteral>(TimeSeriesTagNames::MetricName));
            args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::MetricName));

            for (const auto & [tag_name, column_name] : column_name_by_tag_name)
            {
                args.push_back(make_intrusive<ASTLiteral>(tag_name));
                args.push_back(make_intrusive<ASTIdentifier>(column_name));
            }

            select_list.push_back(makeASTFunction("timeSeriesStoreTags", std::move(args)));""",
    """            args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::ID));
            if (lab_project_tags_stage)
            {
                /// The source WHERE still evaluates ALL original matchers, and
                /// the LEFT JOIN still enforces the original TimeRanges filter.
                /// Only the tags stored in the per-query group registry change.
                /// Missing stage must remain absent; stage="" is a real value.
                args.push_back(make_intrusive<ASTLiteral>(Null{}));
                args.push_back(make_intrusive<ASTLiteral>(String{"stage"}));
                ASTPtr stage_value = tagNameToAST("stage", column_name_by_tag_name);
                if (!column_name_by_tag_name.contains("stage"))
                {
                    stage_value = makeASTFunction(
                        "if",
                        makeASTFunction(
                            "mapContains",
                            make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::Tags),
                            make_intrusive<ASTLiteral>(String{"stage"})),
                        std::move(stage_value),
                        make_intrusive<ASTLiteral>(Null{}));
                }
                args.push_back(std::move(stage_value));
            }
            else
            {
                args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::Tags));
                args.push_back(make_intrusive<ASTLiteral>(TimeSeriesTagNames::MetricName));
                args.push_back(make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::MetricName));
                for (const auto & [tag_name, column_name] : column_name_by_tag_name)
                {
                    args.push_back(make_intrusive<ASTLiteral>(tag_name));
                    args.push_back(make_intrusive<ASTIdentifier>(column_name));
                }
            }
            select_list.push_back(makeASTFunction("timeSeriesStoreTags", std::move(args)));""")
replace_once(src,
    """        tags_table_id, matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids, metric_id_ranges,
        config.table_timestamp_type);""",
    """        tags_table_id, matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids, metric_id_ranges,
        config.table_timestamp_type, config.lab_project_tags_stage);""")

converter="src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp"
replace_once(converter,
    """fromUnixTimestamp64Milli({}), 'samples')
                ),
                flat AS""",
    """fromUnixTimestamp64Milli({}), 'samples_lab_stage')
                ),
                flat AS""")

# No other functions or call sites are changed. This is deliberately not a
# generic selector setting. The translator invokes it only after its strict
# one-point / stage-only AST guard accepts a query.
print("LAB_STAGE_TAGS_CPP_PATCH_OK",flush=True)
