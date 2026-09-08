from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from inventory_aggregator.app.models.config import VendorRules

from .base import Rule
from .builder import build_rule
from .compound import AndRule, NotRule
from .grammar import RuleValidationError, parse_and_validate

__all__ = [
    "Rule",
    "RuleValidationError",
    "CompiledVendorRules",
    "compile_vendor_rules",
    "apply_compiled_rules",
    "parse_and_validate",
    "build_rule",
]


@dataclass
class CompiledVendorRules:
    inclusion: Optional[Rule]
    exclusion: Optional[Rule]


def compile_vendor_rules(
    rules: Optional[VendorRules], allowed_columns: set
) -> Optional[CompiledVendorRules]:
    """rules: VendorRules | None. Returns CompiledVendorRules | None."""
    if rules is None:
        return None
    inclusion = (
        build_rule(parse_and_validate(rules.inclusion_condition, allowed_columns))
        if rules.inclusion_condition
        else None
    )
    exclusion = (
        build_rule(parse_and_validate(rules.exclusion_condition, allowed_columns))
        if rules.exclusion_condition
        else None
    )
    return CompiledVendorRules(inclusion=inclusion, exclusion=exclusion)


def apply_compiled_rules(compiled: Optional[CompiledVendorRules], df: pd.DataFrame) -> np.ndarray:
    """Returns a boolean keep-mask. A row is kept if (inclusion absent OR true) AND
    (exclusion absent OR false)."""
    if compiled is None or (compiled.inclusion is None and compiled.exclusion is None):
        return np.ones(len(df), dtype=bool)
    parts = []
    if compiled.inclusion is not None:
        parts.append(compiled.inclusion)
    if compiled.exclusion is not None:
        parts.append(NotRule(compiled.exclusion))
    return AndRule(parts).evaluate(df) if len(parts) > 1 else parts[0].evaluate(df)
