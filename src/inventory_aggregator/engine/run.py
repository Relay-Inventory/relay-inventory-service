from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from inventory_aggregator.app.models.config import TenantConfig, VendorConfig
from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.config.compiled import compile_tenant_config
from inventory_aggregator.engine.diff import SnapshotDiff, diff_snapshots
from inventory_aggregator.engine.normalize.adjustments import apply_vendor_adjustments
from inventory_aggregator.engine.normalize.sku_map import load_sku_map_from_text
from inventory_aggregator.engine.parsing.csv_parser import ParseError, parse_csv
from inventory_aggregator.engine.pipeline import merge_records, price_records
from inventory_aggregator.engine.rules.apply import filter_by_vendor_rules
from inventory_aggregator.engine.safety import SafetyDecision, SafetyThresholds, evaluate_safety

SKU_MAP_SUFFIX = "::sku_map"
SUPPORTED_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "latin-1": "latin-1",
    "iso-8859-1": "latin-1",
    "iso8859-1": "latin-1",
}


class MissingRequiredColumnsError(ValueError):
    """Raised when a vendor input is missing required columns."""


class DecodeError(ValueError):
    def __init__(self, vendor_id: str, encoding: str, message: str) -> None:
        super().__init__(message)
        self.vendor_id = vendor_id
        self.encoding = encoding


@dataclass
class EngineResult:
    normalized_by_vendor: dict[str, list[dict]]
    merged_rows: list[dict]
    errors: list[ParseError]
    summary: dict
    diff: SnapshotDiff | None = None
    safety: SafetyDecision | None = None


def sku_map_input_key(vendor_id: str) -> str:
    return f"{vendor_id}{SKU_MAP_SUFFIX}"


def _normalize_encoding(encoding: str) -> str:
    normalized = encoding.strip().lower().replace("_", "-")
    return SUPPORTED_ENCODINGS.get(normalized, normalized)


def _decode_bytes(*, raw_bytes: bytes, encoding: str, vendor_id: str) -> str:
    normalized = _normalize_encoding(encoding)
    if normalized not in set(SUPPORTED_ENCODINGS.values()):
        raise DecodeError(
            vendor_id,
            encoding,
            f"unsupported encoding '{encoding}' for vendor {vendor_id}",
        )
    try:
        return raw_bytes.decode(normalized)
    except UnicodeDecodeError as exc:
        raise DecodeError(vendor_id, encoding, str(exc)) from exc


def _parse_vendor_input(
    vendor: VendorConfig,
    *,
    raw_bytes: bytes,
    now: datetime,
    tenant_config: TenantConfig,
    vendor_inputs: dict[str, bytes],
) -> tuple[list[InventoryRecord], list[ParseError]]:
    try:
        encoding = vendor.parser.encoding or "utf-8"
        decoded_text = _decode_bytes(raw_bytes=raw_bytes, encoding=encoding, vendor_id=vendor.vendor_id)
        records, vendor_errors = parse_csv(
            io.StringIO(decoded_text),
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
            decoded_map = _decode_bytes(
                raw_bytes=sku_map_bytes,
                encoding=encoding,
                vendor_id=vendor.vendor_id,
            )
            sku_map = load_sku_map_from_text(decoded_map)
            records = list(sku_map.apply(records))

    return records, vendor_errors


def run_inventory_sync(
    *,
    vendor_inputs: dict[str, bytes],
    tenant_config: TenantConfig,
    run_id: str,
    now: datetime,
    previous_snapshot: pd.DataFrame | None = None,
    safety_thresholds: SafetyThresholds | None = None,
) -> EngineResult:
    # Compiled once per invocation (per process/Lambda invocation), not per vendor -- see
    # IMPLEMENTATION_PLAN.md Sec 8.2.
    compiled_config = compile_tenant_config(tenant_config)

    normalized_by_vendor: dict[str, list[dict]] = {}
    errors: list[ParseError] = []
    vendor_counts: dict[str, int] = {}
    total_rows = 0
    all_records: list[InventoryRecord] = []

    for vendor in tenant_config.vendors:
        raw_bytes = vendor_inputs.get(vendor.vendor_id)
        if raw_bytes is None:
            if vendor.required and tenant_config.error_policy.missing_required_vendor_policy != "warn_only":
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
        compiled_vendor = compiled_config.vendors[vendor.vendor_id]
        records = apply_vendor_adjustments(records, vendor)
        records = filter_by_vendor_rules(records, compiled_vendor)

        errors.extend(vendor_errors)
        all_records.extend(records)
        vendor_counts[vendor.vendor_id] = len(records)
        total_rows += len(records) + len(vendor_errors)
        normalized_by_vendor[vendor.vendor_id] = [record.model_dump() for record in records]

    merged = merge_records(all_records, tenant_config)
    priced = price_records(all_records, merged, tenant_config)
    merged_rows = priced.to_dict(orient="records")

    diff = diff_snapshots(previous_snapshot, priced)
    previous_total_qty = (
        int(previous_snapshot["available_qty"].sum())
        if previous_snapshot is not None and not previous_snapshot.empty
        else 0
    )
    current_total_qty = int(priced["available_qty"].sum()) if not priced.empty else 0
    safety = evaluate_safety(
        diff,
        safety_thresholds or SafetyThresholds(),
        previous_total_qty=previous_total_qty,
        current_total_qty=current_total_qty,
    )

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
        diff=diff,
        safety=safety,
    )
