"""DynamoDB repository for meeting transcripts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import TranscriptSegment
except ImportError:
    from src.core.models import TranscriptSegment


class TranscriptsRepository:
    """Repository for storing and querying meeting transcripts in DynamoDB.

    Key design:
    - PK: USER#<user_id>#MEETING#<meeting_id>
    - SK: SEGMENT#<segment_id>

    This allows efficient queries for all segments of a meeting while
    maintaining user-level tenant isolation.
    """

    # TTL: 90 days retention for transcripts
    TTL_DAYS = 90

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def save_transcript(
        self,
        user_id: str,
        meeting_id: str,
        call_id: str,
        segments: list[TranscriptSegment],
    ) -> None:
        """Save all transcript segments for a meeting.

        Uses batch write for efficiency. Idempotent - overwrites existing segments
        with same segment_id.

        Args:
            user_id: The user ID (partition key component)
            meeting_id: The meeting ID
            call_id: The Bland call ID (for correlation)
            segments: List of transcript segments to save
        """
        if not segments:
            return

        pk = f"USER#{user_id}#MEETING#{meeting_id}"
        ttl = int(datetime.now(UTC).timestamp()) + 86400 * self.TTL_DAYS
        created_at = datetime.now(UTC).isoformat()

        with self.table.batch_writer() as batch:
            for segment in segments:
                item: dict[str, Any] = {
                    "pk": pk,
                    "sk": f"SEGMENT#{segment.segment_id}",
                    "segment_id": segment.segment_id,
                    "t0": Decimal(str(segment.t0)),
                    "t1": Decimal(str(segment.t1)),
                    "speaker": segment.speaker,
                    "text": segment.text,
                    "call_id": call_id,
                    "meeting_id": meeting_id,
                    "user_id": user_id,
                    "created_at": created_at,
                    "ttl": ttl,
                }
                batch.put_item(Item=item)

    def get_transcript(self, user_id: str, meeting_id: str) -> list[TranscriptSegment]:
        """Get all transcript segments for a meeting.

        Args:
            user_id: The user ID
            meeting_id: The meeting ID

        Returns:
            List of TranscriptSegment sorted by t0 (start time)
        """
        pk = f"USER#{user_id}#MEETING#{meeting_id}"

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with("SEGMENT#")
        )

        segments = [self._item_to_segment(item) for item in response.get("Items", [])]

        # Sort by start time
        segments.sort(key=lambda s: s.t0)
        return segments

    def get_segment(
        self, user_id: str, meeting_id: str, segment_id: str
    ) -> TranscriptSegment | None:
        """Get a specific transcript segment.

        Args:
            user_id: The user ID
            meeting_id: The meeting ID
            segment_id: The segment ID

        Returns:
            TranscriptSegment if found, None otherwise
        """
        pk = f"USER#{user_id}#MEETING#{meeting_id}"
        sk = f"SEGMENT#{segment_id}"

        response = self.table.get_item(Key={"pk": pk, "sk": sk})

        item = response.get("Item")
        if not item:
            return None

        return self._item_to_segment(item)

    def delete_transcript(self, user_id: str, meeting_id: str) -> None:
        """Delete all transcript segments for a meeting.

        Args:
            user_id: The user ID
            meeting_id: The meeting ID
        """
        pk = f"USER#{user_id}#MEETING#{meeting_id}"

        # Query for all segments (just need keys)
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with("SEGMENT#"),
            ProjectionExpression="pk, sk",
        )

        items = response.get("Items", [])
        if not items:
            return

        with self.table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

    def transcript_exists(self, user_id: str, meeting_id: str) -> bool:
        """Check if a transcript exists for a meeting.

        Uses a count query for efficiency (doesn't retrieve items).

        Args:
            user_id: The user ID
            meeting_id: The meeting ID

        Returns:
            True if transcript exists, False otherwise
        """
        pk = f"USER#{user_id}#MEETING#{meeting_id}"

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with("SEGMENT#"),
            Select="COUNT",
            Limit=1,
        )

        return response.get("Count", 0) > 0

    def _item_to_segment(self, item: dict[str, Any]) -> TranscriptSegment:
        """Convert a DynamoDB item to a TranscriptSegment."""
        return TranscriptSegment(
            segment_id=item["segment_id"],
            t0=float(item["t0"]),
            t1=float(item["t1"]),
            speaker=item.get("speaker"),
            text=item["text"],
        )
