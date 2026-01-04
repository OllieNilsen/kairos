"""Unit tests for transcripts repository adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.adapters.transcripts_repo import TranscriptsRepository
from src.core.models import TranscriptSegment


class TestTranscriptsRepository:
    """Tests for TranscriptsRepository."""

    def _create_repo(self, mock_table: MagicMock) -> TranscriptsRepository:
        """Create repository with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table
            repo = TranscriptsRepository("test-transcripts-table")
            repo.table = mock_table
            return repo

    def test_save_transcript_writes_all_segments(self) -> None:
        """Should save all transcript segments to DynamoDB."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        segments = [
            TranscriptSegment(segment_id="seg_1", t0=0.0, t1=5.0, speaker="user", text="Hello"),
            TranscriptSegment(
                segment_id="seg_2", t0=5.0, t1=10.0, speaker="assistant", text="Hi there"
            ),
        ]

        repo.save_transcript("user-001", "meeting-123", "call-456", segments)

        # Should batch write all segments
        mock_table.batch_writer.assert_called_once()
        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        assert batch_writer.put_item.call_count == 2

    def test_save_transcript_uses_correct_keys(self) -> None:
        """Should use correct PK/SK format for DynamoDB."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        segments = [
            TranscriptSegment(segment_id="seg_1", t0=0.0, t1=5.0, speaker="user", text="Hello"),
        ]

        repo.save_transcript("user-001", "meeting-123", "call-456", segments)

        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        put_call = batch_writer.put_item.call_args[1]["Item"]

        assert put_call["pk"] == "USER#user-001#MEETING#meeting-123"
        assert put_call["sk"] == "SEGMENT#seg_1"

    def test_save_transcript_stores_segment_data(self) -> None:
        """Should store all segment fields correctly."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        segments = [
            TranscriptSegment(
                segment_id="seg_42", t0=10.5, t1=15.3, speaker="user", text="Test message"
            ),
        ]

        repo.save_transcript("user-001", "meeting-123", "call-789", segments)

        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        item = batch_writer.put_item.call_args[1]["Item"]

        assert item["segment_id"] == "seg_42"
        assert item["t0"] == Decimal("10.5")
        assert item["t1"] == Decimal("15.3")
        assert item["speaker"] == "user"
        assert item["text"] == "Test message"
        assert item["call_id"] == "call-789"
        assert item["meeting_id"] == "meeting-123"
        assert item["user_id"] == "user-001"

    def test_save_transcript_handles_empty_segments(self) -> None:
        """Should handle empty segment list gracefully."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        repo.save_transcript("user-001", "meeting-123", "call-456", [])

        # Should not call batch_writer if no segments
        mock_table.batch_writer.assert_not_called()

    def test_save_transcript_handles_null_speaker(self) -> None:
        """Should handle segment with no speaker."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        segments = [
            TranscriptSegment(segment_id="seg_1", t0=0.0, t1=5.0, speaker=None, text="Hello"),
        ]

        repo.save_transcript("user-001", "meeting-123", "call-456", segments)

        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        item = batch_writer.put_item.call_args[1]["Item"]

        assert item["speaker"] is None

    def test_save_transcript_includes_ttl(self) -> None:
        """Should include TTL for automatic expiration."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        segments = [
            TranscriptSegment(segment_id="seg_1", t0=0.0, t1=5.0, speaker="user", text="Hello"),
        ]

        repo.save_transcript("user-001", "meeting-123", "call-456", segments)

        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        item = batch_writer.put_item.call_args[1]["Item"]

        # TTL should be set (90 days in the future)
        assert "ttl" in item
        now_ts = int(datetime.now(UTC).timestamp())
        assert item["ttl"] > now_ts
        assert item["ttl"] < now_ts + 100 * 86400  # Less than 100 days

    def test_get_transcript_returns_all_segments(self) -> None:
        """Should return all segments for a meeting."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_1",
                    "segment_id": "seg_1",
                    "t0": Decimal("0.0"),
                    "t1": Decimal("5.0"),
                    "speaker": "user",
                    "text": "Hello",
                },
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_2",
                    "segment_id": "seg_2",
                    "t0": Decimal("5.0"),
                    "t1": Decimal("10.0"),
                    "speaker": "assistant",
                    "text": "Hi there",
                },
            ]
        }

        segments = repo.get_transcript("user-001", "meeting-123")

        assert len(segments) == 2
        assert segments[0].segment_id == "seg_1"
        assert segments[0].text == "Hello"
        assert segments[1].segment_id == "seg_2"
        assert segments[1].text == "Hi there"

    def test_get_transcript_returns_empty_list_when_not_found(self) -> None:
        """Should return empty list when no transcript exists."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.query.return_value = {"Items": []}

        segments = repo.get_transcript("user-001", "nonexistent")

        assert len(segments) == 0

    def test_get_transcript_sorts_by_t0(self) -> None:
        """Should return segments sorted by start time."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        # Return items out of order
        mock_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_2",
                    "segment_id": "seg_2",
                    "t0": Decimal("5.0"),
                    "t1": Decimal("10.0"),
                    "speaker": "user",
                    "text": "Second",
                },
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_1",
                    "segment_id": "seg_1",
                    "t0": Decimal("0.0"),
                    "t1": Decimal("5.0"),
                    "speaker": "user",
                    "text": "First",
                },
            ]
        }

        segments = repo.get_transcript("user-001", "meeting-123")

        assert segments[0].text == "First"
        assert segments[1].text == "Second"

    def test_get_segment_returns_segment_when_found(self) -> None:
        """Should return a specific segment by ID."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001#MEETING#meeting-123",
                "sk": "SEGMENT#seg_42",
                "segment_id": "seg_42",
                "t0": Decimal("10.5"),
                "t1": Decimal("15.0"),
                "speaker": "assistant",
                "text": "Found it",
            }
        }

        segment = repo.get_segment("user-001", "meeting-123", "seg_42")

        assert segment is not None
        assert segment.segment_id == "seg_42"
        assert segment.t0 == 10.5
        assert segment.t1 == 15.0
        assert segment.speaker == "assistant"
        assert segment.text == "Found it"

    def test_get_segment_returns_none_when_not_found(self) -> None:
        """Should return None when segment not found."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.get_item.return_value = {}

        segment = repo.get_segment("user-001", "meeting-123", "nonexistent")

        assert segment is None

    def test_get_segment_uses_correct_key(self) -> None:
        """Should use correct PK/SK for get_item."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.get_item.return_value = {}

        repo.get_segment("user-001", "meeting-123", "seg_5")

        mock_table.get_item.assert_called_once_with(
            Key={
                "pk": "USER#user-001#MEETING#meeting-123",
                "sk": "SEGMENT#seg_5",
            }
        )

    def test_delete_transcript_removes_all_segments(self) -> None:
        """Should delete all segments for a meeting."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        # Mock query to return existing segments
        mock_table.query.return_value = {
            "Items": [
                {"pk": "USER#user-001#MEETING#meeting-123", "sk": "SEGMENT#seg_1"},
                {"pk": "USER#user-001#MEETING#meeting-123", "sk": "SEGMENT#seg_2"},
            ]
        }

        repo.delete_transcript("user-001", "meeting-123")

        # Should batch delete all segments
        mock_table.batch_writer.assert_called_once()
        batch_writer = mock_table.batch_writer.return_value.__enter__.return_value
        assert batch_writer.delete_item.call_count == 2

    def test_delete_transcript_handles_empty_transcript(self) -> None:
        """Should handle deletion of non-existent transcript gracefully."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.query.return_value = {"Items": []}

        repo.delete_transcript("user-001", "nonexistent")

        # Should not call batch_writer if nothing to delete
        mock_table.batch_writer.assert_not_called()

    def test_transcript_exists_returns_true_when_exists(self) -> None:
        """Should return True when transcript exists."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.query.return_value = {"Count": 1}

        exists = repo.transcript_exists("user-001", "meeting-123")

        assert exists is True

    def test_transcript_exists_returns_false_when_not_exists(self) -> None:
        """Should return False when transcript doesn't exist."""
        mock_table = MagicMock()
        repo = self._create_repo(mock_table)

        mock_table.query.return_value = {"Count": 0}

        exists = repo.transcript_exists("user-001", "meeting-123")

        assert exists is False
