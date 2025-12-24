"""DynamoDB repository for calendar meetings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import Meeting
except ImportError:
    from src.core.models import Meeting


class MeetingsRepository:
    """Repository for storing and querying calendar meetings in DynamoDB."""

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def save_meeting(self, meeting: Meeting) -> None:
        """Save or update a meeting in DynamoDB.

        Uses meeting_id as the sort key, so updates replace existing meetings.
        """
        item: dict[str, Any] = {
            "user_id": meeting.user_id,
            "meeting_id": meeting.meeting_id,
            "title": meeting.title,
            "start_time": meeting.start_time.isoformat(),
            "end_time": meeting.end_time.isoformat(),
            "attendees": [a.model_dump() for a in meeting.attendees],
            "status": meeting.status,
            "google_etag": meeting.google_etag,
            "created_at": meeting.created_at.isoformat(),
            "ttl": int(datetime.now(UTC).timestamp()) + 86400 * 30,  # 30 days
        }

        # Add optional fields if present
        if meeting.description:
            item["description"] = meeting.description
        if meeting.location:
            item["location"] = meeting.location

        self.table.put_item(Item=item)

    def get_meeting(self, user_id: str, meeting_id: str) -> Meeting | None:
        """Get a specific meeting by ID."""
        response = self.table.get_item(Key={"user_id": user_id, "meeting_id": meeting_id})

        item = response.get("Item")
        if not item:
            return None

        return self._item_to_meeting(item)

    def delete_meeting(self, user_id: str, meeting_id: str) -> None:
        """Delete a meeting from DynamoDB."""
        self.table.delete_item(Key={"user_id": user_id, "meeting_id": meeting_id})

    def list_meetings_for_user(
        self,
        user_id: str,
        start_after: datetime | None = None,
        end_before: datetime | None = None,
        status: str | None = None,
    ) -> list[Meeting]:
        """List meetings for a user, optionally filtered by time range and status.

        Args:
            user_id: The user ID
            start_after: Only include meetings starting after this time
            end_before: Only include meetings ending before this time
            status: Filter by status (pending, debriefed, skipped)

        Returns:
            List of Meeting objects, sorted by start_time
        """
        # Query by user_id partition key
        response = self.table.query(KeyConditionExpression=Key("user_id").eq(user_id))

        meetings = []
        for item in response.get("Items", []):
            meeting = self._item_to_meeting(item)

            # Apply filters
            if start_after and meeting.start_time <= start_after:
                continue
            if end_before and meeting.end_time >= end_before:
                continue
            if status and meeting.status != status:
                continue

            meetings.append(meeting)

        # Sort by start_time
        meetings.sort(key=lambda m: m.start_time)
        return meetings

    def get_pending_meetings(self, user_id: str) -> list[Meeting]:
        """Get all pending (not yet debriefed) meetings for a user.

        Only returns meetings that have ended.
        """
        now = datetime.now(UTC)
        all_meetings = self.list_meetings_for_user(user_id, status="pending")

        # Filter to only ended meetings
        return [m for m in all_meetings if m.end_time < now]

    def mark_debriefed(self, user_id: str, meeting_ids: list[str]) -> None:
        """Mark multiple meetings as debriefed."""
        for meeting_id in meeting_ids:
            self.table.update_item(
                Key={"user_id": user_id, "meeting_id": meeting_id},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": "debriefed"},
            )

    def _item_to_meeting(self, item: dict[str, Any]) -> Meeting:
        """Convert a DynamoDB item to a Meeting object."""
        return Meeting(
            user_id=item["user_id"],
            meeting_id=item["meeting_id"],
            title=item["title"],
            description=item.get("description"),
            location=item.get("location"),
            start_time=datetime.fromisoformat(item["start_time"]),
            end_time=datetime.fromisoformat(item["end_time"]),
            attendees=item.get("attendees", []),
            status=item.get("status", "pending"),
            google_etag=item.get("google_etag"),
            created_at=datetime.fromisoformat(item["created_at"]),
        )
