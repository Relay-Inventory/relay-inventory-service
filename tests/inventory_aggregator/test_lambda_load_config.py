from datetime import datetime

import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.lambda_handlers.load_config import NoConfigFoundError, handler
from inventory_aggregator.persistence.single_table import SingleTable


@pytest.fixture()
def table_name(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
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
        resource.meta.client.get_waiter("table_exists").wait(TableName="shop-data")
        yield "shop-data"


def test_handler_happy_path_returns_all_four_keys(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"tenant_id": "shop-1", "vendors": []}, version=3)

    result = handler({"shop_id": "shop-1", "table_name": table_name})

    assert result["shop_id"] == "shop-1"
    assert result["config_version"] == 3
    assert result["tenant_config"] == {"tenant_id": "shop-1", "vendors": []}
    assert "run_id" in result


def test_handler_raises_when_no_config_exists(table_name: str) -> None:
    with pytest.raises(NoConfigFoundError, match="shop-missing"):
        handler({"shop_id": "shop-missing", "table_name": table_name})


def test_handler_run_id_is_valid_iso8601(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"tenant_id": "shop-1", "vendors": []}, version=1)

    result = handler({"shop_id": "shop-1", "table_name": table_name})

    # datetime.fromisoformat raises ValueError on anything that isn't a valid ISO8601 string
    parsed = datetime.fromisoformat(result["run_id"])
    assert parsed.tzinfo is not None


def test_handler_falls_back_to_env_var_when_table_name_omitted(
    table_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_DATA_TABLE", table_name)
    st = SingleTable(table_name)
    st.put_config("shop-1", {"tenant_id": "shop-1", "vendors": []}, version=1)

    result = handler({"shop_id": "shop-1"})

    assert result["config_version"] == 1
