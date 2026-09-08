from decimal import Decimal

import pandas as pd

from inventory_aggregator.engine.normalize.hashing import hash_normalized_feed


def test_same_qty_cost_different_row_order_produces_same_hash() -> None:
    df1 = pd.DataFrame(
        {
            "sku": ["SKU1", "SKU2"],
            "quantity_available": [5, 10],
            "cost": [Decimal("10.00"), Decimal("20.00")],
        }
    )
    df2 = pd.DataFrame(
        {
            "sku": ["SKU2", "SKU1"],
            "quantity_available": [10, 5],
            "cost": [Decimal("20.00"), Decimal("10.00")],
        }
    )
    assert hash_normalized_feed(df1) == hash_normalized_feed(df2)


def test_extra_embedded_timestamp_column_does_not_affect_hash() -> None:
    df1 = pd.DataFrame(
        {
            "sku": ["SKU1"],
            "quantity_available": [5],
            "cost": [Decimal("10.00")],
        }
    )
    df2 = pd.DataFrame(
        {
            "sku": ["SKU1"],
            "quantity_available": [5],
            "cost": [Decimal("10.00")],
            "exported_at": ["2026-09-08T12:00:00Z"],
        }
    )
    assert hash_normalized_feed(df1) == hash_normalized_feed(df2)


def test_different_quantity_produces_different_hash() -> None:
    df1 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("10.00")]})
    df2 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [6], "cost": [Decimal("10.00")]})
    assert hash_normalized_feed(df1) != hash_normalized_feed(df2)


def test_different_cost_produces_different_hash() -> None:
    df1 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("10.00")]})
    df2 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("11.00")]})
    assert hash_normalized_feed(df1) != hash_normalized_feed(df2)


def test_decimal_trailing_zero_formatting_does_not_produce_a_false_change() -> None:
    """Regression guard for a real risk: Decimal("10.50") and Decimal("10.5") are numerically
    equal but stringify differently -- confirmed directly (str(a) != str(b) even though
    a == b) -- so a naive str(cost) hash would treat a vendor's trailing-zero formatting
    quirk as an inventory change and defeat the whole skip-unchanged-feed optimization."""
    df1 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("10.50")]})
    df2 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("10.5")]})
    assert hash_normalized_feed(df1) == hash_normalized_feed(df2)


def test_non_decimal_numeric_cost_still_hashes_consistently() -> None:
    """cost isn't guaranteed to always arrive as Decimal (e.g. a plain float/int could reach
    this function from a DataFrame built by something other than InventoryRecord.model_dump())
    -- confirm the plain str() fallback path still produces a consistent, comparable hash."""
    df1 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [10.5]})
    df2 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [10.5]})
    assert hash_normalized_feed(df1) == hash_normalized_feed(df2)


def test_empty_dataframe_does_not_raise() -> None:
    df = pd.DataFrame(columns=["sku", "quantity_available", "cost"])
    assert hash_normalized_feed(df) == hash_normalized_feed(df)


def test_nan_cost_treated_same_as_none() -> None:
    """A float NaN can show up in a cost column after certain pandas operations (e.g. a
    groupby that produces a missing value in a numeric-inferred column); confirm it's
    treated the same as None, not stringified as "nan" and hashed as a distinct value."""
    df_nan = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [float("nan")]})
    df_none = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [None]})
    assert hash_normalized_feed(df_nan) == hash_normalized_feed(df_none)


def test_none_cost_handled_without_raising() -> None:
    df1 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [None]})
    df2 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [None]})
    assert hash_normalized_feed(df1) == hash_normalized_feed(df2)
    df3 = pd.DataFrame({"sku": ["SKU1"], "quantity_available": [5], "cost": [Decimal("1.00")]})
    assert hash_normalized_feed(df1) != hash_normalized_feed(df3)
