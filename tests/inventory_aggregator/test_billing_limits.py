from decimal import Decimal

from inventory_aggregator.app.models.config import (
    BestOfferConfig,
    BestOfferLandedCost,
    InboundConfig,
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    ParserConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
    VendorConfig,
)
from inventory_aggregator.billing.limits import MAX_VENDORS_SOFT_CAP, is_over_vendor_cap


def _vendor(vendor_id: str) -> VendorConfig:
    return VendorConfig(
        vendor_id=vendor_id,
        inbound=InboundConfig(type="s3", s3_prefix="prefix/"),
        parser=ParserConfig(format="csv"),
    )


def _tenant_config(vendor_count: int) -> TenantConfig:
    return TenantConfig(
        tenant_id="tenant-a",
        timezone="UTC",
        default_currency="USD",
        vendors=[_vendor(f"v{i}") for i in range(vendor_count)],
        pricing=PricingConfig(
            base_margin_pct=Decimal("0.2"),
            min_price=Decimal("1"),
            shipping_handling_flat=Decimal("0"),
            map_policy=MapPolicyConfig(),
            rounding=RoundingConfig(mode="nearest", increment=Decimal("0.01")),
        ),
        merge=MergeConfig(
            strategy="best_offer",
            best_offer=BestOfferConfig(sort_by=[], landed_cost=BestOfferLandedCost()),
        ),
        output=OutputConfig(columns=["sku"]),
    )


def test_max_vendors_soft_cap_is_25() -> None:
    assert MAX_VENDORS_SOFT_CAP == 25


def test_is_over_vendor_cap_false_at_exactly_the_cap() -> None:
    tenant_config = _tenant_config(MAX_VENDORS_SOFT_CAP)
    assert is_over_vendor_cap(tenant_config) is False


def test_is_over_vendor_cap_false_under_the_cap() -> None:
    tenant_config = _tenant_config(3)
    assert is_over_vendor_cap(tenant_config) is False


def test_is_over_vendor_cap_true_over_the_cap() -> None:
    tenant_config = _tenant_config(MAX_VENDORS_SOFT_CAP + 1)
    assert is_over_vendor_cap(tenant_config) is True
