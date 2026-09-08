from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

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
from inventory_aggregator.persistence.dynamo_tenants import DynamoTenants, TenantRecord


@pytest.fixture()
def dynamo_table_name() -> str:
    return "tenant-configs"


@pytest.fixture()
def dynamodb_table(dynamo_table_name: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
            TableName=dynamo_table_name,
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "config_version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "config_version", "AttributeType": "N"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=dynamo_table_name)
        yield table


def test_get_latest_returns_none_when_missing(dynamo_table_name: str, dynamodb_table):
    tenants = DynamoTenants(dynamo_table_name)
    assert tenants.get_latest("tenant-unknown") is None


def test_get_latest_returns_latest_config_version(dynamo_table_name: str, dynamodb_table):
    tenants = DynamoTenants(dynamo_table_name)
    tenants.put(TenantRecord(tenant_id="tenant-a", config_version=1, config={"k": "v1"}))
    tenants.put(TenantRecord(tenant_id="tenant-a", config_version=2, config={"k": "v2"}))

    latest = tenants.get_latest("tenant-a")

    assert latest is not None
    assert latest.config_version == 2
    assert latest.config == {"k": "v2"}


def test_get_latest_isolated_by_tenant(dynamo_table_name: str, dynamodb_table):
    tenants = DynamoTenants(dynamo_table_name)
    tenants.put(TenantRecord(tenant_id="tenant-a", config_version=1, config={"k": "v1"}))
    tenants.put(TenantRecord(tenant_id="tenant-b", config_version=3, config={"k": "b3"}))
    tenants.put(TenantRecord(tenant_id="tenant-b", config_version=2, config={"k": "b2"}))

    latest_a = tenants.get_latest("tenant-a")
    latest_b = tenants.get_latest("tenant-b")

    assert latest_a is not None
    assert latest_b is not None
    assert latest_a.config_version == 1
    assert latest_b.config_version == 3


def test_tenant_record_config_survives_put_get_roundtrip_with_decimal_fields(
    dynamo_table_name: str, dynamodb_table
):
    tenant_config = TenantConfig(
        tenant_id="tenant-decimal",
        timezone="UTC",
        default_currency="USD",
        vendors=[
            VendorConfig(
                vendor_id="vendor-a",
                inbound=InboundConfig(type="local"),
                parser=ParserConfig(format="csv", column_map={}),
                cost_adjustment=Decimal("1.2345"),
            )
        ],
        pricing=PricingConfig(
            base_margin_pct=Decimal("0.2"),
            min_price=Decimal("12.3456"),
            shipping_handling_flat=Decimal("9.99"),
            map_policy=MapPolicyConfig(),
            rounding=RoundingConfig(mode="nearest", increment=Decimal("0.01")),
        ),
        merge=MergeConfig(
            strategy="best_offer",
            best_offer=BestOfferConfig(
                sort_by=["in_stock_desc"],
                landed_cost=BestOfferLandedCost(include_shipping_handling=True),
            ),
        ),
        output=OutputConfig(columns=["sku", "quantity_available", "price"]),
    )

    tenants = DynamoTenants(dynamo_table_name)
    # error_policy.max_invalid_row_pct was previously a plain `float`, which boto3's
    # DynamoDB resource API rejects outright (TypeError: "Float types are not supported.
    # Use Decimal types instead."), independent of the Decimal fields elsewhere in the
    # document -- confirmed by reproducing the failure with the full, unfiltered dump
    # before the field was retyped to Decimal in app/models/config.py. No exclusion
    # needed now; the full config round-trips.
    record = TenantRecord(
        tenant_id="tenant-decimal",
        config_version=1,
        config=tenant_config.model_dump(),
    )
    tenants.put(record)

    fetched = tenants.get("tenant-decimal", 1)

    assert fetched is not None
    assert fetched.config["pricing"]["base_margin_pct"] == Decimal("0.2")
    assert fetched.config["pricing"]["min_price"] == Decimal("12.3456")
    assert fetched.config["vendors"][0]["cost_adjustment"] == Decimal("1.2345")
