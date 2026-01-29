from decimal import Decimal

from relay_inventory.engine.canonical.models import InventoryRecord
from relay_inventory.engine.merge.best_offer import BestOfferConfig, LandedCostConfig, merge_best_offer


def test_merge_best_offer_prefers_in_stock_and_lower_cost() -> None:
    records = [
        InventoryRecord(
            sku="SKU1",
            vendor_id="a",
            quantity_available=0,
            cost=Decimal("10"),
            price=Decimal("0"),
        ),
        InventoryRecord(
            sku="SKU1",
            vendor_id="b",
            quantity_available=5,
            cost=Decimal("12"),
            price=Decimal("0"),
        ),
        InventoryRecord(
            sku="SKU1",
            vendor_id="c",
            quantity_available=5,
            cost=Decimal("8"),
            price=Decimal("0"),
        ),
    ]
    config = BestOfferConfig(
        landed_cost=LandedCostConfig(include_shipping_handling=True, shipping_handling_flat=Decimal("1")),
        fallback_lead_time_days=7,
    )
    merged = merge_best_offer(records, config=config)
    assert len(merged) == 1
    assert merged[0].vendor_id == "c"
