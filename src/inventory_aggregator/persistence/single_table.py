from __future__ import annotations

from typing import Optional, Type

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel

CONFIG_PREFIX = "CONFIG#"
FEED_STATE_PREFIX = "FEED_STATE#"
RUN_PREFIX = "RUN#"
SKU_MAP_PREFIX = "SKU_MAP#"

# Zero-padded so lexicographic sort (how DynamoDB compares string sort keys) matches numeric
# sort -- "CONFIG#10" sorts *before* "CONFIG#2" without this, which would silently break
# "get the latest config version" the moment a shop's 10th save happened.
_CONFIG_VERSION_WIDTH = 10


class ConfigItem(BaseModel):
    """The mega-object: shop meta, all vendors with nested feed configs, safety thresholds,
    rules -- everything TenantConfig models. Written by the admin UI on config save."""

    shop_id: str
    sk: str
    config_version: int
    config: dict


class FeedStateItem(BaseModel):
    """Small, operationally-mutated item, written by every sync run -- never touches or
    requires reading the ConfigItem, so a run's hash update can never race a concurrent
    config save."""

    shop_id: str
    sk: str
    vendor_id: str
    feed_id: str
    last_normalized_hash: Optional[str] = None
    last_fetch_status: Optional[str] = None
    last_fetched_at: Optional[str] = None
    last_part_key: Optional[str] = None
    """S3 key of the most recent normalized Parquet part actually written for this feed --
    kept even across runs where the feed is unchanged (and therefore no new part is written),
    so a later merge stage can reuse this vendor's last-known-good part instead of silently
    dropping it from the snapshot when its hash hasn't changed."""


class RunItem(BaseModel):
    """One item per sync run, append-only, queried by time range for run history/DLQ triage."""

    shop_id: str
    sk: str
    run_id: str
    status: str
    stage: Optional[str] = None
    config_version: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    failed_stage: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    artifacts: Optional[dict] = None


class SkuMapItem(BaseModel):
    """Maps this shop's internal SKU to the Shopify inventory item it corresponds to, so the
    write step (lambda_handlers/write_to_shopify.py, Commit 4.3) can call `inventorySetQuantities`
    -- that mutation requires Shopify's own `inventoryItemId` GID, which our canonical snapshot
    never carries (it only knows the SKU string). Populated by a separate, not-yet-built sync
    that resolves each SKU against Shopify's `productVariants` once (e.g. during/after OAuth
    install, or an on-demand backfill) -- a real, named gap, not silently assumed solved. A SKU
    with no mapping yet is a normal, expected state (not every SKU may exist in Shopify yet),
    handled by write_to_shopify.py skipping it and recording a per-SKU error rather than failing
    the whole run."""

    shop_id: str
    sk: str
    sku: str
    shopify_inventory_item_id: str


_SK_PREFIX_MODELS: dict[str, Type[BaseModel]] = {
    CONFIG_PREFIX: ConfigItem,
    FEED_STATE_PREFIX: FeedStateItem,
    RUN_PREFIX: RunItem,
    SKU_MAP_PREFIX: SkuMapItem,
}


def config_sk(version: int) -> str:
    return f"{CONFIG_PREFIX}{version:0{_CONFIG_VERSION_WIDTH}d}"


def feed_state_sk(vendor_id: str, feed_id: str) -> str:
    return f"{FEED_STATE_PREFIX}{vendor_id}#{feed_id}"


def run_sk(run_id_iso8601: str) -> str:
    return f"{RUN_PREFIX}{run_id_iso8601}"


def sku_map_sk(sku: str) -> str:
    return f"{SKU_MAP_PREFIX}{sku}"


def _model_for_sk(sk: str) -> Type[BaseModel]:
    for prefix, model in _SK_PREFIX_MODELS.items():
        if sk.startswith(prefix):
            return model
    raise ValueError(f"unknown sk prefix: {sk!r}")


