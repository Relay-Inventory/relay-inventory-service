from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from inventory_aggregator.shopify.oauth import build_install_url, exchange_code_for_token


def test_build_install_url_with_scope_list() -> None:
    url = build_install_url(
        "test-shop.myshopify.com",
        "api-key-123",
        ["read_products", "write_inventory"],
        "https://app.example.com/auth/callback",
        "nonce-abc",
    )

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "test-shop.myshopify.com"
    assert parsed.path == "/admin/oauth/authorize"

    query = parse_qs(parsed.query)
    assert query["client_id"] == ["api-key-123"]
    assert query["scope"] == ["read_products,write_inventory"]
    assert query["redirect_uri"] == ["https://app.example.com/auth/callback"]
    assert query["state"] == ["nonce-abc"]


def test_build_install_url_with_scope_string() -> None:
    url = build_install_url(
        "test-shop.myshopify.com",
        "api-key-123",
        "read_products,write_inventory",
        "https://app.example.com/auth/callback",
        "nonce-abc",
    )

    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["read_products,write_inventory"]


def test_exchange_code_for_token_posts_and_returns_access_token() -> None:
    fake_response = MagicMock()
    fake_response.json.return_value = {"access_token": "shpat_abc123", "scope": "read_products"}
    fake_response.raise_for_status.return_value = None

    with patch("inventory_aggregator.shopify.oauth.requests.post", return_value=fake_response) as post:
        token = exchange_code_for_token(
            "test-shop.myshopify.com", "api-key-123", "api-secret-456", "one-time-code"
        )

    assert token == "shpat_abc123"
    post.assert_called_once()
    called_url = post.call_args.args[0]
    assert called_url == "https://test-shop.myshopify.com/admin/oauth/access_token"
    assert post.call_args.kwargs["json"] == {
        "client_id": "api-key-123",
        "client_secret": "api-secret-456",
        "code": "one-time-code",
    }
    fake_response.raise_for_status.assert_called_once()
