#!/usr/bin/env python3
"""R38: source-pinned one-line R35 without-aggregation metric-name deferral."""
import argparse, difflib, hashlib
from pathlib import Path
PATH = "src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp"
R35_BLOB = "b231510bfa086d20ef0e5bd4037fbd62897ee943"
OLD = """        if (!isOneArgumentAggregationOperator(parent.operator_name) || parent.without
            || parent.children.size() != 1 || parent.children.front() != node)"""
NEW = """        if (!isOneArgumentAggregationOperator(parent.operator_name)
            || parent.children.size() != 1 || parent.children.front() != node)"""

def patch(raw):
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != R35_BLOB:
        raise ValueError(f"Unexpected input blob {blob}; expected R35 {R35_BLOB}")
    source = raw.decode()
    if source.count(OLD) != 1:
        raise ValueError("R35 with exact without guard not present")
    return source.replace(OLD, NEW, 1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source",default=PATH)
    p.add_argument("--out",required=True)
    p.add_argument("--apply",action="store_true")
    args = p.parse_args()
    path=Path(args.source);raw=path.read_bytes();candidate=patch(raw)
    diff="".join(difflib.unified_diff(raw.decode().splitlines(True),candidate.splitlines(True),fromfile="a/"+PATH,tofile="b/"+PATH))
    target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(diff)
    if args.apply:path.write_text(candidate)
    print("R38_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    print("R38_SOURCE_SHA256",hashlib.sha256(candidate.encode()).hexdigest())
if __name__=="__main__":
    main()
