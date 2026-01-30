from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Dict, Optional

import logging

from fastapi import Depends, FastAPI, HTTPException

from botocore.exceptions import BotoCoreError, ClientError
from relay_inventory.adapters.queue.sqs import SqsAdapter
from relay_inventory.adapters.storage.s3 import S3Adapter
from relay_inventory.app.auth.api_key import ApiKeyAuth
from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.app.models.config import TenantConfig
from relay_inventory.app.models.run import RunRequest, RunStatus
from relay_inventory.persistence.dynamo_runs import DynamoRuns, RunRecord
from relay_inventory.persistence.dynamo_tenants import DynamoTenants, TenantRecord


class InMemoryTenants:
    def __init__(self) -> None:
        self._data: Dict[str, TenantRecord] = {}

    def put(self, record: TenantRecord) -> None:
        self._data[record.tenant_id] = record

    def get(self, tenant_id: str, config_version: int) -> Optional[TenantRecord]:
        record = self._data.get(tenant_id)
        if record and record.config_version == config_version:
            return record
        return None

    def get_latest(self, tenant_id: str) -> Optional[TenantRecord]:
        return self._data.get(tenant_id)


class InMemoryRuns:
    def __init__(self) -> None:
        self._data: Dict[str, RunRecord] = {}

    def create(self, record: RunRecord) -> None:
        self._data[record.run_id] = record

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        clear_fields: Optional[list[str]] = None,
        **kwargs: object,
    ) -> None:
        record = self._data[run_id]
        for key, value in kwargs.items():
            setattr(record, key, value)
        if clear_fields:
            for field in clear_fields:
                setattr(record, field, None)
        record.status = status

    def get(self, run_id: str) -> Optional[RunRecord]:
        return self._data.get(run_id)

    def find_running_by_tenant(self, tenant_id: str) -> Optional[RunRecord]:
        for record in self._data.values():
            if record.tenant_id == tenant_id and record.status == "RUNNING":
                return record
        return None


runs_table = os.getenv("RUNS_TABLE")
tenants_table = os.getenv("TENANTS_TABLE")
queue_url = os.getenv("SQS_QUEUE_URL")
s3_bucket = os.getenv("ARTIFACT_BUCKET")
api_keys = set(filter(None, os.getenv("API_KEYS", "").split(",")))

runs_repo = DynamoRuns(runs_table) if runs_table else InMemoryRuns()
tenants_repo = DynamoTenants(tenants_table) if tenants_table else InMemoryTenants()
queue = SqsAdapter(queue_url) if queue_url else None
s3_adapter = S3Adapter(s3_bucket) if s3_bucket else None

logger = logging.getLogger("relay_inventory.api")

auth_dependency = ApiKeyAuth(api_keys)

app = FastAPI()


@app.get("/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/tenants", dependencies=[Depends(auth_dependency)])
async def create_tenant(config: TenantConfig) -> dict[str, str]:
    if config.schema_version != 1:
        raise HTTPException(status_code=400, detail="Unsupported schema_version")
    record = TenantRecord(tenant_id=config.tenant_id, config_version=1, config=config.model_dump())
    tenants_repo.put(record)
    return {"tenant_id": config.tenant_id, "config_version": "1"}


@app.get("/v1/tenants/{tenant_id}", dependencies=[Depends(auth_dependency)])
async def get_tenant(tenant_id: str, config_version: int = 1) -> TenantConfig:
    record = tenants_repo.get(tenant_id, config_version)
    if not record:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantConfig.model_validate(record.config)


@app.put("/v1/tenants/{tenant_id}/config", dependencies=[Depends(auth_dependency)])
async def update_tenant_config(tenant_id: str, config: TenantConfig) -> dict[str, str]:
    if tenant_id != config.tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id mismatch")
    if config.schema_version != 1:
        raise HTTPException(status_code=400, detail="Unsupported schema_version")
    existing = tenants_repo.get_latest(tenant_id) if hasattr(tenants_repo, "get_latest") else None
    next_version = 1
    if existing:
        next_version = existing.config_version + 1
    record = TenantRecord(tenant_id=tenant_id, config_version=next_version, config=config.model_dump())
    tenants_repo.put(record)
    return {"tenant_id": tenant_id, "config_version": str(record.config_version)}


@app.post("/v1/runs", dependencies=[Depends(auth_dependency)])
async def create_run(request: RunRequest) -> RunStatus:
    run_id = str(uuid.uuid4())
    running = runs_repo.find_running_by_tenant(request.tenant_id) if hasattr(runs_repo, "find_running_by_tenant") else None
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"run already running for tenant {request.tenant_id} (run_id={running.run_id})",
        )
    tenant_record = (
        tenants_repo.get_latest(request.tenant_id) if hasattr(tenants_repo, "get_latest") else None
    )
    config_version = tenant_record.config_version if tenant_record else 1
    record = RunRecord(
        run_id=run_id,
        tenant_id=request.tenant_id,
        config_version=config_version,
        status="QUEUED",
        requested_at=datetime.utcnow().isoformat(),
    )
    runs_repo.create(record)
    if queue:
        job = RunJob(
            run_id=run_id,
            tenant_id=request.tenant_id,
            vendors=request.vendors,
            config_version=record.config_version,
        )
        try:
            queue.send(job.model_dump())
        except (BotoCoreError, ClientError) as exc:
            logger.exception("queue_send_failed")
            raise HTTPException(status_code=503, detail="Queue unavailable") from exc
    return RunStatus(
        run_id=run_id,
        tenant_id=request.tenant_id,
        config_version=record.config_version,
        status=record.status,
    )


@app.get("/v1/runs/{run_id}", dependencies=[Depends(auth_dependency)])
async def get_run(run_id: str) -> RunStatus:
    record = runs_repo.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStatus(
        run_id=record.run_id,
        tenant_id=record.tenant_id,
        config_version=record.config_version,
        status=record.status,
        stage=getattr(record, "stage", None),
        requested_at=datetime.fromisoformat(record.requested_at),
        started_at=datetime.fromisoformat(record.started_at)
        if record.started_at
        else None,
        finished_at=(
            datetime.fromisoformat(record.finished_at)
            if getattr(record, "finished_at", None)
            else datetime.fromisoformat(record.completed_at)
            if getattr(record, "completed_at", None)
            else None
        ),
        failed_stage=getattr(record, "failed_stage", None),
        error_code=getattr(record, "error_code", None),
        error_message=getattr(record, "error_message", None),
        errors_artifact_key=getattr(record, "errors_artifact_key", None),
        error_report_key=getattr(record, "error_report_key", None),
        artifacts=record.artifacts or {},
    )


@app.get("/v1/runs/{run_id}/artifacts", dependencies=[Depends(auth_dependency)])
async def get_run_artifacts(run_id: str) -> dict[str, str]:
    record = runs_repo.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if not s3_adapter:
        raise HTTPException(status_code=503, detail="Artifact storage not configured")
    artifacts = record.artifacts or {}
    return {name: s3_adapter.presign(key) for name, key in artifacts.items()}
