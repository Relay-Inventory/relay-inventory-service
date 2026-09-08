from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.rules.builder import build_rule
from inventory_aggregator.engine.rules.comparison import (
    ComparisonOp,
    ComparisonRule,
    ContainsRule,
    MembershipRule,
    StartsWithRule,
)
from inventory_aggregator.engine.rules.compound import AndRule, NotRule, OrRule
from inventory_aggregator.engine.rules.grammar import parse_and_validate

ALLOWED_COLUMNS = set(CANONICAL_COLUMNS)


def _build(expr: str):
    return build_rule(parse_and_validate(expr, ALLOWED_COLUMNS))


def test_build_comparison_eq() -> None:
    rule = _build("quantity_available == 5")
    assert isinstance(rule, ComparisonRule)
    assert rule.field == "quantity_available"
    assert rule.op == ComparisonOp.EQ
    assert rule.value == 5


def test_build_comparison_gt_negative_value() -> None:
    rule = _build("cost > -1")
    assert isinstance(rule, ComparisonRule)
    assert rule.field == "cost"
    assert rule.op == ComparisonOp.GT
    assert rule.value == -1


def test_build_membership_in() -> None:
    rule = _build("vendor_id in ('a', 'b')")
    assert isinstance(rule, MembershipRule)
    assert rule.field == "vendor_id"
    assert rule.values == ["a", "b"]
    assert rule.negate is False


def test_build_membership_not_in_negative_values() -> None:
    rule = _build("quantity_available not in (-1, -2)")
    assert isinstance(rule, MembershipRule)
    assert rule.field == "quantity_available"
    assert rule.values == [-1, -2]
    assert rule.negate is True


def test_build_contains() -> None:
    rule = _build("sku.contains('WIDGET')")
    assert isinstance(rule, ContainsRule)
    assert rule.field == "sku"
    assert rule.substring == "WIDGET"


def test_build_startswith() -> None:
    rule = _build("sku.startswith('ABC')")
    assert isinstance(rule, StartsWithRule)
    assert rule.field == "sku"
    assert rule.prefix == "ABC"


def test_build_and() -> None:
    rule = _build("quantity_available > 0 and cost < 100")
    assert isinstance(rule, AndRule)
    assert len(rule.children) == 2
    assert isinstance(rule.children[0], ComparisonRule)
    assert isinstance(rule.children[1], ComparisonRule)


def test_build_or() -> None:
    rule = _build("quantity_available > 0 or cost < 100")
    assert isinstance(rule, OrRule)
    assert len(rule.children) == 2


def test_build_not() -> None:
    rule = _build("not (quantity_available > 0)")
    assert isinstance(rule, NotRule)
    assert isinstance(rule.child, ComparisonRule)
