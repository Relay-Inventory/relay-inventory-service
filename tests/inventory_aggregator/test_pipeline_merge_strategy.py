from decimal import Decimal

import pytest

from inventory_aggregator.app.models.config import (
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
)
from inventory_aggregator.engine.pipeline import merge_records


def _tenant_config(*, strategy: str, best_offer=None) -> TenantConfig:
    return TenantConfig(
        tenant_id="tenant-a",
        timezone="UTC",
        default_currency="USD",
        vendors=[],
        pricing=PricingConfig(
            base_margin_pct=Decimal("0.2"),
            min_price=Decimal("1"),
            shipping_handling_flat=Decimal("0"),
            map_policy=MapPolicyConfig(),
            rounding=RoundingConfig(mode="nearest", increment=Decimal("0.01")),
        ),
        merge=MergeConfig(strategy=strategy, best_offer=best_offer),
        output=OutputConfig(columns=["sku"]),
    )


def test_merge_records_rejects_unsupported_strategy() -> None:
    config = _tenant_config(strategy="cheapest_only")
    with pytest.raises(ValueError, match="Unsupported merge strategy"):
        merge_records([], config)


def test_merge_records_rejects_best_offer_strategy_missing_best_offer_config() -> None:
    config = _tenant_config(strategy="best_offer", best_offer=None)
    with pytest.raises(ValueError, match="Unsupported merge strategy"):
        merge_records([], config)
