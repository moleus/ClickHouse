#!/usr/bin/env python3
"""R39 source-pinned collision-safe bounded projected tag interning for without."""
import argparse
import difflib
import hashlib
from pathlib import Path

PATH = "src/Interpreters/ContextTimeSeriesTagsCollector.cpp"
SOURCE_BLOB = "a8f83e0830a3fb49293c12793fe68b07b2f0194b"
OLD = """VectorWithMemoryTracking<Group> ContextTimeSeriesTagsCollector::removeTags(const VectorWithMemoryTracking<Group> & groups_, const Strings & tags_to_remove)
{
    return transformTags(groups_, RemoveTagsTransformFunc{tags_to_remove});
}"""
NEW = r'''VectorWithMemoryTracking<Group> ContextTimeSeriesTagsCollector::removeTags(const VectorWithMemoryTracking<Group> & groups_, const Strings & tags_to_remove)
{
    /// R39: For a large batch with few distinct projected groups, reuse each
    /// projected tag set rather than creating/copying one per source group.
    /// Fall back to the original implementation before any collector mutation.
    constexpr size_t min_batch_size = 16384;
    constexpr size_t max_projected_groups = 4096;
    if (groups_.size() < min_batch_size || tags_to_remove.empty())
        return transformTags(groups_, RemoveTagsTransformFunc{tags_to_remove});

    const size_t max_distinct = std::min(max_projected_groups, groups_.size() / 8);
    std::unordered_set<std::string_view> removed;
    removed.reserve(tags_to_remove.size());
    for (const auto & name : tags_to_remove)
        removed.emplace(name);

    /// The shared pointers retain the source labels for the whole call.
    const auto source_tags = getTagsByGroup(groups_);
    VectorWithMemoryTracking<TagNamesAndValuesPtr> distinct_projected;
    distinct_projected.reserve(std::min(max_distinct, size_t{256}));
    VectorWithMemoryTracking<UInt16> indices;
    indices.reserve(groups_.size());
    std::unordered_multimap<UInt64, UInt16> projected_by_hash;
    projected_by_hash.reserve(std::min(max_distinct, size_t{256}));

    for (const auto & tags : source_tags)
    {
        UInt64 hash = 0;
        for (const auto & [name, value] : *tags)
        {
            if (removed.contains(name))
                continue;
            /// Identical to TagsKey's ordered tag hash, with removed keys skipped.
            hash = CityHash_v1_0_2::CityHash64WithSeed(name.data(), name.size(), hash);
            hash = CityHash_v1_0_2::CityHash64WithSeed(value.data(), value.size(), hash);
        }

        /// Hash collisions are possible: accept an existing projected group
        /// only after a full ordered name/value comparison.
        bool reused = false;
        auto [first, last] = projected_by_hash.equal_range(hash);
        for (auto it = first; it != last; ++it)
        {
            const auto & projected = *distinct_projected[it->second];
            size_t pos = 0;
            bool equal = true;
            for (const auto & original : *tags)
            {
                if (removed.contains(original.first))
                    continue;
                if (pos == projected.size() || projected[pos] != original)
                {
                    equal = false;
                    break;
                }
                ++pos;
            }
            if (equal && pos == projected.size())
            {
                indices.push_back(it->second);
                reused = true;
                break;
            }
        }
        if (reused)
            continue;

        if (distinct_projected.size() == max_distinct)
            return transformTags(groups_, RemoveTagsTransformFunc{tags_to_remove});

        auto projected = std::make_shared<TagNamesAndValues>();
        projected->reserve(tags->size());
        for (const auto & name_value : *tags)
            if (!removed.contains(name_value.first))
                projected->push_back(name_value);

        const UInt16 index = static_cast<UInt16>(distinct_projected.size());
        distinct_projected.emplace_back(std::move(projected));
        projected_by_hash.emplace(hash, index);
        indices.push_back(index);
    }

    auto registered = getGroupForTags(distinct_projected);
    VectorWithMemoryTracking<Group> res;
    res.reserve(indices.size());
    for (auto index : indices)
        res.push_back(registered[index]);
    return res;
}'''

def patch(raw):
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != SOURCE_BLOB:
        raise ValueError(f"Unexpected original collector blob {blob} (expected {SOURCE_BLOB})")
    source = raw.decode()
    if source.count(OLD) != 1:
        raise ValueError("Expected exactly one vector removeTags call site")
    source = source.replace("#include <algorithm>\n", "#include <algorithm>\n#include <unordered_map>\n#include <unordered_set>\n#include <string_view>\n", 1)
    return source.replace(OLD, NEW, 1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=PATH)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = Path(args.source)
    raw = source.read_bytes()
    candidate = patch(raw)
    diff = "".join(difflib.unified_diff(
        raw.decode().splitlines(True), candidate.splitlines(True),
        fromfile="a/" + PATH, tofile="b/" + PATH))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(diff)
    if args.apply:
        source.write_text(candidate)
    print("R39_PATCH_SHA256", hashlib.sha256(diff.encode()).hexdigest())
    print("R39_SOURCE_SHA256", hashlib.sha256(candidate.encode()).hexdigest())

if __name__ == "__main__":
    main()
