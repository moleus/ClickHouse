#!/usr/bin/env python3
"""R42: specialize root-only serialization of tags without __name__.

Apply after R35+R37+R40+R41 on the exact source pin.
"""
import argparse, difflib, hashlib
from pathlib import Path

FINALIZER = "src/Storages/TimeSeries/PrometheusQueryToSQL/finalizeSQL.cpp"
GROUP_TAGS = "src/Functions/TimeSeries/timeSeriesGroupToTags.cpp"
BLOBS = {
    FINALIZER: "45d94bea0a74b5f54916931b575b817668974477",
    GROUP_TAGS: "d75dacf9db8c8a71319b1615a1d07e2c9f553c9f",
}

OLD_FINAL = '''    ASTPtr outputGroupTags(const SQLQueryPiece & result)
    {
        auto tags = makeASTFunction("timeSeriesGroupToTags", make_intrusive<ASTIdentifier>(ColumnNames::Group));
        if (!result.metric_name_deferred_to_output)
            return tags;

        auto pair_name = make_intrusive<ASTIdentifier>("tag_pair");
        auto name = makeASTFunction("tupleElement", std::move(pair_name), make_intrusive<ASTLiteral>(1u));
        auto predicate = makeASTFunction("notEquals", std::move(name), make_intrusive<ASTLiteral>("__name__"));
        auto lambda = makeASTFunction(
            "lambda", makeASTFunction("tuple", make_intrusive<ASTIdentifier>("tag_pair")), std::move(predicate));
        return makeASTFunction("arrayFilter", std::move(lambda), std::move(tags));
    }'''
NEW_FINAL = '''    ASTPtr outputGroupTags(const SQLQueryPiece & result)
    {
        if (result.metric_name_deferred_to_output)
            return makeASTFunction("timeSeriesGroupToTagsWithoutName", make_intrusive<ASTIdentifier>(ColumnNames::Group));
        return makeASTFunction("timeSeriesGroupToTags", make_intrusive<ASTIdentifier>(ColumnNames::Group));
    }'''
FUNC = r'''
/// R42: Direct output-only serializer for a root range query whose exact
/// metric name is proven constant. Preserve the original registered group ID
/// while skipping __name__ during output column construction: no temporary
/// full label arrays or arrayFilter allocation.
class FunctionTimeSeriesGroupToTagsWithoutName final : public IFunction
{
public:
    static constexpr auto name = "timeSeriesGroupToTagsWithoutName";

    static FunctionPtr create(ContextPtr context) { return std::make_shared<FunctionTimeSeriesGroupToTagsWithoutName>(context); }

    explicit FunctionTimeSeriesGroupToTagsWithoutName(ContextPtr context)
        : tags_collector(context->getQueryContext()->getTimeSeriesTagsCollector()) {}

    String getName() const override { return name; }

    size_t getNumberOfArguments() const override { return 1; }
    bool isDeterministic() const override { return false; }
    bool isDeterministicInScopeOfQuery() const override { return true; }
    bool isStateful() const override { return true; }
    bool isSuitableForConstantFolding() const override { return false; }
    bool isSuitableForShortCircuitArgumentsExecution(const DataTypesWithConstInfo &) const override { return true; }

    String getSignatureString() const override { return "(UInt64) -> Array(Tuple(String, String))"; }

    DataTypePtr getReturnTypeImpl(const ColumnsWithTypeAndName & arguments) const override
    {
        FunctionTimeSeriesGroupToTags::checkArgumentTypes(arguments);
        return std::make_shared<DataTypeArray>(
            std::make_shared<DataTypeTuple>(
                DataTypes{std::make_shared<DataTypeString>(), std::make_shared<DataTypeString>()}));
    }

    ColumnPtr executeImpl(const ColumnsWithTypeAndName & arguments, const DataTypePtr &, size_t input_rows_count) const override
    {
        const auto groups = TimeSeriesTagsFunctionHelpers::extractGroupFromArgument(name, arguments, 0);
        const auto tags = tags_collector->getTagsByGroup(groups);
        chassert(tags.size() == input_rows_count);

        auto names = ColumnString::create();
        auto values = ColumnString::create();
        auto offsets = ColumnArray::ColumnOffsets::create();

        size_t retained_count = 0;
        for (const auto & row : tags)
        {
            for (const auto & [key, value] : *row)
                retained_count += (key != "__name__");
        }
        names->reserve(retained_count);
        values->reserve(retained_count);
        offsets->reserve(input_rows_count);

        for (const auto & row : tags)
        {
            for (const auto & [key, value] : *row)
            {
                if (key == "__name__")
                    continue;
                names->insertData(key.data(), key.size());
                values->insertData(value.data(), value.size());
            }
            offsets->insertValue(names->size());
        }

        MutableColumns columns;
        columns.reserve(2);
        columns.push_back(std::move(names));
        columns.push_back(std::move(values));
        return ColumnArray::create(ColumnTuple::create(std::move(columns)), std::move(offsets));
    }

private:
    std::shared_ptr<const ContextTimeSeriesTagsCollector> tags_collector;
};

'''
ADD_INCLUDE = '''#include <Functions/FunctionFactory.h>

#include <Columns/ColumnArray.h>
#include <Columns/ColumnString.h>
#include <Columns/ColumnTuple.h>
'''
ADD_REGISTER = '''    factory.registerFunction<FunctionTimeSeriesGroupToTags>(documentation);

    FunctionDocumentation::Description direct_description = R"(
Returns the registered group tags without __name__, for use by the native PromQL root finalizer.
This function does not register a new group and reads only the current query's tags collector.
)";
    FunctionDocumentation::Syntax direct_syntax = "timeSeriesGroupToTagsWithoutName(group)";
    FunctionDocumentation direct_documentation =
        {direct_description, direct_syntax, arguments, {}, returned_value, {}, introduced_in, category};
    factory.registerFunction<FunctionTimeSeriesGroupToTagsWithoutName>(direct_documentation);
'''
def blob(b):
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()

def repl(s,old,new,what):
    if s.count(old)!=1:
        raise ValueError(f"{what}: expected one anchor, found {s.count(old)}")
    return s.replace(old,new,1)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default=".")
    parser.add_argument("--out",required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    root=Path(args.root)
    inp={}
    for path,expected in BLOBS.items():
        raw=(root/path).read_bytes()
        got=blob(raw)
        if got != expected:
            raise ValueError(f"{path}: expected {expected}, got {got}")
        inp[path]=raw.decode()
    out={}
    out[FINALIZER]=repl(inp[FINALIZER],OLD_FINAL,NEW_FINAL,FINALIZER)
    s=repl(inp[GROUP_TAGS],"#include <Functions/FunctionFactory.h>\n",ADD_INCLUDE,"include")
    s=repl(s,"\n\nREGISTER_FUNCTION(TimeSeriesGroupToTags)", "\n"+FUNC+"\nREGISTER_FUNCTION(TimeSeriesGroupToTags)","class")
    s=repl(s,"    factory.registerFunction<FunctionTimeSeriesGroupToTags>(documentation);\n",ADD_REGISTER,"register")
    out[GROUP_TAGS]=s
    diff="".join("".join(difflib.unified_diff(inp[path].splitlines(True),out[path].splitlines(True),
                                 fromfile="a/"+path,tofile="b/"+path)) for path in (FINALIZER,GROUP_TAGS))
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(diff)
    if args.apply:
        for p,v in out.items():
            (root/p).write_text(v)
    print("R42_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    for p,v in out.items():
        print("R42_BLOB",p,blob(v.encode()))
if __name__=="__main__":
    main()
