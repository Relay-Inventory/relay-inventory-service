from decimal import Decimal

import boto3
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
from inventory_aggregator.billing.limits import MAX_VENDORS_SOFT_CAP
from inventory_aggregator.lambda_handlers import persist_and_record as persist_and_record_module
from inventory_aggregator.lambda_handlers.persist_and_record import handler
from inventory_aggregator.persistence.single_table import SingleTable, run_sk


def _vendor(vendor_id: str) -> VendorConfig:
    return VendorConfig(
        vendor_id=vendor_id,
        inbound=InboundConfig(type="s3", s3_prefix="prefix/"),
        parser=ParserConfig(format="csv"),
    )


def _tenant_config(vendor_count: int) -> TenantConfig:
    return TenantConfig(
        tenant_id="tenant-a",
        timezone="UTC",
        default_currency="USD",
        vendors=[_vendor(f"v{i}") for i in range(vendor_count)],
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


def test_persist_and_record_over_vendor_cap_flag_reaches_diff_email(aws, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for COMMIT_PLAN.md Commit 4.6: a shop configured with more than
    MAX_VENDORS_SOFT_CAP vendors must have over_vendor_cap=True reach render_diff_email's call,
    without halting the run."""
    captured: dict = {}
    original_render = persist_and_record_module.render_diff_email

    def _spy_render(*args, **kwargs):
        captured["over_vendor_cap"] = kwargs.get("over_vendor_cap")
        return original_render(*args, **kwargs)

    monkeypatch.setattr(persist_and_record_module, "render_diff_email", _spy_render)

    table = SingleTable(aws["table_name"])
    tenant_config = _tenant_config(MAX_VENDORS_SOFT_CAP + 1)
    table.put_config("shop1", tenant_config.model_dump(), version=3)

    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 3,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": False,
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "SUCCEEDED"
    assert captured["over_vendor_cap"] is True


def test_persist_and_record_under_vendor_cap_flag_is_false(aws, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    original_render = persist_and_record_module.render_diff_email

    def _spy_render(*args, **kwargs):
        captured["over_vendor_cap"] = kwargs.get("over_vendor_cap")
        return original_render(*args, **kwargs)

    monkeypatch.setattr(persist_and_record_module, "render_diff_email", _spy_render)

    table = SingleTable(aws["table_name"])
    tenant_config = _tenant_config(3)
    table.put_config("shop1", tenant_config.model_dump(), version=3)

    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 3,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": False,
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert captured["over_vendor_cap"] is False


def test_persist_and_record_missing_config_treated_as_not_over_cap(aws) -> None:
    """No CONFIG# item exists for this shop_id/version (shouldn't happen in practice for a run
    that reached this stage, but the handler must not crash over it)."""
    s3 = S3Adapter(aws["bucket"])
    s3.upload_bytes("snapshots/shop1/run-1.parquet", b"candidate-bytes")

    result = handler({
        "shop_id": "shop1",
        "run_id": "run-1",
        "config_version": 99,
        "snapshot_key": "snapshots/shop1/run-1.parquet",
        "halted": False,
        "bucket": aws["bucket"],
        "table_name": aws["table_name"],
    })

    assert result["status"] == "SUCCEEDED"
