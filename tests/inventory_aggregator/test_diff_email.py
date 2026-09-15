import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.notifications.diff_email import render_diff_email
from inventory_aggregator.persistence.single_table import RunItem, SingleTable, run_sk


@pytest.fixture()
def table(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="us-east-1")
        dynamo.create_table(
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
        ).meta.client.get_waiter("table_exists").wait(TableName="shop-data")
        yield SingleTable("shop-data")


def _run_item(shop_id: str, run_id: str, *, status: str) -> RunItem:
    return RunItem(shop_id=shop_id, sk=run_sk(run_id), run_id=run_id, status=status)


def test_render_diff_email_normal_success_no_stale_feeds(table: SingleTable) -> None:
    run_item = _run_item("shop1", "run-1", status="SUCCEEDED")
    diff_summary = {"added_skus": 2, "removed_skus": 1, "changed_count": 3, "unchanged_count": 10}

    subject, body = render_diff_email(run_item, diff_summary, None, table=table)

    assert subject == "Inventory Aggregator: sync succeeded for shop1"
    assert body == (
        "<html><body>"
        "<h1>Sync succeeded for shop1</h1>"
        "<p>Run ID: run-1</p>"
        "<h2>What changed</h2>"
        "<ul>"
        "<li>Added SKUs: 2</li>"
        "<li>Removed SKUs: 1</li>"
        "<li>Changed SKUs: 3</li>"
        "</ul>"
        "<h2>Vendor feed health</h2>"
        "<p>All vendor feeds fetched successfully this run.</p>"
        "<h2>Circuit breaker</h2>"
        "<p>All checks passed</p>"
        "</body></html>"
    )


def test_render_diff_email_halted_with_stale_feed(table: SingleTable) -> None:
    table.put_feed_state("shop2", "v1", "default", last_fetch_status="error")
    run_item = _run_item("shop2", "run-2", status="HALTED")
    diff_summary = {"added_skus": 0, "removed_skus": 5, "changed_count": 0}

    subject, body = render_diff_email(
        run_item, diff_summary, "80% of SKUs changed (threshold 50%)", table=table,
    )

    assert subject == "Inventory Aggregator: sync halted for shop2"
    assert body == (
        "<html><body>"
        "<h1>Sync halted for shop2</h1>"
        "<p>Run ID: run-2</p>"
        "<h2>What changed</h2>"
        "<ul>"
        "<li>Added SKUs: 0</li>"
        "<li>Removed SKUs: 5</li>"
        "<li>Changed SKUs: 0</li>"
        "</ul>"
        "<h2>Vendor feed health</h2>"
        "<ul>"
        "<li>v1 / default: error</li>"
        "</ul>"
        "<h2>Circuit breaker</h2>"
        "<p>80% of SKUs changed (threshold 50%)</p>"
        "</body></html>"
    )


def test_render_diff_email_over_vendor_cap_adds_billing_notice(table: SingleTable) -> None:
    run_item = _run_item("shop3", "run-3", status="SUCCEEDED")

    subject, body = render_diff_email(run_item, None, None, table=table, over_vendor_cap=True)

    assert subject == "Inventory Aggregator: sync succeeded for shop3"
    assert body == (
        "<html><body>"
        "<h1>Sync succeeded for shop3</h1>"
        "<p>Run ID: run-3</p>"
        "<h2>What changed</h2>"
        "<ul>"
        "<li>Added SKUs: 0</li>"
        "<li>Removed SKUs: 0</li>"
        "<li>Changed SKUs: 0</li>"
        "</ul>"
        "<h2>Vendor feed health</h2>"
        "<p>All vendor feeds fetched successfully this run.</p>"
        "<h2>Circuit breaker</h2>"
        "<p>All checks passed</p>"
        "<h2>Billing</h2>"
        "<p>You're over the 25-vendor soft cap. Contact us about tier options.</p>"
        "</body></html>"
    )


def test_render_diff_email_ok_feed_not_listed_as_stale_never_fetched_feed_is(table: SingleTable) -> None:
    table.put_feed_state("shop4", "v1", "default", last_fetch_status="ok")
    table.put_feed_state("shop4", "v2", "default", last_fetch_status=None)
    run_item = _run_item("shop4", "run-4", status="SUCCEEDED")

    _, body = render_diff_email(run_item, {}, None, table=table)

    assert "v1 / default" not in body
    assert "<li>v2 / default: never fetched</li>" in body
