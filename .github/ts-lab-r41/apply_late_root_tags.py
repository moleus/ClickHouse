#!/usr/bin/env python3
"""R41: late __name__ projection only for a root, exact-metric native range function.

Run R35, R37, R40 patch helpers first. R41 changes three C++ files, no storage
layout, range aggregate, tag collector, selector or group aggregation behavior.
"""
import argparse
import difflib
import hashlib
from pathlib import Path

COMPILER = "src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp"
PIECE = "src/Storages/TimeSeries/PrometheusQueryToSQL/SQLQueryPiece.h"
FINALIZER = "src/Storages/TimeSeries/PrometheusQueryToSQL/finalizeSQL.cpp"
EXPECTED_BLOBS = {
    COMPILER: "0c835443a5d63f5dae6d2df02be462697a1ed2e5",
    PIECE: "310deb3cce6b6af5a33a8bec9e6b7ed4946b7234",
    FINALIZER: "8f0a2d83dafdc023906f89dc745c97de445fddeb",
}
ROOT_OLD = '''        else if (canProjectMetricNameInjectively(node))
        {
            /// R37: remove the constant name without a second aggregation.
            res = projectMetricNameInjectively(std::move(res), context);
        }'''
ROOT_NEW = '''        else if (!node->parent && canProjectMetricNameInjectively(node))
        {
            /// R41: root output consumes tags, not group IDs. Keep original groups
            /// until final output, then strip the constant name once per result.
            /// This avoids registering N transient projected group IDs.
            res.metric_name_deferred_to_output = true;
        }
        else if (canProjectMetricNameInjectively(node))
        {
            /// R37: intermediate expressions still require projected group IDs.
            res = projectMetricNameInjectively(std::move(res), context);
        }'''
PIECE_OLD = "    bool metric_name_dropped = false;\n"
PIECE_NEW = '''    bool metric_name_dropped = false;

    /// R41: A root exact-metric range expression may leave __name__ in group IDs
    /// and strip it only from serialized output tags. Other nodes never set this.
    bool metric_name_deferred_to_output = false;
'''
FINALIZER_ANCHOR = '''namespace
{
    [[noreturn]] void throwCannotFinalize'''
FINALIZER_HELPER = '''namespace
{
    /// R41: root range functions do not need to register projected group IDs.
    /// Their results are unique after deleting one constant metric name.
    /// Filter output labels after looking up the original group's tags.
    ASTPtr outputGroupTags(const SQLQueryPiece & result)
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
    }

    [[noreturn]] void throwCannotFinalize'''
TAG_EXPRESSION = 'tags = makeASTFunction("timeSeriesGroupToTags", make_intrusive<ASTIdentifier>(ColumnNames::Group));'

def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def replace_once(text, old, new, where):
    if text.count(old) != 1:
        raise ValueError(f"{where}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)

def patch(src):
    out = {}
    compiler = src[COMPILER]
    out[COMPILER] = replace_once(compiler, ROOT_OLD, ROOT_NEW, COMPILER)
    piece = src[PIECE]
    out[PIECE] = replace_once(piece, PIECE_OLD, PIECE_NEW, PIECE)
    finalizer = replace_once(src[FINALIZER], FINALIZER_ANCHOR, FINALIZER_HELPER, FINALIZER)
    if finalizer.count(TAG_EXPRESSION) != 4:
        raise ValueError(f"Expected four finalizer tag expressions, found {finalizer.count(TAG_EXPRESSION)}")
    finalizer = finalizer.replace(TAG_EXPRESSION, 'tags = outputGroupTags(result);')
    out[FINALIZER] = finalizer
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",default=".")
    p.add_argument("--out",required=True)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args()
    root=Path(args.root)
    originals={}
    for file, expected in EXPECTED_BLOBS.items():
        raw=(root/file).read_bytes()
        got=git_blob(raw)
        if got != expected:
            raise ValueError(f"{file}: unexpected blob {got}; expected {expected}")
        originals[file]=raw.decode()
    results=patch(originals)
    patch_path=Path(args.out)
    patch_path.parent.mkdir(parents=True,exist_ok=True)
    diff="".join(difflib.unified_diff(originals[file].splitlines(True),results[file].splitlines(True),
                                     fromfile="a/"+file,tofile="b/"+file)
                 for file in (COMPILER,PIECE,FINALIZER))
    patch_path.write_text(diff)
    if args.apply:
        for file, value in results.items():
            (root/file).write_text(value)
    print("R41_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    for file,value in results.items():
        print("R41_BLOB",file,git_blob(value.encode()))

if __name__=="__main__":
    main()
