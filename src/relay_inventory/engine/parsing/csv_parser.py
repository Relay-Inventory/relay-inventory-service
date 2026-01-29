from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import IO, Any, Dict, Iterable, List, Optional, Tuple

from relay_inventory.engine.canonical.models import InventoryRecord


@dataclass
class ParseError:
    row_number: int
    reason: str
    row_data: Dict[str, Any]


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return Decimal(stripped)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError as exc:
        raise ValueError(f"invalid int: {value}") from exc


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(stripped, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid datetime: {value}")


def parse_csv(
    handle: IO[str],
    *,
    vendor_id: str,
    column_map: Dict[str, str],
    default_condition: Optional[str] = None,
) -> Tuple[List[InventoryRecord], List[ParseError]]:
    reader = csv.DictReader(handle)
    required_fields = ["sku", "quantity_available"]
    missing = []
    for field in required_fields:
        mapped = column_map.get(field, field)
        if not reader.fieldnames or mapped not in reader.fieldnames:
            missing.append(mapped)
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    records: List[InventoryRecord] = []
    errors: List[ParseError] = []

    for row_number, row in enumerate(reader, start=2):
        try:
            sku = row.get(column_map.get("sku", "sku"))
            vendor_sku = row.get(column_map.get("vendor_sku", "vendor_sku"))
            quantity_available = _parse_int(
                row.get(column_map.get("quantity_available", "quantity_available"))
            )
            cost = _parse_decimal(row.get(column_map.get("cost", "cost")))
            map_price = _parse_decimal(row.get(column_map.get("map_price", "map_price")))
            msrp = _parse_decimal(row.get(column_map.get("msrp", "msrp")))
            lead_time_days = _parse_int(
                row.get(column_map.get("lead_time_days", "lead_time_days"))
            )
            price = _parse_decimal(row.get(column_map.get("price", "price")))
            updated_at = _parse_datetime(
                row.get(column_map.get("updated_at", "updated_at"))
            )
            record = InventoryRecord(
                sku=sku or "",
                vendor_sku=vendor_sku,
                vendor_id=vendor_id,
                quantity_available=quantity_available or 0,
                lead_time_days=lead_time_days,
                cost=cost,
                map_price=map_price,
                price=price or Decimal("0"),
                msrp=msrp,
                condition=row.get(column_map.get("condition", "condition"))
                or default_condition,
                brand=row.get(column_map.get("brand", "brand")),
                title=row.get(column_map.get("title", "title")),
                updated_at=updated_at or datetime.utcnow(),
            )
            records.append(record)
        except Exception as exc:  # noqa: BLE001 - capture parse errors for reporting
            errors.append(ParseError(row_number=row_number, reason=str(exc), row_data=row))

    return records, errors


def load_csv_records(
    path: str,
    *,
    vendor_id: str,
    column_map: Dict[str, str],
    default_condition: Optional[str] = None,
) -> Tuple[List[InventoryRecord], List[ParseError]]:
    with open(path, "r", newline="", encoding="utf-8") as handle:
        return parse_csv(
            handle,
            vendor_id=vendor_id,
            column_map=column_map,
            default_condition=default_condition,
        )


def records_to_rows(records: Iterable[InventoryRecord]) -> List[Dict[str, Any]]:
    return [record.model_dump() for record in records]
