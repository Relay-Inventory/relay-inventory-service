from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    tenant_id: str
    run_type: str = "inventory_sync"
    vendors: List[str]


class RunStatus(BaseModel):
    run_id: str
    tenant_id: str
    config_version: int
    status: str
    stage: Optional[str] = None
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    failed_stage: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    errors_artifact_key: Optional[str] = None
    error_report_key: Optional[str] = None
    artifacts: dict[str, str] = Field(default_factory=dict)
