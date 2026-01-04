"""Unit tests for calendar webhook handler - debrief event detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.models import UserState


class TestCheckDebriefEventChanges:
    """Tests for check_debrief_event_changes function."""

    @pytest.fixture
    def future_date_str(self) -> str:
        """Get a date string for tomorrow."""
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")

    @pytest.fixture
    def mock_user_state(self, future_date_str: str) -> UserState:
        """Create a mock user state with debrief event configured."""
        return UserState(
            user_id="user-001",
            debrief_event_id="event-123",
            debrief_event_etag="etag-old",
            next_prompt_at=f"{future_date_str}T17:30:00+00:00",
            prompt_schedule_name=f"kairos-prompt-user-001-{future_date_str}",
            timezone="Europe/London",
        )

    @pytest.fixture
    def mock_calendar_event(self, future_date_str: str) -> dict:
        """Create a mock Google Calendar event."""
        return {
            "id": "event-123",
            "status": "confirmed",
            "etag": "etag-old",
            "start": {"dateTime": f"{future_date_str}T17:30:00Z"},
            "end": {"dateTime": f"{future_date_str}T17:45:00Z"},
            "summary": "📞 Kairos Debrief",
        }

    def test_skips_when_no_user_state_table(self) -> None:
        """Should skip when USER_STATE_TABLE not configured."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        with patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=None):
            mock_calendar = MagicMock()
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "skipped"

    def test_skips_when_user_not_found(self) -> None:
        """Should skip when user state doesn't exist."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = None

        with patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=mock_repo):
            mock_calendar = MagicMock()
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "skipped"

    def test_returns_none_when_no_debrief_event(self) -> None:
        """Should return none when no debrief event is configured."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = UserState(
            user_id="user-001",
            debrief_event_id=None,  # No debrief event
        )

        with patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=mock_repo):
            mock_calendar = MagicMock()
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "none"

    def test_handles_deleted_event_fetch_error(self, mock_user_state: UserState) -> None:
        """Should treat event as deleted when fetch fails."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = mock_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.side_effect = Exception("Not found")

        mock_scheduler = MagicMock()

        with (
            patch(
                "src.handlers.calendar_webhook.get_user_state_repo",
                return_value=mock_repo,
            ),
            patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler),
        ):
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "deleted"
        mock_repo.clear_debrief_event.assert_called_once_with("user-001")

    def test_handles_cancelled_event(
        self, mock_user_state: UserState, mock_calendar_event: dict
    ) -> None:
        """Should treat cancelled event as deleted."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_calendar_event["status"] = "cancelled"

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = mock_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.return_value = mock_calendar_event

        mock_scheduler = MagicMock()

        with (
            patch(
                "src.handlers.calendar_webhook.get_user_state_repo",
                return_value=mock_repo,
            ),
            patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler),
        ):
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "deleted"
        mock_scheduler.delete_schedule.assert_called_once()

    def test_detects_moved_event(
        self, mock_user_state: UserState, mock_calendar_event: dict, future_date_str: str
    ) -> None:
        """Should detect when event is moved to a new time."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        # Change the event time by 1 hour
        mock_calendar_event["start"]["dateTime"] = f"{future_date_str}T18:30:00Z"
        mock_calendar_event["etag"] = "etag-new"

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = mock_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.return_value = mock_calendar_event

        mock_scheduler = MagicMock()

        with (
            patch(
                "src.handlers.calendar_webhook.get_user_state_repo",
                return_value=mock_repo,
            ),
            patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler),
            patch("src.handlers.calendar_webhook._get_account_id", return_value="123456789"),
            patch.dict(
                "os.environ",
                {
                    "SCHEDULER_ROLE_ARN": "arn:aws:iam::123456789:role/scheduler",
                    "AWS_REGION": "eu-west-1",
                },
            ),
        ):
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "moved"
        mock_scheduler.upsert_one_time_schedule.assert_called_once()
        mock_repo.update_prompt_schedule.assert_called_once()

    def test_no_action_when_time_unchanged(
        self, mock_user_state: UserState, mock_calendar_event: dict
    ) -> None:
        """Should take no action when event time hasn't changed."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = mock_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.return_value = mock_calendar_event

        with patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=mock_repo):
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "none"

    def test_updates_etag_when_event_modified_but_time_same(
        self, mock_user_state: UserState, mock_calendar_event: dict
    ) -> None:
        """Should update etag when event modified but time unchanged."""
        from src.handlers.calendar_webhook import check_debrief_event_changes

        mock_calendar_event["etag"] = "etag-new"  # Different etag

        mock_repo = MagicMock()
        mock_repo.get_user_state.return_value = mock_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.return_value = mock_calendar_event

        with patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=mock_repo):
            result = check_debrief_event_changes("user-001", mock_calendar)

        assert result["debrief_action"] == "none"
        mock_repo.update_debrief_event.assert_called_once_with(
            user_id="user-001",
            debrief_event_id="event-123",
            debrief_event_etag="etag-new",
        )


