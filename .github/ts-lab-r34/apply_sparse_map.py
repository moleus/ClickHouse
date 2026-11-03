#!/usr/bin/env python3
"""Apply only the R34 initial-capacity change to the exact stock header."""
import argparse
import difflib
import hashlib
from pathlib import Path

PATH = "src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h"
BLOB = "3088fa89be56922ea6bcb11f6cd7a2cd1af3311e"
OLD = """struct TimeSeriesBucketsHashTableGrower : public HashTableGrower<4>
{
    size_t maxFill() const { return bufSize() / 4 * 3; }
    bool overflow(size_t elems) const { return elems > maxFill(); }

    void increaseSize()
    {
        ++size_degree;
    }

    /// `reserve` sizes the buffer here, so it follows the same 3/4 limit.
    void set(size_t num_elems)
    {
        size_degree = 4;
        while (overflow(num_elems))
            ++size_degree;
    }
};"""
NEW = OLD.replace("HashTableGrower<4>", "HashTableGrower<1>").replace(
    "return bufSize() / 4 * 3;", "return bufSize() / 2 + bufSize() / 4;").replace(
    "size_degree = 4;", "size_degree = 1;")
NEW = ("/// Start with two cells for sparse series. Keep one cell empty at capacity two.\n"
       "/// Larger maps retain the existing 3/4 fill limit.\n" + NEW)


def patch(raw):
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != BLOB:
        raise ValueError(f"Unexpected source blob {actual}; expected {BLOB}")
    text = raw.decode()
    if text.count(OLD) != 1:
        raise ValueError("Expected exactly one original grower")
    return text.replace(OLD, NEW, 1)


def self_test():
    # Check initial sizing, strict empty-slot invariant, and unchanged larger maps.
    for degree in range(1, 64):
        capacity = 1 << degree
        limit = capacity // 2 + capacity // 4
        assert 0 < limit < capacity
        if degree >= 2:
            assert limit == capacity // 4 * 3
    for count in (0, 1, 2, 3, 4, 6, 7, 12, 13, 1000, 1000000):
        capacity = 2
        while count > capacity // 2 + capacity // 4:
            capacity *= 2
        assert count <= capacity // 2 + capacity // 4
        assert count < capacity
    assert NEW.count("HashTableGrower<1>") == 1
    assert "size_degree = 4;" not in NEW


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=PATH)
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    self_test()
    source = Path(args.source)
    raw = source.read_bytes()
    modified = patch(raw)
    diff = "".join(difflib.unified_diff(raw.decode().splitlines(True), modified.splitlines(True),
                                     fromfile="a/" + PATH, tofile="b/" + PATH))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff)
    if args.apply:
        source.write_text(modified)
    print("R34_PATCH_SHA256", hashlib.sha256(diff.encode()).hexdigest())
    print("R34_SOURCE_SHA256", hashlib.sha256(modified.encode()).hexdigest())


if __name__ == "__main__":
    main()
