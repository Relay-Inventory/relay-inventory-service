from __future__ import annotations

from typing import Optional

import boto3
from botocore.exceptions import ClientError

_SECRET_NAME_TEMPLATE = "inventory-aggregator/{shop_id}/shopify-access-token"


def secret_name(shop_id: str) -> str:
    return _SECRET_NAME_TEMPLATE.format(shop_id=shop_id)


class ShopifyTokenStore:
    """Offline Shopify access tokens live in Secrets Manager, never DynamoDB or S3 --
    the plan's own non-negotiable. One secret per shop, at a fixed naming convention
    so IAM policies can be scoped to a single shop's secret by resource ARN rather
    than a wildcard-all-secrets grant."""

    def __init__(self) -> None:
        self.client = boto3.client("secretsmanager")

    def put(self, shop_id: str, access_token: str) -> None:
        name = secret_name(shop_id)
        try:
            self.client.create_secret(Name=name, SecretString=access_token)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceExistsException":
                raise
            self.client.put_secret_value(SecretId=name, SecretString=access_token)

    def get(self, shop_id: str) -> Optional[str]:
        """Mirrors S3Adapter.download_bytes_or_none's ClientError -> None convention:
        a shop that hasn't completed OAuth install yet has no secret, which is a
        normal, expected state, not an error."""

        try:
            response = self.client.get_secret_value(SecretId=secret_name(shop_id))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return None
            raise
        return response["SecretString"]
