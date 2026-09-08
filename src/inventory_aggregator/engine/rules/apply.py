from __future__ import annotations

import pandas as pd

from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.config.compiled import CompiledVendorConfig
from inventory_aggregator.engine.rules import apply_compiled_rules


def filter_by_vendor_rules(
    records: list[InventoryRecord], vendor: CompiledVendorConfig
) -> list[InventoryRecord]:
    """Uses vendor.rules, already compiled once per process by compile_tenant_config -- no
    recompilation here. Records that fail are dropped, not raised as errors -- this is a
    normal filtering step, not a validation step (validation already happened at
    config-load time)."""
    if vendor.rules is None:
        return records
    if not records:
        return records
    df = pd.DataFrame([r.model_dump() for r in records])
    keep_mask = apply_compiled_rules(vendor.rules, df)
    return [r for r, keep in zip(records, keep_mask) if keep]
