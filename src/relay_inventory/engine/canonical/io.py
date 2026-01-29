from __future__ import annotations

import csv
import io
from typing import Iterable, Sequence


def write_csv_bytes(
    rows: Iterable[dict],
    fieldnames: Sequence[str],
    *,
    extrasaction: str = "raise",
) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        extrasaction=extrasaction,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def read_csv_rows(bytes_blob: bytes) -> list[dict]:
    buffer = io.StringIO(bytes_blob.decode("utf-8"))
    reader = csv.DictReader(buffer)
    return list(reader)
