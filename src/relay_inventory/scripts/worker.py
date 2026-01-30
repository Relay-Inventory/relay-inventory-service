from __future__ import annotations

import json
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, List

from botocore.exceptions import BotoCoreError, ClientError

from relay_inventory.adapters.queue.sqs import SqsAdapter, SqsMessage
from relay_inventory.adapters.storage.s3 import S3Adapter
from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.app.models.config import TenantConfig
from relay_inventory.engine.canonical.io import write_csv_bytes
from relay_inventory.engine.canonical.models import CANONICAL_COLUMNS
from relay_inventory.engine.run import (
    DecodeError,
    MissingRequiredColumnsError,
    run_inventory_sync,
    sku_map_input_key,
)
from relay_inventory.persistence.dynamo_runs import DynamoRuns
from relay_inventory.persistence.dynamo_tenants import DynamoTenants
from relay_inventory.util.errors import NonRetryableError, RetryableError
from relay_inventory.util.logging import get_logger, log_event
from relay_inventory.util.metrics import CloudWatchMetrics


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
        self.metrics = CloudWatchMetrics.from_env()
        self.visibility_timeout_seconds = int(os.getenv("WORKER_VISIBILITY_TIMEOUT_SECONDS", "300"))
        self.visibility_heartbeat_seconds = int(os.getenv("WORKER_VISIBILITY_HEARTBEAT_SECONDS", "60"))
        self.tenant_backoff_seconds = int(os.getenv("WORKER_TENANT_BACKOFF_SECONDS", "30"))
        self.poison_max_receives = int(os.getenv("WORKER_POISON_MAX_RECEIVES", "5"))

    def _stage_index(self, stage: str) -> int:
        stages = ["QUEUE", "FETCH_INPUTS", "NORMALIZE", "MERGE_PRICE", "WRITE_OUTPUTS", "COMPLETE"]
        return stages.index(stage)

    def _coerce_stage(self, run_id: str, stage: str | None) -> str | None:
        if not stage or not hasattr(self.runs, "get"):
            return stage
        record = self.runs.get(run_id)
        current = getattr(record, "stage", None) if record else None
        if not current:
            return stage
        if self._stage_index(stage) < self._stage_index(current):
            return current
        return stage

    def _update_run_status(self, run_id: str, status: str, **kwargs: object) -> None:
        stage = kwargs.get("stage")
        if stage:
            kwargs["stage"] = self._coerce_stage(run_id, stage)
        if "started_at" in kwargs and hasattr(self.runs, "get"):
            record = self.runs.get(run_id)
            if record and getattr(record, "started_at", None):
                kwargs.pop("started_at")
        self.runs.update_status(run_id, status, **kwargs)

    def _run_prefix(self, *, run_id: str, tenant_id: str) -> str:
        return f"{run_id}/tenants/{tenant_id}"

    def _ensure_run_prefix(self, *, run_id: str, key: str) -> None:
        if not key.startswith(f"{run_id}/"):
            raise ValueError(f"artifact key must be under {run_id}/ prefix: {key}")

    def _inbound_copy_key(self, *, run_id: str, tenant_id: str, vendor_id: str, source_key: str) -> str:
        filename = PurePosixPath(source_key).name or "inbound"
        return f"{self._run_prefix(run_id=run_id, tenant_id=tenant_id)}/inbound/{vendor_id}/{filename}"

    def _write_error_report(self, *, run_id: str, tenant_id: str, errors: list[dict]) -> str:
        errors_key = f"{self._run_prefix(run_id=run_id, tenant_id=tenant_id)}/reports/errors.json"
        self._ensure_run_prefix(run_id=run_id, key=errors_key)
        try:
            self.s3.upload_text(errors_key, json.dumps(errors, default=str))
        except (BotoCoreError, ClientError) as exc:
            raise RetryableError(str(exc)) from exc
        return errors_key

    def _fail_run(
        self,
        *,
        job: RunJob,
        status: str,
        stage: str,
        error_code: str,
        error_message: str,
        artifacts: Dict[str, str],
        errors_key: str | None,
    ) -> None:
        if not errors_key:
            errors_key = self._write_error_report(
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                errors=[{"error_code": error_code, "error_message": error_message}],
            )
        artifacts.setdefault("errors", errors_key)
        self._update_run_status(
            job.run_id,
            status,
            stage=stage,
            finished_at=datetime.utcnow(),
            failed_stage=stage,
            error_code=error_code,
            error_message=error_message,
            errors_artifact_key=errors_key,
            error_report_key=errors_key,
            artifacts=artifacts,
        )
        self.metrics.record_run_failure(tenant_id=job.tenant_id, failed=True)

    def _find_active_tenant_run(self, *, tenant_id: str, exclude_run_id: str) -> str | None:
        if not hasattr(self.runs, "find_running_by_tenant"):
            return None
        record = self.runs.find_running_by_tenant(tenant_id)
        if record and record.run_id != exclude_run_id:
            return record.run_id
        return None

    def _start_visibility_heartbeat(self, receipt_handle: str) -> tuple[threading.Event, threading.Thread] | None:
        if not self.queue or self.visibility_timeout_seconds <= 0:
            return None
        try:
            self.queue.change_visibility(receipt_handle, self.visibility_timeout_seconds)
        except (BotoCoreError, ClientError) as exc:
            log_event(self.logger, "queue_visibility_error", error=str(exc))
        if self.visibility_heartbeat_seconds <= 0:
            return None
        stop_event = threading.Event()

        def _heartbeat() -> None:
            while not stop_event.wait(self.visibility_heartbeat_seconds):
                try:
                    self.queue.change_visibility(receipt_handle, self.visibility_timeout_seconds)
                except (BotoCoreError, ClientError) as exc:
                    log_event(self.logger, "queue_visibility_error", error=str(exc))

        thread = threading.Thread(target=_heartbeat, daemon=True)
        thread.start()
        return stop_event, thread

    def run_job(self, job: RunJob) -> None:
        log_event(self.logger, "run_started", run_id=job.run_id, tenant_id=job.tenant_id)
        self._update_run_status(
            job.run_id,
            "RUNNING",
            started_at=datetime.utcnow(),
            stage="FETCH_INPUTS",
        )
        tenant_record = self.tenants.get(job.tenant_id, job.config_version)
        if not tenant_record:
            self._fail_run(
                job=job,
                status="FAILED",
                stage="FETCH_INPUTS",
                error_code="missing_tenant_config",
                error_message="missing tenant config",
                artifacts={},
                errors_key=None,
            )
            raise NonRetryableError("missing tenant config")
        config = TenantConfig.model_validate(tenant_record.config)
        artifacts: Dict[str, str] = {}
        warnings: List[str] = []
        missing_vendor_errors: List[Dict[str, str]] = []
        start_time = datetime.utcnow()
        stage_times: Dict[str, float] = {}
        error_policy = config.error_policy
        run_prefix = self._run_prefix(run_id=job.run_id, tenant_id=config.tenant_id)
        reports_prefix = f"{run_prefix}/reports"

        config_snapshot_key = f"{reports_prefix}/config_snapshot.json"
        self._ensure_run_prefix(run_id=job.run_id, key=config_snapshot_key)
        config_snapshot = {
            "run_id": job.run_id,
            "tenant_id": job.tenant_id,
            "config_version": job.config_version,
            "tenant_config": config.model_dump(),
        }
        try:
            self.s3.upload_text(config_snapshot_key, json.dumps(config_snapshot))
        except (BotoCoreError, ClientError) as exc:
            raise RetryableError(str(exc)) from exc
        artifacts["config_snapshot"] = config_snapshot_key

        vendor_inputs: Dict[str, bytes] = {}
        vendor_latest: Dict[str, dict] = {}
        ingest_start = datetime.utcnow()
        for vendor in config.vendors:
            prefix = vendor.inbound.s3_prefix or ""
            try:
                latest = self.s3.list_latest(prefix)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            if not latest:
                vendor_latest[vendor.vendor_id] = {
                    "status": "missing",
                    "s3_prefix": prefix,
                    "required": vendor.required,
                    "expected_prefix": prefix,
                    "reason": "no_objects_found",
                }
                continue
            vendor_latest[vendor.vendor_id] = {
                "status": "found",
                "s3_prefix": prefix,
                "required": vendor.required,
                "expected_prefix": prefix,
                "s3_key": latest.key,
                "etag": latest.etag,
                "size": latest.size,
                "last_modified": latest.last_modified.isoformat() if latest.last_modified else None,
                "selection": "latest_by_last_modified",
            }
            try:
                raw_bytes = self.s3.download_bytes(latest.key)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            vendor_inputs[vendor.vendor_id] = raw_bytes
            inbound_copy_key = self._inbound_copy_key(
                run_id=job.run_id,
                tenant_id=config.tenant_id,
                vendor_id=vendor.vendor_id,
                source_key=latest.key,
            )
            self._ensure_run_prefix(run_id=job.run_id, key=inbound_copy_key)
            try:
                self.s3.upload_bytes(inbound_copy_key, raw_bytes)
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            vendor_latest[vendor.vendor_id]["run_copy_key"] = inbound_copy_key
            artifacts[f"inbound_{vendor.vendor_id}"] = inbound_copy_key
            if vendor.sku_map and vendor.sku_map.s3_key:
                try:
                    sku_bytes = self.s3.download_bytes(vendor.sku_map.s3_key)
                except (BotoCoreError, ClientError) as exc:
                    raise RetryableError(str(exc)) from exc
                vendor_inputs[sku_map_input_key(vendor.vendor_id)] = sku_bytes
        stage_times["ingest_seconds"] = (datetime.utcnow() - ingest_start).total_seconds()

        missing_required = []
        missing_optional = []
        for vendor in config.vendors:
            if vendor.vendor_id in vendor_inputs:
                continue
            vendor_missing = {
                "vendor_id": vendor.vendor_id,
                "expected_prefix": vendor.inbound.s3_prefix or "",
                "required": vendor.required,
            }
            if vendor.required:
                missing_required.append(vendor_missing)
            else:
                missing_optional.append(vendor_missing)

        if missing_required and error_policy.missing_required_vendor_policy != "warn_only":
            expected = ", ".join(
                f"{vendor['vendor_id']} (expected prefix {vendor['expected_prefix']})" for vendor in missing_required
            )
            error_message = f"required vendor inbound missing: {expected}"
            self._fail_run(
                job=job,
                status="FAILED",
                stage="FETCH_INPUTS",
                error_code="REQUIRED_VENDOR_MISSING",
                error_message=error_message,
                artifacts=artifacts,
                errors_key=None,
            )
            raise NonRetryableError(error_message)

        for vendor in missing_optional:
            missing_vendor_errors.append(
                {
                    "error_code": "OPTIONAL_VENDOR_MISSING",
                    "error_message": (
                        f"optional vendor inbound missing for {vendor['vendor_id']} "
                        f"(expected prefix {vendor['expected_prefix']})"
                    ),
                    "vendor_id": vendor["vendor_id"],
                    "expected_prefix": vendor["expected_prefix"],
                }
            )
            warnings.append(f"optional_vendor_missing:{vendor['vendor_id']}")

        if missing_required and error_policy.missing_required_vendor_policy == "warn_only":
            for vendor in missing_required:
                missing_vendor_errors.append(
                    {
                        "error_code": "REQUIRED_VENDOR_MISSING",
                        "error_message": (
                            f"required vendor inbound missing for {vendor['vendor_id']} "
                            f"(expected prefix {vendor['expected_prefix']})"
                        ),
                        "vendor_id": vendor["vendor_id"],
                        "expected_prefix": vendor["expected_prefix"],
                    }
                )
                warnings.append(f"required_vendor_missing:{vendor['vendor_id']}")

        input_manifest_key = f"{reports_prefix}/input_manifest.json"
        self._ensure_run_prefix(run_id=job.run_id, key=input_manifest_key)
        input_manifest = {
            "run_id": job.run_id,
            "tenant_id": job.tenant_id,
            "config_version": job.config_version,
            "generated_at": datetime.utcnow().isoformat(),
            "vendors": vendor_latest,
        }
        try:
            self.s3.upload_text(input_manifest_key, json.dumps(input_manifest))
        except (BotoCoreError, ClientError) as exc:
            raise RetryableError(str(exc)) from exc
        artifacts["input_manifest"] = input_manifest_key

        if config.schema_version != 1:
            error_message = f"unsupported schema_version {config.schema_version}"
            self._fail_run(
                job=job,
                status="FAILED",
                stage="FETCH_INPUTS",
                error_code="unsupported_schema_version",
                error_message=error_message,
                artifacts=artifacts,
                errors_key=None,
            )
            raise NonRetryableError(error_message)

        engine_start = datetime.utcnow()
        self._update_run_status(job.run_id, "RUNNING", stage="NORMALIZE")
        try:
            engine_result = run_inventory_sync(
                vendor_inputs=vendor_inputs,
                tenant_config=config,
                run_id=job.run_id,
                now=engine_start,
            )
        except DecodeError as exc:
            error_message = f"decode error for vendor {exc.vendor_id}: {exc}"
            self._fail_run(
                job=job,
                status="FAILED",
                stage="NORMALIZE",
                error_code="DECODE_ERROR",
                error_message=error_message,
                artifacts=artifacts,
                errors_key=None,
            )
            raise NonRetryableError(error_message) from exc
        except MissingRequiredColumnsError as exc:
            error_message = str(exc)
            self._fail_run(
                job=job,
                status="FAILED",
                stage="NORMALIZE",
                error_code="missing_required_columns",
                error_message=error_message,
                artifacts=artifacts,
                errors_key=None,
            )
            raise NonRetryableError(error_message) from exc
        except ValueError as exc:
            error_message = str(exc)
            self._fail_run(
                job=job,
                status="FAILED",
                stage="NORMALIZE",
                error_code="invalid_input",
                error_message=error_message,
                artifacts=artifacts,
                errors_key=None,
            )
            raise NonRetryableError(error_message) from exc
        stage_times["engine_seconds"] = (datetime.utcnow() - engine_start).total_seconds()
        self._update_run_status(job.run_id, "RUNNING", stage="MERGE_PRICE")

        errors = engine_result.errors
        vendor_counts = engine_result.summary["vendor_record_counts"]
        total_rows = engine_result.summary["total_rows"]

        for vendor_id, normalized_rows in engine_result.normalized_by_vendor.items():
            if vendor_id not in vendor_inputs:
                continue
            normalized_key = (
                f"{run_prefix}/normalized/{vendor_id}/normalized.csv"
            )
            self._ensure_run_prefix(run_id=job.run_id, key=normalized_key)
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
        error_entries = list(missing_vendor_errors)
        if errors:
            error_entries.extend([error.__dict__ for error in errors])
        if error_entries:
            error_key = f"{reports_prefix}/errors.json"
            self._ensure_run_prefix(run_id=job.run_id, key=error_key)
            try:
                self.s3.upload_text(
                    error_key,
                    json.dumps(error_entries, default=str),
                )
            except (BotoCoreError, ClientError) as exc:
                raise RetryableError(str(exc)) from exc
            artifacts["errors"] = error_key

        invalid_rows = len(errors)
        if total_rows == 0 and not missing_vendor_errors:
            self._fail_run(
                job=job,
                status="FAILED",
                stage="MERGE_PRICE",
                error_code="no_rows_parsed",
                error_message="no rows parsed",
                artifacts=artifacts,
                errors_key=error_key,
            )
            log_event(
                self.logger,
                "run_failed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                error_report_key=error_key,
            )
            raise NonRetryableError("no rows parsed")

        exceeds_row_count = invalid_rows > error_policy.max_invalid_rows
        exceeds_row_pct = (invalid_rows / total_rows) > error_policy.max_invalid_row_pct
        if invalid_rows and (exceeds_row_count or exceeds_row_pct):
            self._fail_run(
                job=job,
                status="FAILED",
                stage="MERGE_PRICE",
                error_code="validation_errors",
                error_message="validation errors",
                artifacts=artifacts,
                errors_key=error_key,
            )
            log_event(
                self.logger,
                "run_failed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                error_report_key=error_key,
            )
            raise NonRetryableError("validation errors")

        if invalid_rows:
            warnings.append(
                "invalid_rows_within_threshold"
            )

        output_start = datetime.utcnow()
        self._update_run_status(job.run_id, "RUNNING", stage="WRITE_OUTPUTS")
        output_key = f"{run_prefix}/outputs/merged_inventory.csv"
        self._ensure_run_prefix(run_id=job.run_id, key=output_key)
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

        summary_key = f"{reports_prefix}/run_summary.json"
        self._ensure_run_prefix(run_id=job.run_id, key=summary_key)
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

        self._update_run_status(
            job.run_id,
            "SUCCEEDED",
            stage="COMPLETE",
            finished_at=datetime.utcnow(),
            artifacts=artifacts,
            clear_fields=[
                "failed_stage",
                "error_code",
                "error_message",
                "errors_artifact_key",
                "error_report_key",
            ],
        )
        self.metrics.record_run_failure(tenant_id=job.tenant_id, failed=False)
        log_event(
            self.logger,
            "run_succeeded",
            run_id=job.run_id,
            tenant_id=job.tenant_id,
            artifacts=artifacts,
        )

    def _process_message(self, message: SqsMessage) -> None:
        if not self.queue:
            raise RuntimeError("Queue URL not configured")
        job = RunJob.model_validate(message.body)
        record = self.runs.get(job.run_id) if hasattr(self.runs, "get") else None
        if record and record.status in {"RUNNING", "SUCCEEDED"}:
            log_event(
                self.logger,
                "run_already_processed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                status=record.status,
            )
            try:
                self.queue.delete(message.receipt_handle)
            except (BotoCoreError, ClientError) as delete_exc:
                self.metrics.record_worker_error(error_type="queue_delete_error")
                log_event(self.logger, "queue_delete_error", error=str(delete_exc))
            return
        if record and record.status == "FAILED" and record.error_code == "POISON_JOB":
            log_event(
                self.logger,
                "poison_job_already_failed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                receive_count=message.receive_count,
            )
            return
        if message.receive_count >= self.poison_max_receives:
            self._update_run_status(
                job.run_id,
                "FAILED",
                stage="QUEUE",
                failed_stage="QUEUE",
                finished_at=datetime.utcnow(),
                error_code="POISON_JOB",
                error_message=(
                    f"Job exceeded max receives ({message.receive_count}/{self.poison_max_receives})"
                ),
            )
            self.metrics.record_worker_error(error_type="poison_job")
            log_event(
                self.logger,
                "poison_job_detected",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                receive_count=message.receive_count,
            )
            return
        active_run_id = self._find_active_tenant_run(tenant_id=job.tenant_id, exclude_run_id=job.run_id)
        if active_run_id:
            log_event(
                self.logger,
                "tenant_run_in_progress",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                active_run_id=active_run_id,
            )
            try:
                self.queue.change_visibility(message.receipt_handle, self.tenant_backoff_seconds)
            except (BotoCoreError, ClientError) as exc:
                self.metrics.record_worker_error(error_type="queue_visibility_error")
                log_event(self.logger, "queue_visibility_error", error=str(exc))
            return
        if record and record.status == "FAILED":
            log_event(
                self.logger,
                "run_already_processed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                status=record.status,
            )
            try:
                self.queue.delete(message.receipt_handle)
            except (BotoCoreError, ClientError) as delete_exc:
                self.metrics.record_worker_error(error_type="queue_delete_error")
                log_event(self.logger, "queue_delete_error", error=str(delete_exc))
            return
        heartbeat = self._start_visibility_heartbeat(message.receipt_handle)
        try:
            self.run_job(job)
        except NonRetryableError as exc:
            try:
                self.queue.delete(message.receipt_handle)
            except (BotoCoreError, ClientError) as delete_exc:
                self.metrics.record_worker_error(error_type="queue_delete_error")
                log_event(self.logger, "queue_delete_error", error=str(delete_exc))
            self._update_run_status(
                job.run_id,
                "FAILED",
                finished_at=datetime.utcnow(),
                error_message=str(exc),
            )
            log_event(
                self.logger,
                "run_failed",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                error=str(exc),
            )
        except RetryableError as exc:
            self.metrics.record_worker_error(error_type="run_retryable_error")
            log_event(
                self.logger,
                "run_retryable_error",
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                error=str(exc),
            )
        finally:
            if heartbeat:
                stop_event, thread = heartbeat
                stop_event.set()
                thread.join(timeout=2)
        else:
            try:
                self.queue.delete(message.receipt_handle)
            except (BotoCoreError, ClientError) as delete_exc:
                self.metrics.record_worker_error(error_type="queue_delete_error")
                log_event(self.logger, "queue_delete_error", error=str(delete_exc))

    def run_forever(self) -> None:
        if not self.queue:
            raise RuntimeError("Queue URL not configured")
        worker_concurrency = max(1, int(os.getenv("WORKER_CONCURRENCY", "1")))
        with ThreadPoolExecutor(max_workers=worker_concurrency) as executor:
            futures = set()
            while True:
                completed = {future for future in futures if future.done()}
                if completed:
                    for future in completed:
                        future.result()
                    futures -= completed
                if len(futures) >= worker_concurrency:
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                try:
                    message = self.queue.receive()
                except (BotoCoreError, ClientError) as exc:
                    self.metrics.record_worker_error(error_type="queue_receive_error")
                    log_event(self.logger, "queue_receive_error", error=str(exc))
                    continue
                if not message:
                    continue
                futures.add(executor.submit(self._process_message, message))
