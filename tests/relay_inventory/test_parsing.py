import io
from decimal import Decimal

from relay_inventory.engine.parsing.csv_parser import parse_csv


def test_parse_csv_maps_columns() -> None:
    csv_data = "SKU,QTY,COST\nSKU1,5,10.5\n"
    records, errors = parse_csv(
        io.StringIO(csv_data),
        vendor_id="vendor",
        column_map={"sku": "SKU", "quantity_available": "QTY", "cost": "COST"},
    )
    assert not errors
    assert records[0].sku == "SKU1"
    assert records[0].quantity_available == 5
    assert records[0].cost == Decimal("10.5")
