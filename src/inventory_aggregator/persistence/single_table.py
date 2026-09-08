from __future__ import annotations

from typing import Optional, Type

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel

CONFIG_PREFIX = "CONFIG#"
FEED_STATE_PREFIX = "FEED_STATE#"
RUN_PREFIX = "RUN#"

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


_SK_PREFIX_MODELS: dict[str, Type[BaseModel]] = {
    CONFIG_PREFIX: ConfigItem,
    FEED_STATE_PREFIX: FeedStateItem,
    RUN_PREFIX: RunItem,
}


def config_sk(version: int) -> str:
    return f"{CONFIG_PREFIX}{version:0{_CONFIG_VERSION_WIDTH}d}"


def feed_state_sk(vendor_id: str, feed_id: str) -> str:
    return f"{FEED_STATE_PREFIX}{vendor_id}#{feed_id}"


def run_sk(run_id_iso8601: str) -> str:
    return f"{RUN_PREFIX}{run_id_iso8601}"


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
    ) -> FeedStateItem:
        item = FeedStateItem(
            shop_id=shop_id,
            sk=feed_state_sk(vendor_id, feed_id),
            vendor_id=vendor_id,
            feed_id=feed_id,
            last_normalized_hash=last_normalized_hash,
            last_fetch_status=last_fetch_status,
            last_fetched_at=last_fetched_at,
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
