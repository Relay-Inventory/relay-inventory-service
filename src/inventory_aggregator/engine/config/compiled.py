from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from inventory_aggregator.app.models.config import TenantConfig, VendorConfig
from inventory_aggregator.engine.canonical.models import CANONICAL_COLUMNS
from inventory_aggregator.engine.rules import CompiledVendorRules, compile_vendor_rules


@dataclass
class CompiledVendorConfig:
    config: VendorConfig
    rules: Optional[CompiledVendorRules]


@dataclass
class CompiledTenantConfig:
    config: TenantConfig
    vendors: Dict[str, CompiledVendorConfig] = field(default_factory=dict)


def compile_tenant_config(tenant_config: TenantConfig) -> CompiledTenantConfig:
    vendors = {
        vendor.vendor_id: CompiledVendorConfig(
            config=vendor,
            rules=compile_vendor_rules(vendor.rules, allowed_columns=set(CANONICAL_COLUMNS)),
        )
        for vendor in tenant_config.vendors
    }
    return CompiledTenantConfig(config=tenant_config, vendors=vendors)
