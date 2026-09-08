from __future__ import annotations

from typing import List

from pydantic import BaseModel


class RunJob(BaseModel):
    run_id: str
    tenant_id: str
    vendors: List[str]
    config_version: int
