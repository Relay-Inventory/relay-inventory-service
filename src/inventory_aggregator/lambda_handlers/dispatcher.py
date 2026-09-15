from __future__ import annotations

import json
import os

import boto3
from boto3.dynamodb.conditions import Attr

from inventory_aggregator.persistence.single_table import CONFIG_PREFIX, SingleTable


def list_shop_ids_with_config(table: SingleTable) -> list[str]:
    """A full table Scan filtered by sk prefix is the right call at 'a handful of pre-launch
    merchants' scale -- a GSI for this would be premature, per the same simplification
    philosophy as the rest of Phase 3 (see COMMIT_PLAN.md)."""
    response = table.table.scan(FilterExpression=Attr("sk").begins_with(CONFIG_PREFIX))
    shop_ids = {item["shop_id"] for item in response.get("Items", [])}
    return sorted(shop_ids)


def handler(event: dict, context=None) -> dict:
    table = SingleTable(os.environ["SHOP_DATA_TABLE"])
    state_machine_arn = os.environ["STATE_MACHINE_ARN"]
    sfn_client = boto3.client("stepfunctions")

    started = []
    for shop_id in list_shop_ids_with_config(table):
        response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps({"shop_id": shop_id}),
        )
        started.append({"shop_id": shop_id, "execution_arn": response["executionArn"]})
    return {"started": started}
