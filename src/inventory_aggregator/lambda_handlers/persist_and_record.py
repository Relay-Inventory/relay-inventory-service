from __future__ import annotations

import os

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.persistence.single_table import RunItem, SingleTable, run_sk


def handler(event: dict, context=None) -> dict:
    shop_id = event["shop_id"]
    run_id = event["run_id"]
    config_version = event["config_version"]
    snapshot_key = event["snapshot_key"]
    halted = event["halted"]
    reason = event.get("reason")
    diff_summary = event.get("diff_summary")

    bucket = event.get("bucket") or os.environ["ARTIFACT_BUCKET"]
    s3 = S3Adapter(bucket)
    table = SingleTable(event.get("table_name") or os.environ["SHOP_DATA_TABLE"])

    if not halted:
        # Promote: copy the already-written candidate to latest.parquet. The candidate at
        # snapshot_key is itself the run's 30-day archive copy already (merge.py wrote it to
        # snapshots/<shop_id>/<run_id>.parquet directly) -- this only needs the ONE additional
        # copy to latest.parquet, not two separate writes.
        candidate_bytes = s3.download_bytes(snapshot_key)
        s3.upload_bytes(f"snapshots/{shop_id}/latest.parquet", candidate_bytes)

    run_item = RunItem(
        shop_id=shop_id,
        sk=run_sk(run_id),
        run_id=run_id,
        status="HALTED" if halted else "SUCCEEDED",
        config_version=config_version,
        failed_stage="DiffAndSafety" if halted else None,
        error_message=reason,
        artifacts={"snapshot_key": snapshot_key, **({"diff_summary": diff_summary} if diff_summary else {})},
    )
    table.put_run(shop_id, run_item)

    return {"status": run_item.status}
