from __future__ import annotations

import ast

from .base import Rule
from .comparison import (
    ComparisonOp,
    ComparisonRule,
    ContainsRule,
    MembershipRule,
    StartsWithRule,
)
from .compound import AndRule, NotRule, OrRule

_AST_OP_TO_COMPARISON_OP = {
    ast.Eq: ComparisonOp.EQ,
    ast.NotEq: ComparisonOp.NE,
    ast.Gt: ComparisonOp.GT,
    ast.Lt: ComparisonOp.LT,
    ast.GtE: ComparisonOp.GE,
    ast.LtE: ComparisonOp.LE,
}


def _literal_value(node: ast.expr):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -node.operand.value
    return node.value


def build_rule(tree: ast.Expression) -> Rule:
    """tree must already be validated by grammar.parse_and_validate."""
    return _build(tree.body)


def _build(node: ast.expr) -> Rule:
    if isinstance(node, ast.BoolOp):
        children = [_build(v) for v in node.values]
        return AndRule(children) if isinstance(node.op, ast.And) else OrRule(children)
    if isinstance(node, ast.UnaryOp):
        return NotRule(_build(node.operand))
    if isinstance(node, ast.Call):
        field = node.func.value.id
        literal = node.args[0].value
        return (
            ContainsRule(field, literal)
            if node.func.attr == "contains"
            else StartsWithRule(field, literal)
        )
    field = node.left.id
    op = node.ops[0]
    if isinstance(op, (ast.In, ast.NotIn)):
        values = [_literal_value(elt) for elt in node.comparators[0].elts]
        return MembershipRule(field, values, negate=isinstance(op, ast.NotIn))
    return ComparisonRule(
        field, _AST_OP_TO_COMPARISON_OP[type(op)], _literal_value(node.comparators[0])
    )
