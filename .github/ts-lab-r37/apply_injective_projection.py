#!/usr/bin/env python3
"""R37: source-pinned injective metric-name projection after the validated R35 patch."""
import argparse
import difflib
import hashlib
from pathlib import Path

PATH = "src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp"
# The input MUST be the patched R35 source, not the unmodified ClickHouse source.
R35_BLOB = "b231510bfa086d20ef0e5bd4037fbd62897ee943"

HELPER = r'''    /// R37: The range function's grouping is unique in the original full tag set.
    /// If __name__ has the same exact value in every selected series, deleting it
    /// cannot merge distinct groups. This does not require an immediate parent.
    bool canProjectMetricNameInjectively(const Node * node)
    {
        using QueryTree = PrometheusQueryTree;
        if (!node || node->node_type != QueryTree::NodeType::Function || node->children.size() != 1)
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

    /// A row-preserving projection; unlike dropMetricName(), there is no
    /// anyForEach/countForEach GROUP BY because uniqueness is proven above.
    /// Use new_group in a separate SELECT to avoid alias/column ambiguity.
    SQLQueryPiece projectMetricNameInjectively(SQLQueryPiece && piece, ConverterContext & context)
    {
        SelectQueryBuilder projection;

        projection.select_list.push_back(makeASTFunction(
            "timeSeriesRemoveTag",
            make_intrusive<ASTIdentifier>(ColumnNames::Group),
            make_intrusive<ASTLiteral>("__name__")));
        projection.select_list.back()->setAlias(ColumnNames::NewGroup);
        projection.select_list.push_back(make_intrusive<ASTIdentifier>(ColumnNames::Values));

        context.subqueries.emplace_back(context.subqueries.size(), std::move(piece.select_query), SQLSubqueryType::TABLE);
        projection.from_table = context.subqueries.back().name;

        SelectQueryBuilder renaming;
        renaming.select_list.push_back(make_intrusive<ASTIdentifier>(ColumnNames::NewGroup));
        renaming.select_list.back()->setAlias(ColumnNames::Group);
        renaming.select_list.push_back(make_intrusive<ASTIdentifier>(ColumnNames::Values));

        context.subqueries.emplace_back(context.subqueries.size(), projection.getSelectQuery(), SQLSubqueryType::TABLE);
        renaming.from_table = context.subqueries.back().name;

        piece.select_query = renaming.getSelectQuery();
        piece.metric_name_dropped = true;
        return piece;
    }

'''
ANCHOR = "    /// Checks if the types of the specified arguments are valid for the function."
OLD = '''        if (!canDeferMetricNameRemoval(node))
            res = dropMetricName(std::move(res), context);'''
NEW = '''        if (canDeferMetricNameRemoval(node))
        {
            /// R35: the immediate parent removes the constant metric name.
        }
        else if (canProjectMetricNameInjectively(node))
        {
            /// R37: remove the constant name without a second aggregation.
            res = projectMetricNameInjectively(std::move(res), context);
        }
        else
        {
            res = dropMetricName(std::move(res), context);
        }'''

def patch(raw):
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != R35_BLOB:
        raise ValueError(f"Unexpected source blob {blob}; expected R35 {R35_BLOB}")
    original = raw.decode()
    if original.count(ANCHOR) != 1 or original.count(OLD) != 1:
        raise ValueError("R35 source anchor or expected call site missing")
    result = original.replace(ANCHOR, HELPER + ANCHOR, 1).replace(OLD, NEW, 1)
    assert result.count("projectMetricNameInjectively(") == 2
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=PATH)
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    source = Path(args.source)
    raw = source.read_bytes()
    result = patch(raw)
    diff = "".join(difflib.unified_diff(
        raw.decode().splitlines(True), result.splitlines(True),
        fromfile="a/" + PATH, tofile="b/" + PATH))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff)
    if args.apply:
        source.write_text(result)
    print("R37_PATCH_SHA256", hashlib.sha256(diff.encode()).hexdigest())
    print("R37_SOURCE_SHA256", hashlib.sha256(result.encode()).hexdigest())

if __name__ == "__main__":
    main()
