from decimal import Decimal

from inventory_aggregator.app.models.config import (
    BestOfferConfig,
    BestOfferLandedCost,
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
)
from inventory_aggregator.engine.config.compiled import compile_tenant_config
from inventory_aggregator.engine.run_context import RunContext


def _tenant_config() -> TenantConfig:
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
        merge=MergeConfig(
            strategy="best_offer",
            best_offer=BestOfferConfig(sort_by=[], landed_cost=BestOfferLandedCost()),
        ),
        output=OutputConfig(columns=["sku"]),
    )


def test_run_context_holds_shop_run_and_compiled_config() -> None:
    tenant_config = _tenant_config()
    compiled_config = compile_tenant_config(tenant_config)
    ctx = RunContext(
        shop_id="shop-1",
        run_id="run-1",
        config_version=1,
        compiled_config=compiled_config,
    )
    assert ctx.shop_id == "shop-1"
    assert ctx.run_id == "run-1"
    assert ctx.config_version == 1
    assert ctx.compiled_config is compiled_config
    # Defaults: not a dry run, triggered by schedule, unless overridden.
    assert ctx.write_enabled is True
    assert ctx.trigger == "scheduled"


def test_run_context_dry_run_overrides() -> None:
    ctx = RunContext(
        shop_id="shop-1",
        run_id="run-2",
        config_version=1,
        compiled_config=compile_tenant_config(_tenant_config()),
        write_enabled=False,
        trigger="dry_run",
    )
    assert ctx.write_enabled is False
    assert ctx.trigger == "dry_run"
