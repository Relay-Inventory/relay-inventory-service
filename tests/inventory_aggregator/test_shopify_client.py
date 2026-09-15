from __future__ import annotations

from typing import Any, List, Union

import pytest
from graphql import ExecutionResult
from gql.transport.exceptions import TransportQueryError, TransportServerError
from gql.transport.transport import Transport

from inventory_aggregator.shopify.client import (
    ShopifyAdminClient,
    load_local_schema,
)

SET_QUANTITIES_MUTATION = """
mutation SetQuantities($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      changes {
        name
        delta
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

_VARIABLES = {
    "input": {
        "reason": "correction",
        "name": "available",
        "quantities": [
            {
                "inventoryItemId": "gid://shopify/InventoryItem/111",
                "locationId": "gid://shopify/Location/222",
                "quantity": 5,
            }
        ],
    }
}

_SUCCESS_RESULT = ExecutionResult(
    data={
        "inventorySetQuantities": {
            "inventoryAdjustmentGroup": {
                "createdAt": "2026-09-15T00:00:00Z",
                "reason": "correction",
                "changes": [{"name": "available", "delta": 5}],
            },
            "userErrors": [],
        }
    }
)


class ScriptedTransport(Transport):
    """A minimal fake Transport -- gql.Client accepts any Transport subclass, which is
    the "injectable test transport" the plan calls for -- that replays a pre-programmed
    sequence of ExecutionResults/exceptions, one per execute() call. Keeps the test
    decoupled from gql's real HTTP transport wiring entirely; no network involved."""

    def __init__(self, script: List[Union[ExecutionResult, BaseException]]) -> None:
        self._script = list(script)
        self.call_count = 0

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def execute(self, request: Any, *args: Any, **kwargs: Any) -> ExecutionResult:
        self.call_count += 1
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _client(transport: Transport, **kwargs: Any) -> ShopifyAdminClient:
    return ShopifyAdminClient(
        "test-shop.myshopify.com",
        "shpat_dummy_token",
        transport=transport,
        retry_sleep=lambda seconds: None,
        **kwargs,
    )


def _throttled_error(currently_available: float = 50.0) -> TransportQueryError:
    return TransportQueryError(
        "Throttled",
        errors=[{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
        extensions={
            "cost": {
                "requestedQueryCost": 100,
                "actualQueryCost": None,
                "throttleStatus": {
                    "maximumAvailable": 1000.0,
                    "currentlyAvailable": currently_available,
                    "restoreRate": 50.0,
                },
            }
        },
    )


def test_load_local_schema_contains_inventory_set_quantities() -> None:
    schema_text = load_local_schema()
    assert "inventorySetQuantities" in schema_text


def test_execute_successful_mutation_call() -> None:
    transport = ScriptedTransport([_SUCCESS_RESULT])
    client = _client(transport)

    result = client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert result["inventorySetQuantities"]["userErrors"] == []
    assert result["inventorySetQuantities"]["inventoryAdjustmentGroup"]["changes"] == [
        {"name": "available", "delta": 5}
    ]
    assert transport.call_count == 1


def test_execute_retries_on_throttled_extension_then_succeeds() -> None:
    transport = ScriptedTransport([_throttled_error(currently_available=10.0), _SUCCESS_RESULT])
    client = _client(transport)

    result = client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert result["inventorySetQuantities"]["userErrors"] == []
    assert transport.call_count == 2


def test_execute_retries_on_http_429_then_succeeds() -> None:
    transport = ScriptedTransport([TransportServerError("Too Many Requests", code=429), _SUCCESS_RESULT])
    client = _client(transport)

    result = client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert result["inventorySetQuantities"]["userErrors"] == []
    assert transport.call_count == 2


def test_execute_propagates_non_retryable_query_error() -> None:
    access_denied = TransportQueryError(
        "Access denied",
        errors=[{"message": "Access denied for inventorySetQuantities", "extensions": {"code": "ACCESS_DENIED"}}],
    )
    transport = ScriptedTransport([access_denied])
    client = _client(transport)

    with pytest.raises(TransportQueryError):
        client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert transport.call_count == 1


def test_execute_propagates_non_throttling_server_error() -> None:
    transport = ScriptedTransport([TransportServerError("Internal Server Error", code=500)])
    client = _client(transport)

    with pytest.raises(TransportServerError):
        client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert transport.call_count == 1


def test_execute_gives_up_after_max_attempts_when_always_throttled() -> None:
    transport = ScriptedTransport([_throttled_error() for _ in range(5)])
    client = _client(transport, max_attempts=3)

    with pytest.raises(TransportQueryError):
        client.execute(SET_QUANTITIES_MUTATION, _VARIABLES)

    assert transport.call_count == 3


def test_default_transport_is_httpx_transport_pointed_at_shop_admin_api() -> None:
    from gql.transport.httpx import HTTPXTransport

    client = ShopifyAdminClient("test-shop.myshopify.com", "shpat_dummy_token")

    assert isinstance(client.transport, HTTPXTransport)
    assert client.transport.url == "https://test-shop.myshopify.com/admin/api/2024-10/graphql.json"
    assert client.transport.kwargs["headers"]["X-Shopify-Access-Token"] == "shpat_dummy_token"


def test_custom_schema_string_is_used_instead_of_local_file() -> None:
    minimal_schema = "schema { query: Q } type Q { ping: String! }"
    transport = ScriptedTransport([ExecutionResult(data={"ping": "pong"})])

    client = _client(transport, schema=minimal_schema)
    result = client.execute("query { ping }")

    assert result == {"ping": "pong"}
