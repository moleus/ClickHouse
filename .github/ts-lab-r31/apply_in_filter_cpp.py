#!/usr/bin/env python3
"""R31 source-pinned isolated LAB C++ patch: TimeRanges JOIN -> IN filter.

Only run in a separate copy of ClickHouse at pinned source SHA
52529f0c711566d696917678bf15e7068772fdde, never against master or a
production checkout. This currently changes the lab binary unconditionally;
a safe per-query opt-in and complete E06/R08 oracle are required for promotion.

Default mode is dry-run and generates the exact unified diff. --apply writes
to the explicitly supplied checkout only after invariant checks.
"""
import argparse
import difflib
import hashlib
import pathlib

BEGIN="void addTimeRangesTableJoin("
END="void applyTimeRangeFilterToSelectIDsQuery("
RIGHT_START="        /// LEFT JOIN (<subquery>) AS time_ranges_subquery USING (id)\n"
RIGHT_END="        /// WHERE ... AND (time_ranges_subquery.matched = 1 [OR <id is outside all of metric_id_ranges>])\n"
MATCHED=(
    "            select_list_exp->children.push_back(make_intrusive<ASTLiteral>(UInt64{1}));\n"
    "            select_list_exp->children.back()->setAlias(matched_column);\n"
)
OLD_CONDITION=(
    "            ASTPtr condition = makeASTFunction(\n"
    '                "equals", make_intrusive<ASTIdentifier>(std::vector<String>{subquery_alias, matched_column}), make_intrusive<ASTLiteral>(UInt64{1}));'
)
NEW_CONDITION=(
    "            /// R31 LAB ONLY: equivalent membership after filtering the right TimeRanges rows.\n"
    "            /// The unchanged OR branch still preserves IDs outside metric ranges.\n"
    '            ASTPtr condition = makeASTFunction(\n'
    '                "in", id->clone(), make_intrusive<ASTSubquery>(wrapIntoSelectWithUnionQuery(std::move(subquery_select))));'
)


def rewrite_cpp(source):
    if source.count(BEGIN)!=1 or source.count(END)!=1:
        raise ValueError("Native source function boundaries changed")
    lo=source.index(BEGIN)
    hi=source.index(END)
    if lo>=hi:raise ValueError("Wrong native function order")
    section=source[lo:hi]
    for required in (
        '        const char * subquery_alias = "time_ranges_subquery";\n',
        '        const char * matched_column = "matched";\n',
        RIGHT_START,RIGHT_END,MATCHED,OLD_CONDITION,
        '            subquery_select->setExpression(ASTSelectQuery::Expression::WHERE,',
        '                condition = makeASTForLogicalOr({std::move(condition), makeIDOutOfRangesCondition(id, *metric_id_ranges)});',
    ):
        if section.count(required)!=1:
            raise ValueError("Source mismatch for R31 AST rewrite: "+required[:110])
    section=section.replace('        const char * subquery_alias = "time_ranges_subquery";\n',"")
    section=section.replace('        const char * matched_column = "matched";\n',"")
    section=section.replace(MATCHED,"")
    begin=section.index(RIGHT_START)
    end=section.index(RIGHT_END)
    if begin>=end: raise ValueError("Join block not isolated")
    join_block=section[begin:end]
    if join_block.count("JoinStrictness::All")!=1:
        raise ValueError("Expected one left ALL join strictness")
    section=section[:begin]+section[end:]
    section=section.replace(
        RIGHT_END,
        "        /// WHERE ... AND (id IN matching TimeRanges IDs OR outside metric-ID ranges)\\n",
        1)
    section=section.replace(OLD_CONDITION,NEW_CONDITION)
    if "time_ranges_subquery" in section or "JoinStrictness::All" in section:
        raise ValueError("Residual JOIN alias or strictness in rewritten function")
    if section.count('"in", id->clone()')!=1:
        raise ValueError("Missing new SQL IN AST")
    return source[:lo]+section+source[hi:]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source",required=True)
    ap.add_argument("--out")
    ap.add_argument("--apply",action="store_true")
    args=ap.parse_args()
    path=pathlib.Path(args.source)
    before=path.read_text()
    after=rewrite_cpp(before)
    diff="".join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
        fromfile="stock/"+path.name,tofile="r31-lab/"+path.name))
    if not diff:raise ValueError("No C++ change")
    if args.out:pathlib.Path(args.out).write_text(diff)
    print("R31_CPP_LAB_PATCH",{"applied":args.apply,
          "before_sha256":hashlib.sha256(before.encode()).hexdigest(),
          "after_sha256":hashlib.sha256(after.encode()).hexdigest(),
          "removed_lines":sum(x.startswith("-") and not x.startswith("---") for x in diff.splitlines()),
          "added_lines":sum(x.startswith("+") and not x.startswith("+++") for x in diff.splitlines())})
    if args.apply:path.write_text(after)


if __name__=="__main__": main()
