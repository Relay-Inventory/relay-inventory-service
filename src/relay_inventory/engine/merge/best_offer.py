from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List

from relay_inventory.engine.canonical.models import InventoryRecord


@dataclass
class LandedCostConfig:
    include_shipping_handling: bool
    shipping_handling_flat: Decimal


@dataclass
class BestOfferConfig:
    landed_cost: LandedCostConfig
    fallback_lead_time_days: int


def _landed_cost(record: InventoryRecord, config: LandedCostConfig) -> Decimal:
    if record.cost is None:
        return Decimal("0")
    if config.include_shipping_handling:
        return record.cost + config.shipping_handling_flat
    return record.cost


def merge_best_offer(
    records: Iterable[InventoryRecord],
    *,
    config: BestOfferConfig,
) -> List[InventoryRecord]:
    grouped: Dict[str, List[InventoryRecord]] = {}
    for record in records:
        grouped.setdefault(record.sku, []).append(record)

    merged: List[InventoryRecord] = []
    for sku, sku_records in grouped.items():
        def sort_key(item: InventoryRecord) -> tuple[int, Decimal]:
            in_stock = 1 if item.quantity_available > 0 else 0
            return (-in_stock, _landed_cost(item, config.landed_cost))

        sorted_records = sorted(sku_records, key=sort_key)
        selected = sorted_records[0]
        if selected.lead_time_days is None:
            selected = selected.model_copy(
                update={"lead_time_days": config.fallback_lead_time_days}
            )
        merged.append(selected)
    return merged
