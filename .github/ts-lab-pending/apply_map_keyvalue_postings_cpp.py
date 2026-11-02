#!/usr/bin/env python3
"""Use the built-in TimeSeries v11 keyValuePairs tags_idx postings for safe EQ.

Pin: ClickHouse 52529f0c711566d696917678bf15e7068772fdde,
StorageTimeSeriesSelector.cpp: matcherToAST.
For a positive label matcher with nonempty value and NO materialized tag
column, tags[label] == value is logically equivalent to
mapContainsKeyValue(tags, label, value). This map predicate directly
participates in the built-in text tokenizer=keyValuePairs postings index.
The original path remains for missing/empty, negatives, regex and hot columns.
Never use this optimization for empty string: PromQL absent label matches "".
No index-dependent correctness assumptions: predicate is evaluated exactly.
"""
from pathlib import Path
p=Path("src/Storages/StorageTimeSeriesSelector.cpp")
s=p.read_text()
old="""    ASTPtr matcherToAST(const PrometheusQueryTree::Matcher & matcher, const std::unordered_map<String, String> & column_name_by_tag_name)
    {
        std::string_view function_name;"""
new="""    ASTPtr matcherToAST(const PrometheusQueryTree::Matcher & matcher, const std::unordered_map<String, String> & column_name_by_tag_name)
    {
        /// v11 tags_idx is a native text index with tokenizer=keyValuePairs.
        /// mapContainsKeyValue() is an exact row predicate the index can serve
        /// by intersecting (tag_name, tag_value) posting lists in each part.
        /// An absent PromQL label equals the empty string, so never rewrite
        /// comparisons with empty values. Keep negatives and regex untouched.
        /// A physical tag column may have a faster primary index path.
        if (matcher.matcher_type == PrometheusQueryTree::MatcherType::EQ
            && matcher.label_name != TimeSeriesTagNames::MetricName
            && !matcher.label_value.empty()
            && !column_name_by_tag_name.contains(matcher.label_name))
        {
            return makeASTFunction("mapContainsKeyValue",
                make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::Tags),
                make_intrusive<ASTLiteral>(matcher.label_name),
                make_intrusive<ASTLiteral>(matcher.label_value));
        }

        std::string_view function_name;"""
if s.count(old)!=1:raise RuntimeError(f"Pinned matcherToAST source mismatch {s.count(old)}")
p.write_text(s.replace(old,new,1))
print("LAB_TS_NATIVE_MAP_KEYVALUE_POSTINGS_APPLIED",p)
