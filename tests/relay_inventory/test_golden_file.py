import csv
from pathlib import Path

from freezegun import freeze_time

from relay_inventory.app.config.loader import load_tenant_config
from relay_inventory.engine.canonical.io import read_csv_rows, write_csv_bytes
from relay_inventory.engine.canonical.models import CANONICAL_COLUMNS
from relay_inventory.engine.pipeline import merge_records, price_records, process_vendor


def test_golden_output_matches_expected() -> None:
    config = load_tenant_config("data/relay_inventory/tenant_config.yaml")
    vendor_files = {
        "vendor_1": "data/relay_inventory/vendor_1.csv",
        "vendor_2": "data/relay_inventory/vendor_2.csv",
    }
    with freeze_time("2020-01-01T00:00:00"):
        vendor_results = [
            process_vendor(vendor, source_path=vendor_files[vendor.vendor_id])
            for vendor in config.vendors
        ]
        all_records = [record for result in vendor_results for record in result.records]
        merged = merge_records(all_records, config)
        priced = price_records(merged, config)

    rows = [record.model_dump() for record in priced]

    expected_path = Path("tests/relay_inventory/fixtures/expected_merged.csv")
    with expected_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected_rows = list(reader)

    output_columns = ["sku", "quantity_available", "price", "vendor_id", "updated_at"]
    output_bytes = write_csv_bytes(rows, output_columns, extrasaction="ignore")
    output_rows = read_csv_rows(output_bytes)

    assert output_rows == expected_rows
    assert set(CANONICAL_COLUMNS).issuperset({"sku", "quantity_available", "price", "vendor_id"})
