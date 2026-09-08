from __future__ import annotations

import ast

_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.Gt, ast.Lt, ast.GtE, ast.LtE)
_MEMBERSHIP_OPS = (ast.In, ast.NotIn)
_ALLOWED_CALL_ATTRS = {"contains", "startswith"}


class RuleValidationError(ValueError):
    def __init__(self, message: str, *, node=None) -> None:
        location = f" (line {node.lineno}, col {node.col_offset})" if node is not None else ""
        super().__init__(f"{message}{location}")


def parse_and_validate(expr: str, allowed_columns: set) -> ast.Expression:
    """Parse expr and validate against the whitelist above. Raises RuleValidationError on
    anything outside the grammar. Returns the validated AST for builder.py to consume."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise RuleValidationError(f"could not parse expression: {exc}") from exc
    _validate(tree.body, allowed_columns)
    return tree


def _is_plain_constant(node: ast.expr, types: tuple) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, types)


def _is_negative_numeric_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    )


def _validate_scalar_literal(node: ast.expr) -> None:
    # Constants: str, int, float, bool (bool is a subclass of int, allowed explicitly).
    if _is_plain_constant(node, (str, int, float, bool)):
        return
    if _is_negative_numeric_literal(node):
        return
    raise RuleValidationError(
        "comparator must be a constant literal (or a negative numeric literal)", node=node
    )


def _validate(node: ast.expr, allowed_columns: set) -> None:
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise RuleValidationError("unsupported boolean operator", node=node)
        for value in node.values:
            _validate(value, allowed_columns)
        return

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            raise RuleValidationError("unsupported unary operator", node=node)
        _validate(node.operand, allowed_columns)
        return

    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise RuleValidationError("chained comparisons are not supported", node=node)

        left = node.left
        if not (isinstance(left, ast.Name) and left.id in allowed_columns):
            raise RuleValidationError(
                "left-hand side of a comparison must be an allowed column", node=left
            )

        op = node.ops[0]
        comparator = node.comparators[0]

        if isinstance(op, _COMPARE_OPS):
            _validate_scalar_literal(comparator)
            return

        if isinstance(op, _MEMBERSHIP_OPS):
            if not isinstance(comparator, (ast.List, ast.Tuple)):
                raise RuleValidationError(
                    "in/not in comparator must be a list or tuple literal", node=comparator
                )
            for elt in comparator.elts:
                _validate_scalar_literal(elt)
            return

        raise RuleValidationError("unsupported comparison operator", node=node)

    if isinstance(node, ast.Call):
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _ALLOWED_CALL_ATTRS):
            raise RuleValidationError("unsupported function call", node=node)
        if not (isinstance(func.value, ast.Name) and func.value.id in allowed_columns):
            raise RuleValidationError(
                "method calls are only allowed on allowed columns", node=node
            )
        if len(node.args) != 1 or not _is_plain_constant(node.args[0], (str,)):
            raise RuleValidationError(
                "contains/startswith takes exactly one string literal argument", node=node
            )
        if node.keywords:
            raise RuleValidationError("keyword arguments are not supported", node=node)
        return

    raise RuleValidationError(f"unsupported expression: {type(node).__name__}", node=node)
