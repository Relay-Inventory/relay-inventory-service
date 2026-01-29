from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, Iterable

from relay_inventory.engine.canonical.models import InventoryRecord


@dataclass
class SkuMap:
    mapping: Dict[str, str]

    def apply(self, records: Iterable[InventoryRecord]) -> Iterable[InventoryRecord]:
        for record in records:
            mapped = self.mapping.get(record.sku)
            if mapped:
                yield record.model_copy(update={"sku": mapped})
            else:
                yield record


def _parse_sku_rows(reader: csv.DictReader) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for row in reader:
        vendor_sku = (row.get("vendor_sku") or "").strip()
        sku = (row.get("sku") or "").strip()
        if vendor_sku and sku:
            mapping[vendor_sku] = sku
    return mapping


def load_sku_map(path: str) -> SkuMap:
    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        mapping = _parse_sku_rows(reader)
    return SkuMap(mapping=mapping)


def load_sku_map_from_text(text: str) -> SkuMap:
    reader = csv.DictReader(text.splitlines())
    mapping = _parse_sku_rows(reader)
    return SkuMap(mapping=mapping)
