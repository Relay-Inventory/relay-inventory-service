from datetime import datetime, timezone
from decimal import Decimal

from relay_inventory.engine.canonical.io import write_csv_bytes


def test_csv_output_deterministic_order_and_formatting() -> None:
    rows = [
        {
            "sku": "SKU-002",
            "vendor_id": "vendor-b",
            "price": Decimal("9.9"),
            "updated_at": datetime(2020, 1, 1, 12, 0, 0),
        },
        {
            "sku": "SKU-001",
            "vendor_id": "vendor-b",
            "price": Decimal("10"),
            "updated_at": datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        },
        {
            "sku": "SKU-001",
            "vendor_id": "vendor-a",
            "price": "5",
            "updated_at": "2020-01-01T12:00:00",
        },
    ]
    fieldnames = ["sku", "vendor_id", "price", "updated_at"]

    csv_bytes = write_csv_bytes(rows, fieldnames, extrasaction="raise")

    assert (
        csv_bytes.decode("utf-8")
        == "sku,vendor_id,price,updated_at\n"
        "SKU-001,vendor-a,5.00,2020-01-01T12:00:00Z\n"
        "SKU-001,vendor-b,10.00,2020-01-01T12:00:00Z\n"
        "SKU-002,vendor-b,9.90,2020-01-01T12:00:00Z\n"
    )
