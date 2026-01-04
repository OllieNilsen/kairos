"""Unit tests for meetings repository adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.meetings_repo import MeetingsRepository
from src.core.models import AttendeeInfo, Meeting


class TestMeetingsRepository:
    """Tests for MeetingsRepository."""

    @pytest.fixture
    def mock_dynamodb(self) -> MagicMock:
        """Create a mock DynamoDB table."""
        mock_table = MagicMock()
        return mock_table

    @pytest.fixture
    def repo(self, mock_dynamodb: MagicMock) -> MeetingsRepository:
        """Create repository with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_dynamodb
            repo = MeetingsRepository("test-meetings-table")
            repo.table = mock_dynamodb
            return repo

    @pytest.fixture
    def sample_meeting(self) -> Meeting:
        """Create a sample meeting."""
        return Meeting(
            user_id="user-001",
            meeting_id="meeting-123",
            title="Team Standup",
            description="Daily sync",
            location="Room 101",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=["Alice", "Bob"],
            status="pending",
            google_etag="etag-123",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

    def test_save_meeting(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock, sample_meeting: Meeting
    ) -> None:
        """Should save a meeting to DynamoDB."""
        repo.save_meeting(sample_meeting)

        mock_dynamodb.put_item.assert_called_once()
        item = mock_dynamodb.put_item.call_args[1]["Item"]
        assert item["user_id"] == "user-001"
        assert item["meeting_id"] == "meeting-123"
        assert item["title"] == "Team Standup"
        assert item["description"] == "Daily sync"
        assert item["location"] == "Room 101"
        assert "ttl" in item

    def test_save_meeting_without_optional_fields(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should save meeting without optional fields."""
        meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-456",
            title="Quick Call",
            start_time=datetime(2024, 1, 15, 14, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 14, 15, tzinfo=UTC),
            attendees=[],
            status="pending",
            created_at=datetime.now(UTC),
        )

        repo.save_meeting(meeting)

        item = mock_dynamodb.put_item.call_args[1]["Item"]
        assert "description" not in item
        assert "location" not in item

    def test_get_meeting_found(self, repo: MeetingsRepository, mock_dynamodb: MagicMock) -> None:
        """Should return meeting when found."""
        mock_dynamodb.get_item.return_value = {
            "Item": {
                "user_id": "user-001",
                "meeting_id": "meeting-123",
                "title": "Test Meeting",
                "start_time": "2024-01-15T10:00:00+00:00",
                "end_time": "2024-01-15T10:30:00+00:00",
                "attendees": ["Alice"],
                "status": "pending",
                "created_at": "2024-01-14T12:00:00+00:00",
            }
        }

        meeting = repo.get_meeting("user-001", "meeting-123")

        assert meeting is not None
        assert meeting.meeting_id == "meeting-123"
        assert meeting.title == "Test Meeting"

    def test_get_meeting_not_found(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should return None when meeting not found."""
        mock_dynamodb.get_item.return_value = {}

        meeting = repo.get_meeting("user-001", "nonexistent")

        assert meeting is None

    def test_delete_meeting(self, repo: MeetingsRepository, mock_dynamodb: MagicMock) -> None:
        """Should delete a meeting."""
        repo.delete_meeting("user-001", "meeting-123")

        mock_dynamodb.delete_item.assert_called_once_with(
            Key={"user_id": "user-001", "meeting_id": "meeting-123"}
        )

    def test_list_meetings_for_user(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should list meetings for a user."""
        mock_dynamodb.query.return_value = {
            "Items": [
                {
                    "user_id": "user-001",
                    "meeting_id": "meeting-1",
                    "title": "Meeting 1",
                    "start_time": "2024-01-15T09:00:00+00:00",
                    "end_time": "2024-01-15T09:30:00+00:00",
                    "attendees": [],
                    "status": "pending",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
                {
                    "user_id": "user-001",
                    "meeting_id": "meeting-2",
                    "title": "Meeting 2",
                    "start_time": "2024-01-15T10:00:00+00:00",
                    "end_time": "2024-01-15T10:30:00+00:00",
                    "attendees": [],
                    "status": "pending",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
            ]
        }

        meetings = repo.list_meetings_for_user("user-001")

        assert len(meetings) == 2
        assert meetings[0].meeting_id == "meeting-1"
        assert meetings[1].meeting_id == "meeting-2"

    def test_list_meetings_with_status_filter(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should filter meetings by status."""
        mock_dynamodb.query.return_value = {
            "Items": [
                {
                    "user_id": "user-001",
                    "meeting_id": "meeting-1",
                    "title": "Pending Meeting",
                    "start_time": "2024-01-15T09:00:00+00:00",
                    "end_time": "2024-01-15T09:30:00+00:00",
                    "attendees": [],
                    "status": "pending",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
                {
                    "user_id": "user-001",
                    "meeting_id": "meeting-2",
                    "title": "Debriefed Meeting",
                    "start_time": "2024-01-15T10:00:00+00:00",
                    "end_time": "2024-01-15T10:30:00+00:00",
                    "attendees": [],
                    "status": "debriefed",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
            ]
        }

        meetings = repo.list_meetings_for_user("user-001", status="pending")

        assert len(meetings) == 1
        assert meetings[0].status == "pending"

    def test_get_pending_meetings(self, repo: MeetingsRepository, mock_dynamodb: MagicMock) -> None:
        """Should return only pending meetings that have ended."""
        past_time = datetime.now(UTC) - timedelta(hours=2)
        future_time = datetime.now(UTC) + timedelta(hours=2)

        mock_dynamodb.query.return_value = {
            "Items": [
                {
                    "user_id": "user-001",
                    "meeting_id": "past-meeting",
                    "title": "Past Meeting",
                    "start_time": (past_time - timedelta(hours=1)).isoformat(),
                    "end_time": past_time.isoformat(),
                    "attendees": [],
                    "status": "pending",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
                {
                    "user_id": "user-001",
                    "meeting_id": "future-meeting",
                    "title": "Future Meeting",
                    "start_time": future_time.isoformat(),
                    "end_time": (future_time + timedelta(hours=1)).isoformat(),
                    "attendees": [],
                    "status": "pending",
                    "created_at": "2024-01-14T12:00:00+00:00",
                },
            ]
        }

        meetings = repo.get_pending_meetings("user-001")

        assert len(meetings) == 1
        assert meetings[0].meeting_id == "past-meeting"

    def test_mark_debriefed(self, repo: MeetingsRepository, mock_dynamodb: MagicMock) -> None:
        """Should mark multiple meetings as debriefed."""
        repo.mark_debriefed("user-001", ["meeting-1", "meeting-2", "meeting-3"])

        assert mock_dynamodb.update_item.call_count == 3

    def test_item_to_meeting(self, repo: MeetingsRepository) -> None:
        """Should correctly convert DynamoDB item to Meeting."""
        item = {
            "user_id": "user-001",
            "meeting_id": "meeting-123",
            "title": "Test Meeting",
            "description": "A description",
            "location": "Room 1",
            "start_time": "2024-01-15T10:00:00+00:00",
            "end_time": "2024-01-15T10:30:00+00:00",
            "attendees": ["Alice", "Bob"],
            "status": "pending",
            "google_etag": "etag-123",
            "created_at": "2024-01-14T12:00:00+00:00",
        }

        meeting = repo._item_to_meeting(item)

        assert meeting.user_id == "user-001"
        assert meeting.meeting_id == "meeting-123"
        assert meeting.title == "Test Meeting"
        assert meeting.description == "A description"
        assert meeting.location == "Room 1"
        # Attendees are now AttendeeInfo objects (old string format converted)
        assert meeting.attendee_emails == ["Alice", "Bob"]
        assert meeting.attendee_names == ["Alice", "Bob"]
        assert meeting.google_etag == "etag-123"

    def test_save_meeting_serializes_attendee_info_objects(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should serialize AttendeeInfo objects to dicts when saving."""
        meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-789",
            title="Team Sync",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=[
                AttendeeInfo(name="Alice Smith", email="alice@example.com"),
                AttendeeInfo(name="Bob Jones", email="bob@example.com"),
            ],
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        repo.save_meeting(meeting)

        item = mock_dynamodb.put_item.call_args[1]["Item"]
        # Attendees should be serialized as list of dicts, not AttendeeInfo objects
        assert item["attendees"] == [
            {"name": "Alice Smith", "email": "alice@example.com"},
            {"name": "Bob Jones", "email": "bob@example.com"},
        ]

    def test_save_meeting_handles_attendee_without_email(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should handle AttendeeInfo with None email when saving."""
        meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-790",
            title="External Call",
            start_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 11, 30, tzinfo=UTC),
            attendees=[
                AttendeeInfo(name="External Person", email=None),
            ],
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        repo.save_meeting(meeting)

        item = mock_dynamodb.put_item.call_args[1]["Item"]
        assert item["attendees"] == [{"name": "External Person", "email": None}]

    def test_item_to_meeting_with_attendee_info_dicts(self, repo: MeetingsRepository) -> None:
        """Should correctly deserialize AttendeeInfo dicts from DynamoDB."""
        item = {
            "user_id": "user-001",
            "meeting_id": "meeting-123",
            "title": "Test Meeting",
            "start_time": "2024-01-15T10:00:00+00:00",
            "end_time": "2024-01-15T10:30:00+00:00",
            "attendees": [
                {"name": "Alice Smith", "email": "alice@example.com"},
                {"name": "Bob Jones", "email": "bob@example.com"},
            ],
            "status": "pending",
            "created_at": "2024-01-14T12:00:00+00:00",
        }

        meeting = repo._item_to_meeting(item)

        assert len(meeting.attendees) == 2
        assert meeting.attendees[0].name == "Alice Smith"
        assert meeting.attendees[0].email == "alice@example.com"
        assert meeting.attendees[1].name == "Bob Jones"
        assert meeting.attendees[1].email == "bob@example.com"
        # Properties should work correctly
        assert meeting.attendee_emails == ["alice@example.com", "bob@example.com"]
        assert meeting.attendee_names == ["Alice Smith", "Bob Jones"]

    def test_round_trip_attendee_info_serialization(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should correctly round-trip AttendeeInfo objects through save and get."""
        # Create meeting with AttendeeInfo objects
        original_meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-roundtrip",
            title="Round Trip Test",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=[
                AttendeeInfo(name="Alice Smith", email="alice@example.com"),
                AttendeeInfo(name="Bob Jones", email="bob@example.com"),
                AttendeeInfo(name="Charlie (External)", email=None),
            ],
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        # Save the meeting
        repo.save_meeting(original_meeting)

        # Capture what was saved to DynamoDB
        saved_item = mock_dynamodb.put_item.call_args[1]["Item"]

        # Verify attendees were serialized as dicts
        assert saved_item["attendees"] == [
            {"name": "Alice Smith", "email": "alice@example.com"},
            {"name": "Bob Jones", "email": "bob@example.com"},
            {"name": "Charlie (External)", "email": None},
        ]

        # Simulate retrieving from DynamoDB
        mock_dynamodb.get_item.return_value = {"Item": saved_item}
        retrieved_meeting = repo.get_meeting("user-001", "meeting-roundtrip")

        # Verify the meeting was reconstructed correctly
        assert retrieved_meeting is not None
        assert len(retrieved_meeting.attendees) == 3
        assert isinstance(retrieved_meeting.attendees[0], AttendeeInfo)
        assert retrieved_meeting.attendees[0].name == "Alice Smith"
        assert retrieved_meeting.attendees[0].email == "alice@example.com"
        assert retrieved_meeting.attendees[1].name == "Bob Jones"
        assert retrieved_meeting.attendees[1].email == "bob@example.com"
        assert retrieved_meeting.attendees[2].name == "Charlie (External)"
        assert retrieved_meeting.attendees[2].email is None

        # Verify properties work correctly
        assert retrieved_meeting.attendee_emails == ["alice@example.com", "bob@example.com"]
        assert retrieved_meeting.attendee_names == [
            "Alice Smith",
            "Bob Jones",
            "Charlie (External)",
        ]

    def test_save_meeting_with_attendee_entity_ids(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should save attendee_entity_ids when present (Slice 3)."""
        meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-with-entities",
            title="Meeting with Entities",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=[
                AttendeeInfo(name="Alice Smith", email="alice@example.com"),
                AttendeeInfo(name="Bob Jones", email="bob@example.com"),
            ],
            attendee_entity_ids=["entity-123", "entity-456"],
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        repo.save_meeting(meeting)

        item = mock_dynamodb.put_item.call_args[1]["Item"]
        assert item["attendee_entity_ids"] == ["entity-123", "entity-456"]

    def test_save_meeting_without_attendee_entity_ids(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should not include attendee_entity_ids field when empty (Slice 3)."""
        meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-no-entities",
            title="Meeting without Entity IDs",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=[AttendeeInfo(name="Alice", email="alice@example.com")],
            attendee_entity_ids=[],  # Empty list
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        repo.save_meeting(meeting)

        item = mock_dynamodb.put_item.call_args[1]["Item"]
        # Empty list should not be saved to reduce storage
        assert "attendee_entity_ids" not in item

    def test_item_to_meeting_with_attendee_entity_ids(self, repo: MeetingsRepository) -> None:
        """Should correctly deserialize attendee_entity_ids from DynamoDB (Slice 3)."""
        item = {
            "user_id": "user-001",
            "meeting_id": "meeting-123",
            "title": "Test Meeting",
            "start_time": "2024-01-15T10:00:00+00:00",
            "end_time": "2024-01-15T10:30:00+00:00",
            "attendees": [
                {"name": "Alice Smith", "email": "alice@example.com"},
                {"name": "Bob Jones", "email": "bob@example.com"},
            ],
            "attendee_entity_ids": ["entity-alice", "entity-bob"],
            "status": "pending",
            "created_at": "2024-01-14T12:00:00+00:00",
        }

        meeting = repo._item_to_meeting(item)

        assert meeting.attendee_entity_ids == ["entity-alice", "entity-bob"]

    def test_item_to_meeting_without_attendee_entity_ids(self, repo: MeetingsRepository) -> None:
        """Should default to empty list when attendee_entity_ids not present (Slice 3)."""
        item = {
            "user_id": "user-001",
            "meeting_id": "meeting-123",
            "title": "Test Meeting",
            "start_time": "2024-01-15T10:00:00+00:00",
            "end_time": "2024-01-15T10:30:00+00:00",
            "attendees": [{"name": "Alice", "email": "alice@example.com"}],
            "status": "pending",
            "created_at": "2024-01-14T12:00:00+00:00",
            # Note: attendee_entity_ids not present (old meeting from before Slice 3)
        }

        meeting = repo._item_to_meeting(item)

        # Should default to empty list for backward compatibility
        assert meeting.attendee_entity_ids == []

    def test_round_trip_attendee_entity_ids(
        self, repo: MeetingsRepository, mock_dynamodb: MagicMock
    ) -> None:
        """Should correctly round-trip attendee_entity_ids through save and get (Slice 3)."""
        original_meeting = Meeting(
            user_id="user-001",
            meeting_id="meeting-roundtrip-entities",
            title="Entity Round Trip Test",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            attendees=[
                AttendeeInfo(name="Alice Smith", email="alice@example.com"),
                AttendeeInfo(name="Bob Jones", email="bob@example.com"),
            ],
            attendee_entity_ids=["entity-uuid-1", "entity-uuid-2"],
            status="pending",
            created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        )

        # Save the meeting
        repo.save_meeting(original_meeting)

        # Capture what was saved
        saved_item = mock_dynamodb.put_item.call_args[1]["Item"]
        assert saved_item["attendee_entity_ids"] == ["entity-uuid-1", "entity-uuid-2"]

        # Simulate retrieving from DynamoDB
        mock_dynamodb.get_item.return_value = {"Item": saved_item}
        retrieved_meeting = repo.get_meeting("user-001", "meeting-roundtrip-entities")

        # Verify entity IDs were preserved
        assert retrieved_meeting is not None
        assert retrieved_meeting.attendee_entity_ids == ["entity-uuid-1", "entity-uuid-2"]
