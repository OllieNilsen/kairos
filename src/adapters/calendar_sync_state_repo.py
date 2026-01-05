"""CalendarSyncStateRepository for webhook routing (Slice 4B)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3

from src.core.models import CalendarSyncState

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class CalendarSyncStateRepository:
    """Repository for calendar sync state and webhook routing (Slice 4B).

    Table design:
    - Sync item: PK=USER#<user_id>#PROVIDER#<provider>, SK=SYNC
    - Google channel routing: PK=GOOGLE#CHANNEL#<channel_id>, SK=ROUTE
    - Microsoft sub routing: PK=MS#SUB#<subscription_id>, SK=ROUTE
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

    def save_sync_state(self, state: CalendarSyncState) -> None:
        """Save sync state with routing item transactionally.

        Args:
            state: CalendarSyncState to save
        """
        now_iso = datetime.now(UTC).isoformat()

        # Build SYNC item
        sync_item: dict[str, Any] = {
            "pk": {"S": f"USER#{state.user_id}#PROVIDER#{state.provider}"},
            "sk": {"S": "SYNC"},
            "user_id": {"S": state.user_id},
            "provider": {"S": state.provider},
            "provider_calendar_id": {"S": state.provider_calendar_id},
            "updated_at": {"S": now_iso},
        }

        # Add optional fields
        if state.subscription_id:
            sync_item["subscription_id"] = {"S": state.subscription_id}
        if state.subscription_expiry:
            sync_item["subscription_expiry"] = {"S": state.subscription_expiry.isoformat()}
        if state.last_sync_at:
            sync_item["last_sync_at"] = {"S": state.last_sync_at.isoformat()}
        if state.delta_link:
            sync_item["delta_link"] = {"S": state.delta_link}
        if state.sync_token:
            sync_item["sync_token"] = {"S": state.sync_token}
        if state.channel_token:
            sync_item["channel_token"] = {"S": state.channel_token}
        if state.client_state:
            sync_item["client_state"] = {"S": state.client_state}
        if state.previous_client_state:
            sync_item["previous_client_state"] = {"S": state.previous_client_state}
        if state.previous_client_state_expires:
            sync_item["previous_client_state_expires"] = {
                "S": state.previous_client_state_expires.isoformat()
            }
        if state.error_state:
            sync_item["error_state"] = {"S": state.error_state}
        if not state.created_at:
            sync_item["created_at"] = {"S": now_iso}
        else:
            sync_item["created_at"] = {"S": state.created_at.isoformat()}

        items = [
            # 1. SYNC item
            {"Put": {"TableName": self.table_name, "Item": sync_item}}
        ]

        # 2. Routing item (provider-specific)
        if state.provider == "google" and state.subscription_id:
            route_item: dict[str, Any] = {
                "pk": {"S": f"GOOGLE#CHANNEL#{state.subscription_id}"},
                "sk": {"S": "ROUTE"},
                "user_id": {"S": state.user_id},
                "provider": {"S": "google"},
                "provider_calendar_id": {"S": state.provider_calendar_id},
            }
            if state.channel_token:
                route_item["channel_token"] = {"S": state.channel_token}
            if state.subscription_expiry:
                route_item["channel_expiry"] = {"S": state.subscription_expiry.isoformat()}

            items.append({"Put": {"TableName": self.table_name, "Item": route_item}})

        elif state.provider == "microsoft" and state.subscription_id:
            route_item = {
                "pk": {"S": f"MS#SUB#{state.subscription_id}"},
                "sk": {"S": "ROUTE"},
                "user_id": {"S": state.user_id},
                "provider": {"S": "microsoft"},
            }
            if state.client_state:
                route_item["client_state"] = {"S": state.client_state}
            if state.previous_client_state:
                route_item["previous_client_state"] = {"S": state.previous_client_state}
            if state.previous_client_state_expires:
                route_item["previous_client_state_expires"] = {
                    "S": state.previous_client_state_expires.isoformat()
                }
            if state.subscription_expiry:
                route_item["subscription_expiry"] = {"S": state.subscription_expiry.isoformat()}

            items.append({"Put": {"TableName": self.table_name, "Item": route_item}})

        self.dynamodb.transact_write_items(TransactItems=items)

    def get_by_google_channel_id(self, channel_id: str) -> dict[str, Any] | None:
        """Lookup user_id and channel_token by Google channel_id (O(1) GetItem).

        Args:
            channel_id: Google Calendar channel ID

        Returns:
            Dict with user_id, channel_token, etc. or None if not found
        """
        response = self.table.get_item(Key={"pk": f"GOOGLE#CHANNEL#{channel_id}", "sk": "ROUTE"})
        item = response.get("Item")
        if not item:
            return None

        return {
            "user_id": item["user_id"],
            "provider": item.get("provider", "google"),
            "provider_calendar_id": item.get("provider_calendar_id"),
            "channel_token": item.get("channel_token"),
            "channel_expiry": item.get("channel_expiry"),
        }

    def get_by_microsoft_subscription_id(self, subscription_id: str) -> dict[str, Any] | None:
        """Lookup user_id and client_state by Microsoft subscription_id (O(1) GetItem).

        Args:
            subscription_id: Microsoft Graph subscription ID

        Returns:
            Dict with user_id, client_state, etc. or None if not found
        """
        response = self.table.get_item(Key={"pk": f"MS#SUB#{subscription_id}", "sk": "ROUTE"})
        item = response.get("Item")
        if not item:
            return None

        return {
            "user_id": item["user_id"],
            "provider": item.get("provider", "microsoft"),
            "client_state": item.get("client_state"),
            "previous_client_state": item.get("previous_client_state"),
            "previous_client_state_expires": item.get("previous_client_state_expires"),
            "subscription_expiry": item.get("subscription_expiry"),
        }

    def verify_google_channel_token(self, channel_id: str, token: str) -> bool:
        """Verify Google channel token using constant-time comparison.

        Args:
            channel_id: Google Calendar channel ID
            token: Token from X-Goog-Channel-Token header

        Returns:
            True if token is valid, False otherwise
        """
        route_info = self.get_by_google_channel_id(channel_id)
        if not route_info or not route_info.get("channel_token"):
            return False

        # Constant-time comparison (prevents timing attacks)
        return secrets.compare_digest(route_info["channel_token"], token)

    def verify_microsoft_client_state(self, subscription_id: str, client_state: str) -> bool:
        """Verify Microsoft client_state (current or previous within overlap window).

        Args:
            subscription_id: Microsoft Graph subscription ID
            client_state: clientState from webhook notification

        Returns:
            True if client_state is valid, False otherwise
        """
        route_info = self.get_by_microsoft_subscription_id(subscription_id)
        if not route_info:
            return False

        # Check current client_state
        current = route_info.get("client_state")
        if current and secrets.compare_digest(current, client_state):
            return True

        # Check previous client_state (if within overlap window)
        previous = route_info.get("previous_client_state")
        if previous:
            expires_str = route_info.get("previous_client_state_expires")
            if expires_str:
                expires = datetime.fromisoformat(expires_str)
                # Within overlap window
                if datetime.now(UTC) < expires and secrets.compare_digest(previous, client_state):
                    return True

        return False

    def get_sync_state(self, user_id: str, provider: str) -> CalendarSyncState | None:
        """Get full sync state for user/provider.

        Args:
            user_id: User identifier
            provider: Provider (google/microsoft)

        Returns:
            CalendarSyncState or None if not found
        """
        response = self.table.get_item(
            Key={"pk": f"USER#{user_id}#PROVIDER#{provider}", "sk": "SYNC"}
        )
        item = response.get("Item")
        if not item:
            return None

        return CalendarSyncState(
            user_id=item["user_id"],
            provider=item["provider"],
            provider_calendar_id=item["provider_calendar_id"],
            subscription_id=item.get("subscription_id"),
            subscription_expiry=(
                datetime.fromisoformat(item["subscription_expiry"])
                if "subscription_expiry" in item
                else None
            ),
            last_sync_at=(
                datetime.fromisoformat(item["last_sync_at"]) if "last_sync_at" in item else None
            ),
            delta_link=item.get("delta_link"),
            sync_token=item.get("sync_token"),
            channel_token=item.get("channel_token"),
            client_state=item.get("client_state"),
            previous_client_state=item.get("previous_client_state"),
            previous_client_state_expires=(
                datetime.fromisoformat(item["previous_client_state_expires"])
                if "previous_client_state_expires" in item
                else None
            ),
            error_state=item.get("error_state"),
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )

    def delete_sync_state(self, user_id: str, provider: str) -> None:
        """Delete sync state and routing item atomically.

        Args:
            user_id: User identifier
            provider: Provider (google/microsoft)
        """
        # Fetch sync state to get subscription_id for routing item deletion
        state = self.get_sync_state(user_id, provider)
        if not state or not state.subscription_id:
            return

        items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": {
                        "pk": {"S": f"USER#{user_id}#PROVIDER#{provider}"},
                        "sk": {"S": "SYNC"},
                    },
                }
            }
        ]

        # Add routing item deletion
        if provider == "google":
            items.append(
                {
                    "Delete": {
                        "TableName": self.table_name,
                        "Key": {
                            "pk": {"S": f"GOOGLE#CHANNEL#{state.subscription_id}"},
                            "sk": {"S": "ROUTE"},
                        },
                    }
                }
            )
        elif provider == "microsoft":
            items.append(
                {
                    "Delete": {
                        "TableName": self.table_name,
                        "Key": {
                            "pk": {"S": f"MS#SUB#{state.subscription_id}"},
                            "sk": {"S": "ROUTE"},
                        },
                    }
                }
            )

        self.dynamodb.transact_write_items(TransactItems=items)