class SingleTable:
    """One table, three item shapes disambiguated by the sk prefix, linked by the shop_id
    partition key -- not one-table-per-entity like the legacy internal-automation DAO
    pattern (which this deliberately does not mirror; see COMMIT_PLAN.md Commit 2.1)."""

    def __init__(self, table_name: str) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)

    def get_item(self, shop_id: str, sk: str) -> Optional[BaseModel]:
        response = self.table.get_item(Key={"shop_id": shop_id, "sk": sk})
        item = response.get("Item")
        if not item:
            return None
        return _model_for_sk(sk).model_validate(item)

    def put_item(self, item: BaseModel) -> None:
        self.table.put_item(Item=item.model_dump())

    def query(self, shop_id: str, sk_prefix: str, *, scan_index_forward: bool = True, limit: Optional[int] = None) -> list[BaseModel]:
        kwargs = dict(
            KeyConditionExpression=Key("shop_id").eq(shop_id) & Key("sk").begins_with(sk_prefix),
            ScanIndexForward=scan_index_forward,
        )
        if limit is not None:
            kwargs["Limit"] = limit
        response = self.table.query(**kwargs)
        return [_model_for_sk(item["sk"]).model_validate(item) for item in response.get("Items", [])]

    # --- CONFIG# convenience methods ---

    def put_config(self, shop_id: str, config: dict, *, version: int) -> ConfigItem:
        """Always writes a new version -- never overwrites a previous one, so
        RunContext.config_version pinning has something stable to pin to even if the
        merchant edits config mid-run."""
        item = ConfigItem(shop_id=shop_id, sk=config_sk(version), config_version=version, config=config)
        self.put_item(item)
        return item

    def get_config(self, shop_id: str, version: int) -> Optional[ConfigItem]:
        return self.get_item(shop_id, config_sk(version))

    def get_latest_config(self, shop_id: str) -> Optional[ConfigItem]:
        results = self.query(shop_id, CONFIG_PREFIX, scan_index_forward=False, limit=1)
        return results[0] if results else None

    # --- FEED_STATE# convenience methods ---

    def put_feed_state(
        self,
        shop_id: str,
        vendor_id: str,
        feed_id: str,
        *,
        last_normalized_hash: Optional[str] = None,
        last_fetch_status: Optional[str] = None,
        last_fetched_at: Optional[str] = None,
        last_part_key: Optional[str] = None,
    ) -> FeedStateItem:
        item = FeedStateItem(
            shop_id=shop_id,
            sk=feed_state_sk(vendor_id, feed_id),
            vendor_id=vendor_id,
            feed_id=feed_id,
            last_normalized_hash=last_normalized_hash,
            last_fetch_status=last_fetch_status,
            last_fetched_at=last_fetched_at,
            last_part_key=last_part_key,
        )
        self.put_item(item)
        return item

    def get_feed_state(self, shop_id: str, vendor_id: str, feed_id: str) -> Optional[FeedStateItem]:
        return self.get_item(shop_id, feed_state_sk(vendor_id, feed_id))

    # --- RUN# convenience methods ---

    def put_run(self, shop_id: str, run: RunItem) -> None:
        self.put_item(run)

    def query_runs(self, shop_id: str, *, scan_index_forward: bool = False, limit: Optional[int] = None) -> list[RunItem]:
        """Defaults to most-recent-first, since ISO8601 timestamps sort correctly
        lexicographically -- no zero-padding trick needed here, unlike CONFIG#."""
        return self.query(shop_id, RUN_PREFIX, scan_index_forward=scan_index_forward, limit=limit)

    # --- SKU_MAP# convenience methods ---

    def put_sku_mapping(self, shop_id: str, sku: str, shopify_inventory_item_id: str) -> SkuMapItem:
        item = SkuMapItem(
            shop_id=shop_id, sk=sku_map_sk(sku), sku=sku,
            shopify_inventory_item_id=shopify_inventory_item_id,
        )
        self.put_item(item)
        return item

    def get_sku_mapping(self, shop_id: str, sku: str) -> Optional[SkuMapItem]:
        return self.get_item(shop_id, sku_map_sk(sku))
