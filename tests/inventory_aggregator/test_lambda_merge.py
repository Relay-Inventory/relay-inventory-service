from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.engine.canonical.io import read_parquet_bytes, write_parquet_bytes
from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.app.models.config import (
    BestOfferConfig,
    BestOfferLandedCost,
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
)
from inventory_aggregator.lambda_handlers.merge import handler
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


def _tenant_config() -> TenantConfig:
    return TenantConfig(
        tenant_id="shop-1",
        timezone="UTC",
        default_currency="USD",
        vendors=[],
        pricing=PricingConfig(
            base_margin_pct=Decimal("0.1"),
            min_price=Decimal("0"),
            shipping_handling_flat=Decimal("0"),
            map_policy=MapPolicyConfig(enforce=False),
            rounding=RoundingConfig(mode="nearest", increment=Decimal("0.01")),
        ),
        merge=MergeConfig(
            strategy="best_offer",
            best_offer=BestOfferConfig(landed_cost=BestOfferLandedCost()),
        ),
        output=OutputConfig(columns=["sku", "available_qty", "price"]),
    )


def _record(sku: str, vendor_id: str, quantity_available: int, cost: str) -> InventoryRecord:
    return InventoryRecord(
        sku=sku,
        vendor_id=vendor_id,
        quantity_available=quantity_available,
        cost=Decimal(cost),
        price=Decimal("0"),
    )


def _write_part(s3, key: str, records: list[InventoryRecord]) -> None:
    df = __import__("pandas").DataFrame([r.model_dump() for r in records])
    s3.put_object(Bucket=BUCKET, Key=key, Body=write_parquet_bytes(df))


def _base_event(**overrides) -> dict:
    event = {
        "shop_id": "shop-1",
        "run_id": "run-2",
        "tenant_config": _tenant_config().model_dump(),
        "bucket": BUCKET,
        "table_name": TABLE,
    }
    event.update(overrides)
    return event


def test_merge_multiple_vendors_three_way_sku_overlap(aws_env) -> None:
    # SKU1 is offered by all three vendors -- the cheapest in-stock vendor (vendor-c) must win.
    part_a = "parts/shop-1/run-2/vendor-a.parquet"
    part_b = "parts/shop-1/run-2/vendor-b.parquet"
    part_c = "parts/shop-1/run-2/vendor-c.parquet"
    _write_part(aws_env, part_a, [_record("SKU1", "vendor-a", 5, "10")])
    _write_part(aws_env, part_b, [_record("SKU1", "vendor-b", 3, "12")])
    _write_part(aws_env, part_c, [_record("SKU1", "vendor-c", 2, "8"), _record("SKU2", "vendor-c", 4, "5")])

    vendor_results = [
        {"vendor_id": "vendor-a", "feed_id": "default", "changed": True, "part_key": part_a},
        {"vendor_id": "vendor-b", "feed_id": "default", "changed": True, "part_key": part_b},
        {"vendor_id": "vendor-c", "feed_id": "default", "changed": True, "part_key": part_c},
    ]

    result = handler(_base_event(vendor_results=vendor_results))

    snapshot = read_parquet_bytes(aws_env.get_object(Bucket=BUCKET, Key=result["snapshot_key"])["Body"].read())
    sku1 = snapshot[snapshot["sku"] == "SKU1"].iloc[0]
    assert sku1["available_qty"] == 10
    assert sku1["source_vendor_id"] == "vendor-c"
    assert sku1["vendor_count"] == 3

    sku2 = snapshot[snapshot["sku"] == "SKU2"].iloc[0]
    assert sku2["available_qty"] == 4


def test_unchanged_feed_reuses_previous_run_part_instead_of_dropping(aws_env) -> None:
    table = SingleTable(TABLE)
    # Simulate what fetch_and_hash would have recorded on a prior run where vendor-b's feed
    # was last actually written.
    previous_part_key = "parts/shop-1/run-1/vendor-b.parquet"
    _write_part(aws_env, previous_part_key, [_record("SKU9", "vendor-b", 6, "3")])
    table.put_feed_state(
        "shop-1",
        "vendor-b",
        "default",
        last_normalized_hash="some-hash",
        last_fetch_status="ok",
        last_part_key=previous_part_key,
    )

    # This run: vendor-a changed and wrote a fresh part; vendor-b was unchanged (no part_key).
    part_a = "parts/shop-1/run-2/vendor-a.parquet"
    _write_part(aws_env, part_a, [_record("SKU1", "vendor-a", 5, "10")])

    vendor_results = [
        {"vendor_id": "vendor-a", "feed_id": "default", "changed": True, "part_key": part_a},
        {"vendor_id": "vendor-b", "feed_id": "default", "changed": False, "part_key": None},
    ]

    result = handler(_base_event(vendor_results=vendor_results))

    snapshot = read_parquet_bytes(aws_env.get_object(Bucket=BUCKET, Key=result["snapshot_key"])["Body"].read())
    # vendor-b's SKU9 must still be present in the merged snapshot even though it wrote no
    # part this run -- this is the bug the unchanged-feed-part-reuse fix guards against.
    assert "SKU9" in set(snapshot["sku"])
    sku9 = snapshot[snapshot["sku"] == "SKU9"].iloc[0]
    assert sku9["available_qty"] == 6
    assert sku9["source_vendor_id"] == "vendor-b"


def test_vendor_with_no_part_and_no_prior_feed_state_is_skipped_not_failed(aws_env) -> None:
    """A vendor reporting changed=False/part_key=None with no FEED_STATE# history at all
    shouldn't happen in normal operation (fetch_and_hash always writes part+state together
    the first time), but merge must not fail the whole run over one vendor's data gap --
    it should skip that vendor and merge everyone else."""
    part_a = "parts/shop-1/run-2/vendor-a.parquet"
    _write_part(aws_env, part_a, [_record("SKU1", "vendor-a", 5, "10")])

    vendor_results = [
        {"vendor_id": "vendor-a", "feed_id": "default", "changed": True, "part_key": part_a},
        {"vendor_id": "vendor-never-seen", "feed_id": "default", "changed": False, "part_key": None},
    ]

    result = handler(_base_event(vendor_results=vendor_results))

    snapshot = read_parquet_bytes(aws_env.get_object(Bucket=BUCKET, Key=result["snapshot_key"])["Body"].read())
    assert set(snapshot["sku"]) == {"SKU1"}


def test_output_written_to_run_scoped_snapshot_key_never_latest(aws_env) -> None:
    part_a = "parts/shop-1/run-2/vendor-a.parquet"
    _write_part(aws_env, part_a, [_record("SKU1", "vendor-a", 5, "10")])
    vendor_results = [
        {"vendor_id": "vendor-a", "feed_id": "default", "changed": True, "part_key": part_a},
    ]

    result = handler(_base_event(run_id="run-2", vendor_results=vendor_results))

    assert result["snapshot_key"] == "snapshots/shop-1/run-2.parquet"
    with pytest.raises(aws_env.exceptions.NoSuchKey):
        aws_env.get_object(Bucket=BUCKET, Key="snapshots/shop-1/latest.parquet")
