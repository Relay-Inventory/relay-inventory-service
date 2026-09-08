from __future__ import annotations

from dataclasses import dataclass

from inventory_aggregator.engine.config.compiled import CompiledTenantConfig


@dataclass
class RunContext:
    shop_id: str
    run_id: str
    config_version: int
    compiled_config: CompiledTenantConfig
    write_enabled: bool = True
    trigger: str = "scheduled"
