from decimal import Decimal

import pandas as pd

from inventory_aggregator.engine.canonical.io import read_parquet_bytes, write_parquet_bytes


def test_parquet_roundtrip_preserves_decimal_precision() -> None:
    df = pd.DataFrame(
        {
            "sku": ["SKU-001", "SKU-002", "SKU-003"],
            "cost": [Decimal("10.50"), Decimal("0.10"), None],
            "price": [Decimal("25.995"), Decimal("1234567890.123456789"), Decimal("9.99")],
        }
    )

    blob = write_parquet_bytes(df)
    result = read_parquet_bytes(blob)

    assert result["cost"][0] == Decimal("10.50")
    assert result["cost"][1] == Decimal("0.10")
    assert result["cost"][2] is None
    assert result["price"][1] == Decimal("1234567890.123456789")
    assert list(result["sku"]) == ["SKU-001", "SKU-002", "SKU-003"]


def test_parquet_roundtrip_preserves_int_and_string_columns() -> None:
    df = pd.DataFrame(
        {
            "sku": ["SKU-001", "SKU-002"],
            "available_qty": [10, 0],
            "source_vendor_id": ["vendor_1", None],
        }
    )

    result = read_parquet_bytes(write_parquet_bytes(df))

    assert list(result["available_qty"]) == [10, 0]
    assert result["source_vendor_id"][0] == "vendor_1"
    # A None in a plain object/string column round-trips as pandas NaN, not None -- verified
    # directly, not assumed. This matches reconcile()'s own established convention for a
    # missing source_vendor_id (see test_reconcile.py's use of pd.isna(), not `is None`),
    # so downstream code reading a Parquet-sourced snapshot must already do the same.
    assert pd.isna(result["source_vendor_id"][1])


def test_parquet_roundtrip_empty_dataframe() -> None:
    df = pd.DataFrame(columns=["sku", "available_qty", "source_vendor_id", "source_cost", "vendor_count"])
    result = read_parquet_bytes(write_parquet_bytes(df))
    assert list(result.columns) == list(df.columns)
    assert len(result) == 0
