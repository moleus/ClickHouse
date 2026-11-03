import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from apply_any_join_cpp import patch


SRC=("""
namespace DB {
void addTimeRangesTableJoin(
    ASTSelectQuery & select_query,
    const StorageID & time_ranges_table_id)
{
    auto matched_column = "matched";
    table_join->kind = JoinKind::Left;
    table_join->strictness = JoinStrictness::All;
}
void applyTimeRangeFilterToSelectIDsQuery(
    ASTSelectQuery & select_query)
{
    table_join->strictness = JoinStrictness::All;
}
}
""")


class R30CppPatch(unittest.TestCase):
    def test_one_site_only(self):
        new=patch(SRC)
        self.assertEqual(new.count("JoinStrictness::Any;"),1)
        self.assertEqual(new.count("JoinStrictness::All;"),1)
        self.assertIn("every matching right row has matched=1",new)
        self.assertIn("void applyTimeRangeFilterToSelectIDsQuery",new)
    def test_no_double_patch(self):
        with self.assertRaisesRegex(ValueError,"exactly one"):
            patch(patch(SRC))
    def test_missing_left_kind(self):
        with self.assertRaisesRegex(ValueError,"Expected LEFT"):
            patch(SRC.replace("JoinKind::Left","JoinKind::Inner"))
    def test_moved_function_rejected(self):
        with self.assertRaisesRegex(ValueError,"boundaries"):
            patch(SRC.replace("void applyTimeRangeFilterToSelectIDsQuery","void someOtherFunction"))


if __name__=="__main__":unittest.main()
