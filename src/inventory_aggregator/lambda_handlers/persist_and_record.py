from __future__ import annotations

import logging
import os

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.models.config import TenantConfig
from inventory_aggregator.billing.limits import is_over_vendor_cap
from inventory_aggregator.notifications.diff_email import render_diff_email
from inventory_aggregator.notifications.send_email import EmailSender, LoggingEmailSender
from inventory_aggregator.persistence.single_table import RunItem, SingleTable, run_sk

logger = logging.getLogger(__name__)


def _is_over_vendor_cap_for_run(table: SingleTable, shop_id: str, config_version: int) -> bool:
    """Looks up the exact CONFIG# version this run was pinned to (never "latest" -- a run must
    be judged against the config it actually ran with) and checks it against the vendor soft
    cap (billing/limits.py). Missing config (shouldn't happen for a run that got this far, but
    not this function's job to raise about it) is treated as not-over-cap rather than failing
    the run."""
    config_item = table.get_config(shop_id, config_version)
    if config_item is None:
        return False
    tenant_config = TenantConfig.model_validate(config_item.config)
    return is_over_vendor_cap(tenant_config)


def _send_diff_email(
    table: SingleTable,
    run_item: RunItem,
    diff_summary: dict | None,
    safety_reason: str | None,
    email_sender: EmailSender,
    *,
    over_vendor_cap: bool = False,
) -> None:
    """Best-effort notification for both SUCCEEDED and HALTED runs (COMMIT_PLAN.md Commit 4.5 --
    "the merchant should hear it from you, never from a customer"). An email provider failure
    must NEVER change the run's own already-recorded status -- caught and logged here, never
    re-raised."""
    try:
        subject, body = render_diff_email(
            run_item, diff_summary, safety_reason, table=table, over_vendor_cap=over_vendor_cap,
        )
        email_sender.send(subject, body)
    except Exception:
        logger.exception(
            "diff email failed for shop_id=%s run_id=%s -- run status (%s) unaffected",
            run_item.shop_id, run_item.run_id, run_item.status,
        )


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

    over_vendor_cap = _is_over_vendor_cap_for_run(table, shop_id, config_version)
    _send_diff_email(
        table, run_item, diff_summary, reason, LoggingEmailSender(), over_vendor_cap=over_vendor_cap,
    )

    return {"status": run_item.status}
