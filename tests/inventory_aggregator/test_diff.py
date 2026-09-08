import pandas as pd

from inventory_aggregator.engine.diff import diff_snapshots


def _snapshot(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_diff_first_run_treats_everything_as_added() -> None:
    current = _snapshot(
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"},
            {"sku": "SKU2", "available_qty": 3, "source_vendor_id": "b"},
        ]
    )
    result = diff_snapshots(None, current)
    assert sorted(result.added_skus) == ["SKU1", "SKU2"]
    assert result.removed_skus == []
    assert result.changed.empty
    assert result.unchanged_count == 0


def test_diff_first_run_with_empty_previous_dataframe_treats_everything_as_added() -> None:
    previous = _snapshot([])
    current = _snapshot([{"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"}])
    result = diff_snapshots(previous, current)
    assert result.added_skus == ["SKU1"]
    assert result.removed_skus == []
    assert result.unchanged_count == 0


def test_diff_detects_added_and_removed_skus() -> None:
    previous = _snapshot(
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"},
            {"sku": "SKU_GONE", "available_qty": 2, "source_vendor_id": "a"},
        ]
    )
    current = _snapshot(
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"},
            {"sku": "SKU_NEW", "available_qty": 4, "source_vendor_id": "b"},
        ]
    )
    result = diff_snapshots(previous, current)
    assert result.added_skus == ["SKU_NEW"]
    assert result.removed_skus == ["SKU_GONE"]
    assert result.unchanged_count == 1
    assert result.changed.empty


def test_diff_detects_quantity_change() -> None:
    previous = _snapshot([{"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"}])
    current = _snapshot([{"sku": "SKU1", "available_qty": 9, "source_vendor_id": "a"}])
    result = diff_snapshots(previous, current)
    assert len(result.changed) == 1
    row = result.changed.iloc[0]
    assert row["previous_qty"] == 5
    assert row["current_qty"] == 9
    assert result.unchanged_count == 0


def test_diff_detects_source_vendor_change_with_same_quantity() -> None:
    previous = _snapshot([{"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"}])
    current = _snapshot([{"sku": "SKU1", "available_qty": 5, "source_vendor_id": "b"}])
    result = diff_snapshots(previous, current)
    assert len(result.changed) == 1
    row = result.changed.iloc[0]
    assert row["previous_source_vendor_id"] == "a"
    assert row["current_source_vendor_id"] == "b"
    assert result.unchanged_count == 0


def test_diff_unchanged_rows_not_in_changed_frame() -> None:
    previous = _snapshot(
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"},
            {"sku": "SKU2", "available_qty": 3, "source_vendor_id": "b"},
        ]
    )
    current = _snapshot(
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "a"},
            {"sku": "SKU2", "available_qty": 7, "source_vendor_id": "b"},
        ]
    )
    result = diff_snapshots(previous, current)
    assert "SKU1" not in result.changed["sku"].tolist()
    assert result.changed["sku"].tolist() == ["SKU2"]
    assert result.unchanged_count == 1
