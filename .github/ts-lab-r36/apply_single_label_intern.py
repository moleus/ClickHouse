#!/usr/bin/env python3
"""Source-pinned R36 low-cardinality single-label projection fast path."""
import argparse, difflib, hashlib
from pathlib import Path

PATH = "src/Interpreters/ContextTimeSeriesTagsCollector.cpp"
BLOB = "a8f83e0830a3fb49293c12793fe68b07b2f0194b"
OLD = """VectorWithMemoryTracking<Group> ContextTimeSeriesTagsCollector::removeAllTagsExcept(const VectorWithMemoryTracking<Group> & groups_, const Strings & tags_to_keep)
{
    return transformTags(groups_, RemoveAllTagsExceptTransformFunc{tags_to_keep});
}"""
NEW = """VectorWithMemoryTracking<Group> ContextTimeSeriesTagsCollector::removeAllTagsExcept(const VectorWithMemoryTracking<Group> & groups_, const Strings & tags_to_keep)
{
    /// R36: Intern the distinct projected label values, not all source labels.
    /// All string_views point into shared tags held by tags_vector for this call.
    constexpr size_t max_interned_values = 4096;
    if (tags_to_keep.size() != 1 || groups_.size() < 128)
        return transformTags(groups_, RemoveAllTagsExceptTransformFunc{tags_to_keep});

    const size_t max_distinct = std::min(max_interned_values, groups_.size() / 8);
    auto tags_vector = getTagsByGroup(groups_);
    std::unordered_map<std::string_view, UInt16> interned;
    interned.reserve(std::min(max_distinct, size_t{256}));
    VectorWithMemoryTracking<TagNamesAndValuesPtr> projected_tags;
    projected_tags.reserve(std::min(max_distinct, size_t{256}));
    VectorWithMemoryTracking<UInt16> indices;
    indices.reserve(groups_.size());
    const String & label = tags_to_keep.front();
    UInt16 missing_index = std::numeric_limits<UInt16>::max();

    for (const auto & tags : tags_vector)
    {
        bool found = false;
        std::string_view value;
        for (const auto & [name, tag_value] : *tags)
        {
            if (name == label)
            {
                value = tag_value;
                found = true;
                break;
            }
        }

        if (!found && missing_index != std::numeric_limits<UInt16>::max())
        {
            indices.push_back(missing_index);
            continue;
        }
        if (found)
        {
            auto it = interned.find(value);
            if (it != interned.end())
            {
                indices.push_back(it->second);
                continue;
            }
        }

        /// Fallback before altering the collector for pathological cardinality.
        if (projected_tags.size() == max_distinct)
            return transformTags(groups_, RemoveAllTagsExceptTransformFunc{tags_to_keep});

        UInt16 index = static_cast<UInt16>(projected_tags.size());
        if (found)
        {
            auto projected = std::make_shared<TagNamesAndValues>();
            projected->emplace_back(label, String{value});
            projected_tags.push_back(std::move(projected));
            interned.emplace(value, index);
        }
        else
        {
            projected_tags.push_back(getTagsByGroup(getGroupForNoTags()));
            missing_index = index;
        }
        indices.push_back(index);
    }

    auto new_groups = getGroupForTags(projected_tags);
    VectorWithMemoryTracking<Group> result;
    result.reserve(indices.size());
    for (UInt16 index : indices)
        result.push_back(new_groups[index]);
    return result;
}"""

def patch(raw):
    got = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if got != BLOB:
        raise ValueError(f"Unexpected source blob {got}; expected {BLOB}")
    source = raw.decode()
    if source.count(OLD) != 1:
        raise ValueError("Expected exactly one removeAllTagsExcept(vector) call site")
    source = source.replace("#include <algorithm>\n", "#include <algorithm>\n#include <limits>\n#include <string_view>\n", 1)
    return source.replace(OLD, NEW, 1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=PATH)
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    raw = Path(args.source).read_bytes()
    modified = patch(raw)
    diff = "".join(difflib.unified_diff(raw.decode().splitlines(True), modified.splitlines(True),
                                      fromfile="a/" + PATH, tofile="b/" + PATH))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff)
    if args.apply:
        Path(args.source).write_text(modified)
    print("R36_PATCH_SHA256", hashlib.sha256(diff.encode()).hexdigest())
    print("R36_SOURCE_SHA256", hashlib.sha256(modified.encode()).hexdigest())

if __name__ == "__main__":
    main()
