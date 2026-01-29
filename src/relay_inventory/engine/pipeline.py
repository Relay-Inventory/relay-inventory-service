from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from relay_inventory.app.models.config import TenantConfig, VendorConfig
from relay_inventory.engine.canonical.models import InventoryRecord
from relay_inventory.engine.merge.best_offer import BestOfferConfig, LandedCostConfig, merge_best_offer
from relay_inventory.engine.normalize.sku_map import SkuMap, load_sku_map
from relay_inventory.engine.parsing.csv_parser import ParseError, load_csv_records
from relay_inventory.engine.pricing.pricing import MapPolicy, PricingRules, RoundingRule, apply_pricing


@dataclass
class VendorResult:
    vendor_id: str
    records: List[InventoryRecord]
    errors: List[ParseError]


def process_vendor(
    vendor_config: VendorConfig,
    *,
    source_path: str,
) -> VendorResult:
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

    return VendorResult(vendor_id=vendor_config.vendor_id, records=records, errors=errors)


def merge_records(records: Iterable[InventoryRecord], config: TenantConfig) -> List[InventoryRecord]:
    if config.merge.strategy != "best_offer" or not config.merge.best_offer:
        raise ValueError("Unsupported merge strategy")
    best_offer = config.merge.best_offer
    landed_cost = LandedCostConfig(
        include_shipping_handling=best_offer.landed_cost.include_shipping_handling,
        shipping_handling_flat=config.pricing.shipping_handling_flat,
    )
    merge_config = BestOfferConfig(
        landed_cost=landed_cost,
        fallback_lead_time_days=best_offer.fallback_lead_time_days,
    )
    return merge_best_offer(records, config=merge_config)


def price_records(records: Iterable[InventoryRecord], config: TenantConfig) -> List[InventoryRecord]:
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
    return apply_pricing(records, rules)
