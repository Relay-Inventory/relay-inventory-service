from __future__ import annotations

import os
from typing import Any

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.models.config import TenantConfig
from inventory_aggregator.engine.canonical.io import read_parquet_bytes
from inventory_aggregator.persistence.single_table import SingleTable
from inventory_aggregator.shopify.client import ShopifyAdminClient
from inventory_aggregator.shopify.secrets import ShopifyTokenStore

# Shopify's documented limit on quantities per inventorySetQuantities call.
_BATCH_SIZE = 250

_SET_QUANTITIES_MUTATION = """
mutation SetQuantities($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup {
      changes { name delta }
    }
    userErrors { field message }
  }
}
"""


def _batched(rows: list[dict], size: int):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def handler(event: dict, context: Any = None) -> dict:
    shop_id = event["shop_id"]
    snapshot_key = event["snapshot_key"]
    tenant_config = TenantConfig.model_validate(event["tenant_config"])

    if not tenant_config.location_id:
        raise ValueError(
            f"shop {shop_id} has no location_id configured -- cannot write to Shopify"
        )

    bucket = event.get("bucket") or os.environ["ARTIFACT_BUCKET"]
    s3 = S3Adapter(bucket)
    table = SingleTable(event.get("table_name") or os.environ["SHOP_DATA_TABLE"])

    access_token = ShopifyTokenStore().get(shop_id)
    if not access_token:
        raise ValueError(
            f"shop {shop_id} has no Shopify access token -- OAuth install incomplete"
        )

    client = ShopifyAdminClient(tenant_config.shopify_domain, access_token)

    snapshot = read_parquet_bytes(s3.download_bytes(snapshot_key))

    errors: list[dict] = []
    quantities: list[dict] = []
    for row in snapshot.to_dict("records"):
        sku = row["sku"]
        mapping = table.get_sku_mapping(shop_id, sku)
        if mapping is None:
            errors.append({"sku": sku, "error": "no_shopify_inventory_mapping"})
            continue
        quantities.append(
            {
                "inventoryItemId": mapping.shopify_inventory_item_id,
                "locationId": tenant_config.location_id,
                "quantity": int(row["available_qty"]),
            }
        )

    written_count = 0
    for batch in _batched(quantities, _BATCH_SIZE):
        result = client.execute(
            _SET_QUANTITIES_MUTATION,
            {
                "input": {
                    "reason": "correction",
                    "name": "available",
                    "ignoreCompareQuantity": True,
                    "quantities": batch,
                }
            },
        )
        payload = result["inventorySetQuantities"]
        for user_error in payload.get("userErrors") or []:
            errors.append({"error": user_error.get("message"), "field": user_error.get("field")})
        changes = (payload.get("inventoryAdjustmentGroup") or {}).get("changes") or []
        written_count += len(changes)

    if errors and written_count == 0:
        write_status = "FAILED"
    elif errors:
        write_status = "PARTIAL"
    else:
        write_status = "SUCCEEDED"

    return {"write_status": write_status, "written_count": written_count, "errors": errors}
