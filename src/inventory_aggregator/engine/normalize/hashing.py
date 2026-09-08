from __future__ import annotations

import hashlib
from decimal import Decimal


import pandas as pd


def _normalize_cost(cost: object) -> str:
    """Decimal("10.50") and Decimal("10.5") are numerically equal but stringify differently
    -- a real risk here, not a hypothetical one, since vendors' export tools commonly vary
    trailing-zero formatting between runs with no actual value change (confirmed: str(a) !=
    str(b) even though a == b). .normalize() strips trailing zeros deterministically (it
    produces scientific notation for round numbers like 100 -> "1E+2", which looks odd but
    is exactly as deterministic -- this function only needs equal values to produce equal
    strings, not to be human-readable)."""
    if cost is None or (isinstance(cost, float) and cost != cost):  # NaN check without pandas import here
        return ""
    if isinstance(cost, Decimal):
        return str(cost.normalize())
    return str(cost)


def hash_normalized_feed(df: pd.DataFrame) -> str:
    """Hash normalized data (sorted by sku, then hashing only the qty+cost columns), not raw
    file bytes -- vendors regenerate feed files with embedded timestamps and reordered rows
    constantly, without any inventory actually changing, so a raw byte hash or ETag would
    almost never match and the skip-unchanged-feed optimization would never fire.

    Any other column (embedded timestamps, extra vendor-specific fields) is ignored entirely,
    by design -- only quantity_available and cost changing should count as a real change."""
    if df.empty:
        return hashlib.sha256(b"").hexdigest()

    sorted_df = df.sort_values("sku", kind="stable")
    hasher = hashlib.sha256()
    for qty, cost in zip(sorted_df["quantity_available"], sorted_df["cost"]):
        hasher.update(f"{qty}|{_normalize_cost(cost)}\n".encode("utf-8"))
    return hasher.hexdigest()
