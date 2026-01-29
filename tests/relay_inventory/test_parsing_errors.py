import io

import pytest

from relay_inventory.engine.parsing.csv_parser import parse_csv


def test_parse_csv_records_errors() -> None:
    csv_data = "SKU,QTY,COST\nSKU1,not-a-number,10.5\n"
    records, errors = parse_csv(
        io.StringIO(csv_data),
        vendor_id="vendor",
        column_map={"sku": "SKU", "quantity_available": "QTY", "cost": "COST"},
    )
    assert not records
    assert errors
    assert "invalid int" in errors[0].reason


def test_parse_csv_missing_required_columns() -> None:
    csv_data = "SKU,COST\nSKU1,10.5\n"
    with pytest.raises(ValueError, match="missing columns"):
        parse_csv(
            io.StringIO(csv_data),
            vendor_id="vendor",
            column_map={"sku": "SKU", "quantity_available": "QTY", "cost": "COST"},
        )
