#!/usr/bin/env python3
"""Laboratory patch: prehash pending TimeSeries tags OUTSIDE exclusive mutex.

Experiment only; default source is unchanged in the fork. This changes no
observable group/ID semantics: the pending list retains the original order,
and the actual deduplication plus ID map insertion remain under the same
exclusive lock. It only moves TagsKey/CityHash work before lock acquisition.
"""
from pathlib import Path

path=Path("src/Interpreters/ContextTimeSeriesTagsCollector.cpp")
s=path.read_text()
before="""    {
        std::lock_guard lock{mutex};
        auto & id_map = getTypedIDMap<IDType>().map;

        for (size_t i = 0; i != num_rows; ++i)
        {
            if (null_map && null_map[i])
                continue;

            if (found_groups[i] != INVALID_GROUP)
                continue;

            Group group = tryAddGroupUnlocked(tags_vector[i]);

            typename HashMap<IDType, Group, IDMapHash>::LookupResult it = nullptr;
            bool inserted = false;
            id_map.emplace(id_getter.get(i), it, inserted);

            if (inserted)
                it->getMapped() = group;
            else if (it->getMapped() != group)
                throwIDWasAddedWithOtherTags(id_data, i, tags_vector[i], groups.at(it->getMapped()));
        }
    }
"""
after="""    /// Precompute the tag hash outside the exclusive lock. Large high-churn
    /// inserts otherwise serialize CityHash over every newly registered tag
    /// set, blocking concurrent readers and tag-registration workers.
    ///
    /// Keep the original row order and do NOT publish a new group outside the
    /// critical section. All map modifications still occur under the lock.
    std::vector<std::pair<size_t, TagsKey>> pending;
    pending.reserve(num_rows_to_store - num_found_groups);
    for (size_t i = 0; i != num_rows; ++i)
    {
        if (null_map && null_map[i])
            continue;
        if (found_groups[i] != INVALID_GROUP)
            continue;
        pending.emplace_back(i, TagsKey{tags_vector[i]});
    }

    {
        std::lock_guard lock{mutex};
        auto & id_map = getTypedIDMap<IDType>().map;

        for (auto & [i, key] : pending)
        {
            Group group = tryAddGroupUnlocked(std::move(key));

            typename HashMap<IDType, Group, IDMapHash>::LookupResult it = nullptr;
            bool inserted = false;
            id_map.emplace(id_getter.get(i), it, inserted);

            if (inserted)
                it->getMapped() = group;
            else if (it->getMapped() != group)
                throwIDWasAddedWithOtherTags(id_data, i, tags_vector[i], groups.at(it->getMapped()));
        }
    }
"""
assert s.count(before)==1,"Pinned ContextTimeSeriesTagsCollector.cpp implementation changed"
path.write_text(s.replace(before,after))
print("TAGCOLLECTOR_PREHASH_OUTSIDE_MUTEX_PATCH_APPLIED",flush=True)
