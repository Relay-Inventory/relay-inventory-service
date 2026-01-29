from relay_inventory.engine.normalize.sku_map import load_sku_map_from_text
from relay_inventory.engine.canonical.models import InventoryRecord


def test_sku_map_rewrites_vendor_sku() -> None:
    text = "vendor_sku,sku\nVEND-1,SKU-001\n"
    sku_map = load_sku_map_from_text(text)
    record = InventoryRecord(
        sku="VEND-1",
        vendor_id="vendor",
        quantity_available=1,
        price=0,
    )
    result = list(sku_map.apply([record]))[0]
    assert result.sku == "SKU-001"
