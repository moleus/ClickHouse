#!/usr/bin/env python3
"""Research only: skip expensive direct grouped avg plan for small samples targets.

The threshold is an eligibility heuristic based on MergeTree part row counts,
not a tested production cost model. It preserves exact result semantics
by falling back to the unchanged PromQL converter.
"""
from pathlib import Path
p=Path("src/Storages/StoragePrometheusQuery.cpp")
s=p.read_text()
old=r"""        const auto tags_meta = time_series_storage->getTargetTable(ViewTarget::Tags, context)->getInMemoryMetadataPtr(context, false);
        const auto & id_type = tags_meta->columns.get("id").type;
        if (val_type->getName() == "Float64"
            && id_type->getName() == "Tuple(UInt64, LowCardinality(UUID))")
        {"""
new=r"""        const auto tags_meta = time_series_storage->getTargetTable(ViewTarget::Tags, context)->getInMemoryMetadataPtr(context, false);
        const auto & id_type = tags_meta->columns.get("id").type;
        const auto sample_rows = time_series_storage->getTargetTable(ViewTarget::Samples, context)->totalRows(context);
        /// Lab-only cost gate: small tables use the original per-series plan.
        /// This is NOT a production cardinality/cost estimator.
        if (val_type->getName() == "Float64"
            && id_type->getName() == "Tuple(UInt64, LowCardinality(UUID))"
            && sample_rows && *sample_rows >= 16384)
        {"""
if s.count(old)!=1:raise RuntimeError(f"unexpected type guard count: {s.count(old)}")
p.write_text(s.replace(old,new,1))
print("LAB_TS_SIZE_GUARD_CPP_APPLIED",p)
