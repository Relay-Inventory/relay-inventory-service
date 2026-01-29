from relay_inventory.engine.canonical.io import read_csv_rows, write_csv_bytes


def test_csv_output_round_trips_special_characters() -> None:
    rows = [
        {
            "sku": "SKU-1",
            "title": 'ACME, "Premium"\nWheel',
            "quantity_available": 5,
        }
    ]
    fieldnames = ["sku", "title", "quantity_available"]

    csv_bytes = write_csv_bytes(rows, fieldnames, extrasaction="raise")

    assert csv_bytes.endswith(b"\n")
    assert read_csv_rows(csv_bytes) == [
        {
            "sku": "SKU-1",
            "title": 'ACME, "Premium"\nWheel',
            "quantity_available": "5",
        }
    ]
