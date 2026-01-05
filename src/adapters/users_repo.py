"""UsersRepository for multi-user primitives (Slice 4B)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError

from src.core.models import User

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class PhoneAlreadyRegisteredError(Exception):
    """Raised when phone number is already registered to another user."""

    pass


class PhoneEnumerationRateLimitError(Exception):
    """Raised when phone lookup rate limit is exceeded (security)."""

    pass


class UsersRepository:
    """Repository for user profiles and routing lookups (Slice 4B).

    Table design:
    - Profile item: PK=USER#<user_id>, SK=PROFILE
    - Phone routing: PK=PHONE#<e164>, SK=ROUTE
    - Email routing: PK=EMAIL#<email>, SK=ROUTE
    """

    def __init__(
        self,
        table_name: str,
        *,
        dynamodb: Any = None,
        table: Table | None = None,
    ) -> None:
        """Initialize repository.

        Args:
            table_name: DynamoDB table name
            dynamodb: Optional boto3 DynamoDB client (for transactions)
            table: Optional boto3 Table resource (for get/update operations)
        """
        self.table_name = table_name
        self._dynamodb = dynamodb
        self._table = table
        self._phone_lookup_window: dict[int, int] = {}  # timestamp_hour -> count

    @property
    def dynamodb(self) -> Any:
        """Get or create DynamoDB client."""
        if self._dynamodb is None:
            self._dynamodb = boto3.client("dynamodb")
        return self._dynamodb

    @property
    def table(self) -> Table:
        """Get or create DynamoDB Table resource."""
        if self._table is None:
            dynamodb_resource = boto3.resource("dynamodb")
            self._table = dynamodb_resource.Table(self.table_name)
        return self._table

    def create_user(self, user: User) -> None:
        """Create user with profile and routing items atomically.

        Args:
            user: User object to create

        Raises:
            PhoneAlreadyRegisteredError: If phone number already registered
        """
        now_iso = datetime.now(UTC).isoformat()

        # Normalize email for routing
        email_normalized = user.primary_email.lower()

        items = [
            # 1. Profile item
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": {
                        "pk": {"S": f"USER#{user.user_id}"},
                        "sk": {"S": "PROFILE"},
                        "user_id": {"S": user.user_id},
                        "primary_email": {"S": user.primary_email},
                        "phone_number_e164": {"S": user.phone_number_e164},
                        "timezone": {"S": user.timezone},
                        "preferred_prompt_time": {"S": user.preferred_prompt_time},
                        "status": {"S": user.status},
                        **(
                            {"default_calendar_provider": {"S": user.default_calendar_provider}}
                            if user.default_calendar_provider
                            else {}
                        ),
                        "created_at": {"S": now_iso},
                        "updated_at": {"S": now_iso},
                    },
                    "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
                }
            },
            # 2. Phone routing item (MUST be unique)
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": {
                        "pk": {"S": f"PHONE#{user.phone_number_e164}"},
                        "sk": {"S": "ROUTE"},
                        "user_id": {"S": user.user_id},
                        "status": {"S": user.status},
                        "created_at": {"S": now_iso},
                    },
                    "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
                }
            },
            # 3. Email routing item (MUST be unique)
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": {
                        "pk": {"S": f"EMAIL#{email_normalized}"},
                        "sk": {"S": "ROUTE"},
                        "user_id": {"S": user.user_id},
                        "created_at": {"S": now_iso},
                    },
                    "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
                }
            },
        ]

        try:
            self.dynamodb.transact_write_items(TransactItems=items)
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                raise PhoneAlreadyRegisteredError(
                    f"Phone {user.phone_number_e164} or email {user.primary_email} already registered"
                ) from e
            raise

    def get_user_by_phone(
        self, phone_number_e164: str, *, enforce_rate_limit: bool = False
    ) -> str | None:
        """Lookup user_id by phone number (O(1) GetItem).

        Args:
            phone_number_e164: Phone number in E.164 format
            enforce_rate_limit: If True, enforce enumeration protection (10/hour)

        Returns:
            user_id or None if not found

        Raises:
            PhoneEnumerationRateLimitError: If rate limit exceeded
        """
        if enforce_rate_limit:
            self._check_phone_lookup_rate_limit()

        response = self.table.get_item(Key={"pk": f"PHONE#{phone_number_e164}", "sk": "ROUTE"})
        item = response.get("Item")
        return item["user_id"] if item else None

    def get_user_by_email(self, email: str) -> str | None:
        """Lookup user_id by email (O(1) GetItem).

        Args:
            email: User email address

        Returns:
            user_id or None if not found
        """
        email_normalized = email.lower()
        response = self.table.get_item(Key={"pk": f"EMAIL#{email_normalized}", "sk": "ROUTE"})
        item = response.get("Item")
        return item["user_id"] if item else None

    def get_user_profile(self, user_id: str) -> User | None:
        """Get full user profile.

        Args:
            user_id: User identifier

        Returns:
            User object or None if not found
        """
        response = self.table.get_item(Key={"pk": f"USER#{user_id}", "sk": "PROFILE"})
        item = response.get("Item")
        if not item:
            return None

        return User(
            user_id=item["user_id"],
            primary_email=item["primary_email"],
            phone_number_e164=item["phone_number_e164"],
            timezone=item.get("timezone", "UTC"),
            preferred_prompt_time=item.get("preferred_prompt_time", "17:30"),
            status=item.get("status", "active"),
            default_calendar_provider=item.get("default_calendar_provider"),
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )

    def update_user_status(self, user_id: str, status: str) -> None:
        """Update user status (active/paused/stopped).

        Args:
            user_id: User identifier
            status: New status
        """
        self.table.update_item(
            Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
            UpdateExpression="SET #status = :status, updated_at = :updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def delete_user(self, user_id: str) -> None:
        """Delete user profile and routing items atomically.

        Args:
            user_id: User identifier
        """
        # Fetch profile to get phone and email for routing item deletion
        profile = self.get_user_profile(user_id)
        if not profile:
            return

        email_normalized = profile.primary_email.lower()

        items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": {
                        "pk": {"S": f"USER#{user_id}"},
                        "sk": {"S": "PROFILE"},
                    },
                }
            },
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": {
                        "pk": {"S": f"PHONE#{profile.phone_number_e164}"},
                        "sk": {"S": "ROUTE"},
                    },
                }
            },
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": {
                        "pk": {"S": f"EMAIL#{email_normalized}"},
                        "sk": {"S": "ROUTE"},
                    },
                }
            },
        ]

        self.dynamodb.transact_write_items(TransactItems=items)

    def _check_phone_lookup_rate_limit(self) -> None:
        """Enforce phone lookup rate limit (10/hour for enumeration protection).

        Raises:
            PhoneEnumerationRateLimitError: If rate limit exceeded
        """
        current_hour = int(time.time() / 3600)

        # Clean old windows (keep last 2 hours)
        old_windows = [h for h in self._phone_lookup_window if h < current_hour - 1]
        for h in old_windows:
            del self._phone_lookup_window[h]

        # Increment current window
        self._phone_lookup_window[current_hour] = self._phone_lookup_window.get(current_hour, 0) + 1

        # Check limit (10/hour)
        if self._phone_lookup_window[current_hour] > 10:
            raise PhoneEnumerationRateLimitError("Phone enumeration rate limit exceeded (10/hour)")
