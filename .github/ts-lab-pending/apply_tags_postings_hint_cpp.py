#!/usr/bin/env python3
"""Experimental TimeSeries physical-tag text-postings hint.

In version 11 tags Map contains every label even when tags_to_columns
materializes a physical column. The existing per-part tags_idx text
index on tags (keyValuePairs tokenizer) is bypassed if matcherToAST
chooses only that physical column. For nonempty positive exact
matchers, preserve the cheap physical predicate AND add an equivalent
map predicate to permit using the existing postings index. Guard on
version >=11 AND actual tags_idx metadata. Negative/empty/regex not
reduced here. No new persisted index, no change to any data encoding.
"""
from pathlib import Path
p=Path("src/Storages/StorageTimeSeriesSelector.cpp")
s=p.read_text()
def replace(old,new,expected=1):
    global s
    n=s.count(old)
    if n!=expected:raise RuntimeError(f"expected {expected} occurrences, got {n}: {old[:110]!r}")
    s=s.replace(old,new)

replace(
"""    ASTPtr matcherToAST(const PrometheusQueryTree::Matcher & matcher, const std::unordered_map<String, String> & column_name_by_tag_name)
    {
        std::string_view function_name;""",
"""    ASTPtr matcherToAST(
        const PrometheusQueryTree::Matcher & matcher,
        const std::unordered_map<String, String> & column_name_by_tag_name,
        bool use_text_postings_hint = false)
    {
        /// In v11 tags Map has every label, even those with an explicit
        /// tags_to_columns physical column. The text keyValuePairs index
        /// indexes the Map but not the separate physical columns.
        /// This redundant equality is safe only for nonempty EQ terms
        /// and when that index has been verified on the target table.
        auto add_postings_hint = [&](ASTPtr condition, const String & exact_value) -> ASTPtr
        {
            if (!use_text_postings_hint || exact_value.empty()
                || matcher.label_name == TimeSeriesTagNames::MetricName
                || !column_name_by_tag_name.contains(matcher.label_name))
                return condition;

            ASTPtr map_condition = makeASTFunction(
                "equals",
                makeASTFunction("arrayElement",
                    make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::Tags),
                    make_intrusive<ASTLiteral>(matcher.label_name)),
                make_intrusive<ASTLiteral>(exact_value));
            return makeASTFunction("and", std::move(condition), std::move(map_condition));
        };

        std::string_view function_name;""")
replace(
"""                if (terms->size() == 1)
                    return makeASTFunction(add_not ? "notEquals" : "equals",
                        tagNameToAST(matcher.label_name, column_name_by_tag_name), make_intrusive<ASTLiteral>(std::move(terms->front())));""",
"""                if (terms->size() == 1)
                {
                    const String exact_value = std::move(terms->front());
                    ASTPtr condition = makeASTFunction(add_not ? "notEquals" : "equals",
                        tagNameToAST(matcher.label_name, column_name_by_tag_name), make_intrusive<ASTLiteral>(exact_value));
                    if (!add_not)
                        condition = add_postings_hint(std::move(condition), exact_value);
                    return condition;
                }""")
replace(
"""        ASTPtr res = makeASTFunction(function_name, tagNameToAST(matcher.label_name, column_name_by_tag_name), make_intrusive<ASTLiteral>(value));
        if (add_not)
            res = makeASTFunction("not", res);
        return res;""",
"""        ASTPtr res = makeASTFunction(function_name, tagNameToAST(matcher.label_name, column_name_by_tag_name), make_intrusive<ASTLiteral>(value));
        if (add_not)
            res = makeASTFunction("not", res);
        else if (matcher_type == PrometheusQueryTree::MatcherType::EQ)
            res = add_postings_hint(std::move(res), value);
        return res;""")
replace(
"""        const TableTimeRange & time_range_to_filter_ids,
        const DataTypePtr & table_timestamp_type)
    {
        ASTs asts;
        for (const auto & matcher : matchers)
            asts.push_back(matcherToAST(matcher, column_name_by_tag_name));""",
"""        const TableTimeRange & time_range_to_filter_ids,
        const DataTypePtr & table_timestamp_type,
        bool use_text_postings_hint = false)
    {
        ASTs asts;
        for (const auto & matcher : matchers)
            asts.push_back(matcherToAST(matcher, column_name_by_tag_name, use_text_postings_hint));""")
replace(
"""        const std::optional<TimeSeriesIDRanges> & metric_id_ranges,
        const DataTypePtr & table_timestamp_type)
    {
        auto select_query = make_intrusive<ASTSelectQuery>();""",
"""        const std::optional<TimeSeriesIDRanges> & metric_id_ranges,
        const DataTypePtr & table_timestamp_type,
        bool use_text_postings_hint = false)
    {
        auto select_query = make_intrusive<ASTSelectQuery>();""")
replace(
"""            auto where_filter = makeWhereFilterForTagsTable(matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids, table_timestamp_type);""",
"""            auto where_filter = makeWhereFilterForTagsTable(
                matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids,
                table_timestamp_type, use_text_postings_hint);""")
replace(
"""        select_query = makeSelectQueryFromTagsTable(
            tags_table_id, matchers, makeColumnNameByTagNameMap(time_series_settings), time_ranges_table_id, time_range_to_filter_ids,
            metric_id_ranges, table_timestamp_type);""",
"""        /// Version 11 writes all labels into tags even with tags_to_columns.
        /// In earlier versions the Map may omit materialized labels.
        const bool use_text_postings_hint =
            time_series_storage.getVersion() >= 11 && tags_table_metadata->getSecondaryIndices().has("tags_idx");
        select_query = makeSelectQueryFromTagsTable(
            tags_table_id, matchers, makeColumnNameByTagNameMap(time_series_settings), time_ranges_table_id, time_range_to_filter_ids,
            metric_id_ranges, table_timestamp_type, use_text_postings_hint);""")
replace(
"""    ASTPtr select_query_from_tags_table = makeSelectQueryFromTagsTable(
        tags_table_id, matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids, metric_id_ranges,
        config.table_timestamp_type);""",
"""    const bool use_text_postings_hint =
        time_series_storage->getVersion() >= 11 && tags_table_metadata->getSecondaryIndices().has("tags_idx");
    ASTPtr select_query_from_tags_table = makeSelectQueryFromTagsTable(
        tags_table_id, matchers, column_name_by_tag_name, time_ranges_table_id, time_range_to_filter_ids, metric_id_ranges,
        config.table_timestamp_type, use_text_postings_hint);""")
p.write_text(s)
print("LAB_TAGS_TEXT_POSTINGS_HINT_APPLIED",p, "bytes",len(s))
