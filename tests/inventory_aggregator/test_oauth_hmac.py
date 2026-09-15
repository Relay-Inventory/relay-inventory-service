from __future__ import annotations

import hashlib
import hmac

from inventory_aggregator.shopify.oauth import verify_hmac

API_SECRET = "sh_shared_secret_1234"


def _signed_params(params: dict, *, secret: str = API_SECRET) -> dict:
    """Builds a Shopify-style OAuth callback query string dict, signed the same way
    Shopify signs one: sort the params, join as "key=value" pairs with "&", HMAC-SHA256
    with the app's API secret."""

    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return {**params, "hmac": digest}


def test_verify_hmac_accepts_known_good_signature() -> None:
    params = _signed_params(
        {
            "shop": "test-shop.myshopify.com",
            "code": "0907a61c0c8d55e99db179b68161bc00",
            "state": "nonce-123",
            "timestamp": "1690000000",
        }
    )

    assert verify_hmac(params, API_SECRET) is True


def test_verify_hmac_rejects_tampered_param() -> None:
    params = _signed_params(
        {
            "shop": "test-shop.myshopify.com",
            "code": "0907a61c0c8d55e99db179b68161bc00",
            "state": "nonce-123",
            "timestamp": "1690000000",
        }
    )
    # Attacker changes the shop after the signature was computed -- the digest no
    # longer matches, so this must fail closed.
    params["shop"] = "attacker-shop.myshopify.com"

    assert verify_hmac(params, API_SECRET) is False


def test_verify_hmac_rejects_wrong_secret() -> None:
    params = _signed_params({"shop": "test-shop.myshopify.com", "code": "abc"})

    assert verify_hmac(params, "a-completely-different-secret") is False


def test_verify_hmac_rejects_missing_hmac_param() -> None:
    assert verify_hmac({"shop": "test-shop.myshopify.com", "code": "abc"}, API_SECRET) is False


def test_verify_hmac_fails_closed_on_non_string_hmac_value() -> None:
    params = _signed_params({"shop": "test-shop.myshopify.com", "code": "abc"})
    # Some caller passes a non-string value in (e.g. a parsed-but-not-stringified query
    # dict) -- hmac.compare_digest raises TypeError for this; must still return False,
    # never propagate.
    params["hmac"] = 12345

    assert verify_hmac(params, API_SECRET) is False
