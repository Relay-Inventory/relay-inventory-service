from __future__ import annotations

import os

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.models.config import TenantConfig
from inventory_aggregator.engine.canonical.io import read_parquet_bytes, write_parquet_bytes
from inventory_aggregator.engine.canonical.models import InventoryRecord
from inventory_aggregator.engine.pipeline import merge_records, price_records
from inventory_aggregator.persistence.single_table import SingleTable


def handler(event: dict, context=None) -> dict:
    shop_id = event["shop_id"]
    run_id = event["run_id"]
    tenant_config = TenantConfig.model_validate(event["tenant_config"])
    vendor_results = event["vendor_results"]  # list of fetch_and_hash's return dicts

    bucket = event.get("bucket") or os.environ["ARTIFACT_BUCKET"]
    s3 = S3Adapter(bucket)
    table_name = event.get("table_name") or os.environ["SHOP_DATA_TABLE"]
    table = SingleTable(table_name)

    all_records: list[InventoryRecord] = []
    for result in vendor_results:
        part_key = result.get("part_key")
        if not part_key:
            # Unchanged feed this run -- fetch_and_hash deliberately skipped writing a new
            # part because the normalized hash matched FEED_STATE#. Reusing the *previous*
            # run's part here (rather than skipping the vendor outright) is required for
            # correctness: without this, an unchanged vendor's inventory would silently vanish
            # from every snapshot after its first appearance, which is a real bug, not a
            # cosmetic gap in the "skip unchanged feed" optimization.
            vendor_id = result["vendor_id"]
            feed_id = result.get("feed_id", "default")
            feed_state = table.get_feed_state(shop_id, vendor_id, feed_id)
            part_key = feed_state.last_part_key if feed_state else None
            if not part_key:
                # No previously-written part exists for this vendor at all (shouldn't happen
                # in normal operation -- fetch_and_hash always writes a part together with the
                # FEED_STATE# update the first time a vendor's hash is seen -- but guard
                # against it rather than raising, since one vendor's history gap shouldn't
                # fail the whole run).
                continue
        df = read_parquet_bytes(s3.download_bytes(part_key))
        all_records.extend(InventoryRecord.model_validate(row) for row in df.to_dict(orient="records"))

    merged = merge_records(all_records, tenant_config)
    priced = price_records(all_records, merged, tenant_config)

    snapshot_key = f"snapshots/{shop_id}/{run_id}.parquet"
    s3.upload_bytes(snapshot_key, write_parquet_bytes(priced))

    return {"snapshot_key": snapshot_key}
