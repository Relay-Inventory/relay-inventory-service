from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "SkuMapConfig":
        if bool(self.s3_key) == bool(self.local_path):
            raise ValueError("exactly one of s3_key or local_path must be set")
        return self


class VendorRules(BaseModel):
    inclusion_condition: Optional[str] = None
    exclusion_condition: Optional[str] = None


class VendorConfig(BaseModel):
    vendor_id: str
    required: bool = True
    inbound: InboundConfig
    parser: ParserConfig
    sku_map: Optional[SkuMapConfig] = None
    buffer_qty: int = 0
    min_qty_threshold: int = 0
    cost_adjustment: Decimal = Decimal("0")
    margin_floor: Optional[Decimal] = None
    rules: Optional[VendorRules] = None

    @field_validator("buffer_qty", "min_qty_threshold")
    @classmethod
    def non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0")
        return value

    @model_validator(mode="after")
    def _validate_rules(self) -> "VendorConfig":
        if self.rules is not None:
            from inventory_aggregator.engine.rules import compile_vendor_rules
            from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS

            compile_vendor_rules(self.rules, allowed_columns=set(CANONICAL_COLUMNS))
        return self


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


class ErrorPolicy(BaseModel):
    max_invalid_rows: int = 0
    max_invalid_row_pct: Decimal = Field(
        default=Decimal("0.0"),
        description="Maximum invalid row ratio (0.0-1.0).",
    )
    fail_on_missing_required_columns: bool = True
    missing_required_vendor_policy: str = "fail"


class TenantConfig(BaseModel):
    schema_version: int = 1
    tenant_id: str
    timezone: str
    default_currency: str
    vendors: List[VendorConfig]
    pricing: PricingConfig
    merge: MergeConfig
    output: OutputConfig
    error_policy: ErrorPolicy = Field(default_factory=ErrorPolicy)
