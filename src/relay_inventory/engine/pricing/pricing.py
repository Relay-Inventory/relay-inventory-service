from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from relay_inventory.engine.canonical.models import InventoryRecord


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
