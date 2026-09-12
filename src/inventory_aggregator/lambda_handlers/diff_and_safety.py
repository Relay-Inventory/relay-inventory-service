from __future__ import annotations

import os

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.engine.canonical.io import read_parquet_bytes
from inventory_aggregator.engine.diff import diff_snapshots
from inventory_aggregator.engine.safety import SafetyThresholds, evaluate_safety


def handler(event: dict, context=None) -> dict:
    shop_id = event["shop_id"]
    snapshot_key = event["snapshot_key"]

    bucket = event.get("bucket") or os.environ["ARTIFACT_BUCKET"]
    s3 = S3Adapter(bucket)

    current = read_parquet_bytes(s3.download_bytes(snapshot_key))
    previous_bytes = s3.download_bytes_or_none(f"snapshots/{shop_id}/latest.parquet")
    previous = read_parquet_bytes(previous_bytes) if previous_bytes is not None else None

    diff = diff_snapshots(previous, current)

    previous_total_qty = int(previous["available_qty"].sum()) if previous is not None and not previous.empty else 0
    current_total_qty = int(current["available_qty"].sum()) if not current.empty else 0

    # Per-tenant threshold overrides are IMPLEMENTATION_PLAN.md Phase 2.4, not yet built --
    # every shop gets SafetyThresholds() defaults for now. Real, named gap, not silently assumed.
    safety = evaluate_safety(
        diff, SafetyThresholds(),
        previous_total_qty=previous_total_qty, current_total_qty=current_total_qty,
    )

    return {
        "halted": safety.halted,
        "reason": safety.reason,
        "diff_summary": {
            "added_skus": len(diff.added_skus),
            "removed_skus": len(diff.removed_skus),
            "changed_count": len(diff.changed),
            "unchanged_count": diff.unchanged_count,
        },
    }
