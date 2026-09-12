from __future__ import annotations

import io
import os

import pandas as pd

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.models.config import VendorConfig
from inventory_aggregator.engine.canonical.io import write_parquet_bytes
from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.config.compiled import CompiledVendorConfig
from inventory_aggregator.engine.normalize.adjustments import apply_vendor_adjustments
from inventory_aggregator.engine.normalize.hashing import hash_normalized_feed
from inventory_aggregator.engine.parsing.csv_parser import parse_csv
from inventory_aggregator.engine.rules import compile_vendor_rules
from inventory_aggregator.engine.rules.apply import filter_by_vendor_rules
from inventory_aggregator.persistence.single_table import SingleTable

_HASH_COLUMNS = ["sku", "quantity_available", "cost"]


def handler(event: dict, context=None) -> dict:
    shop_id = event["shop_id"]
    run_id = event["run_id"]
    vendor_config = VendorConfig.model_validate(event["vendor_config"])
    feed_id = event.get("feed_id", "default")  # a vendor may have multiple feeds later; single feed per vendor for now

    bucket = event.get("bucket") or os.environ["ARTIFACT_BUCKET"]
    s3 = S3Adapter(bucket)
    table_name = event.get("table_name") or os.environ["SHOP_DATA_TABLE"]
    table = SingleTable(table_name)

    raw_key = f"raw/{shop_id}/{vendor_config.vendor_id}/{run_id}.csv"
    raw_bytes = s3.download_bytes(raw_key)
    encoding = vendor_config.parser.encoding or "utf-8"
    decoded = raw_bytes.decode(encoding)

    records, errors = parse_csv(
        io.StringIO(decoded),
        vendor_id=vendor_config.vendor_id,
        column_map=vendor_config.parser.column_map,
    )

    # Same three-step normalize/filter sequence engine/pipeline.py's process_vendor runs (minus
    # sku_map, which fetch_and_hash doesn't need per the task's explicit function list) --
    # applied here directly since process_vendor reads from a local file path, not S3 bytes.
    records = apply_vendor_adjustments(records, vendor_config)

    # Build just this one vendor's CompiledVendorConfig directly (same shape
    # compile_tenant_config would produce for it), rather than constructing a fake
    # TenantConfig just to get one entry back out of compile_tenant_config's vendors dict.
    compiled_rules = compile_vendor_rules(vendor_config.rules, allowed_columns=set(CANONICAL_COLUMNS))
    compiled_vendor = CompiledVendorConfig(config=vendor_config, rules=compiled_rules)
    records = filter_by_vendor_rules(records, compiled_vendor)

    if records:
        df = pd.DataFrame([r.model_dump() for r in records])
    else:
        df = pd.DataFrame(columns=_HASH_COLUMNS)
    new_hash = hash_normalized_feed(df)

    existing_state = table.get_feed_state(shop_id, vendor_config.vendor_id, feed_id)
    changed = existing_state is None or existing_state.last_normalized_hash != new_hash

    part_key = None
    if changed:
        part_key = f"parts/{shop_id}/{run_id}/{vendor_config.vendor_id}.parquet"
        s3.upload_bytes(part_key, write_parquet_bytes(df))
        table.put_feed_state(
            shop_id,
            vendor_config.vendor_id,
            feed_id,
            last_normalized_hash=new_hash,
            last_fetch_status="ok",
            last_part_key=part_key,
        )

    return {
        "vendor_id": vendor_config.vendor_id,
        "feed_id": feed_id,
        "changed": changed,
        "part_key": part_key,
        "error_count": len(errors),
    }
