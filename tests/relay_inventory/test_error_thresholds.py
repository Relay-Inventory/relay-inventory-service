import json

import boto3
import pytest
from moto import mock_aws

from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.persistence.dynamo_runs import DynamoRuns
from relay_inventory.persistence.dynamo_tenants import DynamoTenants, TenantRecord
from relay_inventory.scripts.worker import Worker
from relay_inventory.util.errors import NonRetryableError


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture()
def aws_resources(aws_env):
    with mock_aws():
        yield


@pytest.fixture()
def s3_bucket(aws_resources):
    client = boto3.client("s3", region_name="us-east-1")
    bucket = "inventory-bucket"
    client.create_bucket(Bucket=bucket)
    return bucket


@pytest.fixture()
def dynamodb_tables(aws_resources):
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    tenants_table = resource.create_table(
        TableName="tenant-configs",
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
    runs_table = resource.create_table(
        TableName="run-records",
        KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    tenants_table.meta.client.get_waiter("table_exists").wait(TableName="tenant-configs")
    runs_table.meta.client.get_waiter("table_exists").wait(TableName="run-records")
    return {"tenants": tenants_table.name, "runs": runs_table.name}


def _base_config(error_policy: dict) -> dict:
    return {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "timezone": "UTC",
        "default_currency": "USD",
        "vendors": [
            {
                "vendor_id": "vendor-a",
                "inbound": {"type": "s3", "s3_prefix": "vendor-a/"},
                "parser": {"format": "csv", "column_map": {}},
            }
        ],
        "pricing": {
            "base_margin_pct": 0.1,
            "min_price": 1,
            "shipping_handling_flat": 0,
            "map_policy": {"enforce": True, "map_floor_behavior": "max(price, map_price)"},
            "rounding": {"mode": "nearest", "increment": "0.01"},
        },
        "merge": {
            "strategy": "best_offer",
            "best_offer": {"sort_by": [], "landed_cost": {"include_shipping_handling": True}},
        },
        "output": {"format": "csv", "columns": ["sku", "quantity_available", "price"]},
        "error_policy": error_policy,
    }


def _put_tenant_config(table_name: str, config: dict) -> None:
    tenants = DynamoTenants(table_name)
    tenants.put(TenantRecord(tenant_id="tenant-a", config_version=1, config=config))


def _upload_csv(bucket: str, key: str, csv_data: str) -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=bucket, Key=key, Body=csv_data.encode("utf-8"))


def _create_worker(bucket: str, runs_table: str, tenants_table: str) -> Worker:
    return Worker(bucket=bucket, runs_table=runs_table, tenants_table=tenants_table)


def test_invalid_rows_under_threshold_succeeds(s3_bucket, dynamodb_tables):
    config = _base_config({"max_invalid_rows": 1, "max_invalid_row_pct": 0.6})
    _put_tenant_config(dynamodb_tables["tenants"], config)
    csv_data = "sku,quantity_available,price\nSKU1,10,5.00\nSKU2,not-a-number,4.00\n"
    _upload_csv(s3_bucket, "vendor-a/input.csv", csv_data)
    worker = _create_worker(s3_bucket, dynamodb_tables["runs"], dynamodb_tables["tenants"])

    job = RunJob(run_id="run-1", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)
    worker.run_job(job)

    runs = DynamoRuns(dynamodb_tables["runs"])
    record = runs.get("run-1")
    assert record is not None
    assert record.status == "SUCCEEDED"

    client = boto3.client("s3", region_name="us-east-1")
    errors_obj = client.get_object(
        Bucket=s3_bucket, Key="tenants/tenant-a/reports/run-1/errors.json"
    )
    errors = json.loads(errors_obj["Body"].read().decode("utf-8"))
    assert len(errors) == 1


def test_invalid_rows_exceed_threshold_fails(s3_bucket, dynamodb_tables):
    config = _base_config({"max_invalid_rows": 0, "max_invalid_row_pct": 0.1})
    _put_tenant_config(dynamodb_tables["tenants"], config)
    csv_data = "sku,quantity_available,price\nSKU1,10,5.00\nSKU2,not-a-number,4.00\n"
    _upload_csv(s3_bucket, "vendor-a/input.csv", csv_data)
    worker = _create_worker(s3_bucket, dynamodb_tables["runs"], dynamodb_tables["tenants"])

    job = RunJob(run_id="run-2", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)
    with pytest.raises(NonRetryableError):
        worker.run_job(job)

    runs = DynamoRuns(dynamodb_tables["runs"])
    record = runs.get("run-2")
    assert record is not None
    assert record.status == "FAILED"
    assert record.error_report_key == "tenants/tenant-a/reports/run-2/errors.json"


def test_missing_required_columns_fails_fast(s3_bucket, dynamodb_tables):
    config = _base_config({"max_invalid_rows": 1, "max_invalid_row_pct": 0.5})
    _put_tenant_config(dynamodb_tables["tenants"], config)
    csv_data = "sku,price\nSKU1,5.00\n"
    _upload_csv(s3_bucket, "vendor-a/input.csv", csv_data)
    worker = _create_worker(s3_bucket, dynamodb_tables["runs"], dynamodb_tables["tenants"])

    job = RunJob(run_id="run-3", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)
    with pytest.raises(NonRetryableError, match="missing columns"):
        worker.run_job(job)
