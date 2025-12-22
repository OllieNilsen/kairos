"""AWS DynamoDB adapter for call deduplication."""

from __future__ import annotations

from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


class CallDeduplicator:
    """Deduplicator using DynamoDB to prevent duplicate call processing."""

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def is_duplicate(self, call_id: str) -> bool:
        """Check if a call_id has already been processed.

        Uses conditional put to atomically check and mark as processed.

        Args:
            call_id: The Bland AI call ID

        Returns:
            True if this call_id was already processed (duplicate)
            False if this is a new call_id (not a duplicate)
        """
        try:
            self.table.put_item(
                Item={
                    "call_id": call_id,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "ttl": int(datetime.now(timezone.utc).timestamp()) + 86400 * 7,  # 7 days
                },
                ConditionExpression="attribute_not_exists(call_id)",
            )
            # Successfully wrote - this is NOT a duplicate
            return False
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Item already exists - this IS a duplicate
                return True
            # Some other error - re-raise
            raise