class TestHandleDebriefDeleted:
    """Tests for _handle_debrief_deleted function."""

    def test_deletes_schedule_and_clears_state(self) -> None:
        """Should delete schedule and clear user state."""
        from src.handlers.calendar_webhook import _handle_debrief_deleted

        user_state = UserState(
            user_id="user-001",
            prompt_schedule_name="kairos-prompt-user-001-2024-01-15",
        )

        mock_repo = MagicMock()
        mock_scheduler = MagicMock()

        with patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler):
            result = _handle_debrief_deleted("user-001", user_state, mock_repo)

        assert result["debrief_action"] == "deleted"
        mock_scheduler.delete_schedule.assert_called_once_with("kairos-prompt-user-001-2024-01-15")
        mock_repo.clear_debrief_event.assert_called_once_with("user-001")

    def test_skips_schedule_delete_when_no_schedule(self) -> None:
        """Should skip schedule deletion when no schedule exists."""
        from src.handlers.calendar_webhook import _handle_debrief_deleted

        user_state = UserState(
            user_id="user-001",
            prompt_schedule_name=None,  # No schedule
        )

        mock_repo = MagicMock()
        mock_scheduler = MagicMock()

        with patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler):
            result = _handle_debrief_deleted("user-001", user_state, mock_repo)

        assert result["debrief_action"] == "deleted"
        mock_scheduler.delete_schedule.assert_not_called()
        mock_repo.clear_debrief_event.assert_called_once()


class TestHandleDebriefMoved:
    """Tests for _handle_debrief_moved function."""

    @pytest.fixture
    def future_date_str(self) -> str:
        """Get a date string for tomorrow."""
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")

    @pytest.fixture
    def mock_user_state(self, future_date_str: str) -> UserState:
        """Create a mock user state."""
        return UserState(
            user_id="user-001",
            timezone="Europe/London",
            prompt_schedule_name=f"kairos-prompt-user-001-{future_date_str}",
        )

    @pytest.fixture
    def mock_event(self) -> dict:
        """Create a mock calendar event."""
        return {
            "id": "event-123",
            "etag": "etag-new",
        }

    def test_reschedules_to_new_time(self, mock_user_state: UserState, mock_event: dict) -> None:
        """Should reschedule prompt to new event time."""
        from src.handlers.calendar_webhook import _handle_debrief_moved

        new_time = datetime.now(UTC) + timedelta(hours=2)

        mock_repo = MagicMock()
        mock_scheduler = MagicMock()

        with (
            patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler),
            patch("src.handlers.calendar_webhook._get_account_id", return_value="123456789"),
            patch.dict(
                "os.environ",
                {
                    "SCHEDULER_ROLE_ARN": "arn:aws:iam::123456789:role/scheduler",
                    "AWS_REGION": "eu-west-1",
                    "PROMPT_SENDER_FUNCTION_NAME": "kairos-prompt-sender",
                },
            ),
        ):
            result = _handle_debrief_moved(
                "user-001", mock_user_state, mock_repo, new_time, mock_event
            )

        assert result["debrief_action"] == "moved"
        mock_scheduler.upsert_one_time_schedule.assert_called_once()
        mock_repo.update_prompt_schedule.assert_called_once()
        mock_repo.update_debrief_event.assert_called_once()

    def test_deletes_when_new_time_in_past(
        self, mock_user_state: UserState, mock_event: dict
    ) -> None:
        """Should delete schedule when new time is in the past."""
        from src.handlers.calendar_webhook import _handle_debrief_moved

        past_time = datetime.now(UTC) - timedelta(hours=1)

        mock_repo = MagicMock()
        mock_scheduler = MagicMock()

        with patch("src.handlers.calendar_webhook.get_scheduler", return_value=mock_scheduler):
            result = _handle_debrief_moved(
                "user-001", mock_user_state, mock_repo, past_time, mock_event
            )

        assert result["debrief_action"] == "deleted_past"
        mock_scheduler.delete_schedule.assert_called_once()
        mock_repo.clear_debrief_event.assert_called_once()

    def test_fails_when_scheduler_role_not_configured(
        self, mock_user_state: UserState, mock_event: dict
    ) -> None:
        """Should fail gracefully when scheduler role not configured."""
        from src.handlers.calendar_webhook import _handle_debrief_moved

        new_time = datetime.now(UTC) + timedelta(hours=2)

        mock_repo = MagicMock()

        with patch.dict("os.environ", {"SCHEDULER_ROLE_ARN": ""}, clear=False):
            result = _handle_debrief_moved(
                "user-001", mock_user_state, mock_repo, new_time, mock_event
            )

        assert result["debrief_action"] == "reschedule_failed"


