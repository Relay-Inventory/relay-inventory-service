from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

DECIMAL_FIELDS = {"cost", "map_price", "price", "msrp"}
DATETIME_FIELDS = {"updated_at"}


def _format_decimal(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return value
    return str(decimal_value.quantize(Decimal("0.01")))


def _format_datetime(value: object) -> object:
    if value is None:
        return None
    datetime_value: datetime | None = None
    if isinstance(value, datetime):
        datetime_value = value
    elif isinstance(value, str):
        try:
            datetime_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return value
    if datetime_value.tzinfo is None:
        datetime_value = datetime_value.replace(tzinfo=timezone.utc)
    else:
        datetime_value = datetime_value.astimezone(timezone.utc)
    return datetime_value.isoformat().replace("+00:00", "Z")


def _normalize_row(row: dict) -> dict:
    normalized = dict(row)
    for field in DECIMAL_FIELDS:
        if field in normalized:
            normalized[field] = _format_decimal(normalized[field])
    for field in DATETIME_FIELDS:
        if field in normalized:
            normalized[field] = _format_datetime(normalized[field])
    return normalized


def write_csv_bytes(
    rows: Iterable[dict],
    fieldnames: Sequence[str],
    *,
    extrasaction: str = "raise",
) -> bytes:
    normalized_rows = [_normalize_row(row) for row in rows]
    include_vendor_id = "vendor_id" in fieldnames
    normalized_rows.sort(
        key=lambda row: (
            str(row.get("sku", "")),
            str(row.get("vendor_id", "")) if include_vendor_id else "",
        )
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        extrasaction=extrasaction,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in normalized_rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def read_csv_rows(bytes_blob: bytes) -> list[dict]:
    buffer = io.StringIO(bytes_blob.decode("utf-8"))
    reader = csv.DictReader(buffer)
    return list(reader)
