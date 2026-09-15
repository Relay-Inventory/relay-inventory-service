from decimal import Decimal

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.models.config import (
    BestOfferConfig,
    BestOfferLandedCost,
    InboundConfig,
    MapPolicyConfig,
    MergeConfig,
    OutputConfig,
    ParserConfig,
    PricingConfig,
    RoundingConfig,
    TenantConfig,
    VendorConfig,
)
from inventory_aggregator.engine.canonical.io import write_parquet_bytes
from inventory_aggregator.lambda_handlers import write_to_shopify as write_to_shopify_module
from inventory_aggregator.lambda_handlers.write_to_shopify import handler
from inventory_aggregator.persistence.single_table import SingleTable


def _vendor(vendor_id: str) -> VendorConfig:
    return VendorConfig(
        vendor_id=vendor_id,
        inbound=InboundConfig(type="s3", s3_prefix="prefix/"),
        parser=ParserConfig(format="csv"),
    )


def _tenant_config(*, location_id: str = "gid://shopify/Location/1") -> TenantConfig:
    return TenantConfig(
        tenant_id="tenant-a",
        shopify_domain="tenant-a.myshopify.com",
        timezone="UTC",
        default_currency="USD",
        location_id=location_id,
        vendors=[_vendor("v1")],
        pricing=PricingConfig(
            base_margin_pct=Decimal("0.2"),
            min_price=Decimal("1"),
            shipping_handling_flat=Decimal("0"),
            map_policy=MapPolicyConfig(),
            rounding=RoundingConfig(mode="nearest", increment=Decimal("0.01")),
        ),
        merge=MergeConfig(
            strategy="best_offer",
            best_offer=BestOfferConfig(sort_by=[], landed_cost=BestOfferLandedCost()),
        ),
        output=OutputConfig(columns=["sku"]),
    )


class _FakeAdminClient:
    """Stands in for ShopifyAdminClient -- no real gql transport, no real network. Records
    every execute() call and returns a scripted sequence of responses/exceptions."""

    calls: list[dict] = []

    def __init__(self, shop_domain: str, access_token: str) -> None:
        self.shop_domain = shop_domain
        self.access_token = access_token

    def execute(self, query: str, variable_values: dict | None = None) -> dict:
        _FakeAdminClient.calls.append({"query": query, "variables": variable_values})
        return _FakeAdminClient.responses.pop(0)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeAdminClient.calls = []
    _FakeAdminClient.responses = []
    yield


