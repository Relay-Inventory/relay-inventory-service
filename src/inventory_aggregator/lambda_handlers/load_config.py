from __future__ import annotations

import os
from datetime import datetime, timezone

from inventory_aggregator.persistence.single_table import SingleTable


class NoConfigFoundError(RuntimeError):
    """Raised when a shop has no CONFIG# item at all -- there's no sensible 'skip' behavior
    for this, unlike a missing individual vendor file. Left uncaught here on purpose: Step
    Functions marks the execution failed, which is correct -- there's no sensible retry/skip
    for "this shop has no config at all"."""


def handler(event: dict, context=None) -> dict:
    shop_id = event["shop_id"]
    table_name = event.get("table_name") or os.environ["SHOP_DATA_TABLE"]
    table = SingleTable(table_name)

    config_item = table.get_latest_config(shop_id)
    if config_item is None:
        raise NoConfigFoundError(f"no CONFIG# item found for shop_id={shop_id!r}")

    run_id = datetime.now(timezone.utc).isoformat()
    return {
        "shop_id": shop_id,
        "run_id": run_id,
        "config_version": config_item.config_version,
        "tenant_config": config_item.config,
    }
