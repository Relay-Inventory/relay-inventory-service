from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List

from botocore.exceptions import BotoCoreError, ClientError

from relay_inventory.adapters.queue.sqs import SqsAdapter
from relay_inventory.adapters.storage.s3 import S3Adapter
from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.app.models.config import TenantConfig
from relay_inventory.engine.canonical.io import write_csv_bytes
from relay_inventory.engine.canonical.models import CANONICAL_COLUMNS
from relay_inventory.engine.run import MissingRequiredColumnsError, run_inventory_sync, sku_map_input_key
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

        artifacts: Dict[str, str] = {}
        warnings: List[str] = []
        start_time = datetime.utcnow()
        stage_times: Dict[str, float] = {}
        error_policy = config.error_policy

        vendor_inputs: Dict[str, bytes] = {}
        ingest_start = datetime.utcnow()
        for vendor in config.vendors:
            prefix = vendor.inbound.s3_prefix or ""
            try:
                latest = self.s3.list_latest(prefix)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            if not latest:
                continue
            try:
                raw_text = self.s3.download_text(latest.key)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            vendor_inputs[vendor.vendor_id] = raw_text.encode("utf-8")
            if vendor.sku_map and vendor.sku_map.s3_key:
                try:
                    sku_text = self.s3.download_text(vendor.sku_map.s3_key)
                except (BotoCoreError, ClientError) as exc:
                    raise RetryableError(str(exc)) from exc
                vendor_inputs[sku_map_input_key(vendor.vendor_id)] = sku_text.encode("utf-8")
        stage_times["ingest_seconds"] = (datetime.utcnow() - ingest_start).total_seconds()

        engine_start = datetime.utcnow()
        try:
            engine_result = run_inventory_sync(
                vendor_inputs=vendor_inputs,
                tenant_config=config,
                run_id=job.run_id,
                now=engine_start,
            )
        except MissingRequiredColumnsError as exc:
            raise NonRetryableError(str(exc)) from exc
        except ValueError as exc:
            raise NonRetryableError(str(exc)) from exc
        stage_times["engine_seconds"] = (datetime.utcnow() - engine_start).total_seconds()

        errors = engine_result.errors
        vendor_counts = engine_result.summary["vendor_record_counts"]
        total_rows = engine_result.summary["total_rows"]

        for vendor_id, normalized_rows in engine_result.normalized_by_vendor.items():
            if vendor_id not in vendor_inputs:
                continue
            normalized_key = (
                f"tenants/{config.tenant_id}/normalized/{vendor_id}/{job.run_id}/normalized.csv"
            )
            normalized_bytes = write_csv_bytes(
                normalized_rows,
                CANONICAL_COLUMNS,
                extrasaction="raise",
            )
            try:
                self.s3.upload_bytes(normalized_key, normalized_bytes)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            artifacts[f"normalized_{vendor_id}"] = normalized_key

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

        output_start = datetime.utcnow()
        output_key = f"tenants/{config.tenant_id}/outputs/{job.run_id}/merged_inventory.csv"
        output_columns = config.output.columns or CANONICAL_COLUMNS
        output_bytes = write_csv_bytes(
            engine_result.merged_rows,
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
        summary_data = engine_result.summary
        summary = {
            "run_id": job.run_id,
            "tenant_id": job.tenant_id,
            "config_version": job.config_version,
            "vendor_count": summary_data["vendor_count"],
            "record_count": summary_data["record_count"],
            "vendor_record_counts": vendor_counts,
            "invalid_rows": summary_data["invalid_rows"],
            "total_rows": summary_data["total_rows"],
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
