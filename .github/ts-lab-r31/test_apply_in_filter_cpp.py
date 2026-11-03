#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from apply_in_filter_cpp import rewrite_cpp

NATIVE=(
    "void addTimeRangesTableJoin(\n"
    "    ASTSelectQuery & select_query)\n"
    "{\n"
    '        const char * subquery_alias = "time_ranges_subquery";\n'
    '        const char * matched_column = "matched";\n'
    "        ASTPtr id = make_intrusive<ASTIdentifier>(TimeSeriesColumnNames::ID);\n"
    "        auto subquery_select = make_intrusive<ASTSelectQuery>();\n"
    "        {\n"
    "            auto select_list_exp = make_intrusive<ASTExpressionList>();\n"
    "            select_list_exp->children.push_back(id->clone());\n"
    "            select_list_exp->children.push_back(make_intrusive<ASTLiteral>(UInt64{1}));\n"
    "            select_list_exp->children.back()->setAlias(matched_column);\n"
    "            subquery_select->setExpression(ASTSelectQuery::Expression::SELECT, select_list_exp);\n"
    "            subquery_select->setExpression(ASTSelectQuery::Expression::WHERE, foo);\n"
    "        }\n"
    "        /// LEFT JOIN (<subquery>) AS time_ranges_subquery USING (id)\n"
    "        {\n"
    "            auto table_join = make_intrusive<ASTTableJoin>();\n"
    "            table_join->strictness = JoinStrictness::All;\n"
    "        }\n"
    "        /// WHERE ... AND (time_ranges_subquery.matched = 1 [OR <id is outside all of metric_id_ranges>])\n"
    "        {\n"
    "            ASTPtr condition = makeASTFunction(\n"
    '                "equals", make_intrusive<ASTIdentifier>(std::vector<String>{subquery_alias, matched_column}), make_intrusive<ASTLiteral>(UInt64{1}));\n'
    "            if (metric_id_ranges)\n"
    "                condition = makeASTForLogicalOr({std::move(condition), makeIDOutOfRangesCondition(id, *metric_id_ranges)});\n"
    "            select_query.setExpression(ASTSelectQuery::Expression::WHERE, std::move(condition));\n"
    "        }\n"
    "}\n"
    "void applyTimeRangeFilterToSelectIDsQuery(\n"
    "    ASTSelectQuery & select_query)\n"
    "{\n"
    "  /// unrelated source remains unchanged\n"
    "}\n"
)

class R31CppPatchTests(unittest.TestCase):
    def test_rewrite_one_function_and_preserve_escape(self):
        patched=rewrite_cpp(NATIVE)
        self.assertEqual(patched.count('"in", id->clone()'),1)
        self.assertNotIn("time_ranges_subquery",patched)
        self.assertNotIn("JoinStrictness::All",patched)
        self.assertIn("makeIDOutOfRangesCondition(id, *metric_id_ranges)",patched)
        self.assertIn("void applyTimeRangeFilterToSelectIDsQuery(",patched)
        self.assertIn("unrelated source remains unchanged",patched)

    def test_double_patch_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"Source mismatch"):
            rewrite_cpp(rewrite_cpp(NATIVE))

    def test_unexpected_right_hand_projection_fails(self):
        changed=NATIVE.replace("UInt64{1}","UInt64{2}",1)
        with self.assertRaisesRegex(ValueError,"Source mismatch"):
            rewrite_cpp(changed)

    def test_no_function_boundary(self):
        with self.assertRaisesRegex(ValueError,"boundaries"):
            rewrite_cpp(NATIVE.replace("void applyTimeRangeFilterToSelectIDsQuery(","void changedName("))


if __name__=="__main__":unittest.main()
