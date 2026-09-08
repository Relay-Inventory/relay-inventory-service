from decimal import Decimal

import pytest
from pydantic import ValidationError

from inventory_aggregator.app.config.loader import load_tenant_config
from inventory_aggregator.app.models.config import (
    InboundConfig,
    ParserConfig,
    SkuMapConfig,
    VendorConfig,
    VendorRules,
)


def test_invalid_schema_version(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 2\ntenant_id: test\ntimezone: UTC\ndefault_currency: USD\nvendors: []\npricing: {base_margin_pct: 0, min_price: 0, shipping_handling_flat: 0, map_policy: {enforce: true}, rounding: {mode: nearest, increment: 0.01}}\nmerge: {strategy: best_offer}\noutput: {columns: [sku]}\n")
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        load_tenant_config(path)


def _vendor_kwargs(**overrides):
    kwargs = dict(
        vendor_id="v1",
        inbound=InboundConfig(type="s3", s3_prefix="prefix/"),
        parser=ParserConfig(format="csv"),
    )
    kwargs.update(overrides)
    return kwargs


def test_vendor_config_defaults() -> None:
    vendor = VendorConfig(**_vendor_kwargs())
    assert vendor.buffer_qty == 0
    assert vendor.min_qty_threshold == 0
    assert vendor.cost_adjustment == Decimal("0")
    assert vendor.margin_floor is None
    assert vendor.rules is None


def test_vendor_config_rejects_negative_buffer_qty() -> None:
    with pytest.raises(ValidationError):
        VendorConfig(**_vendor_kwargs(buffer_qty=-1))


def test_vendor_config_rejects_negative_min_qty_threshold() -> None:
    with pytest.raises(ValidationError):
        VendorConfig(**_vendor_kwargs(min_qty_threshold=-1))


def test_sku_map_config_rejects_both_sources_set() -> None:
    with pytest.raises(ValidationError):
        SkuMapConfig(type="csv", s3_key="key", local_path="path")


def test_sku_map_config_rejects_neither_source_set() -> None:
    with pytest.raises(ValidationError):
        SkuMapConfig(type="csv")


def test_tenant_config_rejects_invalid_vendor_rule() -> None:
    with pytest.raises(ValidationError):
        VendorConfig(
            **_vendor_kwargs(
                rules=VendorRules(inclusion_condition="__import__('os')")
            )
        )


def test_tenant_config_accepts_valid_vendor_rule() -> None:
    vendor = VendorConfig(
        **_vendor_kwargs(
            rules=VendorRules(inclusion_condition="quantity_available > 0")
        )
    )
    assert vendor.rules is not None


def test_config_module_imports_without_circular_import_error() -> None:
    import inventory_aggregator.app.models.config  # noqa: F401
