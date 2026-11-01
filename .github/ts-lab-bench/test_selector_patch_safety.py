#!/usr/bin/env python3
"""Fail-closed regression check for the experimental v11 selector patch helper."""
import hashlib
import pathlib
import subprocess
import sys
import tempfile

REPO=pathlib.Path(__file__).resolve().parents[2]
PATCH=REPO/'.github/ts-lab-pending/apply_selector_exact_no_timeranges_patch.py'
OLD="""    const StorageID time_ranges_table_id = time_series_storage->hasTarget(ViewTarget::TimeRanges)
        ? time_series_storage->getTargetTableID(ViewTarget::TimeRanges, context)
        : StorageID::createEmpty();
    const TableTimeRange time_range_to_filter_ids = getTimeRangeToFilterIDs(table_time_range, time_ranges_table_id, *time_series_settings);"""
SOURCE="void StorageTimeSeriesSelector::readImpl(\n"+OLD+"\n}\n"

with tempfile.TemporaryDirectory(prefix='tslab-selector-safety-') as tmp:
    cwd=pathlib.Path(tmp)
    target=cwd/'src/Storages/StorageTimeSeriesSelector.cpp'
    target.parent.mkdir(parents=True)
    target.write_text(SOURCE)
    before=hashlib.sha256(target.read_bytes()).hexdigest()
    result=subprocess.run([sys.executable,str(PATCH)],cwd=cwd,capture_output=True,text=True,check=True)
    after=hashlib.sha256(target.read_bytes()).hexdigest()
    assert before==after,('unsafe default modified source',before,after)
    assert 'SAFE DEFAULT' in result.stdout,result.stdout
    print('PASS fail-closed: no source modifications without opt-in',flush=True)
    result=subprocess.run([sys.executable,str(PATCH),'--allow-unverified-instance-bypass'],cwd=cwd,capture_output=True,text=True,check=True)
    modified=target.read_text()
    assert 'skip_time_ranges_for_exact_tag' in modified
    assert 'matcher.label_name == "instance"' in modified
    assert hashlib.sha256(target.read_bytes()).hexdigest()!=before
    print('PASS explicit test-only opt-in patches pinned source fixture',flush=True)
    second=subprocess.run([sys.executable,str(PATCH),'--allow-unverified-instance-bypass'],cwd=cwd,capture_output=True,text=True)
    assert second.returncode!=0,'patch should not silently apply twice'
    print('PASS pinned patch rejects repeated/unknown source',flush=True)
