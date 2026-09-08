from decimal import Decimal

from inventory_aggregator.app.models.config import (
    InboundConfig,
    ParserConfig,
    VendorConfig,
    VendorRules,
)
from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS, InventoryRecord
from inventory_aggregator.engine.config.compiled import CompiledVendorConfig
from inventory_aggregator.engine.rules import compile_vendor_rules
from inventory_aggregator.engine.rules.apply import filter_by_vendor_rules


def _vendor(rules: VendorRules | None = None) -> VendorConfig:
    return VendorConfig(
        vendor_id="vendor-a",
        inbound=InboundConfig(type="s3"),
        parser=ParserConfig(format="csv"),
        rules=rules,
    )


def _compiled_vendor(rules: VendorRules | None = None) -> CompiledVendorConfig:
    vendor = _vendor(rules)
    compiled_rules = compile_vendor_rules(rules, allowed_columns=set(CANONICAL_COLUMNS))
    return CompiledVendorConfig(config=vendor, rules=compiled_rules)


def _record(sku: str, *, quantity_available: int = 5, condition: str | None = None) -> InventoryRecord:
    return InventoryRecord(
        sku=sku,
        vendor_id="vendor-a",
        quantity_available=quantity_available,
        cost=Decimal("5.00"),
        price=Decimal("10.00"),
        condition=condition,
    )


def test_filter_by_vendor_rules_no_rules_returns_all() -> None:
    compiled = _compiled_vendor(None)
    records = [_record("SKU1"), _record("SKU2")]
    result = filter_by_vendor_rules(records, compiled)
    assert result == records


def test_filter_by_vendor_rules_inclusion_only() -> None:
    compiled = _compiled_vendor(VendorRules(inclusion_condition="quantity_available > 0"))
    records = [_record("KEEP", quantity_available=5), _record("DROP", quantity_available=0)]
    result = filter_by_vendor_rules(records, compiled)
    assert [r.sku for r in result] == ["KEEP"]


def test_filter_by_vendor_rules_exclusion_only() -> None:
    compiled = _compiled_vendor(VendorRules(exclusion_condition="condition == 'used'"))
    records = [_record("KEEP", condition="new"), _record("DROP", condition="used")]
    result = filter_by_vendor_rules(records, compiled)
    assert [r.sku for r in result] == ["KEEP"]


def test_filter_by_vendor_rules_inclusion_and_exclusion_combined() -> None:
    compiled = _compiled_vendor(
        VendorRules(
            inclusion_condition="quantity_available > 0",
            exclusion_condition="condition == 'used'",
        )
    )
    records = [
        _record("KEEP", quantity_available=5, condition="new"),
        _record("DROP_ZERO_QTY", quantity_available=0, condition="new"),
        _record("DROP_USED", quantity_available=5, condition="used"),
    ]
    result = filter_by_vendor_rules(records, compiled)
    assert [r.sku for r in result] == ["KEEP"]


def test_filter_by_vendor_rules_empty_input() -> None:
    compiled = _compiled_vendor(VendorRules(inclusion_condition="quantity_available > 0"))
    assert filter_by_vendor_rules([], compiled) == []
