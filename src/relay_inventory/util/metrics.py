from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from relay_inventory.util.logging import get_logger


@dataclass(frozen=True)
class MetricDimension:
    name: str
    value: str


class CloudWatchMetrics:
    def __init__(self, *, namespace: str, enabled: bool) -> None:
        self.namespace = namespace
        self.enabled = enabled
        self.client = boto3.client("cloudwatch") if enabled else None
        self.logger = get_logger(self.__class__.__name__)

    @classmethod
    def from_env(cls) -> "CloudWatchMetrics":
        enabled = os.getenv("CLOUDWATCH_METRICS_ENABLED", "false").lower() == "true"
        namespace = os.getenv("CLOUDWATCH_METRICS_NAMESPACE", "RelayInventory")
        return cls(namespace=namespace, enabled=enabled)

    def _put_metric(
        self,
        *,
        name: str,
        value: float,
        unit: str = "Count",
        dimensions: Optional[Iterable[MetricDimension]] = None,
    ) -> None:
        if not self.enabled or not self.client:
            return
        payload = {
            "MetricName": name,
            "Value": value,
            "Unit": unit,
        }
        if dimensions:
            payload["Dimensions"] = [
                {"Name": dimension.name, "Value": dimension.value} for dimension in dimensions
            ]
        try:
            self.client.put_metric_data(
                Namespace=self.namespace,
                MetricData=[payload],
            )
        except (BotoCoreError, ClientError) as exc:
            self.logger.warning("cloudwatch_metric_failed", extra={"error": str(exc), "metric": name})

    def record_run_failure(self, *, tenant_id: str, failed: bool) -> None:
        value = 1.0 if failed else 0.0
        self._put_metric(
            name="RunFailed",
            value=value,
            dimensions=[MetricDimension(name="tenant_id", value=tenant_id)],
        )
        self._put_metric(name="RunFailed", value=value)

    def record_worker_error(self, *, error_type: str) -> None:
        self._put_metric(
            name="WorkerError",
            value=1.0,
            dimensions=[MetricDimension(name="error_type", value=error_type)],
        )
