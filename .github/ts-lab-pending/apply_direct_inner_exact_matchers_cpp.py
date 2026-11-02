#!/usr/bin/env python3
"""Exact nonempty label matcher prefilter for the guarded late-tags grouped avg.

Apply AFTER apply_direct_inner_grouped_cpp.py and apply_direct_inner_late_tags_cpp.py.
Only positive EQ matchers with safe literal labels/values are supported.
Nonpositive, regex, missing or empty-value queries keep the original converter.
A series-ID IN subquery filters samples BEFORE the heavy per-ID aggregation.
This is not a name-specific heuristic, and the original WHERE predicate remains
exact through mapContainsKeyValue() evaluation. Must not be deployed before
authorization and dedup/out-of-order/security tests.
"""
from pathlib import Path

p=Path("src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp")
s=p.read_text()
old=r'''        const auto & matchers = sel->getInstantSelector()->matchers;
        if (matchers.size() != 1
            || matchers[0].label_name != "__name__"
            || matchers[0].matcher_type != PrometheusQueryTree::MatcherType::EQ
            || matchers[0].label_value.empty())
            return {};
        const String & metric = matchers[0].label_value;
        for (char c : metric)
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                  || (c >= '0' && c <= '9') || c == '_' || c == ':'))
                return {};'''
new=r'''        const auto & matchers = sel->getInstantSelector()->matchers;
        if (matchers.empty()
            || matchers[0].label_name != "__name__"
            || matchers[0].matcher_type != PrometheusQueryTree::MatcherType::EQ
            || matchers[0].label_value.empty())
            return {};
        const String & metric = matchers[0].label_value;
        for (char c : metric)
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                  || (c >= '0' && c <= '9') || c == '_' || c == ':'))
                return {};

        /// Avoid SQL literal interpolation for arbitrary PromQL labels; the
        /// narrow lab path accepts only unambiguous safe ASCII literals.
        /// In particular, absent/empty, negative and regexp matching must
        /// retain the normal PromQL translator without any reinterpretation.
        const auto safe_literal = [](const String & text)
        {
            if (text.empty()) return false;
            for (char c : text)
                if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                      || (c >= '0' && c <= '9') || c == '_' || c == '-'
                      || c == ':' || c == '.'))
                    return false;
            return true;
        };

        String exact_label_filter;
        for (size_t i = 1; i < matchers.size(); ++i)
        {
            const auto & m = matchers[i];
            if (m.label_name == "__name__"
                || m.matcher_type != PrometheusQueryTree::MatcherType::EQ
                || !safe_literal(m.label_name) || !safe_literal(m.label_value))
                return {};
            exact_label_filter += fmt::format(
                " AND mapContainsKeyValue(tags,'{}','{}')", m.label_name, m.label_value);
        }'''
if s.count(old)!=1:raise RuntimeError(f"matcher guard source changed: {s.count(old)}")
s=s.replace(old,new,1)
old=r'''        /// Duplicate-timestamp semantics:
        /// timeSeriesGroupArray keeps MAX real sample at a timestamp;'''
new=r'''        /// Physical samples are sorted by (id,bucket). For selective,
        /// positive exact matchers, build the id set from the current
        /// owner-managed tags table BEFORE grouping scalar samples. This is
        /// a real cardinality-dependent filter, not a hardcoded label name.
        /// The final JOIN retains the original metric and stage group labels.
        const String id_filter = exact_label_filter.empty() ? String{"1"}
            : fmt::format("s.id IN (SELECT id FROM {} FINAL WHERE metric_name='{}' {})",
                tableName(settings.lab_tags_target_id), metric, exact_label_filter);

        /// Duplicate-timestamp semantics:
        /// timeSeriesGroupArray keeps MAX real sample at a timestamp;'''
if s.count(old)!=1:raise RuntimeError(f"late stage marker missing: {s.count(old)}")
s=s.replace(old,new,1)
old=r'''                    WHERE s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                ),'''
new=r'''                    WHERE s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                      AND {}
                ),'''
if s.count(old)!=1:raise RuntimeError(f"src table template mismatch: {s.count(old)}")
s=s.replace(old,new,1)
old=r'''            tableName(settings.lab_samples_target_id),
            last,start-window,
            start,step,step,points,'''
new=r'''            tableName(settings.lab_samples_target_id),
            last,start-window,id_filter,
            start,step,step,points,'''
if s.count(old)!=1:raise RuntimeError(f"format arguments mismatch: {s.count(old)}")
s=s.replace(old,new,1)
p.write_text(s)
print("LAB_TS_LATE_TAGS_EXACT_LABEL_PREFILTER_CPP_APPLIED",p)
