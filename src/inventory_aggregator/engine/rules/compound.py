from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Rule


class AndRule(Rule):
    def __init__(self, children: list) -> None:
        self.children = children

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        result = np.ones(len(df), dtype=bool)
        for child in self.children:
            result &= child.evaluate(df)
        return result


class OrRule(Rule):
    def __init__(self, children: list) -> None:
        self.children = children

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        result = np.zeros(len(df), dtype=bool)
        for child in self.children:
            result |= child.evaluate(df)
        return result


class NotRule(Rule):
    def __init__(self, child) -> None:
        self.child = child

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        return ~self.child.evaluate(df)
