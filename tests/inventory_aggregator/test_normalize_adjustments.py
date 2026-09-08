from decimal import Decimal

from inventory_aggregator.app.models.config import (
    InboundConfig,
    ParserConfig,
    VendorConfig,
)
from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.normalize.adjustments import apply_vendor_adjustments


def _vendor(**overrides) -> VendorConfig:
    defaults = dict(
        vendor_id="vendor-a",
        inbound=InboundConfig(type="s3"),
        parser=ParserConfig(format="csv"),
    )
    defaults.update(overrides)
    return VendorConfig(**defaults)


def _record(**overrides) -> InventoryRecord:
    defaults = dict(
        sku="SKU1",
        vendor_id="vendor-a",
        quantity_available=10,
        cost=Decimal("5.00"),
        price=Decimal("10.00"),
    )
    defaults.update(overrides)
    return InventoryRecord(**defaults)


def test_buffer_qty_subtracts_and_floors_at_zero() -> None:
    vendor = _vendor(buffer_qty=20)
    record = _record(quantity_available=5)
    [result] = apply_vendor_adjustments([record], vendor)
    assert result.quantity_available == 0


def test_cost_adjustment_added_to_cost() -> None:
    vendor = _vendor(cost_adjustment=Decimal("2"))
    record = _record(cost=Decimal("10"))
    [result] = apply_vendor_adjustments([record], vendor)
    assert result.cost == Decimal("12")


def test_cost_adjustment_skipped_when_cost_is_none() -> None:
    vendor = _vendor(cost_adjustment=Decimal("2"))
    record = _record(cost=None)
    [result] = apply_vendor_adjustments([record], vendor)
    assert result.cost is None


def test_min_qty_threshold_drops_row() -> None:
    vendor = _vendor(min_qty_threshold=5)
    record = _record(quantity_available=3)
    assert apply_vendor_adjustments([record], vendor) == []


def test_min_qty_threshold_keeps_row_at_boundary() -> None:
    vendor = _vendor(min_qty_threshold=5)
    record = _record(quantity_available=5)
    [result] = apply_vendor_adjustments([record], vendor)
    assert result.quantity_available == 5


def test_defaults_are_no_ops() -> None:
    vendor = _vendor()
    record = _record(quantity_available=7, cost=Decimal("3.5"))
    [result] = apply_vendor_adjustments([record], vendor)
    assert result.quantity_available == 7
    assert result.cost == Decimal("3.5")


def test_buffer_and_threshold_combined() -> None:
    vendor = _vendor(buffer_qty=3, min_qty_threshold=1)
    kept = _record(sku="KEEP", quantity_available=5)  # 5-3=2, >=1, kept
    dropped = _record(sku="DROP", quantity_available=3)  # 3-3=0, <1, dropped
    result = apply_vendor_adjustments([kept, dropped], vendor)
    assert [r.sku for r in result] == ["KEEP"]
    assert result[0].quantity_available == 2
