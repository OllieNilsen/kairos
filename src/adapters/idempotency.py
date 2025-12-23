"""Idempotency helpers for SMS and call deduplication."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError


class IdempotencyStore:
    """Generic idempotency store using DynamoDB conditional writes.

    Used for:
    - SMS send deduplication (prevent double-sending prompts)
    - Inbound SMS deduplication (prevent double-processing Twilio webhooks)
    - Call batch deduplication (prevent multiple calls per day)
    - Daily leases (prevent duplicate Lambda executions)
    """

    def __init__(
        self,
        table_name: str,
        region: str = "eu-west-1",
        ttl_days: int = 7,
    ) -> None:
        """Initialize the idempotency store.

        Args:
            table_name: DynamoDB table name
            region: AWS region
            ttl_days: Days until records auto-expire (default 7)
        """
        self.table_name = table_name
        self.ttl_days = ttl_days
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def try_acquire(self, key: str, metadata: dict[str, Any] | None = None) -> bool:
        """Try to acquire an idempotency lock for the given key.

        Uses conditional PutItem with attribute_not_exists to ensure
        only the first caller succeeds.

        Args:
            key: Unique key for the operation
            metadata: Optional metadata to store with the record

        Returns:
            True if this is the first acquisition (proceed with operation)
            False if already acquired (skip/duplicate)
        """
        now = datetime.now(UTC)
        ttl = int(now.timestamp()) + (self.ttl_days * 86400)

        item = {
            "idempotency_key": key,
            "created_at": now.isoformat(),
            "ttl": ttl,
        }

        if metadata:
            item["metadata"] = metadata

        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(idempotency_key)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def check_exists(self, key: str) -> bool:
        """Check if an idempotency key exists (without acquiring).

        Args:
            key: The idempotency key to check

        Returns:
            True if key exists, False otherwise
        """
        response = self.table.get_item(
            Key={"idempotency_key": key},
            ProjectionExpression="idempotency_key",
        )
        return "Item" in response

    def release(self, key: str) -> None:
        """Release an idempotency key (for retryable failures).

        Only use this if the operation failed and should be retried.

        Args:
            key: The idempotency key to release
        """
        self.table.delete_item(Key={"idempotency_key": key})


class SMSSendDedup(IdempotencyStore):
    """Deduplication for outbound SMS sends.

    Prevents sending the same daily prompt SMS multiple times.
    """

    @staticmethod
    def make_key(user_id: str, date_str: str) -> str:
        """Generate the idempotency key for a daily prompt.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)

        Returns:
            Idempotency key string
        """
        return f"sms-send:{user_id}#{date_str}"

    def try_send_daily_prompt(self, user_id: str, date_str: str) -> bool:
        """Try to mark daily prompt as sent.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)

        Returns:
            True if this is the first send attempt for the day
        """
        key = self.make_key(user_id, date_str)
        return self.try_acquire(key, {"type": "daily_prompt"})

    def release_daily_prompt(self, user_id: str, date_str: str) -> None:
        """Release the daily prompt lock (for retryable failures).

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)
        """
        key = self.make_key(user_id, date_str)
        self.release(key)


class InboundSMSDedup(IdempotencyStore):
    """Deduplication for inbound SMS webhook processing.

    Prevents processing the same Twilio message multiple times.
    """

    @staticmethod
    def make_key(message_sid: str) -> str:
        """Generate the idempotency key for an inbound message.

        Args:
            message_sid: Twilio MessageSid

        Returns:
            Idempotency key string
        """
        return f"sms-in:{message_sid}"

    def try_process_message(self, message_sid: str) -> bool:
        """Try to mark an inbound message as being processed.

        Args:
            message_sid: Twilio MessageSid

        Returns:
            True if this is the first processing attempt
        """
        key = self.make_key(message_sid)
        return self.try_acquire(key, {"type": "inbound_sms"})


class CallBatchDedup(IdempotencyStore):
    """Deduplication for daily call batches.

    Prevents initiating multiple calls for the same user on the same day.
    """

    @staticmethod
    def make_key(user_id: str, date_str: str) -> str:
        """Generate the idempotency key for a daily call.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)

        Returns:
            Idempotency key string
        """
        return f"call-batch:{user_id}#{date_str}"

    def try_initiate_call(self, user_id: str, date_str: str) -> bool:
        """Try to mark daily call as initiated.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)

        Returns:
            True if this is the first call attempt for the day
        """
        key = self.make_key(user_id, date_str)
        return self.try_acquire(key, {"type": "daily_call"})

    def release_call(self, user_id: str, date_str: str) -> None:
        """Release the daily call lock (for retryable failures).

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)
        """
        key = self.make_key(user_id, date_str)
        self.release(key)

    def release_call_batch(self, user_id: str, date_str: str) -> None:
        """Alias for release_call for backwards compatibility."""
        self.release_call(user_id, date_str)


class CallRetryDedup(IdempotencyStore):
    """Deduplication for call retries.

    Prevents scheduling or executing the same retry multiple times.
    """

    @staticmethod
    def make_key(user_id: str, date_str: str, retry_number: int) -> str:
        """Generate the idempotency key for a call retry.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)
            retry_number: Retry attempt number (1, 2, 3)

        Returns:
            Idempotency key string
        """
        return f"call-retry:{user_id}#{date_str}#{retry_number}"

    def try_schedule_retry(self, user_id: str, date_str: str, retry_number: int) -> bool:
        """Try to mark a retry as scheduled.

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)
            retry_number: Retry attempt number (1, 2, 3)

        Returns:
            True if this is the first schedule attempt for this retry
        """
        key = self.make_key(user_id, date_str, retry_number)
        return self.try_acquire(key, {"type": "call_retry", "retry_number": retry_number})

    def release_retry(self, user_id: str, date_str: str, retry_number: int) -> None:
        """Release a retry lock (for retryable failures).

        Args:
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)
            retry_number: Retry attempt number
        """
        key = self.make_key(user_id, date_str, retry_number)
        self.release(key)


class DailyLease:
    """Simple lease/fencing mechanism for daily operations.

    Prevents multiple concurrent executions of the same daily operation
    (e.g., if Lambda retries or EventBridge fires twice).

    Uses time-based expiration so that if a Lambda crashes, the lease
    eventually expires and another execution can proceed.
    """

    def __init__(
        self,
        table_name: str,
        region: str = "eu-west-1",
        lease_duration_seconds: int = 300,  # 5 minutes
    ) -> None:
        """Initialize the lease manager.

        Args:
            table_name: DynamoDB table name
            region: AWS region
            lease_duration_seconds: How long the lease is held
        """
        self.table_name = table_name
        self.lease_duration = lease_duration_seconds
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    @staticmethod
    def make_key(operation: str, user_id: str, date_str: str) -> str:
        """Generate the lease key.

        Args:
            operation: Operation name (e.g., "daily-plan")
            user_id: User identifier
            date_str: Date string (YYYY-MM-DD)

        Returns:
            Lease key string
        """
        return f"{operation}:{user_id}#{date_str}"

    def try_acquire(self, lease_key: str, owner: str) -> bool:
        """Try to acquire a lease.

        Args:
            lease_key: Unique key for the lease
            owner: Identifier for this lease holder (e.g., Lambda request ID)

        Returns:
            True if lease acquired, False if already held by another owner
        """
        now = datetime.now(UTC)
        expires_at = now.timestamp() + self.lease_duration
        ttl = int(now.timestamp()) + 86400  # Auto-cleanup after 1 day

        try:
            self.table.put_item(
                Item={
                    "idempotency_key": lease_key,
                    "owner": owner,
                    "acquired_at": now.isoformat(),
                    "expires_at": expires_at,
                    "ttl": ttl,
                },
                ConditionExpression=("attribute_not_exists(idempotency_key) OR expires_at < :now"),
                ExpressionAttributeValues={":now": now.timestamp()},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release(self, lease_key: str, owner: str) -> bool:
        """Release a lease (only if we own it).

        Args:
            lease_key: The lease key
            owner: The owner who acquired the lease

        Returns:
            True if released, False if we didn't own it
        """
        try:
            self.table.delete_item(
                Key={"idempotency_key": lease_key},
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": owner},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
