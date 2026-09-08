#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from inventory_aggregator.app.config.loader import load_tenant_config
from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.config.compiled import compile_tenant_config
from inventory_aggregator.engine.pipeline import merge_records, price_records, process_vendor

DEFAULT_FIXTURES_DIR = Path("tests/inventory_aggregator/fixtures/three_vendor_overlap")


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
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to tenant config YAML")
    parser.add_argument("--tenant", help="Tenant id for default fixture config")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help=f"Run against the committed three-vendor-overlap fixture set ({DEFAULT_FIXTURES_DIR})",
    )
    parser.add_argument(
        "--vendor-file",
        action="append",
        default=[],
        help="Vendor file mapping (vendor_id=path)",
    )
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument(
        "--summary",
        action="store_true",
        default=True,
        help="Print a per-stage row-count/diff summary (default: on)",
    )
    parser.add_argument("--no-summary", dest="summary", action="store_false")
    args = parser.parse_args()

    if not args.config and not args.tenant and not args.fixtures:
        raise ValueError("Provide --config, --tenant, or --fixtures")

    if args.fixtures:
        config_path = DEFAULT_FIXTURES_DIR / "tenant_config.yaml"
    elif args.tenant and not args.config:
        config_path = Path("data/inventory_aggregator/tenant_config.yaml")
    else:
        config_path = Path(args.config)

    config = load_tenant_config(config_path)
    compiled_config = compile_tenant_config(config)

    vendor_files = parse_vendor_files(args.vendor_file)
    if args.fixtures and not vendor_files:
        vendor_files = {
            vendor.vendor_id: str(DEFAULT_FIXTURES_DIR / f"{vendor.vendor_id}.csv")
            for vendor in config.vendors
        }
    elif args.tenant and not vendor_files:
        vendor_files = {
            "vendor_1": "data/inventory_aggregator/vendor_1.csv",
            "vendor_2": "data/inventory_aggregator/vendor_2.csv",
        }

    vendor_results = []
    for vendor in config.vendors:
        if vendor.vendor_id not in vendor_files:
            raise ValueError(f"Missing vendor file for {vendor.vendor_id}")
        result = process_vendor(
            compiled_config.vendors[vendor.vendor_id],
            source_path=vendor_files[vendor.vendor_id],
        )
        vendor_results.append(result)
        normalized_rows = [record.model_dump() for record in result.records]
        write_csv(
            Path(args.output_dir)
            / "normalized"
            / f"{vendor.vendor_id}_normalized.csv",
            normalized_rows,
            CANONICAL_COLUMNS,
        )
        if args.summary:
            print(
                f"[{vendor.vendor_id}] {len(result.records)} rows kept, "
                f"{len(result.errors)} parse errors"
            )

    all_records = [record for result in vendor_results for record in result.records]
    merged = merge_records(all_records, config)
    priced = price_records(all_records, merged, config)

    if args.summary:
        print(f"[merge] {len(merged)} SKUs reconciled")
        for row in priced.to_dict(orient="records"):
            print(
                f"  {row['sku']}: available_qty={row['available_qty']} "
                f"source_vendor_id={row['source_vendor_id']} vendor_count={row['vendor_count']} "
                f"price={row.get('price')}"
            )

    output_columns = config.output.columns or CANONICAL_COLUMNS
    output_rows = priced.to_dict(orient="records")
    write_csv(Path(args.output_dir) / "merged_inventory.csv", output_rows, output_columns)

    if args.summary:
        print(f"Wrote {len(output_rows)} rows to {Path(args.output_dir) / 'merged_inventory.csv'}")


if __name__ == "__main__":
    main()
