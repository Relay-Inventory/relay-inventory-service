from decimal import Decimal

import pandas as pd

from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.merge.reconcile import reconcile


def _record(sku, vendor_id, quantity_available, cost, **kwargs) -> InventoryRecord:
    defaults = dict(
        sku=sku,
        vendor_id=vendor_id,
        quantity_available=quantity_available,
        cost=cost,
        price=Decimal("0"),
    )
    defaults.update(kwargs)
    return InventoryRecord(**defaults)


def test_reconcile_sums_quantity_across_vendors() -> None:
    records = [
        _record("SKU1", "a", 5, Decimal("10")),
        _record("SKU1", "b", 3, Decimal("12")),
        _record("SKU1", "c", 2, Decimal("8")),
    ]
    result = reconcile(records, shipping_handling_flat=Decimal("1"))
    row = result[result["sku"] == "SKU1"].iloc[0]
    assert row["available_qty"] == 10


def test_reconcile_picks_cheapest_in_stock_vendor_as_source() -> None:
    records = [
        _record("SKU1", "a", 0, Decimal("1")),  # out of stock, cheapest — must not win
        _record("SKU1", "b", 5, Decimal("12")),
        _record("SKU1", "c", 5, Decimal("8")),
    ]
    result = reconcile(records, shipping_handling_flat=Decimal("1"))
    row = result[result["sku"] == "SKU1"].iloc[0]
    assert row["source_vendor_id"] == "c"
    assert row["source_cost"] == Decimal("8")


def test_reconcile_vendor_count_is_correct() -> None:
    records = [
        _record("SKU1", "a", 5, Decimal("10")),
        _record("SKU1", "b", 3, Decimal("12")),
        _record("SKU2", "a", 1, Decimal("5")),
    ]
    result = reconcile(records, shipping_handling_flat=Decimal("0"))
    sku1 = result[result["sku"] == "SKU1"].iloc[0]
    sku2 = result[result["sku"] == "SKU2"].iloc[0]
    assert sku1["vendor_count"] == 2
    assert sku2["vendor_count"] == 1


def test_reconcile_all_vendors_out_of_stock() -> None:
    records = [
        _record("SKU1", "a", 0, Decimal("10")),
        _record("SKU1", "b", 0, Decimal("12")),
    ]
    result = reconcile(records, shipping_handling_flat=Decimal("0"))
    row = result[result["sku"] == "SKU1"].iloc[0]
    assert row["available_qty"] == 0
    assert pd.isna(row["source_vendor_id"])


def test_reconcile_cost_none_never_wins_idxmin() -> None:
    records = [
        _record("SKU1", "a", 5, None),
        _record("SKU1", "b", 5, Decimal("10")),
    ]
    result = reconcile(records, shipping_handling_flat=Decimal("0"))
    row = result[result["sku"] == "SKU1"].iloc[0]
    assert row["source_vendor_id"] == "b"


def test_reconcile_empty_input_returns_empty_dataframe_with_correct_columns() -> None:
    result = reconcile([], shipping_handling_flat=Decimal("0"))
    assert list(result.columns) == ["sku", "available_qty", "source_vendor_id", "source_cost", "vendor_count"]
    assert len(result) == 0


def test_reconcile_is_deterministic() -> None:
    records = [
        _record("SKU2", "b", 3, Decimal("12")),
        _record("SKU1", "c", 1, Decimal("5")),
        _record("SKU1", "a", 2, Decimal("5")),
    ]
    first = reconcile(records, shipping_handling_flat=Decimal("0"))
    second = reconcile(records, shipping_handling_flat=Decimal("0"))
    pd.testing.assert_frame_equal(first.reset_index(drop=True), second.reset_index(drop=True))
