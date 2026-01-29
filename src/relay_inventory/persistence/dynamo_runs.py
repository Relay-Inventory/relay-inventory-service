from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import boto3


@dataclass
class RunRecord:
    run_id: str
    tenant_id: str
    config_version: int
    status: str
    stage: Optional[str] = None
    requested_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    completed_at: Optional[str] = None
    failed_stage: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    errors_artifact_key: Optional[str] = None
    error_report_key: Optional[str] = None
    artifacts: Dict[str, str] | None = None


class DynamoRuns:
    def __init__(self, table_name: str) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)

    def create(self, record: RunRecord) -> None:
        self.table.put_item(Item=record.__dict__)

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        stage: Optional[str] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        failed_stage: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        errors_artifact_key: Optional[str] = None,
        error_report_key: Optional[str] = None,
        artifacts: Optional[Dict[str, str]] = None,
        clear_fields: Optional[list[str]] = None,
    ) -> None:
        expression = ["#status = :status"]
        names = {"#status": "status"}
        values: Dict[str, Any] = {":status": status}
        if stage:
            expression.append("#stage = :stage")
            names["#stage"] = "stage"
            values[":stage"] = stage
        if started_at:
            expression.append("started_at = :started_at")
            values[":started_at"] = started_at.isoformat()
        if finished_at:
            expression.append("finished_at = :finished_at")
            values[":finished_at"] = finished_at.isoformat()
        if completed_at:
            expression.append("completed_at = :completed_at")
            values[":completed_at"] = completed_at.isoformat()
        if failed_stage:
            expression.append("failed_stage = :failed_stage")
            values[":failed_stage"] = failed_stage
        if error_code:
            expression.append("error_code = :error_code")
            values[":error_code"] = error_code
        if error_message:
            expression.append("error_message = :error_message")
            values[":error_message"] = error_message
        if errors_artifact_key:
            expression.append("errors_artifact_key = :errors_artifact_key")
            values[":errors_artifact_key"] = errors_artifact_key
        if error_report_key:
            expression.append("error_report_key = :error_report_key")
            values[":error_report_key"] = error_report_key
        if artifacts:
            expression.append("artifacts = :artifacts")
            values[":artifacts"] = artifacts
        remove_fields = []
        if clear_fields:
            for field in clear_fields:
                remove_fields.append(field)
        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression=(
                "SET " + ", ".join(expression) + (" REMOVE " + ", ".join(remove_fields) if remove_fields else "")
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def get(self, run_id: str) -> Optional[RunRecord]:
        response = self.table.get_item(Key={"run_id": run_id})
        item = response.get("Item")
        if not item:
            return None
        return RunRecord(**item)
