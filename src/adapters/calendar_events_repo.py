"""Repository for KCNF calendar events with redirect handling.

This repository implements the Put+Update redirect pattern for handling
start_time changes, and redirect-following logic for queries.
"""

from __future__ import annotations

import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from src.core.models import KairosCalendarEvent


class RedirectLoopError(Exception):
    """Raised when redirect loop detected (data corruption)."""

    def __init__(self, user_id: str, sk: str):
        self.user_id = user_id
        self.sk = sk
        super().__init__(f"Redirect loop detected for user_id={user_id}, sk={sk}")


class RedirectHopLimitError(Exception):
    """Raised when redirect hop limit exceeded (data corruption or excessive chaining)."""

    def __init__(self, user_id: str, sk: str, limit: int):
        self.user_id = user_id
        self.sk = sk
        self.limit = limit
        super().__init__(f"Redirect hop limit ({limit}) exceeded for user_id={user_id}, sk={sk}")


class CalendarEventsRepository:
    """Repository for KCNF calendar events.

    Implements Put+Update redirect pattern for start_time changes and
    redirect-following logic for all queries.
    """

    def __init__(self, table_name: str, region_name: str = "us-east-1"):
        """Initialize repository.

        Args:
            table_name: DynamoDB table name (kairos-calendar-events)
            region_name: AWS region
        """
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region_name)
        self.table = self.dynamodb.Table(table_name)

    def _compute_gsi_day(self, event: KairosCalendarEvent, user_timezone: str) -> str:
        """Compute GSI_DAY key in user's local timezone.

        CRITICAL: Day MUST be computed in user's local timezone for correct queries.

        Args:
            event: KCNF event
            user_timezone: User's IANA timezone (e.g., "America/New_York")

        Returns:
            GSI1PK string: USER#<user_id>#DAY#YYYY-MM-DD
        """
        # Import here to avoid module-level dependency
        from zoneinfo import ZoneInfo

        # Convert event start to user's local timezone
        user_tz = ZoneInfo(user_timezone)
        local_start = event.start.astimezone(user_tz)
        day_str = local_start.strftime("%Y-%m-%d")

        return f"USER#{event.user_id}#DAY#{day_str}"

    def _compute_main_sk(self, event: KairosCalendarEvent) -> str:
        """Compute main table sort key.

        SK format: EVT#<start_iso>#<provider>#<provider_event_id>

        Args:
            event: KCNF event

        Returns:
            Sort key string
        """
        start_iso = event.start.isoformat()
        return f"EVT#{start_iso}#{event.provider}#{event.provider_event_id}"

    def _compute_gsi_provider_id(self, event: KairosCalendarEvent) -> tuple[str, str]:
        """Compute GSI_PROVIDER_ID keys.

        Args:
            event: KCNF event

        Returns:
            Tuple of (GSI2PK, GSI2SK)
        """
        gsi2pk = f"USER#{event.user_id}"
        gsi2sk = f"PROVIDER#{event.provider}#EVENT#{event.provider_event_id}"
        return gsi2pk, gsi2sk

    def _serialize(self, event: KairosCalendarEvent, user_timezone: str) -> dict[str, Any]:
        """Serialize KCNF event to DynamoDB item.

        Args:
            event: KCNF event
            user_timezone: User's IANA timezone (for GSI_DAY computation)

        Returns:
            DynamoDB item dict
        """
        # Compute keys
        pk = f"USER#{event.user_id}"
        sk = self._compute_main_sk(event)
        gsi1pk = self._compute_gsi_day(event, user_timezone)
        gsi1sk = f"{event.start.isoformat()}#{event.provider}#{event.provider_event_id}"
        gsi2pk, gsi2sk = self._compute_gsi_provider_id(event)

        # Serialize event model to dict
        event_dict = event.model_dump(mode="json")

        # Build DynamoDB item
        item = {
            "pk": pk,
            "sk": sk,
            "gsi1pk": gsi1pk,
            "gsi1sk": gsi1sk,
            "gsi2pk": gsi2pk,
            "gsi2sk": gsi2sk,
            **event_dict,
        }

        return item

    def _deserialize(self, item: dict[str, Any]) -> KairosCalendarEvent:
        """Deserialize DynamoDB item to KCNF event.

        Args:
            item: DynamoDB item dict

        Returns:
            KairosCalendarEvent
        """
        # Remove DynamoDB-specific keys
        event_data = {
            k: v
            for k, v in item.items()
            if k not in ["pk", "sk", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"]
        }
        return KairosCalendarEvent(**event_data)

    def save_event(self, event: KairosCalendarEvent, user_timezone: str) -> None:
        """Save a new calendar event.

        Simple put operation (does not handle start_time changes).
        Use update_event_start_time() for handling start_time changes.

        Args:
            event: KCNF event to save
            user_timezone: User's IANA timezone (for GSI_DAY computation)
        """
        item = self._serialize(event, user_timezone)
        self.table.put_item(Item=item)

    def update_event_start_time(
        self,
        old_event: KairosCalendarEvent,
        new_event: KairosCalendarEvent,
        user_timezone: str,
    ) -> None:
        """Update event when start_time changes (Put+Update redirect pattern).

        Uses TransactWriteItems to atomically:
        1. Put new event at new SK
        2. Update old item to redirect tombstone

        Args:
            old_event: Existing event (with old start_time)
            new_event: New event (with new start_time)
            user_timezone: User's IANA timezone (for GSI_DAY computation)

        Raises:
            ClientError: If transaction fails (e.g., version conflict, new item exists)
        """
        # Compute keys
        pk = f"USER#{old_event.user_id}"
        old_sk = self._compute_main_sk(old_event)
        new_sk = self._compute_main_sk(new_event)

        # Serialize new event
        new_item = self._serialize(new_event, user_timezone)

        # TransactWriteItems: Put new + Update old
        # Use low-level client with proper boto3 serialization
        from boto3.dynamodb.types import TypeSerializer

        serializer = TypeSerializer()

        try:
            self.dynamodb.meta.client.transact_write_items(
                TransactItems=[
                    # 1. Put new event at new SK
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {k: serializer.serialize(v) for k, v in new_item.items()},
                            "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
                        }
                    },
                    # 2. Update old item to redirect tombstone
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": {
                                "pk": serializer.serialize(pk),
                                "sk": serializer.serialize(old_sk),
                            },
                            "ConditionExpression": "attribute_exists(pk) AND item_type = :event_type AND provider_version = :provider_version",
                            "UpdateExpression": "SET item_type = :redirect_type, redirect_to_sk = :new_sk, #ttl = :ttl "
                            "REMOVE title, description, #location, attendees, organizer, conference, recurrence",
                            "ExpressionAttributeNames": {
                                "#ttl": "ttl",
                                "#location": "location",
                            },
                            "ExpressionAttributeValues": {
                                ":event_type": serializer.serialize("event"),
                                ":redirect_type": serializer.serialize("redirect"),
                                ":new_sk": serializer.serialize(new_sk),
                                ":ttl": serializer.serialize(int(time.time()) + 3600),
                                ":provider_version": serializer.serialize(
                                    old_event.provider_version
                                ),
                            },
                        }
                    },
                ]
            )
        except ClientError as e:
            # Re-raise with context
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": f"Failed to update event start_time: {e}",
                    }
                },
                "TransactWriteItems",
            ) from e

    def get_event(
        self, user_id: str, sk: str, *, max_redirect_hops: int = 2
    ) -> KairosCalendarEvent | None:
        """Get single event by PK/SK. Follows redirects with bounded hop limit.

        Args:
            user_id: User ID
            sk: Sort key
            max_redirect_hops: Maximum number of redirects to follow (default: 2)

        Returns:
            KairosCalendarEvent or None if not found

        Raises:
            RedirectLoopError: If redirect loop detected
            RedirectHopLimitError: If hop limit exceeded
        """
        visited = set()
        current_sk = sk

        for _ in range(max_redirect_hops + 1):
            # Detect redirect loops
            if current_sk in visited:
                raise RedirectLoopError(user_id=user_id, sk=current_sk)
            visited.add(current_sk)

            # Get item
            response = self.table.get_item(Key={"pk": f"USER#{user_id}", "sk": current_sk})
            item = response.get("Item")

            if not item:
                return None

            if item.get("item_type") != "redirect":
                return self._deserialize(item)

            # Follow redirect
            current_sk = item["redirect_to_sk"]

        # Exceeded hop limit
        raise RedirectHopLimitError(user_id=user_id, sk=sk, limit=max_redirect_hops)

    def get_by_provider_event_id(
        self, user_id: str, provider: str, provider_event_id: str
    ) -> KairosCalendarEvent | None:
        """Get event by provider event ID. Handles redirects and duplicate items.

        Args:
            user_id: User ID
            provider: Provider name (google/microsoft)
            provider_event_id: Provider event ID

        Returns:
            KairosCalendarEvent or None if not found
        """
        # Query GSI_PROVIDER_ID
        gsi2pk = f"USER#{user_id}"
        gsi2sk = f"PROVIDER#{provider}#EVENT#{provider_event_id}"

        response = self.table.query(
            IndexName="GSI_PROVIDER_ID",
            KeyConditionExpression="gsi2pk = :pk AND gsi2sk = :sk",
            ExpressionAttributeValues={":pk": gsi2pk, ":sk": gsi2sk},
        )

        items = response.get("Items", [])
        if not items:
            return None

        # Prefer event items over redirects
        event_items = [i for i in items if i.get("item_type") == "event"]
        if event_items:
            if len(event_items) == 1:
                return self._deserialize(event_items[0])
            # Multiple event items (data corruption): pick newest
            canonical = max(event_items, key=lambda x: x.get("ingested_at", ""))
            # Log warning (would use logger in production)
            print(
                f"WARNING: duplicate_provider_id_items: user_id={user_id}, provider={provider}, "
                f"event_id={provider_event_id}, count={len(event_items)}"
            )
            return self._deserialize(canonical)

        # Only redirects: follow one
        redirect_items = [i for i in items if i.get("item_type") == "redirect"]
        if redirect_items:
            return self.get_event(user_id, redirect_items[0]["redirect_to_sk"])

        return None

    def list_events_by_day(
        self, user_id: str, date: str, user_timezone: str
    ) -> list[KairosCalendarEvent]:
        """Query GSI_DAY. Returns ONLY real events (filters out tombstones).

        Args:
            user_id: User ID
            date: Date string in YYYY-MM-DD format
            user_timezone: User's IANA timezone (for GSI_DAY computation)

        Returns:
            List of KairosCalendarEvent objects (tombstones filtered out)
        """
        gsi1pk = f"USER#{user_id}#DAY#{date}"

        response = self.table.query(
            IndexName="GSI_DAY",
            KeyConditionExpression="gsi1pk = :pk",
            ExpressionAttributeValues={":pk": gsi1pk},
        )

        items = response.get("Items", [])

        # Filter to only event items (exclude redirects/tombstones)
        events = []
        for item in items:
            if item.get("item_type") == "event":
                events.append(self._deserialize(item))

        return events
