import pandas as pd

from inventory_aggregator.engine.diff import SnapshotDiff
from inventory_aggregator.engine.safety import SafetyThresholds, evaluate_safety


def _changed_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["sku", "previous_qty", "current_qty", "previous_source_vendor_id", "current_source_vendor_id"])
    return pd.DataFrame(rows)


def test_evaluate_safety_trips_max_changed_sku_pct() -> None:
    # 10 total SKUs, 6 changed (1 in "changed" rows, 5 added) => 60% > 50% threshold.
    diff = SnapshotDiff(
        added_skus=[f"NEW{i}" for i in range(5)],
        removed_skus=[],
        changed=_changed_df(
            [{"sku": "SKU1", "previous_qty": 5, "current_qty": 9, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}]
        ),
        unchanged_count=4,
    )
    decision = evaluate_safety(diff, SafetyThresholds(), previous_total_qty=100, current_total_qty=100)
    assert decision.halted is True
    assert "60% of SKUs changed" in decision.reason
    assert "50%" in decision.reason


def test_evaluate_safety_trips_max_zeroed_skus() -> None:
    # Keep changed_pct low (60/200 = 30%), but 51 of those changed rows zeroed out (> default 50).
    changed_rows = []
    for i in range(51):
        changed_rows.append(
            {"sku": f"ZERO{i}", "previous_qty": 5, "current_qty": 0, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}
        )
    for i in range(9):
        changed_rows.append(
            {"sku": f"QTY{i}", "previous_qty": 5, "current_qty": 6, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}
        )
    diff = SnapshotDiff(
        added_skus=[],
        removed_skus=[],
        changed=_changed_df(changed_rows),
        unchanged_count=140,
    )
    decision = evaluate_safety(diff, SafetyThresholds(), previous_total_qty=1000, current_total_qty=980)
    assert decision.halted is True
    assert "51 SKUs zeroed out" in decision.reason
    assert "threshold 50" in decision.reason


def test_evaluate_safety_trips_max_qty_drop_pct() -> None:
    # Only 1 changed SKU out of a large total (low changed_pct), no zeroed SKUs, but a large
    # aggregate quantity drop.
    diff = SnapshotDiff(
        added_skus=[],
        removed_skus=[],
        changed=_changed_df(
            [{"sku": "SKU1", "previous_qty": 100, "current_qty": 40, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}]
        ),
        unchanged_count=199,
    )
    decision = evaluate_safety(diff, SafetyThresholds(), previous_total_qty=1000, current_total_qty=400)
    assert decision.halted is True
    assert "60% drop in total available quantity" in decision.reason
    assert "threshold 50%" in decision.reason


def test_evaluate_safety_normal_small_diff_passes() -> None:
    diff = SnapshotDiff(
        added_skus=["NEW1"],
        removed_skus=[],
        changed=_changed_df(
            [{"sku": "SKU1", "previous_qty": 10, "current_qty": 8, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}]
        ),
        unchanged_count=98,
    )
    decision = evaluate_safety(diff, SafetyThresholds(), previous_total_qty=1000, current_total_qty=980)
    assert decision.halted is False
    assert decision.reason is None


def test_evaluate_safety_when_multiple_thresholds_trip_changed_sku_pct_wins_first() -> None:
    # Construct a diff where all three thresholds would independently trip:
    # - changed_pct: 100% of SKUs changed (all removed)
    # - zeroed_skus: 51 zeroed changed rows
    # - qty_drop_pct: 90% quantity drop
    # Our implementation checks max_changed_sku_pct first, so that reason must win.
    changed_rows = [
        {"sku": f"ZERO{i}", "previous_qty": 5, "current_qty": 0, "previous_source_vendor_id": "a", "current_source_vendor_id": "a"}
        for i in range(51)
    ]
    diff = SnapshotDiff(
        added_skus=[],
        removed_skus=[f"GONE{i}" for i in range(60)],
        changed=_changed_df(changed_rows),
        unchanged_count=0,
    )
    decision = evaluate_safety(diff, SafetyThresholds(), previous_total_qty=1000, current_total_qty=100)
    assert decision.halted is True
    assert "of SKUs changed" in decision.reason
    assert "zeroed" not in decision.reason
    assert "drop in total available quantity" not in decision.reason
