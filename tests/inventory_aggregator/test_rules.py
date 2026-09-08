import numpy as np
import pandas as pd
import pytest

from inventory_aggregator.engine.rules import (
    CompiledVendorRules,
    apply_compiled_rules,
    compile_vendor_rules,
)
from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.rules.comparison import (
    ComparisonOp,
    ComparisonRule,
    ContainsRule,
    MembershipRule,
    StartsWithRule,
)
from inventory_aggregator.engine.rules.compound import AndRule, NotRule, OrRule
from inventory_aggregator.app.models.config import VendorRules

ALLOWED_COLUMNS = set(CANONICAL_COLUMNS)


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sku": ["ABC-1", "XYZ-2", "ABC-3", "MNO-4"],
            "vendor_id": ["v1", "v2", "v1", "v3"],
            "quantity_available": [0, 5, 10, 2],
            "cost": [1.0, -1.0, 100.0, 50.0],
        }
    )


@pytest.mark.parametrize(
    "op,expected",
    [
        (ComparisonOp.EQ, [False, False, False, True]),
        (ComparisonOp.NE, [True, True, True, False]),
        (ComparisonOp.GT, [False, True, True, False]),
        (ComparisonOp.LT, [True, False, False, False]),
        (ComparisonOp.GE, [False, True, True, True]),
        (ComparisonOp.LE, [True, False, False, True]),
    ],
)
def test_comparison_rule_ops(df: pd.DataFrame, op: ComparisonOp, expected: list) -> None:
    rule = ComparisonRule("quantity_available", op, 2)
    np.testing.assert_array_equal(rule.evaluate(df), np.array(expected))


def test_membership_rule_no_negate(df: pd.DataFrame) -> None:
    rule = MembershipRule("vendor_id", ["v1"])
    np.testing.assert_array_equal(rule.evaluate(df), np.array([True, False, True, False]))


def test_membership_rule_negate(df: pd.DataFrame) -> None:
    rule = MembershipRule("vendor_id", ["v1"], negate=True)
    np.testing.assert_array_equal(rule.evaluate(df), np.array([False, True, False, True]))


def test_contains_rule(df: pd.DataFrame) -> None:
    rule = ContainsRule("sku", "ABC")
    np.testing.assert_array_equal(rule.evaluate(df), np.array([True, False, True, False]))


def test_starts_with_rule(df: pd.DataFrame) -> None:
    rule = StartsWithRule("sku", "ABC")
    np.testing.assert_array_equal(rule.evaluate(df), np.array([True, False, True, False]))


def test_and_rule(df: pd.DataFrame) -> None:
    rule = AndRule(
        [
            ComparisonRule("quantity_available", ComparisonOp.GT, 0),
            ComparisonRule("cost", ComparisonOp.GT, 0),
        ]
    )
    np.testing.assert_array_equal(rule.evaluate(df), np.array([False, False, True, True]))


def test_or_rule(df: pd.DataFrame) -> None:
    rule = OrRule(
        [
            ComparisonRule("quantity_available", ComparisonOp.EQ, 0),
            ComparisonRule("cost", ComparisonOp.LT, 0),
        ]
    )
    np.testing.assert_array_equal(rule.evaluate(df), np.array([True, True, False, False]))


def test_not_rule(df: pd.DataFrame) -> None:
    rule = NotRule(ComparisonRule("quantity_available", ComparisonOp.EQ, 0))
    np.testing.assert_array_equal(rule.evaluate(df), np.array([False, True, True, True]))


def test_nested_compound_rule(df: pd.DataFrame) -> None:
    # (quantity_available > 0 AND cost > 0) OR NOT (vendor_id in ["v1"])
    rule = OrRule(
        [
            AndRule(
                [
                    ComparisonRule("quantity_available", ComparisonOp.GT, 0),
                    ComparisonRule("cost", ComparisonOp.GT, 0),
                ]
            ),
            NotRule(MembershipRule("vendor_id", ["v1"])),
        ]
    )
    # row0: qty=0,cost=1 -> AND=False; not(v1 in [v1])=not True=False -> False
    # row1: qty=5,cost=-1 -> AND=False; not(v2 in [v1])=not False=True -> True
    # row2: qty=10,cost=100 -> AND=True -> True
    # row3: qty=2,cost=50 -> AND=True -> True
    np.testing.assert_array_equal(rule.evaluate(df), np.array([False, True, True, True]))


def test_compile_vendor_rules_none_returns_none() -> None:
    assert compile_vendor_rules(None, allowed_columns=ALLOWED_COLUMNS) is None


def test_apply_compiled_rules_with_none_keeps_all(df: pd.DataFrame) -> None:
    mask = apply_compiled_rules(None, df)
    np.testing.assert_array_equal(mask, np.ones(len(df), dtype=bool))

    empty_compiled = CompiledVendorRules(inclusion=None, exclusion=None)
    mask2 = apply_compiled_rules(empty_compiled, df)
    np.testing.assert_array_equal(mask2, np.ones(len(df), dtype=bool))


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("quantity_available == 0", [True, False, False, False]),
        ("quantity_available != 0", [False, True, True, True]),
        ("quantity_available > 2", [False, True, True, False]),
        ("quantity_available < 2", [True, False, False, False]),
        ("quantity_available >= 2", [False, True, True, True]),
        ("quantity_available <= 2", [True, False, False, True]),
        ("vendor_id in ('v1', 'v3')", [True, False, True, True]),
        ("vendor_id not in ('v1', 'v3')", [False, True, False, False]),
        ("sku.contains('ABC')", [True, False, True, False]),
        ("sku.startswith('ABC')", [True, False, True, False]),
    ],
)
def test_compile_and_apply_from_expression_string(
    df: pd.DataFrame, expr: str, expected: list
) -> None:
    rules = VendorRules(inclusion_condition=expr)
    compiled = compile_vendor_rules(rules, allowed_columns=ALLOWED_COLUMNS)
    mask = apply_compiled_rules(compiled, df)
    np.testing.assert_array_equal(mask, np.array(expected))


def test_compile_and_apply_exclusion_condition(df: pd.DataFrame) -> None:
    rules = VendorRules(exclusion_condition="quantity_available == 0")
    compiled = compile_vendor_rules(rules, allowed_columns=ALLOWED_COLUMNS)
    mask = apply_compiled_rules(compiled, df)
    np.testing.assert_array_equal(mask, np.array([False, True, True, True]))


def test_compile_and_apply_inclusion_and_exclusion_combined(df: pd.DataFrame) -> None:
    rules = VendorRules(
        inclusion_condition="vendor_id in ('v1', 'v3')",
        exclusion_condition="quantity_available == 0",
    )
    compiled = compile_vendor_rules(rules, allowed_columns=ALLOWED_COLUMNS)
    mask = apply_compiled_rules(compiled, df)
    # inclusion: [True, False, True, True]; exclusion True where qty==0 -> not-exclusion:
    # [False, True, True, True]
    # AND -> [False, False, True, True]
    np.testing.assert_array_equal(mask, np.array([False, False, True, True]))
