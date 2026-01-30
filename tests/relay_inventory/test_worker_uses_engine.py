from __future__ import annotations

from datetime import datetime

import pytest

from relay_inventory.app.jobs.schema import RunJob
from relay_inventory.persistence.dynamo_tenants import TenantRecord
from relay_inventory.scripts.worker import Worker
from relay_inventory.util.errors import NonRetryableError


class FakeRuns:
    def __init__(self) -> None:
        self.updates = []

    def update_status(self, run_id: str, status: str, **kwargs) -> None:
        self.updates.append((run_id, status, kwargs))


class FakeTenants:
    def __init__(self, config: dict) -> None:
        self.config = config

    def get(self, tenant_id: str, config_version: int) -> TenantRecord:
        return TenantRecord(tenant_id=tenant_id, config_version=config_version, config=self.config)


class VersionedTenants:
    def __init__(self) -> None:
        self._configs: dict[tuple[str, int], dict] = {}

    def put(self, tenant_id: str, config_version: int, config: dict) -> None:
        self._configs[(tenant_id, config_version)] = config

    def get(self, tenant_id: str, config_version: int) -> TenantRecord:
        config = self._configs[(tenant_id, config_version)]
        return TenantRecord(tenant_id=tenant_id, config_version=config_version, config=config)


class FakeS3:
    def __init__(self) -> None:
        self.uploaded_bytes = {}
        self.uploaded_text = {}

    def list_latest(self, prefix: str):
        class Location:
            def __init__(self, key: str) -> None:
                self.key = key

        return Location(f"{prefix}latest.csv")

    def download_bytes(self, key: str) -> bytes:
        return b"sku,quantity_available,price\nSKU1,1,1.00\n"

    def upload_bytes(self, key: str, body: bytes) -> None:
        self.uploaded_bytes[key] = body

    def upload_text(self, key: str, body: str) -> None:
        self.uploaded_text[key] = body


class FakeS3BadEncoding(FakeS3):
    def download_bytes(self, key: str) -> bytes:
        return b"sku,quantity_available,price\nSKU\xe9,1,1.00\n"


def test_worker_calls_engine(monkeypatch):
    config = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "timezone": "UTC",
        "default_currency": "USD",
        "vendors": [
            {
                "vendor_id": "vendor-a",
                "inbound": {"type": "s3", "s3_prefix": "vendor-a/"},
                "parser": {"format": "csv", "column_map": {}},
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
    worker = Worker(bucket="bucket", runs_table="runs", tenants_table="tenants")
    worker.runs = FakeRuns()
    worker.tenants = FakeTenants(config)
    worker.s3 = FakeS3()

    call_args = {}

    def fake_run_inventory_sync(*, vendor_inputs, tenant_config, run_id, now):
        from relay_inventory.engine.run import EngineResult

        call_args["vendor_inputs"] = vendor_inputs
        call_args["tenant_config"] = tenant_config
        call_args["run_id"] = run_id
        call_args["now"] = now
        return EngineResult(
            normalized_by_vendor={
                "vendor-a": [
                    {
                        "sku": "SKU1",
                        "quantity_available": 1,
                        "price": "1.00",
                        "vendor_id": "vendor-a",
                        "updated_at": now.isoformat(),
                    }
                ]
            },
            merged_rows=[
                {
                    "sku": "SKU1",
                    "quantity_available": 1,
                    "price": "1.00",
                    "vendor_id": "vendor-a",
                    "updated_at": now.isoformat(),
                }
            ],
            errors=[],
            summary={
                "run_id": run_id,
                "vendor_count": 1,
                "vendor_record_counts": {"vendor-a": 1},
                "record_count": 1,
                "invalid_rows": 0,
                "total_rows": 1,
            },
        )

    import relay_inventory.scripts.worker as worker_module

    monkeypatch.setattr(worker_module, "run_inventory_sync", fake_run_inventory_sync)

    job = RunJob(run_id="run-1", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)
    worker.run_job(job)

    assert call_args["run_id"] == "run-1"
    assert "vendor-a" in call_args["vendor_inputs"]
    assert isinstance(call_args["vendor_inputs"]["vendor-a"], bytes)
    assert isinstance(call_args["now"], datetime)


def test_worker_reports_decode_error() -> None:
    config = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "timezone": "UTC",
        "default_currency": "USD",
        "vendors": [
            {
                "vendor_id": "vendor-a",
                "inbound": {"type": "s3", "s3_prefix": "vendor-a/"},
                "parser": {"format": "csv", "column_map": {}, "encoding": "utf-8"},
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
    worker = Worker(bucket="bucket", runs_table="runs", tenants_table="tenants")
    worker.runs = FakeRuns()
    worker.tenants = FakeTenants(config)
    worker.s3 = FakeS3BadEncoding()

    job = RunJob(run_id="run-1", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)

    with pytest.raises(NonRetryableError):
        worker.run_job(job)

    _, status, kwargs = worker.runs.updates[-1]
    assert status == "FAILED"
    assert kwargs["error_code"] == "DECODE_ERROR"
    assert "vendor-a" in kwargs["error_message"]


def test_worker_uses_pinned_config_version(monkeypatch) -> None:
    config_v1 = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "timezone": "UTC",
        "default_currency": "USD",
        "vendors": [
            {
                "vendor_id": "vendor-a",
                "inbound": {"type": "s3", "s3_prefix": "vendor-a/"},
                "parser": {"format": "csv", "column_map": {}},
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
    config_v2 = {**config_v1, "default_currency": "EUR"}
    tenants = VersionedTenants()
    tenants.put("tenant-a", 1, config_v1)
    tenants.put("tenant-a", 2, config_v2)

    worker = Worker(bucket="bucket", runs_table="runs", tenants_table="tenants")
    worker.runs = FakeRuns()
    worker.tenants = tenants
    worker.s3 = FakeS3()

    call_args = {}

    def fake_run_inventory_sync(*, vendor_inputs, tenant_config, run_id, now):
        from relay_inventory.engine.run import EngineResult

        call_args["tenant_config"] = tenant_config
        return EngineResult(
            normalized_by_vendor={},
            merged_rows=[],
            errors=[],
            summary={
                "run_id": run_id,
                "vendor_count": 0,
                "vendor_record_counts": {},
                "record_count": 0,
                "invalid_rows": 0,
                "total_rows": 0,
            },
        )

    import relay_inventory.scripts.worker as worker_module

    monkeypatch.setattr(worker_module, "run_inventory_sync", fake_run_inventory_sync)

    job = RunJob(run_id="run-1", tenant_id="tenant-a", vendors=["vendor-a"], config_version=1)
    worker.run_job(job)

    assert call_args["tenant_config"].default_currency == "USD"
    snapshot = next(iter(worker.s3.uploaded_text.values()))
    assert '"config_version": 1' in snapshot
    assert '"default_currency": "USD"' in snapshot
