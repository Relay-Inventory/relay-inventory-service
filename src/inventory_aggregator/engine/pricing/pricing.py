from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

import pandas as pd

from inventory_aggregator.engine.canonical.models import InventoryRecord


@dataclass
class MapPolicy:
    enforce: bool
    map_floor_behavior: str = "max(price, map_price)"


@dataclass
class RoundingRule:
    mode: str
    increment: Decimal


@dataclass
class PricingRules:
    base_margin_pct: Decimal
    min_price: Decimal
    shipping_handling_flat: Decimal
    map_policy: MapPolicy
    rounding: RoundingRule


def _round_price(value: Decimal, rounding: RoundingRule) -> Decimal:
    if rounding.increment <= 0:
        return value
    increments = (value / rounding.increment).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return increments * rounding.increment


def compute_price(cost: Decimal, rules: PricingRules, map_price: Decimal | None) -> Decimal:
    landed_cost = cost + rules.shipping_handling_flat
    candidate = landed_cost * (Decimal("1") + rules.base_margin_pct)
    if candidate < rules.min_price:
        candidate = rules.min_price
    candidate = _round_price(candidate, rules.rounding)
    if rules.map_policy.enforce and map_price is not None:
        if rules.map_policy.map_floor_behavior == "max(price, map_price)":
            candidate = max(candidate, map_price)
    return candidate


def apply_pricing(records: Iterable[InventoryRecord], rules: PricingRules) -> list[InventoryRecord]:
    priced: list[InventoryRecord] = []
    for record in records:
        if record.cost is None:
            priced.append(record)
            continue
        new_price = compute_price(record.cost, rules, record.map_price)
        priced.append(record.model_copy(update={"price": new_price}))
    return priced


def apply_pricing_to_snapshot(
    snapshot: pd.DataFrame,
    records_by_key: dict,  # dict[tuple[str, str], InventoryRecord], keyed by (sku, vendor_id)
    rules: PricingRules,
) -> pd.DataFrame:
    """Returns snapshot with a new 'price' column (Decimal), computed via compute_price using
    the source vendor's own cost/map_price."""
    prices = []
    for _, row in snapshot.iterrows():
        key = (row["sku"], row["source_vendor_id"])
        record = records_by_key.get(key)
        if record is None or record.cost is None:
            prices.append(None)
            continue
        prices.append(compute_price(record.cost, rules, record.map_price))
    result = snapshot.copy()
    result["price"] = prices
    return result
