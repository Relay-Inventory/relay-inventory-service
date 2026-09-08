from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd

from .base import Rule


class ComparisonOp(str, Enum):
    EQ = "=="
    NE = "!="
    GT = ">"
    LT = "<"
    GE = ">="
    LE = "<="


_NUMPY_OPS = {
    ComparisonOp.EQ: np.equal,
    ComparisonOp.NE: np.not_equal,
    ComparisonOp.GT: np.greater,
    ComparisonOp.LT: np.less,
    ComparisonOp.GE: np.greater_equal,
    ComparisonOp.LE: np.less_equal,
}


class ComparisonRule(Rule):
    def __init__(self, field: str, op: ComparisonOp, value) -> None:
        self.field, self.op, self.value = field, op, value

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        return _NUMPY_OPS[self.op](df[self.field].to_numpy(), self.value)


class MembershipRule(Rule):
    def __init__(self, field: str, values: list, negate: bool = False) -> None:
        self.field, self.values, self.negate = field, values, negate

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        mask = np.isin(df[self.field].to_numpy(), self.values)
        return ~mask if self.negate else mask


class ContainsRule(Rule):
    def __init__(self, field: str, substring: str) -> None:
        self.field, self.substring = field, substring

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        return df[self.field].str.contains(self.substring, na=False).to_numpy()


class StartsWithRule(Rule):
    def __init__(self, field: str, prefix: str) -> None:
        self.field, self.prefix = field, prefix

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        return df[self.field].str.startswith(self.prefix, na=False).to_numpy()