@pytest.fixture()
def aws(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(write_to_shopify_module, "ShopifyAdminClient", _FakeAdminClient)
    with mock_aws():
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket="test-bucket")

        dynamo = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamo.create_table(
            TableName="shop-data",
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
        table.meta.client.get_waiter("table_exists").wait(TableName="shop-data")

        secrets_client = boto3.client("secretsmanager", region_name="us-east-1")
        secrets_client.create_secret(
            Name="inventory-aggregator/shop1/shopify-access-token",
            SecretString="test-access-token",
        )

        yield {"bucket": "test-bucket", "table_name": "shop-data"}


def _upload_snapshot(bucket: str, key: str, rows: list[dict]) -> None:
    df = pd.DataFrame(rows, columns=["sku", "available_qty"])
    S3Adapter(bucket).upload_bytes(key, write_parquet_bytes(df))


def test_write_to_shopify_full_success(aws) -> None:
    _upload_snapshot(aws["bucket"], "snapshots/shop1/run-1.parquet", [{"sku": "SKU1", "available_qty": 5}])
    table = SingleTable(aws["table_name"])
    table.put_sku_mapping("shop1", "SKU1", "gid://shopify/InventoryItem/111")

    _FakeAdminClient.responses = [
        {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"name": "available", "delta": 5}]},
                "userErrors": [],
            }
        }
    ]

    result = handler({
        "shop_id": "shop1",
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "tenant_config": _tenant_config().model_dump(mode="json"),
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result == {"write_status": "SUCCEEDED", "written_count": 1, "errors": []}
    assert len(_FakeAdminClient.calls) == 1
    sent_quantities = _FakeAdminClient.calls[0]["variables"]["input"]["quantities"]
    assert sent_quantities == [
        {"inventoryItemId": "gid://shopify/InventoryItem/111", "locationId": "gid://shopify/Location/1", "quantity": 5}
    ]


def test_write_to_shopify_full_failure_missing_access_token_propagates(aws) -> None:
    _upload_snapshot(aws["bucket"], "snapshots/shop2/run-1.parquet", [{"sku": "SKU1", "available_qty": 5}])

    with pytest.raises(ValueError, match="no Shopify access token"):
        handler({
            "shop_id": "shop2",
            "snapshot_key": "snapshots/shop2/run-1.parquet",
            "tenant_config": _tenant_config().model_dump(mode="json"),
            "bucket": aws["bucket"],
            "table_name": aws["table_name"],
        })


def test_write_to_shopify_missing_location_id_raises(aws) -> None:
    with pytest.raises(ValueError, match="no location_id"):
        handler({
            "shop_id": "shop1",
            "snapshot_key": "snapshots/shop1/run-1.parquet",
            "tenant_config": _tenant_config(location_id=None).model_dump(mode="json"),
            "bucket": aws["bucket"],
            "table_name": aws["table_name"],
        })


def test_write_to_shopify_partial_success_unmapped_sku_recorded_as_error(aws) -> None:
    _upload_snapshot(
        aws["bucket"], "snapshots/shop1/run-1.parquet",
        [{"sku": "SKU-MAPPED", "available_qty": 5}, {"sku": "SKU-UNMAPPED", "available_qty": 3}],
    )
    table = SingleTable(aws["table_name"])
    table.put_sku_mapping("shop1", "SKU-MAPPED", "gid://shopify/InventoryItem/111")
    # SKU-UNMAPPED deliberately has no mapping.

    _FakeAdminClient.responses = [
        {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"name": "available", "delta": 5}]},
                "userErrors": [],
            }
        }
    ]

    result = handler({
        "shop_id": "shop1",
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "tenant_config": _tenant_config().model_dump(mode="json"),
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["write_status"] == "PARTIAL"
    assert result["written_count"] == 1
    assert result["errors"] == [{"sku": "SKU-UNMAPPED", "error": "no_shopify_inventory_mapping"}]


def test_write_to_shopify_all_unmapped_is_failed_with_zero_written(aws) -> None:
    _upload_snapshot(aws["bucket"], "snapshots/shop1/run-1.parquet", [{"sku": "SKU-UNMAPPED", "available_qty": 3}])

    result = handler({
        "shop_id": "shop1",
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "tenant_config": _tenant_config().model_dump(mode="json"),
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result == {
        "write_status": "FAILED",
        "written_count": 0,
        "errors": [{"sku": "SKU-UNMAPPED", "error": "no_shopify_inventory_mapping"}],
    }
    assert _FakeAdminClient.calls == []


def test_write_to_shopify_shopify_user_errors_recorded_as_partial(aws) -> None:
    _upload_snapshot(
        aws["bucket"], "snapshots/shop1/run-1.parquet",
        [{"sku": "SKU1", "available_qty": 5}, {"sku": "SKU2", "available_qty": 2}],
    )
    table = SingleTable(aws["table_name"])
    table.put_sku_mapping("shop1", "SKU1", "gid://shopify/InventoryItem/111")
    table.put_sku_mapping("shop1", "SKU2", "gid://shopify/InventoryItem/222")

    _FakeAdminClient.responses = [
        {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"name": "available", "delta": 5}]},
                "userErrors": [{"field": ["quantities", "1"], "message": "inventory item not stocked at location"}],
            }
        }
    ]

    result = handler({
        "shop_id": "shop1",
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "tenant_config": _tenant_config().model_dump(mode="json"),
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["write_status"] == "PARTIAL"
    assert result["written_count"] == 1
    assert result["errors"] == [
        {"error": "inventory item not stocked at location", "field": ["quantities", "1"]}
    ]


def test_write_to_shopify_batches_over_250_quantities(aws) -> None:
    rows = [{"sku": f"SKU{i}", "available_qty": 1} for i in range(300)]
    _upload_snapshot(aws["bucket"], "snapshots/shop1/run-1.parquet", rows)
    table = SingleTable(aws["table_name"])
    for i in range(300):
        table.put_sku_mapping("shop1", f"SKU{i}", f"gid://shopify/InventoryItem/{i}")

    _FakeAdminClient.responses = [
        {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"name": "available", "delta": 1}] * 250},
                "userErrors": [],
            }
        },
        {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"name": "available", "delta": 1}] * 50},
                "userErrors": [],
            }
        },
    ]

    result = handler({
        "shop_id": "shop1",
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "tenant_config": _tenant_config().model_dump(mode="json"),
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["write_status"] == "SUCCEEDED"
    assert result["written_count"] == 300
    assert len(_FakeAdminClient.calls) == 2
    assert len(_FakeAdminClient.calls[0]["variables"]["input"]["quantities"]) == 250
    assert len(_FakeAdminClient.calls[1]["variables"]["input"]["quantities"]) == 50
