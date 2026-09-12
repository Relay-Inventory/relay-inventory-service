import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.lambda_handlers.dispatcher import handler, list_shop_ids_with_config
from inventory_aggregator.persistence.single_table import SingleTable


@pytest.fixture()
def table_name(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
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
        yield "shop-data"


def test_list_shop_ids_with_config_returns_distinct_shops(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"marker": "a"}, version=1)
    st.put_config("shop-1", {"marker": "b"}, version=2)  # second version, same shop
    st.put_config("shop-2", {"marker": "c"}, version=1)
    st.put_feed_state("shop-1", "vendor_1", "feed_1", last_normalized_hash="x")  # not a CONFIG# item

    shop_ids = list_shop_ids_with_config(st)
    assert shop_ids == ["shop-1", "shop-2"]


def test_handler_starts_one_execution_per_shop(table_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"marker": "a"}, version=1)
    st.put_config("shop-2", {"marker": "b"}, version=1)

    monkeypatch.setenv("SHOP_DATA_TABLE", table_name)
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123456789012:stateMachine:test")

    mock_sfn = MagicMock()
    mock_sfn.start_execution.return_value = {"executionArn": "arn:aws:states:...:execution:test"}
    with patch("inventory_aggregator.lambda_handlers.dispatcher.boto3.client", return_value=mock_sfn):
        result = handler({})

    assert len(result["started"]) == 2
    assert {s["shop_id"] for s in result["started"]} == {"shop-1", "shop-2"}
    assert mock_sfn.start_execution.call_count == 2


def test_handler_no_shops_starts_nothing(table_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOP_DATA_TABLE", table_name)
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123456789012:stateMachine:test")
    mock_sfn = MagicMock()
    with patch("inventory_aggregator.lambda_handlers.dispatcher.boto3.client", return_value=mock_sfn):
        result = handler({})
    assert result["started"] == []
    mock_sfn.start_execution.assert_not_called()
