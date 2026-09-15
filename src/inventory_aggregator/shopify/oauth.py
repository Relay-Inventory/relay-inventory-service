from __future__ import annotations

import hashlib
import hmac
from typing import Iterable, Mapping, Union
from urllib.parse import urlencode

import requests

OAUTH_AUTHORIZE_PATH = "/admin/oauth/authorize"
OAUTH_TOKEN_PATH = "/admin/oauth/access_token"

_REQUEST_TIMEOUT_SECONDS = 30


def _join_scopes(scopes: Union[str, Iterable[str]]) -> str:
    if isinstance(scopes, str):
        return scopes
    return ",".join(scopes)


def build_install_url(
    shop_domain: str,
    api_key: str,
    scopes: Union[str, Iterable[str]],
    redirect_uri: str,
    state: str,
) -> str:
    """Standard Shopify OAuth authorize-URL construction (the "Install" link a merchant
    clicks to begin granting this app access to their shop)."""

    params = {
        "client_id": api_key,
        "scope": _join_scopes(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://{shop_domain}{OAUTH_AUTHORIZE_PATH}?{urlencode(params)}"


def exchange_code_for_token(shop_domain: str, api_key: str, api_secret: str, code: str) -> str:
    """POSTs to https://<shop_domain>/admin/oauth/access_token to trade the OAuth
    callback's one-time `code` for the shop's offline access token.

    Callers MUST verify_hmac() the callback query string before calling this -- this
    function trusts its inputs and performs no HMAC validation itself.
    """

    response = requests.post(
        f"https://{shop_domain}{OAUTH_TOKEN_PATH}",
        json={"client_id": api_key, "client_secret": api_secret, "code": code},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def verify_hmac(query_params: Mapping[str, str], api_secret: str) -> bool:
    """Verifies the HMAC Shopify attaches to every OAuth callback query string, per
    Shopify's documented scheme: sort the remaining params, concatenate as
    "key=value" pairs joined by "&", HMAC-SHA256 it with the app's API secret, and
    compare digests -- this is a real security boundary (anyone can hit the callback
    URL with an arbitrary query string), so this fails closed on anything unexpected
    rather than raising: missing hmac param, tampered params, or a garbled digest all
    just return False.
    """

    provided_hmac = query_params.get("hmac")
    if not provided_hmac:
        return False

    message = "&".join(
        f"{key}={value}" for key, value in sorted(query_params.items()) if key != "hmac"
    )
    digest = hmac.new(
        api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    try:
        return hmac.compare_digest(digest, provided_hmac)
    except TypeError:
        return False
