from __future__ import annotations

from datetime import datetime

import pytest

from relay_inventory.app.models.config import TenantConfig
from relay_inventory.engine.run import DecodeError, run_inventory_sync


def _base_config(encoding: str) -> TenantConfig:
    return TenantConfig.model_validate(
        {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "timezone": "UTC",
            "default_currency": "USD",
            "vendors": [
                {
                    "vendor_id": "vendor-a",
                    "inbound": {"type": "s3", "s3_prefix": "vendor-a/"},
                    "parser": {"format": "csv", "column_map": {}, "encoding": encoding},
                }
            ],
            "pricing": {
                "base_margin_pct": 0.1,
                "min_price": 1,
                "shipping_handling_flat": 0,
                "map_policy": {"enforce": True, "map_floor_behavior": "max(price, map_price)"},
                "rounding": {"mode": "nearest", "increment": "0.01"},
            },
            "merge": {
                "strategy": "best_offer",
                "best_offer": {"sort_by": [], "landed_cost": {"include_shipping_handling": True}},
            },
            "output": {"format": "csv", "columns": ["sku", "quantity_available", "price"]},
            "error_policy": {"max_invalid_rows": 0, "max_invalid_row_pct": 0.0},
        }
    )


def test_latin1_vendor_input_parses() -> None:
    tenant_config = _base_config("latin-1")
    raw_bytes = "sku,quantity_available,price\nSKUé,1,1.00\n".encode("latin-1")
    result = run_inventory_sync(
        vendor_inputs={"vendor-a": raw_bytes},
        tenant_config=tenant_config,
        run_id="run-1",
        now=datetime.utcnow(),
    )

    assert not result.errors
    assert result.normalized_by_vendor["vendor-a"][0]["sku"] == "SKUé"


def test_decode_error_includes_vendor_id() -> None:
    tenant_config = _base_config("utf-8")
    raw_bytes = "sku,quantity_available,price\nSKUé,1,1.00\n".encode("latin-1")

    with pytest.raises(DecodeError) as excinfo:
        run_inventory_sync(
            vendor_inputs={"vendor-a": raw_bytes},
            tenant_config=tenant_config,
            run_id="run-1",
            now=datetime.utcnow(),
        )

    assert excinfo.value.vendor_id == "vendor-a"
