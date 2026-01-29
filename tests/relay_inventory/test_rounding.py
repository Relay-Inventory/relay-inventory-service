from decimal import Decimal

from relay_inventory.engine.pricing.pricing import MapPolicy, PricingRules, RoundingRule, compute_price


def test_rounding_respects_map_floor() -> None:
    rules = PricingRules(
        base_margin_pct=Decimal("0.2"),
        min_price=Decimal("0"),
        shipping_handling_flat=Decimal("9.99"),
        map_policy=MapPolicy(enforce=True),
        rounding=RoundingRule(mode="nearest", increment=Decimal("0.99")),
    )
    price = compute_price(Decimal("10"), rules, Decimal("25"))
    assert price == Decimal("25")
