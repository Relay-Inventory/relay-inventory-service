from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import pandas as pd

from inventory_aggregator.app.models.config import TenantConfig
from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.config.compiled import CompiledVendorConfig
from inventory_aggregator.engine.merge.reconcile import reconcile
from inventory_aggregator.engine.normalize.adjustments import apply_vendor_adjustments
from inventory_aggregator.engine.normalize.sku_map import SkuMap, load_sku_map
from inventory_aggregator.engine.parsing.csv_parser import ParseError, load_csv_records
from inventory_aggregator.engine.pricing.pricing import (
    MapPolicy,
    PricingRules,
    RoundingRule,
    apply_pricing_to_snapshot,
)
from inventory_aggregator.engine.rules.apply import filter_by_vendor_rules


@dataclass
class VendorResult:
    vendor_id: str
    records: List[InventoryRecord]
    errors: List[ParseError]


def process_vendor(
    compiled_vendor: CompiledVendorConfig,
    *,
    source_path: str,
) -> VendorResult:
    vendor_config = compiled_vendor.config
    records, errors = load_csv_records(
        source_path,
        vendor_id=vendor_config.vendor_id,
        column_map=vendor_config.parser.column_map,
    )

    sku_map: SkuMap | None = None
    if vendor_config.sku_map and vendor_config.sku_map.local_path:
        sku_map = load_sku_map(vendor_config.sku_map.local_path)
    if sku_map:
        records = list(sku_map.apply(records))

    records = apply_vendor_adjustments(records, vendor_config)
    records = filter_by_vendor_rules(records, compiled_vendor)

    return VendorResult(vendor_id=vendor_config.vendor_id, records=records, errors=errors)


def merge_records(records: Iterable[InventoryRecord], config: TenantConfig) -> pd.DataFrame:
    """Returns the reconciled snapshot: sku, available_qty (summed across vendors),
    source_vendor_id/source_cost (cheapest in-stock vendor), vendor_count."""
    if config.merge.strategy != "best_offer" or not config.merge.best_offer:
        raise ValueError("Unsupported merge strategy")
    return reconcile(list(records), shipping_handling_flat=config.pricing.shipping_handling_flat)


def price_records(
    all_records: Iterable[InventoryRecord],
    snapshot: pd.DataFrame,
    config: TenantConfig,
) -> pd.DataFrame:
    """all_records is the pre-merge, per-vendor record list (used to look up the source
    vendor's own cost/map_price by (sku, vendor_id)); snapshot is merge_records' output."""
    records_by_key = {(r.sku, r.vendor_id): r for r in all_records}
    rules = PricingRules(
        base_margin_pct=config.pricing.base_margin_pct,
        min_price=config.pricing.min_price,
        shipping_handling_flat=config.pricing.shipping_handling_flat,
        map_policy=MapPolicy(
            enforce=config.pricing.map_policy.enforce,
            map_floor_behavior=config.pricing.map_policy.map_floor_behavior,
        ),
        rounding=RoundingRule(
            mode=config.pricing.rounding.mode,
            increment=config.pricing.rounding.increment,
        ),
    )
    return apply_pricing_to_snapshot(snapshot, records_by_key, rules)
