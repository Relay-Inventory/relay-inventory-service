from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from gql import Client, gql as gql_query
from gql.transport.exceptions import TransportQueryError, TransportServerError
from gql.transport.httpx import HTTPXTransport
from gql.transport.transport import Transport
from tenacity import Retrying, retry_if_exception, stop_after_attempt

DEFAULT_API_VERSION = "2024-10"

_SCHEMA_DIR = Path(__file__).parent / "schema"

# GraphQL error extension codes Shopify uses to signal its leaky-bucket rate limiter
# tripped -- distinct from a query that is simply wrong (which is NOT retryable).
_THROTTLED_EXTENSION_CODES = {"THROTTLED", "Throttled"}

# Shopify's documented standard-plan cost bucket restores at 50 points/second. Used only
# as a *fallback* estimate of how many points a throttled query burned when the response
# doesn't tell us the bucket's total capacity -- the real backoff calculation below always
# prefers the actual `currentlyAvailable` value straight off the response.
_LEAKY_BUCKET_RESTORE_RATE_PER_SECOND = 50.0
_ASSUMED_QUERY_COST_POINTS = 50.0

_MIN_THROTTLE_WAIT_SECONDS = 0.5
_MAX_THROTTLE_WAIT_SECONDS = 10.0


def load_local_schema(api_version: str = DEFAULT_API_VERSION) -> str:
    """Reads the vendored, hand-written-subset SDL file for the given API version. See
    shopify/schema/admin_<version>.graphql for why it's a subset and how to replace it."""

    return (_SCHEMA_DIR / f"admin_{api_version}.graphql").read_text()


def _currently_available_points(exc: BaseException) -> Optional[float]:
    """Shopify puts the leaky-bucket state on the response's top-level `extensions.cost`
    -- gql surfaces that as TransportQueryError.extensions (the ExecutionResult-level
    extensions, not any individual error's own extensions)."""

    if not isinstance(exc, TransportQueryError) or not exc.extensions:
        return None
    cost = exc.extensions.get("cost") or {}
    throttle_status = cost.get("throttleStatus") or {}
    return throttle_status.get("currentlyAvailable")


def is_throttled_error(exc: BaseException) -> bool:
    """True for Shopify's documented throttling signals: HTTP 429, or a GraphQL
    THROTTLED/Throttled error extension code. Anything else (a malformed query, an auth
    failure, ...) is not retryable -- retrying those would just burn time hitting the
    same permanent failure."""

    if isinstance(exc, TransportServerError):
        return exc.code == 429
    if isinstance(exc, TransportQueryError):
        for error in exc.errors or []:
            extensions = (error or {}).get("extensions") or {}
            if extensions.get("code") in _THROTTLED_EXTENSION_CODES:
                return True
    return False


def _throttle_wait_seconds(retry_state: Any) -> float:
    """Backs off proportionally to how empty Shopify's leaky bucket actually is, per the
    `currentlyAvailable` cost extension, instead of blind exponential backoff -- Shopify's
    API tells you exactly how depleted the bucket is, so use it."""

    exc = retry_state.outcome.exception()
    available = _currently_available_points(exc)
    if available is None:
        # No cost extension on this error (e.g. a bare HTTP 429) -- fall back to a capped
        # exponential backoff keyed off the attempt number.
        wait_seconds = 2 ** (retry_state.attempt_number - 1)
    else:
        deficit = max(0.0, _ASSUMED_QUERY_COST_POINTS - available)
        wait_seconds = deficit / _LEAKY_BUCKET_RESTORE_RATE_PER_SECOND
    return min(_MAX_THROTTLE_WAIT_SECONDS, max(_MIN_THROTTLE_WAIT_SECONDS, wait_seconds))


class ShopifyAdminClient:
    """Wraps a gql.Client pointed at a shop's Admin API GraphQL endpoint.

    Schema-driven on purpose (the concrete benefit `gql` was chosen for over a hand-rolled
    httpx wrapper, per IMPLEMENTATION_PLAN.md): passing `schema` (or defaulting to the
    vendored local file) lets gql validate every query/mutation document locally, before
    it ever reaches the network.
    """

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        schema: Optional[str] = None,
        transport: Optional[Transport] = None,
        max_attempts: int = 5,
        retry_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.shop_domain = shop_domain
        self.api_version = api_version
        self.max_attempts = max_attempts
        self._retry_sleep = retry_sleep

        schema_str = schema if schema is not None else load_local_schema(api_version)

        if transport is None:
            transport = HTTPXTransport(
                url=f"https://{shop_domain}/admin/api/{api_version}/graphql.json",
                headers={
                    "X-Shopify-Access-Token": access_token,
                    "Content-Type": "application/json",
                },
            )
        self.transport = transport
        self.client = Client(transport=transport, schema=schema_str)

    def _execute_once(self, document: Any, variable_values: Optional[Mapping[str, Any]]) -> dict:
        with self.client as session:
            return session.execute(document, variable_values=dict(variable_values or {}))

    def execute(self, query: str, variable_values: Optional[Mapping[str, Any]] = None) -> dict:
        """Executes a GraphQL query/mutation string, validated locally against the
        client's schema, with retry/backoff on Shopify's throttling signals. Any other
        error (a bad query, an auth failure, a non-throttling server error) propagates
        immediately -- it is not the retry loop's job to mask a real failure."""

        document = gql_query(query)
        retrying = Retrying(
            sleep=self._retry_sleep,
            stop=stop_after_attempt(self.max_attempts),
            wait=_throttle_wait_seconds,
            retry=retry_if_exception(is_throttled_error),
            reraise=True,
        )
        return retrying(self._execute_once, document, variable_values)
