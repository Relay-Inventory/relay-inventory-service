from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from relay_inventory.app.models.config import TenantConfig

SUPPORTED_SCHEMA_VERSIONS = {1}


def load_tenant_config(path: str | Path) -> TenantConfig:
    data: Dict[str, Any]
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    config = TenantConfig.model_validate(data)
    if config.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported schema_version {config.schema_version}")
    return config
