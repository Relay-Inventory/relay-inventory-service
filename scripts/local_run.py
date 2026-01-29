#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from relay_inventory.app.config.loader import load_tenant_config
from relay_inventory.engine.canonical.models import CANONICAL_COLUMNS
from relay_inventory.engine.pipeline import merge_records, price_records, process_vendor


def parse_vendor_files(values: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("vendor file must be vendor_id=path")
        vendor_id, path = value.split("=", 1)
        mapping[vendor_id] = path
    return mapping


def write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to tenant config YAML")
    parser.add_argument("--tenant", help="Tenant id for default fixture config")
    parser.add_argument(
        "--vendor-file",
        action="append",
        default=[],
        help="Vendor file mapping (vendor_id=path)",
    )
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    if not args.config and not args.tenant:
        raise ValueError("Provide --config or --tenant")
    if args.tenant and not args.config:
        config_path = Path("data/relay_inventory/tenant_config.yaml")
    else:
        config_path = Path(args.config)
    config = load_tenant_config(config_path)
    vendor_files = parse_vendor_files(args.vendor_file)
    if args.tenant and not vendor_files:
        vendor_files = {
            "vendor_1": "data/relay_inventory/vendor_1.csv",
            "vendor_2": "data/relay_inventory/vendor_2.csv",
        }

    vendor_results = []
    for vendor in config.vendors:
        if vendor.vendor_id not in vendor_files:
            raise ValueError(f"Missing vendor file for {vendor.vendor_id}")
        result = process_vendor(vendor, source_path=vendor_files[vendor.vendor_id])
        vendor_results.append(result)
        normalized_rows = [record.model_dump() for record in result.records]
        write_csv(
            Path(args.output_dir)
            / "normalized"
            / f"{vendor.vendor_id}_normalized.csv",
            normalized_rows,
            CANONICAL_COLUMNS,
        )

    all_records = [record for result in vendor_results for record in result.records]
    merged = merge_records(all_records, config)
    priced = price_records(merged, config)

    output_columns = config.output.columns or CANONICAL_COLUMNS
    output_rows = [record.model_dump() for record in priced]
    write_csv(Path(args.output_dir) / "merged_inventory.csv", output_rows, output_columns)


if __name__ == "__main__":
    main()
