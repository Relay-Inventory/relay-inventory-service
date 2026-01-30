from moto import mock_aws

from relay_inventory.adapters.queue.sqs import SqsAdapter
from relay_inventory.adapters.storage.s3 import S3Adapter


@mock_aws
def test_s3_adapter_round_trip() -> None:
    import boto3

    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")

    adapter = S3Adapter("test-bucket")
    adapter.upload_text("inbound/file.txt", "hello")
    latest = adapter.list_latest("inbound/")
    assert latest is not None
    assert latest.key == "inbound/file.txt"
    assert adapter.download_text("inbound/file.txt") == "hello"
    assert adapter.download_bytes("inbound/file.txt") == b"hello"


@mock_aws
def test_sqs_adapter_send_receive_delete() -> None:
    import boto3

    client = boto3.client("sqs", region_name="us-east-1")
    response = client.create_queue(QueueName="test-queue")
    queue_url = response["QueueUrl"]

    adapter = SqsAdapter(queue_url)
    adapter.send({"run_id": "123"})
    message = adapter.receive()
    assert message is not None
    assert message.body["run_id"] == "123"
    adapter.delete(message.receipt_handle)
