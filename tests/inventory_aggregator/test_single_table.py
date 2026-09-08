import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.persistence.single_table import (
    ConfigItem,
    FeedStateItem,
    RunItem,
    SingleTable,
    config_sk,
    feed_state_sk,
    run_sk,
)


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


def test_config_put_get_roundtrip(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"tenant_id": "shop-1", "vendors": []}, version=1)
    fetched = st.get_config("shop-1", 1)
    assert fetched is not None
    assert fetched.config_version == 1
    assert fetched.config["tenant_id"] == "shop-1"
    assert fetched.sk == config_sk(1)


def test_feed_state_put_get_roundtrip(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_feed_state("shop-1", "vendor_1", "feed_1", last_normalized_hash="abc123", last_fetch_status="ok")
    fetched = st.get_feed_state("shop-1", "vendor_1", "feed_1")
    assert fetched is not None
    assert fetched.last_normalized_hash == "abc123"
    assert fetched.last_fetch_status == "ok"
    assert fetched.sk == feed_state_sk("vendor_1", "feed_1")


def test_run_put_get_roundtrip(table_name: str) -> None:
    st = SingleTable(table_name)
    run = RunItem(shop_id="shop-1", sk=run_sk("2026-01-01T00:00:00Z"), run_id="run-1", status="SUCCEEDED")
    st.put_run("shop-1", run)
    fetched = st.get_item("shop-1", run_sk("2026-01-01T00:00:00Z"))
    assert fetched is not None
    assert isinstance(fetched, RunItem)
    assert fetched.run_id == "run-1"
    assert fetched.status == "SUCCEEDED"


def test_get_latest_config_orders_by_version_not_lexicographic_string(table_name: str) -> None:
    """Regression guard for a real trap: unpadded sort keys ('CONFIG#10' vs 'CONFIG#2')
    sort lexicographically, which would put version 2 after version 10 -- wrong. Versions
    1, 2, and 10 specifically exercise this, since 1 < 2 < 10 numerically but "10" < "2"
    as strings."""
    st = SingleTable(table_name)
    for version in (1, 2, 10):
        st.put_config("shop-1", {"tenant_id": "shop-1", "version_marker": version}, version=version)
    latest = st.get_latest_config("shop-1")
    assert latest is not None
    assert latest.config_version == 10
    assert latest.config["version_marker"] == 10


def test_put_config_never_overwrites_previous_version(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"marker": "v1"}, version=1)
    st.put_config("shop-1", {"marker": "v2"}, version=2)
    v1 = st.get_config("shop-1", 1)
    v2 = st.get_config("shop-1", 2)
    assert v1 is not None and v1.config["marker"] == "v1"
    assert v2 is not None and v2.config["marker"] == "v2"


def test_feed_state_update_never_touches_config_item(table_name: str) -> None:
    """Guards the read-modify-write race this design exists to avoid: a FEED_STATE# write
    must not require reading, or affect, the CONFIG# item at all."""
    st = SingleTable(table_name)
    st.put_config("shop-1", {"marker": "original"}, version=1)
    st.put_feed_state("shop-1", "vendor_1", "feed_1", last_normalized_hash="hash-a")
    st.put_feed_state("shop-1", "vendor_1", "feed_1", last_normalized_hash="hash-b")

    config_after = st.get_config("shop-1", 1)
    assert config_after is not None
    assert config_after.config["marker"] == "original"

    feed_state_after = st.get_feed_state("shop-1", "vendor_1", "feed_1")
    assert feed_state_after is not None
    assert feed_state_after.last_normalized_hash == "hash-b"


def test_query_scoped_by_shop_id_partition(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_config("shop-1", {"marker": "shop-1-config"}, version=1)
    st.put_config("shop-2", {"marker": "shop-2-config"}, version=1)
    shop_1_configs = st.query("shop-1", "CONFIG#")
    assert len(shop_1_configs) == 1
    assert shop_1_configs[0].config["marker"] == "shop-1-config"


def test_get_item_returns_none_when_missing(table_name: str) -> None:
    st = SingleTable(table_name)
    assert st.get_config("shop-1", 1) is None
    assert st.get_feed_state("shop-1", "vendor_1", "feed_1") is None
    assert st.get_item("shop-1", "CONFIG#0000000001") is None


def test_get_item_raises_on_unknown_sk_prefix(table_name: str) -> None:
    st = SingleTable(table_name)
    # Write directly via the underlying table, bypassing the typed put_* helpers, since no
    # public API constructs an item with an unrecognized sk -- this exercises the defensive
    # "unknown sk prefix" branch a malformed/legacy item would trip.
    st.table.put_item(Item={"shop_id": "shop-1", "sk": "UNKNOWN#123", "junk": "data"})
    with pytest.raises(ValueError, match="unknown sk prefix"):
        st.get_item("shop-1", "UNKNOWN#123")


def test_query_runs_returns_most_recent_first(table_name: str) -> None:
    st = SingleTable(table_name)
    st.put_run("shop-1", RunItem(shop_id="shop-1", sk=run_sk("2026-01-01T00:00:00Z"), run_id="run-1", status="SUCCEEDED"))
    st.put_run("shop-1", RunItem(shop_id="shop-1", sk=run_sk("2026-01-02T00:00:00Z"), run_id="run-2", status="SUCCEEDED"))
    runs = st.query_runs("shop-1")
    assert [r.run_id for r in runs] == ["run-2", "run-1"]
    assert st.query_runs("shop-1", limit=1)[0].run_id == "run-2"
