from __future__ import annotations

from inventory_aggregator.app.models.config import VendorConfig
from inventory_aggregator.engine.canonical.models import InventoryRecord


def apply_vendor_adjustments(
    records: list[InventoryRecord], vendor: VendorConfig
) -> list[InventoryRecord]:
    """Apply buffer_qty (subtract, floor at 0), cost_adjustment (add), and drop rows whose
    post-buffer quantity is below min_qty_threshold. Pure function -- returns a new list,
    does not mutate input records."""
    adjusted: list[InventoryRecord] = []
    for record in records:
        new_qty = max(record.quantity_available - vendor.buffer_qty, 0)
        if new_qty < vendor.min_qty_threshold:
            continue
        updates = {"quantity_available": new_qty}
        if record.cost is not None:
            updates["cost"] = record.cost + vendor.cost_adjustment
        adjusted.append(record.model_copy(update=updates))
    return adjusted
