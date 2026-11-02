#!/usr/bin/env python3
"""Narrow lab-only patch: preserve default, allow explicitly supplied JOIN settings.

The native TimeSeries selector normally overrides join_algorithm and
optimize_read_in_order in a copied Context. This prevents experiments that
compare native full PromQL with other JOIN plans. Default behavior is unchanged.
"""
from pathlib import Path

path = Path("src/Storages/StorageTimeSeriesSelector.cpp")
text = path.read_text()
before = '''    context.setSetting("join_algorithm", String{"full_sorting_merge"});
    context.setSetting("optimize_read_in_order", true);'''
after = '''    if (!context.getSettingsRef().isChanged("join_algorithm"))
        context.setSetting("join_algorithm", String{"full_sorting_merge"});
    if (!context.getSettingsRef().isChanged("optimize_read_in_order"))
        context.setSetting("optimize_read_in_order", true);'''
assert text.count(before) == 1, "Expected pinned selector logic exactly once"
path.write_text(text.replace(before, after))
print("JOIN_OVERRIDE_PATCH_APPLIED", path, flush=True)
