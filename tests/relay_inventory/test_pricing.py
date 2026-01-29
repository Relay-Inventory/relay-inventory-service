from decimal import Decimal

from relay_inventory.engine.canonical.models import InventoryRecord
from relay_inventory.engine.pricing.pricing import MapPolicy, PricingRules, RoundingRule, apply_pricing


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
