import pytest

from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.rules.grammar import RuleValidationError, parse_and_validate

ALLOWED_COLUMNS = set(CANONICAL_COLUMNS)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "cost.__class__",
        "a < b < c",
        "vendor_id + '1'",
        "[x for x in range(10)]",
        "lambda x: x",
        "quantity_available",
        "unknown_column == 1",
        "sku.upper()",
        'f"{sku}"',
    ],
)
def test_rejects_unsafe_expressions(expr: str) -> None:
    with pytest.raises(RuleValidationError):
        parse_and_validate(expr, ALLOWED_COLUMNS)


def test_negative_literal_comparison_accepted() -> None:
    parse_and_validate("cost > -1", ALLOWED_COLUMNS)
    parse_and_validate("quantity_available == -1", ALLOWED_COLUMNS)


def test_negative_literal_in_membership_list_accepted() -> None:
    parse_and_validate("quantity_available not in (-1, -2)", ALLOWED_COLUMNS)
