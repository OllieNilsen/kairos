"""DynamoDB repository for meeting transcript segments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import TranscriptSegment
except ImportError:
    from src.core.models import TranscriptSegment


class TranscriptsRepository:
    """Repository for storing and querying transcript segments in DynamoDB."""

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
        """Save transcript segments to DynamoDB.

        Idempotent: overwrites existing segments with same segment_id.

        Args:
            user_id: User identifier for partitioning
            meeting_id: Meeting identifier
            call_id: Call identifier (for idempotency)
            segments: List of transcript segments to store
        """
        # Use batch write for efficiency
        with self.table.batch_writer() as batch:
            for segment in segments:
                item: dict[str, Any] = {
                    "pk": f"USER#{user_id}#MEETING#{meeting_id}",
                    "sk": f"SEGMENT#{segment.segment_id}",
                    "segment_id": segment.segment_id,
                    "t0": segment.t0,
                    "t1": segment.t1,
                    "text": segment.text,
                    "call_id": call_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "ttl": int(datetime.now(UTC).timestamp()) + 86400 * 90,  # 90 days
                }

                # Add optional speaker field
                if segment.speaker:
                    item["speaker"] = segment.speaker

                batch.put_item(Item=item)

    def get_transcript(self, user_id: str, meeting_id: str) -> list[TranscriptSegment]:
        """Get all transcript segments for a meeting.

        Args:
            user_id: User identifier
            meeting_id: Meeting identifier

        Returns:
            List of TranscriptSegment objects, sorted by t0
        """
        pk = f"USER#{user_id}#MEETING#{meeting_id}"

        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
            ExpressionAttributeValues={
                ":pk": pk,
                ":sk_prefix": "SEGMENT#",
            },
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
            user_id: User identifier
            meeting_id: Meeting identifier
            segment_id: Segment identifier

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

    def _item_to_segment(self, item: dict[str, Any]) -> TranscriptSegment:
        """Convert a DynamoDB item to a TranscriptSegment object."""
        return TranscriptSegment(
            segment_id=item["segment_id"],
            t0=float(item["t0"]),
            t1=float(item["t1"]),
            speaker=item.get("speaker"),
            text=item["text"],
        )
