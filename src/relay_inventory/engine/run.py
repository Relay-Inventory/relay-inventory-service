from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

from relay_inventory.app.models.config import TenantConfig, VendorConfig
from relay_inventory.engine.canonical.models import InventoryRecord
from relay_inventory.engine.normalize.sku_map import load_sku_map_from_text
from relay_inventory.engine.parsing.csv_parser import ParseError, parse_csv
from relay_inventory.engine.pipeline import merge_records, price_records

SKU_MAP_SUFFIX = "::sku_map"


class MissingRequiredColumnsError(ValueError):
    """Raised when a vendor input is missing required columns."""


@dataclass
class EngineResult:
    normalized_by_vendor: dict[str, list[dict]]
    merged_rows: list[dict]
    errors: list[ParseError]
    summary: dict


def sku_map_input_key(vendor_id: str) -> str:
    return f"{vendor_id}{SKU_MAP_SUFFIX}"


def _parse_vendor_input(
    vendor: VendorConfig,
    *,
    raw_bytes: bytes,
    now: datetime,
    tenant_config: TenantConfig,
    vendor_inputs: dict[str, bytes],
) -> tuple[list[InventoryRecord], list[ParseError]]:
    try:
        records, vendor_errors = parse_csv(
            io.StringIO(raw_bytes.decode("utf-8")),
            vendor_id=vendor.vendor_id,
            column_map=vendor.parser.column_map,
            now=now,
        )
    except ValueError as exc:
        message = str(exc)
        if "missing columns:" in message.lower():
            if tenant_config.error_policy.fail_on_missing_required_columns:
                raise MissingRequiredColumnsError(message) from exc
            return [], [ParseError(row_number=0, reason=message, row_data={"vendor": vendor.vendor_id})]
        raise

    if vendor.sku_map and vendor.sku_map.s3_key:
        sku_map_bytes = vendor_inputs.get(sku_map_input_key(vendor.vendor_id))
        if sku_map_bytes is None:
            vendor_errors.append(
                ParseError(
                    row_number=0,
                    reason="missing sku map",
                    row_data={"vendor": vendor.vendor_id},
                )
            )
        else:
            sku_map = load_sku_map_from_text(sku_map_bytes.decode("utf-8"))
            records = list(sku_map.apply(records))

    return records, vendor_errors


def run_inventory_sync(
    *,
    vendor_inputs: dict[str, bytes],
    tenant_config: TenantConfig,
    run_id: str,
    now: datetime,
) -> EngineResult:
    normalized_by_vendor: dict[str, list[dict]] = {}
    errors: list[ParseError] = []
    vendor_counts: dict[str, int] = {}
    total_rows = 0
    all_records: list[InventoryRecord] = []

    for vendor in tenant_config.vendors:
        raw_bytes = vendor_inputs.get(vendor.vendor_id)
        if raw_bytes is None:
            errors.append(
                ParseError(row_number=0, reason="missing inbound file", row_data={"vendor": vendor.vendor_id})
            )
            normalized_by_vendor[vendor.vendor_id] = []
            vendor_counts[vendor.vendor_id] = 0
            continue

        records, vendor_errors = _parse_vendor_input(
            vendor,
            raw_bytes=raw_bytes,
            now=now,
            tenant_config=tenant_config,
            vendor_inputs=vendor_inputs,
        )
        errors.extend(vendor_errors)
        all_records.extend(records)
        vendor_counts[vendor.vendor_id] = len(records)
        total_rows += len(records) + len(vendor_errors)
        normalized_by_vendor[vendor.vendor_id] = [record.model_dump() for record in records]

    merged = merge_records(all_records, tenant_config)
    priced = price_records(merged, tenant_config)
    merged_rows = [record.model_dump() for record in priced]

    summary = {
        "run_id": run_id,
        "vendor_count": len(tenant_config.vendors),
        "vendor_record_counts": vendor_counts,
        "record_count": len(priced),
        "invalid_rows": len(errors),
        "total_rows": total_rows,
    }

    return EngineResult(
        normalized_by_vendor=normalized_by_vendor,
        merged_rows=merged_rows,
        errors=errors,
        summary=summary,
    )
