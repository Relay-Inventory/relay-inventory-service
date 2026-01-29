import boto3
import pytest
from moto import mock_aws

from relay_inventory.persistence.dynamo_tenants import DynamoTenants, TenantRecord


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
