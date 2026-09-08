from decimal import Decimal

import pandas as pd

from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.pricing.pricing import (
    MapPolicy,
    PricingRules,
    RoundingRule,
    apply_pricing,
    apply_pricing_to_snapshot,
)


def test_pricing_applies_margin_and_floor() -> None:
    record = InventoryRecord(
        sku="SKU1",
        vendor_id="vendor",
        quantity_available=5,
        cost=Decimal("10"),
        price=Decimal("0"),
    )
    rules = PricingRules(
        base_margin_pct=Decimal("0.2"),
        min_price=Decimal("25"),
        shipping_handling_flat=Decimal("5"),
        map_policy=MapPolicy(enforce=True),
        rounding=RoundingRule(mode="nearest", increment=Decimal("0.01")),
    )
    result = apply_pricing([record], rules)[0]
    assert result.price == Decimal("25")


def test_pricing_applies_map_floor() -> None:
    record = InventoryRecord(
        sku="SKU2",
        vendor_id="vendor",
        quantity_available=1,
        cost=Decimal("20"),
        map_price=Decimal("40"),
        price=Decimal("0"),
    )
    rules = PricingRules(
        base_margin_pct=Decimal("0.1"),
        min_price=Decimal("10"),
        shipping_handling_flat=Decimal("0"),
        map_policy=MapPolicy(enforce=True),
        rounding=RoundingRule(mode="nearest", increment=Decimal("0.01")),
    )
    result = apply_pricing([record], rules)[0]
    assert result.price == Decimal("40")


def test_apply_pricing_to_snapshot_uses_source_vendor_map_price() -> None:
    vendor_a = InventoryRecord(
        sku="SKU1",
        vendor_id="a",
        quantity_available=5,
        cost=Decimal("10"),
        map_price=Decimal("50"),
        price=Decimal("0"),
    )
    vendor_b = InventoryRecord(
        sku="SKU1",
        vendor_id="b",
        quantity_available=5,
        cost=Decimal("10"),
        map_price=Decimal("15"),
        price=Decimal("0"),
    )
    records_by_key = {
        ("SKU1", "a"): vendor_a,
        ("SKU1", "b"): vendor_b,
    }
    rules = PricingRules(
        base_margin_pct=Decimal("0.1"),
        min_price=Decimal("0"),
        shipping_handling_flat=Decimal("0"),
        map_policy=MapPolicy(enforce=True),
        rounding=RoundingRule(mode="nearest", increment=Decimal("0.01")),
    )
    snapshot = pd.DataFrame(
        [{"sku": "SKU1", "available_qty": 10, "source_vendor_id": "b", "source_cost": Decimal("10"), "vendor_count": 2}]
    )
    result = apply_pricing_to_snapshot(snapshot, records_by_key, rules)
    row = result[result["sku"] == "SKU1"].iloc[0]
    # source vendor is "b" (map_price=15), not "a" (map_price=50)
    assert row["price"] == Decimal("15")


def test_apply_pricing_to_snapshot_handles_missing_record_gracefully() -> None:
    records_by_key: dict = {}
    rules = PricingRules(
        base_margin_pct=Decimal("0.1"),
        min_price=Decimal("0"),
        shipping_handling_flat=Decimal("0"),
        map_policy=MapPolicy(enforce=True),
        rounding=RoundingRule(mode="nearest", increment=Decimal("0.01")),
    )
    snapshot = pd.DataFrame(
        [{"sku": "SKU1", "available_qty": 10, "source_vendor_id": "missing", "source_cost": Decimal("10"), "vendor_count": 1}]
    )
    result = apply_pricing_to_snapshot(snapshot, records_by_key, rules)
    row = result[result["sku"] == "SKU1"].iloc[0]
    assert row["price"] is None
