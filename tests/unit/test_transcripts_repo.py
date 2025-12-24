"""Unit tests for transcripts repository adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.transcripts_repo import TranscriptsRepository
from src.core.models import TranscriptSegment


class TestTranscriptsRepository:
    """Tests for TranscriptsRepository."""

    @pytest.fixture
    def mock_dynamodb(self) -> MagicMock:
        """Create a mock DynamoDB table."""
        mock_table = MagicMock()
        return mock_table

    @pytest.fixture
    def repo(self, mock_dynamodb: MagicMock) -> TranscriptsRepository:
        """Create repository with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_dynamodb
            repo = TranscriptsRepository("test-transcripts-table")
            repo.table = mock_dynamodb
            return repo

    @pytest.fixture
    def sample_segments(self) -> list[TranscriptSegment]:
        """Create sample transcript segments."""
        return [
            TranscriptSegment(
                segment_id="seg_0001",
                t0=0.0,
                t1=5.2,
                speaker="user",
                text="Hello, this is the first segment.",
            ),
            TranscriptSegment(
                segment_id="seg_0002",
                t0=5.2,
                t1=10.5,
                speaker="assistant",
                text="This is the second segment from the assistant.",
            ),
            TranscriptSegment(
                segment_id="seg_0003",
                t0=10.5,
                t1=15.0,
                speaker="user",
                text="And this is the third segment.",
            ),
        ]

    def test_save_transcript(
        self,
        repo: TranscriptsRepository,
        mock_dynamodb: MagicMock,
        sample_segments: list[TranscriptSegment],
    ) -> None:
        """Should save transcript segments using batch writer."""
        # Mock batch writer context manager
        mock_batch = MagicMock()
        mock_dynamodb.batch_writer.return_value.__enter__.return_value = mock_batch

        repo.save_transcript(
            user_id="user-001",
            meeting_id="meeting-123",
            call_id="call-456",
            segments=sample_segments,
        )

        # Verify batch writer was used
        mock_dynamodb.batch_writer.assert_called_once()

        # Verify all segments were written
        assert mock_batch.put_item.call_count == 3

        # Verify first segment structure
        first_call = mock_batch.put_item.call_args_list[0]
        item = first_call[1]["Item"]
        assert item["pk"] == "USER#user-001#MEETING#meeting-123"
        assert item["sk"] == "SEGMENT#seg_0001"
        assert item["segment_id"] == "seg_0001"
        assert item["t0"] == 0.0
        assert item["t1"] == 5.2
        assert item["speaker"] == "user"
        assert item["text"] == "Hello, this is the first segment."
        assert item["call_id"] == "call-456"
        assert "created_at" in item
        assert "ttl" in item

    def test_save_transcript_without_speaker(
        self, repo: TranscriptsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should handle segments without speaker field."""
        mock_batch = MagicMock()
        mock_dynamodb.batch_writer.return_value.__enter__.return_value = mock_batch

        segments = [
            TranscriptSegment(
                segment_id="seg_0001",
                t0=0.0,
                t1=5.0,
                speaker=None,
                text="Segment without speaker.",
            )
        ]

        repo.save_transcript(
            user_id="user-001",
            meeting_id="meeting-123",
            call_id="call-456",
            segments=segments,
        )

        item = mock_batch.put_item.call_args[1]["Item"]
        # Speaker should not be in item if None
        assert "speaker" not in item

    def test_get_transcript(self, repo: TranscriptsRepository, mock_dynamodb: MagicMock) -> None:
        """Should retrieve all segments for a meeting sorted by t0."""
        mock_dynamodb.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_0002",
                    "segment_id": "seg_0002",
                    "t0": 5.2,
                    "t1": 10.5,
                    "speaker": "assistant",
                    "text": "Second segment.",
                },
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_0001",
                    "segment_id": "seg_0001",
                    "t0": 0.0,
                    "t1": 5.2,
                    "speaker": "user",
                    "text": "First segment.",
                },
                {
                    "pk": "USER#user-001#MEETING#meeting-123",
                    "sk": "SEGMENT#seg_0003",
                    "segment_id": "seg_0003",
                    "t0": 10.5,
                    "t1": 15.0,
                    "text": "Third segment (no speaker).",
                },
            ]
        }

        segments = repo.get_transcript("user-001", "meeting-123")

        # Verify query was called correctly
        mock_dynamodb.query.assert_called_once()
        call_args = mock_dynamodb.query.call_args[1]
        assert ":pk" in call_args["ExpressionAttributeValues"]
        assert call_args["ExpressionAttributeValues"][":pk"] == "USER#user-001#MEETING#meeting-123"

        # Verify segments are sorted by t0
        assert len(segments) == 3
        assert segments[0].segment_id == "seg_0001"
        assert segments[0].t0 == 0.0
        assert segments[1].segment_id == "seg_0002"
        assert segments[1].t0 == 5.2
        assert segments[2].segment_id == "seg_0003"
        assert segments[2].t0 == 10.5

        # Verify segment without speaker is handled
        assert segments[2].speaker is None

    def test_get_transcript_empty(
        self, repo: TranscriptsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should return empty list when no segments found."""
        mock_dynamodb.query.return_value = {"Items": []}

        segments = repo.get_transcript("user-001", "nonexistent")

        assert segments == []

    def test_get_segment_found(self, repo: TranscriptsRepository, mock_dynamodb: MagicMock) -> None:
        """Should retrieve a specific segment by ID."""
        mock_dynamodb.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001#MEETING#meeting-123",
                "sk": "SEGMENT#seg_0001",
                "segment_id": "seg_0001",
                "t0": 0.0,
                "t1": 5.2,
                "speaker": "user",
                "text": "Test segment.",
            }
        }

        segment = repo.get_segment("user-001", "meeting-123", "seg_0001")

        assert segment is not None
        assert segment.segment_id == "seg_0001"
        assert segment.t0 == 0.0
        assert segment.t1 == 5.2
        assert segment.speaker == "user"
        assert segment.text == "Test segment."

        # Verify get_item was called with correct keys
        mock_dynamodb.get_item.assert_called_once()
        call_args = mock_dynamodb.get_item.call_args[1]
        assert call_args["Key"]["pk"] == "USER#user-001#MEETING#meeting-123"
        assert call_args["Key"]["sk"] == "SEGMENT#seg_0001"

    def test_get_segment_not_found(
        self, repo: TranscriptsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should return None when segment not found."""
        mock_dynamodb.get_item.return_value = {}

        segment = repo.get_segment("user-001", "meeting-123", "nonexistent")

        assert segment is None

    def test_item_to_segment(self, repo: TranscriptsRepository) -> None:
        """Should correctly convert DynamoDB item to TranscriptSegment."""
        item = {
            "pk": "USER#user-001#MEETING#meeting-123",
            "sk": "SEGMENT#seg_0001",
            "segment_id": "seg_0001",
            "t0": 0.0,
            "t1": 5.2,
            "speaker": "user",
            "text": "Test segment.",
        }

        segment = repo._item_to_segment(item)

        assert segment.segment_id == "seg_0001"
        assert segment.t0 == 0.0
        assert segment.t1 == 5.2
        assert segment.speaker == "user"
        assert segment.text == "Test segment."

    def test_item_to_segment_without_speaker(self, repo: TranscriptsRepository) -> None:
        """Should handle items without speaker field."""
        item = {
            "pk": "USER#user-001#MEETING#meeting-123",
            "sk": "SEGMENT#seg_0001",
            "segment_id": "seg_0001",
            "t0": 0.0,
            "t1": 5.2,
            "text": "Segment without speaker.",
        }

        segment = repo._item_to_segment(item)

        assert segment.segment_id == "seg_0001"
        assert segment.speaker is None
        assert segment.text == "Segment without speaker."

    def test_save_transcript_idempotency(
        self,
        repo: TranscriptsRepository,
        mock_dynamodb: MagicMock,
        sample_segments: list[TranscriptSegment],
    ) -> None:
        """Should be idempotent - saving same segments twice should overwrite."""
        mock_batch = MagicMock()
        mock_dynamodb.batch_writer.return_value.__enter__.return_value = mock_batch

        # Save twice with same call_id
        repo.save_transcript(
            user_id="user-001",
            meeting_id="meeting-123",
            call_id="call-456",
            segments=sample_segments,
        )

        repo.save_transcript(
            user_id="user-001",
            meeting_id="meeting-123",
            call_id="call-456",
            segments=sample_segments,
        )

        # Both saves should succeed (DynamoDB put_item overwrites)
        assert mock_batch.put_item.call_count == 6  # 3 segments × 2 saves
