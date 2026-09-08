from inventory_aggregator.app.models.config import (
    InboundConfig,
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    ParserConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
    VendorConfig,
    VendorRules,
)
from inventory_aggregator.engine.config.compiled import compile_tenant_config


def _pricing() -> PricingConfig:
    return PricingConfig(
        base_margin_pct="0.2",
        min_price="1.00",
        shipping_handling_flat="0.00",
        map_policy=MapPolicyConfig(),
        rounding=RoundingConfig(),
    )


def _vendor(vendor_id: str, rules=None) -> VendorConfig:
    return VendorConfig(
        vendor_id=vendor_id,
        inbound=InboundConfig(type="s3", s3_prefix="prefix/"),
        parser=ParserConfig(format="csv"),
        rules=rules,
    )


def _tenant_config(vendors) -> TenantConfig:
    return TenantConfig(
        tenant_id="tenant-1",
        timezone="UTC",
        default_currency="USD",
        vendors=vendors,
        pricing=_pricing(),
        merge=MergeConfig(strategy="best_offer"),
        output=OutputConfig(columns=["sku"]),
    )


def test_compile_tenant_config_builds_one_entry_per_vendor() -> None:
    tenant_config = _tenant_config(
        [
            _vendor("v1"),
            _vendor("v2", rules=VendorRules(inclusion_condition="quantity_available > 0")),
        ]
    )
    compiled = compile_tenant_config(tenant_config)
    assert set(compiled.vendors.keys()) == {"v1", "v2"}
    assert compiled.config is tenant_config
    assert compiled.vendors["v1"].config.vendor_id == "v1"
    assert compiled.vendors["v2"].config.vendor_id == "v2"


def test_compile_tenant_config_vendor_with_no_rules_has_none_rules() -> None:
    tenant_config = _tenant_config([_vendor("v1")])
    compiled = compile_tenant_config(tenant_config)
    assert compiled.vendors["v1"].rules is None


def test_compile_tenant_config_vendor_with_rules_is_compiled() -> None:
    tenant_config = _tenant_config(
        [_vendor("v1", rules=VendorRules(inclusion_condition="quantity_available > 0"))]
    )
    compiled = compile_tenant_config(tenant_config)
    compiled_rules = compiled.vendors["v1"].rules
    assert compiled_rules is not None
    assert compiled_rules.inclusion is not None
    assert compiled_rules.exclusion is None
