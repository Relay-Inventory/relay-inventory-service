from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Dict, List

from botocore.exceptions import BotoCoreError, ClientError

from relay_inventory.adapters.queue.sqs import SqsAdapter
from relay_inventory.adapters.storage.s3 import S3Adapter
from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.app.models.config import TenantConfig
from relay_inventory.engine.canonical.io import write_csv_bytes
from relay_inventory.engine.canonical.models import CANONICAL_COLUMNS, InventoryRecord
from relay_inventory.engine.merge.best_offer import BestOfferConfig, LandedCostConfig, merge_best_offer
from relay_inventory.engine.normalize.sku_map import load_sku_map_from_text
from relay_inventory.engine.parsing.csv_parser import ParseError, parse_csv
from relay_inventory.engine.pricing.pricing import MapPolicy, PricingRules, RoundingRule, apply_pricing
from relay_inventory.persistence.dynamo_runs import DynamoRuns
from relay_inventory.persistence.dynamo_tenants import DynamoTenants
from relay_inventory.util.errors import NonRetryableError, RetryableError
from relay_inventory.util.logging import get_logger, log_event


class Worker:
    def __init__(
        self,
        *,
        bucket: str,
        runs_table: str,
        tenants_table: str,
        queue_url: str | None = None,
    ) -> None:
        self.s3 = S3Adapter(bucket)
        self.runs = DynamoRuns(runs_table)
        self.tenants = DynamoTenants(tenants_table)
        self.queue = SqsAdapter(queue_url) if queue_url else None
        self.logger = get_logger(self.__class__.__name__)

    def run_job(self, job: RunJob) -> None:
        log_event(self.logger, "run_started", run_id=job.run_id, tenant_id=job.tenant_id)
        self.runs.update_status(job.run_id, "RUNNING", started_at=datetime.utcnow())
        tenant_record = self.tenants.get(job.tenant_id, job.config_version)
        if not tenant_record:
            self.runs.update_status(
                job.run_id,
                "FAILED",
                completed_at=datetime.utcnow(),
                error_report_key="missing_tenant_config",
            )
            raise NonRetryableError("missing tenant config")
        config = TenantConfig.model_validate(tenant_record.config)
        if config.schema_version != 1:
            raise NonRetryableError(f"unsupported schema_version {config.schema_version}")

        all_records: List[InventoryRecord] = []
        errors: List[ParseError] = []
        artifacts: Dict[str, str] = {}
        vendor_counts: Dict[str, int] = {}
        total_rows = 0
        warnings: List[str] = []
        start_time = datetime.utcnow()
        stage_times: Dict[str, float] = {}
        error_policy = config.error_policy

        ingest_start = datetime.utcnow()
        for vendor in config.vendors:
            prefix = vendor.inbound.s3_prefix or ""
            try:
                latest = self.s3.list_latest(prefix)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            if not latest:
                errors.append(
                    ParseError(row_number=0, reason="missing inbound file", row_data={"vendor": vendor.vendor_id})
                )
                continue
            try:
                raw_text = self.s3.download_text(latest.key)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            try:
                records, vendor_errors = parse_csv(
                    io.StringIO(raw_text),
                    vendor_id=vendor.vendor_id,
                    column_map=vendor.parser.column_map,
                )
            except ValueError as exc:
                if "missing columns:" in str(exc).lower():
                    if error_policy.fail_on_missing_required_columns:
                        raise NonRetryableError(str(exc)) from exc
                    errors.append(
                        ParseError(
                            row_number=0,
                            reason=str(exc),
                            row_data={"vendor": vendor.vendor_id},
                        )
                    )
                    records = []
                    vendor_errors = []
                else:
                    raise NonRetryableError(str(exc)) from exc
            if vendor.sku_map and vendor.sku_map.s3_key:
                try:
                    sku_text = self.s3.download_text(vendor.sku_map.s3_key)
                except (BotoCoreError, ClientError) as exc:
                    raise RetryableError(str(exc)) from exc
                sku_map = load_sku_map_from_text(sku_text)
                records = list(sku_map.apply(records))
            all_records.extend(records)
            errors.extend(vendor_errors)
            vendor_counts[vendor.vendor_id] = len(records)
            total_rows += len(records) + len(vendor_errors)

            normalized_key = (
                f"tenants/{config.tenant_id}/normalized/{vendor.vendor_id}/{job.run_id}/normalized.csv"
            )
            normalized_rows = [record.model_dump() for record in records]
            normalized_bytes = write_csv_bytes(
                normalized_rows,
                CANONICAL_COLUMNS,
                extrasaction="raise",
            )
            try:
                self.s3.upload_bytes(normalized_key, normalized_bytes)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            artifacts[f"normalized_{vendor.vendor_id}"] = normalized_key
        stage_times["ingest_seconds"] = (datetime.utcnow() - ingest_start).total_seconds()

        error_key = None
        if errors:
            error_key = f"tenants/{config.tenant_id}/reports/{job.run_id}/errors.json"
            try:
                self.s3.upload_text(
                    error_key,
                    json.dumps([error.__dict__ for error in errors], default=str),
                )
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            artifacts["errors"] = error_key

        invalid_rows = len(errors)
        if total_rows == 0:
            self.runs.update_status(
                job.run_id,
                "FAILED",
                completed_at=datetime.utcnow(),
                error_report_key=error_key or "no_rows_parsed",
                artifacts=artifacts,
            )
            log_event(self.logger, "run_failed", run_id=job.run_id, error_report_key=error_key)
            raise NonRetryableError("no rows parsed")

        exceeds_row_count = invalid_rows > error_policy.max_invalid_rows
        exceeds_row_pct = (invalid_rows / total_rows) > error_policy.max_invalid_row_pct
        if invalid_rows and (exceeds_row_count or exceeds_row_pct):
            self.runs.update_status(
                job.run_id,
                "FAILED",
                completed_at=datetime.utcnow(),
                error_report_key=error_key or "validation_errors",
                artifacts=artifacts,
            )
            log_event(self.logger, "run_failed", run_id=job.run_id, error_report_key=error_key)
            raise NonRetryableError("validation errors")

        if invalid_rows:
            warnings.append(
                "invalid_rows_within_threshold"
            )

        if config.merge.strategy != "best_offer" or not config.merge.best_offer:
            raise NonRetryableError("unsupported merge strategy")
        merge_start = datetime.utcnow()
        landed_cost = LandedCostConfig(
            include_shipping_handling=config.merge.best_offer.landed_cost.include_shipping_handling,
            shipping_handling_flat=config.pricing.shipping_handling_flat,
        )
        merge_config = BestOfferConfig(
            landed_cost=landed_cost,
            fallback_lead_time_days=config.merge.best_offer.fallback_lead_time_days,
        )
        merged = merge_best_offer(all_records, config=merge_config)
        stage_times["merge_seconds"] = (datetime.utcnow() - merge_start).total_seconds()

        pricing_start = datetime.utcnow()
        pricing_rules = PricingRules(
            base_margin_pct=config.pricing.base_margin_pct,
            min_price=config.pricing.min_price,
            shipping_handling_flat=config.pricing.shipping_handling_flat,
            map_policy=MapPolicy(
                enforce=config.pricing.map_policy.enforce,
                map_floor_behavior=config.pricing.map_policy.map_floor_behavior,
            ),
            rounding=RoundingRule(
                mode=config.pricing.rounding.mode,
                increment=config.pricing.rounding.increment,
            ),
        )
        priced = apply_pricing(merged, pricing_rules)
        stage_times["pricing_seconds"] = (datetime.utcnow() - pricing_start).total_seconds()

        output_start = datetime.utcnow()
        output_key = f"tenants/{config.tenant_id}/outputs/{job.run_id}/merged_inventory.csv"
        output_columns = config.output.columns or CANONICAL_COLUMNS
        output_rows = [record.model_dump() for record in priced]
        output_bytes = write_csv_bytes(
            output_rows,
            output_columns,
            extrasaction="ignore",
        )
        try:
            self.s3.upload_bytes(output_key, output_bytes)
        except (BotoCoreError, ClientError) as exc:
            raise RetryableError(str(exc)) from exc
        artifacts["merged_inventory"] = output_key
        stage_times["output_seconds"] = (datetime.utcnow() - output_start).total_seconds()

        summary_key = f"tenants/{config.tenant_id}/reports/{job.run_id}/run_summary.json"
        completed_at = datetime.utcnow()
        duration_seconds = (completed_at - start_time).total_seconds()
        summary = {
            "run_id": job.run_id,
            "tenant_id": job.tenant_id,
            "config_version": job.config_version,
            "vendor_count": len(config.vendors),
            "record_count": len(priced),
            "vendor_record_counts": vendor_counts,
            "invalid_rows": len(errors),
            "total_rows": total_rows,
            "warnings": warnings,
            "duration_seconds": duration_seconds,
            "stage_times": stage_times,
            "completed_at": completed_at.isoformat(),
        }
        try:
            self.s3.upload_text(summary_key, json.dumps(summary))
        except (BotoCoreError, ClientError) as exc:
            raise RetryableError(str(exc)) from exc
        artifacts["run_summary"] = summary_key

        self.runs.update_status(
            job.run_id,
            "SUCCEEDED",
            completed_at=datetime.utcnow(),
            artifacts=artifacts,
        )
        log_event(self.logger, "run_succeeded", run_id=job.run_id, artifacts=artifacts)

    def run_forever(self) -> None:
        if not self.queue:
            raise RuntimeError("Queue URL not configured")
        while True:
            try:
                message = self.queue.receive()
            except (BotoCoreError, ClientError) as exc:
                log_event(self.logger, "queue_receive_error", error=str(exc))
                continue
            if not message:
                continue
            job = RunJob.model_validate(message.body)
            try:
                self.run_job(job)
            except NonRetryableError as exc:
                try:
                    self.queue.delete(message.receipt_handle)
                except (BotoCoreError, ClientError) as delete_exc:
                    log_event(self.logger, "queue_delete_error", error=str(delete_exc))
                    continue
                self.runs.update_status(
                    job.run_id,
                    "FAILED",
                    completed_at=datetime.utcnow(),
                    error_report_key=str(exc),
                )
                log_event(self.logger, "run_failed", run_id=job.run_id, error=str(exc))
            except RetryableError as exc:
                log_event(self.logger, "run_retryable_error", run_id=job.run_id, error=str(exc))
            else:
                try:
                    self.queue.delete(message.receipt_handle)
                except (BotoCoreError, ClientError) as delete_exc:
                    log_event(self.logger, "queue_delete_error", error=str(delete_exc))
