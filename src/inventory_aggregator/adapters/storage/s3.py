from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import boto3


@dataclass
class S3Location:
    bucket: str
    key: str
    etag: Optional[str] = None
    size: Optional[int] = None
    last_modified: Optional[datetime] = None


class S3Adapter:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.client = boto3.client("s3")

    def list_latest(self, prefix: str) -> Optional[S3Location]:
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        contents = response.get("Contents", [])
        if not contents:
            return None
        latest = max(contents, key=lambda item: item["LastModified"])
        return S3Location(
            bucket=self.bucket,
            key=latest["Key"],
            etag=latest.get("ETag"),
            size=latest.get("Size"),
            last_modified=latest.get("LastModified"),
        )

    def download_text(self, key: str) -> str:
        return self.download_bytes(key).decode("utf-8")

    def download_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def upload_text(self, key: str, body: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"))

    def upload_bytes(self, key: str, body: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    def presign(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def upload_lines(self, key: str, lines: Iterable[str]) -> None:
        data = "".join(lines)
        self.upload_text(key, data)
