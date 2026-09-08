import ast

import pytest

from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.rules.grammar import RuleValidationError, _validate, parse_and_validate

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
        "def foo(): pass",  # not a valid 'eval'-mode expression -> SyntaxError at parse time
        "cost > quantity_available",  # comparator must be a literal, not another column
        "+quantity_available",  # unary '+' -- only 'not' is allowed as a unary operator
        "~quantity_available",  # unary '~' -- same as above
        "quantity_available in sku",  # in/not-in comparator must be a list/tuple literal
        "cost is None",  # 'is'/'is not' are not in the supported comparison operator set
        "'literal'.startswith('x')",  # method call target must be a Name, not another literal
        "unknown_col.startswith('x')",  # method call target must be an allowed column
        "sku.startswith(123)",  # contains/startswith argument must be a string literal
        "sku.startswith('a', 'b')",  # contains/startswith takes exactly one argument
        "sku.startswith()",  # contains/startswith requires exactly one argument
        "sku.startswith('a', extra='b')",  # keyword arguments are never allowed
    ],
)
def test_rejects_unsafe_expressions(expr: str) -> None:
    with pytest.raises(RuleValidationError):
        parse_and_validate(expr, ALLOWED_COLUMNS)


def test_validate_rejects_boolop_with_unsupported_operator() -> None:
    """BoolOp.op is always And or Or for any expression ast.parse can actually produce from
    real Python source -- this defensive branch (grammar.py's `if not isinstance(node.op,
    (ast.And, ast.Or))`) can only be exercised by handing _validate a hand-built AST node
    directly, not through parse_and_validate's public string-based API."""
    # Build it by parsing real source (so it has real lineno/col_offset attributes, which
    # RuleValidationError's formatting reads unconditionally) and then corrupting .op --
    # a hand-built ast.BoolOp() from scratch has no position info and would fail for an
    # unrelated reason.
    tree = ast.parse("quantity_available > 0 and cost > 0", mode="eval")
    bool_op = tree.body
    assert isinstance(bool_op, ast.BoolOp)
    bool_op.op = object()
    with pytest.raises(RuleValidationError, match="unsupported boolean operator"):
        _validate(bool_op, ALLOWED_COLUMNS)


def test_negative_literal_comparison_accepted() -> None:
    parse_and_validate("cost > -1", ALLOWED_COLUMNS)
    parse_and_validate("quantity_available == -1", ALLOWED_COLUMNS)


def test_negative_literal_in_membership_list_accepted() -> None:
    parse_and_validate("quantity_available not in (-1, -2)", ALLOWED_COLUMNS)
