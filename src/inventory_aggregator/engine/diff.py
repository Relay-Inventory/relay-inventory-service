from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class SnapshotDiff:
    added_skus: list  # list[str] - in current, not in previous
    removed_skus: list  # list[str] - in previous, not in current
    changed: pd.DataFrame  # sku, previous_qty, current_qty, previous_source_vendor_id, current_source_vendor_id — only changed rows
    unchanged_count: int
    is_first_run: bool = False


def diff_snapshots(previous: pd.DataFrame | None, current: pd.DataFrame) -> SnapshotDiff:
    """previous is None on a tenant's first-ever run — treat every current SKU as 'added'.

    is_first_run is a real, load-bearing field, not informational: every SKU necessarily shows
    up as "added" on a first run, which would otherwise always read as a 100% change against
    evaluate_safety's max_changed_sku_pct threshold -- a shop's first sync would halt every
    single time, with no way to ever reach a successful baseline, since a halted run never
    promotes a snapshot. evaluate_safety uses this flag to exempt that one check specifically
    on a first run, not to skip safety checking altogether."""
    if previous is None or previous.empty:
        return SnapshotDiff(
            added_skus=current["sku"].tolist() if not current.empty else [],
            removed_skus=[],
            changed=pd.DataFrame(columns=["sku", "previous_qty", "current_qty", "previous_source_vendor_id", "current_source_vendor_id"]),
            unchanged_count=0,
            is_first_run=True,
        )
    merged = pd.merge(
        previous[["sku", "available_qty", "source_vendor_id"]],
        current[["sku", "available_qty", "source_vendor_id"]],
        on="sku", how="outer", suffixes=("_previous", "_current"), indicator=True,
    )
    added_skus = merged[merged["_merge"] == "right_only"]["sku"].tolist()
    removed_skus = merged[merged["_merge"] == "left_only"]["sku"].tolist()
    both = merged[merged["_merge"] == "both"]
    is_changed = (both["available_qty_previous"] != both["available_qty_current"]) | \
                 (both["source_vendor_id_previous"] != both["source_vendor_id_current"])
    changed_rows = both[is_changed].rename(columns={
        "available_qty_previous": "previous_qty", "available_qty_current": "current_qty",
        "source_vendor_id_previous": "previous_source_vendor_id",
        "source_vendor_id_current": "current_source_vendor_id",
    })[["sku", "previous_qty", "current_qty", "previous_source_vendor_id", "current_source_vendor_id"]]
    unchanged_count = len(both) - len(changed_rows)
    return SnapshotDiff(added_skus=added_skus, removed_skus=removed_skus, changed=changed_rows, unchanged_count=unchanged_count)
