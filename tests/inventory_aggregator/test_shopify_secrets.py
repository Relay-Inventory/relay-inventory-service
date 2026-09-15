from __future__ import annotations

import pytest
from moto import mock_aws

from inventory_aggregator.shopify.secrets import ShopifyTokenStore, secret_name


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield


def test_secret_name_uses_fixed_naming_convention() -> None:
    assert secret_name("shop-123") == "inventory-aggregator/shop-123/shopify-access-token"


@mock_aws
def test_put_then_get_round_trips_access_token(aws_env) -> None:
    store = ShopifyTokenStore()
    store.put("shop-1", "shpat_first_token")

    assert store.get("shop-1") == "shpat_first_token"


@mock_aws
def test_get_returns_none_when_secret_does_not_exist(aws_env) -> None:
    store = ShopifyTokenStore()

    assert store.get("shop-unknown") is None


@mock_aws
def test_put_twice_updates_existing_secret(aws_env) -> None:
    store = ShopifyTokenStore()
    store.put("shop-1", "shpat_first_token")
    store.put("shop-1", "shpat_rotated_token")

    assert store.get("shop-1") == "shpat_rotated_token"


@mock_aws
def test_secrets_are_isolated_by_shop_id(aws_env) -> None:
    store = ShopifyTokenStore()
    store.put("shop-a", "shpat_a")
    store.put("shop-b", "shpat_b")

    assert store.get("shop-a") == "shpat_a"
    assert store.get("shop-b") == "shpat_b"


@mock_aws
def test_get_reraises_unexpected_client_error(aws_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    store = ShopifyTokenStore()

    def _boom(**kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalServiceError", "Message": "boom"}}, "GetSecretValue"
        )

    monkeypatch.setattr(store.client, "get_secret_value", _boom)

    with pytest.raises(ClientError):
        store.get("shop-1")


@mock_aws
def test_put_reraises_unexpected_client_error(aws_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    store = ShopifyTokenStore()

    def _boom(**kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalServiceError", "Message": "boom"}}, "CreateSecret"
        )

    monkeypatch.setattr(store.client, "create_secret", _boom)

    with pytest.raises(ClientError):
        store.put("shop-1", "shpat_token")
