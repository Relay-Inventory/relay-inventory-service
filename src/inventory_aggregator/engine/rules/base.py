from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class Rule(ABC):
    """A compiled, vectorized row-predicate over a canonical DataFrame."""

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        """Return a boolean numpy array, one entry per row of df."""
        ...
