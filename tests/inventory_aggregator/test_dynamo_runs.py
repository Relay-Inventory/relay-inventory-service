import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.persistence.dynamo_runs import DynamoRuns, RunRecord


@pytest.fixture()
def dynamo_table_name() -> str:
    return "runs"


@pytest.fixture()
def dynamodb_table(dynamo_table_name: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
            TableName=dynamo_table_name,
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=dynamo_table_name)
        yield table


def test_run_record_field_order_no_longer_matters():
    # `stage` (has a default) is declared before `requested_at` (no default) on
    # RunRecord. With a plain dataclass this ordering raises TypeError at class
    # definition time; pydantic has no such restriction.
    record = RunRecord(
        run_id="run-1",
        tenant_id="tenant-a",
        config_version=1,
        status="RUNNING",
        requested_at="2026-09-08T00:00:00+00:00",
    )
    assert record.stage is None
    assert record.requested_at == "2026-09-08T00:00:00+00:00"


def test_create_and_get_roundtrip(dynamo_table_name: str, dynamodb_table):
    runs = DynamoRuns(dynamo_table_name)
    record = RunRecord(
        run_id="run-1",
        tenant_id="tenant-a",
        config_version=1,
        status="RUNNING",
        requested_at="2026-09-08T00:00:00+00:00",
    )

    runs.create(record)
    fetched = runs.get("run-1")

    assert fetched is not None
    assert fetched.run_id == "run-1"
    assert fetched.tenant_id == "tenant-a"
    assert fetched.stage is None
