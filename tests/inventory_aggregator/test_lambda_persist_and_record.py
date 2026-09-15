import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.lambda_handlers.persist_and_record import handler
from inventory_aggregator.persistence.single_table import SingleTable, run_sk


@pytest.fixture()
def aws(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
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
        yield {"bucket": "test-bucket", "table_name": "shop-data"}


def test_persist_and_record_promotes_candidate_and_writes_succeeded_run(aws) -> None:
    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 3,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": False,
        "diff_summary": {"added_skus": 2, "removed_skus": 0, "changed_count": 0, "unchanged_count": 10},
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "SUCCEEDED"
    assert s3.download_bytes("snapshots/shop1/latest.parquet") == b"candidate-bytes"

    table = SingleTable(aws["table_name"])
    run_item = table.get_item("shop1", run_sk("run-1"))
    assert run_item is not None
    assert run_item.status == "SUCCEEDED"
    assert run_item.config_version == 3
    assert run_item.failed_stage is None
    assert run_item.error_message is None
    assert run_item.artifacts["snapshot_key"] == "snapshots/shop1/run-1.parquet"
    assert run_item.artifacts["diff_summary"]["added_skus"] == 2


def test_persist_and_record_halted_run_does_not_create_latest_and_preserves_reason(aws) -> None:
    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 1,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": True,
        "reason": "80% of SKUs changed (threshold 50%)",
        "diff_summary": {"added_skus": 8, "removed_skus": 0, "changed_count": 0, "unchanged_count": 2},
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "HALTED"
    assert s3.download_bytes_or_none("snapshots/shop1/latest.parquet") is None

    table = SingleTable(aws["table_name"])
    run_item = table.get_item("shop1", run_sk("run-1"))
    assert run_item is not None
    assert run_item.status == "HALTED"
    assert run_item.failed_stage == "DiffAndSafety"
    assert run_item.error_message == "80% of SKUs changed (threshold 50%)"
    assert run_item.artifacts["diff_summary"]["added_skus"] == 8


def test_persist_and_record_halted_run_leaves_existing_latest_untouched(aws) -> None:
    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/latest.parquet", b"old-latest-bytes")
    s3.upload_bytes("snapshots/shop1/run-2.parquet", b"new-candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-2",
        "config_version": 1,
        "snapshot_key": "snapshots/shop1/run-2.parquet",
        "halted": True,
        "reason": "some threshold reason",
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "HALTED"
    assert s3.download_bytes("snapshots/shop1/latest.parquet") == b"old-latest-bytes"


def test_persist_and_record_email_failure_does_not_change_run_status(aws, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for COMMIT_PLAN.md Commit 4.5: an email provider failure must never
    propagate out of the handler or alter the run's own already-recorded status."""

    class RaisingEmailSender:
        def send(self, subject: str, html_body: str, *, to=None) -> None:
            raise RuntimeError("email provider had a bad moment")

    monkeypatch.setattr(
        "inventory_aggregator.lambda_handlers.persist_and_record.LoggingEmailSender",
        RaisingEmailSender,
    )

    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 3,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": False,
        "diff_summary": {"added_skus": 2, "removed_skus": 0, "changed_count": 0, "unchanged_count": 10},
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "SUCCEEDED"

    table = SingleTable(aws["table_name"])
    run_item = table.get_item("shop1", run_sk("run-1"))
    assert run_item is not None
    assert run_item.status == "SUCCEEDED"


def test_persist_and_record_email_failure_does_not_change_halted_status(aws, monkeypatch: pytest.MonkeyPatch) -> None:
    class RaisingEmailSender:
        def send(self, subject: str, html_body: str, *, to=None) -> None:
            raise RuntimeError("email provider had a bad moment")

    monkeypatch.setattr(
        "inventory_aggregator.lambda_handlers.persist_and_record.LoggingEmailSender",
        RaisingEmailSender,
    )

    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 1,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": True,
        "reason": "80% of SKUs changed (threshold 50%)",
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "HALTED"

    table = SingleTable(aws["table_name"])
    run_item = table.get_item("shop1", run_sk("run-1"))
    assert run_item is not None
    assert run_item.status == "HALTED"
