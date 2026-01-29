from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class InboundConfig(BaseModel):
    type: str
    s3_prefix: Optional[str] = None


class ParserConfig(BaseModel):
    format: str
    delimiter: str = ","
    encoding: str = "utf-8"
    column_map: Dict[str, str] = Field(default_factory=dict)


class SkuMapConfig(BaseModel):
    type: str
    s3_key: Optional[str] = None
    local_path: Optional[str] = None


class VendorConfig(BaseModel):
    vendor_id: str
    inbound: InboundConfig
    parser: ParserConfig
    sku_map: Optional[SkuMapConfig] = None


class MapPolicyConfig(BaseModel):
    enforce: bool = True
    map_floor_behavior: str = "max(price, map_price)"


class RoundingConfig(BaseModel):
    mode: str = "nearest"
    increment: Decimal = Decimal("0.01")


class PricingConfig(BaseModel):
    base_margin_pct: Decimal
    min_price: Decimal
    shipping_handling_flat: Decimal
    map_policy: MapPolicyConfig
    rounding: RoundingConfig


class BestOfferLandedCost(BaseModel):
    include_shipping_handling: bool = True


class BestOfferConfig(BaseModel):
    sort_by: List[str] = Field(default_factory=list)
    landed_cost: BestOfferLandedCost
    fallback_lead_time_days: int = 7


class MergeConfig(BaseModel):
    strategy: str
    best_offer: Optional[BestOfferConfig] = None


class OutputConfig(BaseModel):
    format: str = "csv"
    columns: List[str]


class TenantConfig(BaseModel):
    schema_version: int = 1
    tenant_id: str
    timezone: str
    default_currency: str
    vendors: List[VendorConfig]
    pricing: PricingConfig
    merge: MergeConfig
    output: OutputConfig
