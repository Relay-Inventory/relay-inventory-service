from decimal import Decimal

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from inventory_aggregator.engine.canonical.io import read_parquet_bytes
from inventory_aggregator.lambda_handlers.fetch_and_hash import handler
from inventory_aggregator.persistence.single_table import SingleTable

BUCKET = "artifact-bucket"
TABLE = "shop-data"


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "shop_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "shop_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        resource.meta.client.get_waiter("table_exists").wait(TableName=TABLE)
        yield s3


def _vendor_config(**overrides) -> dict:
    config = {
        "vendor_id": "vendor-a",
        "inbound": {"type": "s3"},
        "parser": {"format": "csv"},
    }
    config.update(overrides)
    return config


def _upload_raw(s3, shop_id: str, vendor_id: str, run_id: str, csv_text: str) -> None:
    s3.put_object(
        Bucket=BUCKET,
        Key=f"raw/{shop_id}/{vendor_id}/{run_id}.csv",
        Body=csv_text.encode("utf-8"),
    )


def _base_event(**overrides) -> dict:
    event = {
        "shop_id": "shop-1",
        "run_id": "run-1",
        "vendor_config": _vendor_config(),
        "bucket": BUCKET,
        "table_name": TABLE,
    }
    event.update(overrides)
    return event


def test_changed_feed_writes_part_and_updates_feed_state(aws_env) -> None:
    csv_text = "sku,quantity_available,cost,price\nSKU1,10,5.00,9.99\nSKU2,3,2.00,4.99\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-1", csv_text)

    result = handler(_base_event())

    assert result["changed"] is True
    assert result["vendor_id"] == "vendor-a"
    assert result["part_key"] == "parts/shop-1/run-1/vendor-a.parquet"
    assert result["error_count"] == 0

    part_bytes = aws_env.get_object(Bucket=BUCKET, Key=result["part_key"])["Body"].read()
    df = read_parquet_bytes(part_bytes)
    assert set(df["sku"]) == {"SKU1", "SKU2"}

    table = SingleTable(TABLE)
    feed_state = table.get_feed_state("shop-1", "vendor-a", "default")
    assert feed_state is not None
    assert feed_state.last_normalized_hash is not None
    assert feed_state.last_part_key == result["part_key"]
    assert feed_state.last_fetch_status == "ok"


def test_unchanged_feed_skips_s3_write(aws_env) -> None:
    csv_text = "sku,quantity_available,cost,price\nSKU1,10,5.00,9.99\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-1", csv_text)
    first = handler(_base_event(run_id="run-1"))
    assert first["changed"] is True

    # Same normalized qty/cost, different unrelated column formatting/order -- the hash only
    # looks at sku/quantity_available/cost, so this must still be treated as unchanged.
    csv_text_2 = "sku,quantity_available,cost,price\nSKU1,10,5.00,11.00\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-2", csv_text_2)
    second = handler(_base_event(run_id="run-2"))

    assert second["changed"] is False
    assert second["part_key"] is None

    with pytest.raises(aws_env.exceptions.NoSuchKey):
        aws_env.get_object(Bucket=BUCKET, Key="parts/shop-1/run-2/vendor-a.parquet")

    # FEED_STATE# must still point at run-1's part -- it was never overwritten.
    table = SingleTable(TABLE)
    feed_state = table.get_feed_state("shop-1", "vendor-a", "default")
    assert feed_state.last_part_key == "parts/shop-1/run-1/vendor-a.parquet"


def test_inclusion_rule_filters_rows(aws_env) -> None:
    csv_text = "sku,quantity_available,cost,price\nKEEP,5,1.00,2.00\nDROP,0,1.00,2.00\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-1", csv_text)

    vendor_config = _vendor_config(rules={"inclusion_condition": "quantity_available > 0"})
    result = handler(_base_event(vendor_config=vendor_config))

    assert result["changed"] is True
    part_bytes = aws_env.get_object(Bucket=BUCKET, Key=result["part_key"])["Body"].read()
    df = read_parquet_bytes(part_bytes)
    assert list(df["sku"]) == ["KEEP"]


def test_all_rows_excluded_by_rules_still_hashes_and_writes_empty_part(aws_env) -> None:
    """Covers the empty-DataFrame branch: a vendor whose feed parses to rows but has every
    row filtered out by its own inclusion rule still produces a valid (empty) hash and a
    valid (empty) Parquet part, rather than raising on an empty DataFrame."""
    csv_text = "sku,quantity_available,cost,price\nSKU1,0,5.00,9.99\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-1", csv_text)

    vendor_config = _vendor_config(rules={"inclusion_condition": "quantity_available > 0"})
    result = handler(_base_event(vendor_config=vendor_config))

    assert result["changed"] is True
    part_bytes = aws_env.get_object(Bucket=BUCKET, Key=result["part_key"])["Body"].read()
    df = read_parquet_bytes(part_bytes)
    assert len(df) == 0


def test_buffer_qty_adjusts_quantities(aws_env) -> None:
    csv_text = "sku,quantity_available,cost,price\nSKU1,10,5.00,9.99\n"
    _upload_raw(aws_env, "shop-1", "vendor-a", "run-1", csv_text)

    vendor_config = _vendor_config(buffer_qty=3)
    result = handler(_base_event(vendor_config=vendor_config))

    part_bytes = aws_env.get_object(Bucket=BUCKET, Key=result["part_key"])["Body"].read()
    df = read_parquet_bytes(part_bytes)
    row = df[df["sku"] == "SKU1"].iloc[0]
    assert row["quantity_available"] == 7
