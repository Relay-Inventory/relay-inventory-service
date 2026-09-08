from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import boto3


@dataclass
class SqsMessage:
    receipt_handle: str
    body: Dict[str, Any]
    receive_count: int = 1


class SqsAdapter:
    def __init__(self, queue_url: str) -> None:
        self.queue_url = queue_url
        self.client = boto3.client("sqs")

    def send(self, payload: Dict[str, Any]) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(payload))

    def receive(self) -> Optional[SqsMessage]:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            AttributeNames=["ApproximateReceiveCount"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        body = json.loads(message.get("Body", "{}"))
        attributes = message.get("Attributes", {})
        receive_count = int(attributes.get("ApproximateReceiveCount", "1"))
        return SqsMessage(
            receipt_handle=message["ReceiptHandle"],
            body=body,
            receive_count=receive_count,
        )

    def delete(self, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)

    def change_visibility(self, receipt_handle: str, timeout_seconds: int) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=timeout_seconds,
        )
