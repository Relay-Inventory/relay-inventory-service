import boto3
import pandas as pd
from moto import mock_aws

from inventory_aggregator.engine.canonical.io import write_parquet_bytes
from inventory_aggregator.lambda_handlers.diff_and_safety import handler


def _upload_snapshot(client, bucket: str, key: str, rows: list[dict]) -> None:
    df = pd.DataFrame(rows, columns=["sku", "available_qty", "source_vendor_id"])
    client.put_object(Bucket=bucket, Key=key, Body=write_parquet_bytes(df))


@mock_aws
def test_diff_and_safety_first_ever_run_does_not_raise_and_treats_everything_as_added() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    _upload_snapshot(
        client, "test-bucket", "snapshots/shop1/run-1.parquet",
        [
            {"sku": "SKU1", "available_qty": 5, "source_vendor_id": "v1"},
            {"sku": "SKU2", "available_qty": 3, "source_vendor_id": "v2"},
        ],
    )
    # deliberately no snapshots/shop1/latest.parquet uploaded -- this shop has never run before

    result = handler({"shop_id": "shop1", "snapshot_key": "snapshots/shop1/run-1.parquet", "bucket": "test-bucket"})

    assert result["diff_summary"] == {
        "added_skus": 2,
        "removed_skus": 0,
        "changed_count": 0,
        "unchanged_count": 0,
    }


@mock_aws
def test_diff_and_safety_trips_threshold_returns_halted_with_reason() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    previous_rows = [{"sku": f"SKU{i}", "available_qty": 10, "source_vendor_id": "v1"} for i in range(10)]
    _upload_snapshot(client, "test-bucket", "snapshots/shop1/latest.parquet", previous_rows)
    # zero out 6 of the 10 SKUs -- 60% changed, over the default 50% max_changed_sku_pct.
    current_rows = [
        {"sku": f"SKU{i}", "available_qty": (0 if i < 6 else 10), "source_vendor_id": "v1"}
        for i in range(10)
    ]
    _upload_snapshot(client, "test-bucket", "snapshots/shop1/run-2.parquet", current_rows)

    result = handler({"shop_id": "shop1", "snapshot_key": "snapshots/shop1/run-2.parquet", "bucket": "test-bucket"})

    assert result["halted"] is True
    assert isinstance(result["reason"], str) and result["reason"]
    assert "changed" in result["reason"]


@mock_aws
def test_diff_and_safety_normal_small_diff_not_halted() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    previous_rows = [{"sku": f"SKU{i}", "available_qty": 10, "source_vendor_id": "v1"} for i in range(100)]
    _upload_snapshot(client, "test-bucket", "snapshots/shop1/latest.parquet", previous_rows)
    # Only one SKU's quantity drops slightly -- 1% changed, no zeroed SKUs, negligible qty drop.
    current_rows = [
        {"sku": f"SKU{i}", "available_qty": (8 if i == 0 else 10), "source_vendor_id": "v1"}
        for i in range(100)
    ]
    _upload_snapshot(client, "test-bucket", "snapshots/shop1/run-2.parquet", current_rows)

    result = handler({"shop_id": "shop1", "snapshot_key": "snapshots/shop1/run-2.parquet", "bucket": "test-bucket"})

    assert result["halted"] is False
    assert result["reason"] is None