class TestEntityAutoCreation:
    """Tests for entity auto-creation from calendar attendees (Slice 3)."""

    @pytest.fixture
    def mock_event_with_attendees(self) -> dict:
        """Create a mock Google Calendar event with attendees."""
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        return {
            "id": "meeting-with-attendees",
            "status": "confirmed",
            "etag": "etag-123",
            "summary": "Team Meeting",
            "start": {"dateTime": tomorrow.isoformat()},
            "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat()},
            "attendees": [
                {"email": "alice@example.com", "displayName": "Alice Smith"},
                {"email": "bob@example.com", "displayName": "Bob Jones"},
                {"displayName": "Charlie (External)"},  # No email
            ],
        }

    def test_creates_entities_for_attendees_with_emails(
        self, mock_event_with_attendees: dict
    ) -> None:
        """Should create resolved entities for attendees with emails."""
        from src.handlers.calendar_webhook import sync_calendar_events

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_meeting.return_value = None
        mock_calendar = MagicMock()
        mock_calendar.list_events.return_value = [mock_event_with_attendees]

        # Mock entities repo
        mock_entities_repo = MagicMock()
        mock_entity_alice = MagicMock(entity_id="entity-alice")
        mock_entity_bob = MagicMock(entity_id="entity-bob")
        mock_entities_repo.get_or_create_by_email.side_effect = [
            mock_entity_alice,
            mock_entity_bob,
        ]

        with (
            patch.dict("os.environ", {"USER_ID": "user-001"}, clear=False),
            patch(
                "src.handlers.calendar_webhook.get_meetings_repo", return_value=mock_meetings_repo
            ),
            patch("src.handlers.calendar_webhook.get_calendar_client", return_value=mock_calendar),
            patch(
                "src.handlers.calendar_webhook.get_entities_repo", return_value=mock_entities_repo
            ),
            patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=None),
            patch("src.handlers.calendar_webhook.check_debrief_event_changes", return_value={}),
        ):
            result = sync_calendar_events()

        # Verify entities were created for attendees with emails
        assert mock_entities_repo.get_or_create_by_email.call_count == 2
        mock_entities_repo.get_or_create_by_email.assert_any_call(
            "user-001", "alice@example.com", "Alice Smith"
        )
        mock_entities_repo.get_or_create_by_email.assert_any_call(
            "user-001", "bob@example.com", "Bob Jones"
        )

        # Verify meeting was saved with entity IDs
        mock_meetings_repo.save_meeting.assert_called_once()
        saved_meeting = mock_meetings_repo.save_meeting.call_args[0][0]
        assert saved_meeting.attendee_entity_ids == ["entity-alice", "entity-bob"]
        assert result["synced"] == 1

    def test_skips_attendees_without_emails(self, mock_event_with_attendees: dict) -> None:
        """Should skip attendees without email addresses."""
        from src.handlers.calendar_webhook import sync_calendar_events

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_meeting.return_value = None
        mock_calendar = MagicMock()
        mock_calendar.list_events.return_value = [mock_event_with_attendees]

        mock_entities_repo = MagicMock()
        mock_entities_repo.get_or_create_by_email.return_value = MagicMock(entity_id="entity-123")

        with (
            patch.dict("os.environ", {"USER_ID": "user-001"}, clear=False),
            patch(
                "src.handlers.calendar_webhook.get_meetings_repo", return_value=mock_meetings_repo
            ),
            patch("src.handlers.calendar_webhook.get_calendar_client", return_value=mock_calendar),
            patch(
                "src.handlers.calendar_webhook.get_entities_repo", return_value=mock_entities_repo
            ),
            patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=None),
            patch("src.handlers.calendar_webhook.check_debrief_event_changes", return_value={}),
        ):
            sync_calendar_events()

        # Should only call for attendees with emails (Alice and Bob, not Charlie)
        assert mock_entities_repo.get_or_create_by_email.call_count == 2

    def test_graceful_degradation_when_entity_creation_fails(
        self, mock_event_with_attendees: dict
    ) -> None:
        """Should continue with meeting sync even if entity creation fails."""
        from src.handlers.calendar_webhook import sync_calendar_events

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_meeting.return_value = None
        mock_calendar = MagicMock()
        mock_calendar.list_events.return_value = [mock_event_with_attendees]

        # Mock entities repo to raise an exception
        mock_entities_repo = MagicMock()
        mock_entities_repo.get_or_create_by_email.side_effect = Exception("DynamoDB error")

        with (
            patch.dict("os.environ", {"USER_ID": "user-001"}, clear=False),
            patch(
                "src.handlers.calendar_webhook.get_meetings_repo", return_value=mock_meetings_repo
            ),
            patch("src.handlers.calendar_webhook.get_calendar_client", return_value=mock_calendar),
            patch(
                "src.handlers.calendar_webhook.get_entities_repo", return_value=mock_entities_repo
            ),
            patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=None),
            patch("src.handlers.calendar_webhook.check_debrief_event_changes", return_value={}),
        ):
            result = sync_calendar_events()

        # Meeting should still be saved despite entity creation failure
        mock_meetings_repo.save_meeting.assert_called_once()
        saved_meeting = mock_meetings_repo.save_meeting.call_args[0][0]
        # attendee_entity_ids should be empty due to failure
        assert saved_meeting.attendee_entity_ids == []
        assert result["synced"] == 1

    def test_syncs_meetings_without_entities_repo(self, mock_event_with_attendees: dict) -> None:
        """Should sync meetings normally when entities repo not configured."""
        from src.handlers.calendar_webhook import sync_calendar_events

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_meeting.return_value = None
        mock_calendar = MagicMock()
        mock_calendar.list_events.return_value = [mock_event_with_attendees]

        with (
            patch.dict("os.environ", {"USER_ID": "user-001"}, clear=False),
            patch(
                "src.handlers.calendar_webhook.get_meetings_repo", return_value=mock_meetings_repo
            ),
            patch("src.handlers.calendar_webhook.get_calendar_client", return_value=mock_calendar),
            patch(
                "src.handlers.calendar_webhook.get_entities_repo", return_value=None
            ),  # No entities repo
            patch("src.handlers.calendar_webhook.get_user_state_repo", return_value=None),
            patch("src.handlers.calendar_webhook.check_debrief_event_changes", return_value={}),
        ):
            result = sync_calendar_events()

        # Meeting should still be saved
        mock_meetings_repo.save_meeting.assert_called_once()
        saved_meeting = mock_meetings_repo.save_meeting.call_args[0][0]
        # attendee_entity_ids should be empty (default)
        assert saved_meeting.attendee_entity_ids == []
        assert result["synced"] == 1
