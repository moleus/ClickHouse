#!/usr/bin/env python3
"""R30 experimental source patch, NOT a promoted ClickHouse default.

Apply only in a fresh pinned-source benchmark checkout after SQL and
full native semantic gates. Never use this on the shared benchmark source.
"""
import argparse
import difflib
import hashlib
import pathlib

BEGIN = "void addTimeRangesTableJoin("
END = "void applyTimeRangeFilterToSelectIDsQuery("
OLD = "table_join->strictness = JoinStrictness::All;"
NEW = ("/// R30 LAB: right rows were filtered by the requested time range and\n"
       "            /// every matching right row has matched=1. Only membership matters\n"
       "            /// to SAMPLES id IN; prevent multiplicative duplicate outputs.\n"
       "            table_join->strictness = JoinStrictness::Any;")


def patch(source):
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise ValueError("TimeRanges source function boundaries changed")
    lo=source.index(BEGIN)
    hi=source.index(END)
    if not (0 <= lo < hi) or source[lo:hi].count(OLD) != 1:
        raise ValueError("Expected exactly one ALL strictness inside TimeRanges selector")
    segment=source[lo:hi]
    if "table_join->kind = JoinKind::Left;" not in segment or "matched_column" not in segment:
        raise ValueError("Expected LEFT matched membership join")
    return source[:lo]+segment.replace(OLD,NEW,1)+source[hi:]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",required=True)
    p.add_argument("--apply",action="store_true",help="Modify specified isolated checkout source; default is dry-run")
    p.add_argument("--out",help="Write unified diff")
    a=p.parse_args()
    target=pathlib.Path(a.source)
    before=target.read_text()
    after=patch(before)
    delta="".join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                                     fromfile="stock/"+target.name,tofile="r30/"+target.name))
    if not delta: raise ValueError("No patch generated")
    if a.out: pathlib.Path(a.out).write_text(delta)
    print("R30_PATCH",{"before_sha256":hashlib.sha256(before.encode()).hexdigest(),
                       "after_sha256":hashlib.sha256(after.encode()).hexdigest(),
                       "lines_removed":sum(x.startswith("-") and not x.startswith("---") for x in delta.splitlines()),
                       "lines_added":sum(x.startswith("+") and not x.startswith("+++") for x in delta.splitlines()),
                       "applied":a.apply})
    if a.apply: target.write_text(after)


if __name__=="__main__":main()
