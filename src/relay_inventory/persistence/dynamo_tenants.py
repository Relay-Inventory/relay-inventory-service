from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key


@dataclass
class TenantRecord:
    tenant_id: str
    config_version: int
    config: dict


class DynamoTenants:
    def __init__(self, table_name: str) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)

    def put(self, record: TenantRecord) -> None:
        self.table.put_item(Item=record.__dict__)

    def get(self, tenant_id: str, config_version: int) -> Optional[TenantRecord]:
        response = self.table.get_item(
            Key={"tenant_id": tenant_id, "config_version": config_version}
        )
        item = response.get("Item")
        if not item:
            return None
        return TenantRecord(**item)

    def get_latest(self, tenant_id: str) -> Optional[TenantRecord]:
        response = self.table.query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None
        return TenantRecord(**items[0])
