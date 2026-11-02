#!/usr/bin/env python3
"""Restrict lab-only metric-ID range SQL to the canonical tuple ID type.

With a different ID type, the original PromQL converter must remain active.
The residual tags-ID lookup handles noncanonical VALUEs of this tuple type,
but does not imply correct authorization for direct inner target reads.
"""
from pathlib import Path
p=Path("src/Storages/StoragePrometheusQuery.cpp")
s=p.read_text()
old=r"""        if (val_type->getName() == "Float64")
        {
            evaluation_settings.lab_samples_target_id = time_series_storage->getTargetTableID(ViewTarget::Samples, context);
            evaluation_settings.lab_tags_target_id = time_series_storage->getTargetTableID(ViewTarget::Tags, context);
        }"""
new=r"""        const auto tags_meta = time_series_storage->getTargetTable(ViewTarget::Tags, context)->getInMemoryMetadataPtr(context, false);
        const auto & id_type = tags_meta->columns.get("id").type;
        if (val_type->getName() == "Float64"
            && id_type->getName() == "Tuple(UInt64, LowCardinality(UUID))")
        {
            evaluation_settings.lab_samples_target_id = time_series_storage->getTargetTableID(ViewTarget::Samples, context);
            evaluation_settings.lab_tags_target_id = time_series_storage->getTargetTableID(ViewTarget::Tags, context);
        }"""
if s.count(old)!=1:raise RuntimeError(f"expected one settings guard, got {s.count(old)}")
p.write_text(s.replace(old,new,1))
print("LAB_TS_ID_TYPE_GUARD_APPLIED",p)
