"""Unit tests for prompt sender handler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.models import Meeting, UserState


class TestPromptSenderHandler:
    """Tests for prompt sender Lambda handler."""

    @pytest.fixture
    def mock_env(self) -> dict[str, str]:
        """Environment variables for testing."""
        return {
            "USER_STATE_TABLE": "kairos-user-state",
            "IDEMPOTENCY_TABLE": "kairos-idempotency",
            "MEETINGS_TABLE": "kairos-meetings",
            "SSM_BLAND_API_KEY": "/kairos/bland-api-key",
            "WEBHOOK_URL": "https://example.com/webhook",
            "AWS_REGION": "eu-west-1",
        }

    @pytest.fixture
    def sample_user_state(self) -> UserState:
        """Create sample user state."""
        return UserState(
            user_id="user-001",
            phone_number="+447700900000",
            stopped=False,
            daily_call_made=False,
            call_successful=False,
            retries_today=0,
        )

    @pytest.fixture
    def sample_meetings(self) -> list[Meeting]:
        """Create sample meetings."""
        past_time = datetime.now(UTC) - timedelta(hours=2)
        return [
            Meeting(
                user_id="user-001",
                meeting_id="meeting-1",
                title="Team Standup",
                start_time=past_time - timedelta(hours=1),
                end_time=past_time,
                attendees=["Alice", "Bob"],
                status="pending",
                created_at=datetime.now(UTC),
            ),
            Meeting(
                user_id="user-001",
                meeting_id="meeting-2",
                title="Client Call",
                start_time=past_time - timedelta(hours=2),
                end_time=past_time - timedelta(hours=1),
                attendees=["Client"],
                status="pending",
                created_at=datetime.now(UTC),
            ),
        ]

    def test_already_sent_returns_200(self, mock_env: dict[str, str]) -> None:
        """Should return 200 if SMS already sent today."""
        from src.handlers.prompt_sender import handler

        event = {"user_id": "user-001", "date": "2024-01-15"}

        mock_sms_dedup = MagicMock()
        mock_sms_dedup.try_send_daily_prompt.return_value = False

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.SMSSendDedup", return_value=mock_sms_dedup),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "already_sent"

    def test_user_not_found_returns_404(self, mock_env: dict[str, str]) -> None:
        """Should return 404 if user not found."""
        from src.handlers.prompt_sender import handler

        event = {"user_id": "user-001", "date": "2024-01-15"}

        mock_sms_dedup = MagicMock()
        mock_sms_dedup.try_send_daily_prompt.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = None

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.SMSSendDedup", return_value=mock_sms_dedup),
            patch(
                "src.handlers.prompt_sender.UserStateRepository",
                return_value=mock_user_repo,
            ),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 404

    def test_user_stopped_returns_200(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should return 200 if user has stopped."""
        from src.handlers.prompt_sender import handler

        sample_user_state.stopped = True
        event = {"user_id": "user-001", "date": "2024-01-15"}

        mock_sms_dedup = MagicMock()
        mock_sms_dedup.try_send_daily_prompt.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state
        mock_user_repo.can_prompt.return_value = (False, "stopped")

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.SMSSendDedup", return_value=mock_sms_dedup),
            patch(
                "src.handlers.prompt_sender.UserStateRepository",
                return_value=mock_user_repo,
            ),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "stopped"

    def test_no_meetings_returns_200(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should return 200 if no pending meetings."""
        from src.handlers.prompt_sender import handler

        event = {"user_id": "user-001", "date": "2024-01-15"}

        mock_sms_dedup = MagicMock()
        mock_sms_dedup.try_send_daily_prompt.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state
        mock_user_repo.can_prompt.return_value = (True, "ok")

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_pending_meetings.return_value = []

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.SMSSendDedup", return_value=mock_sms_dedup),
            patch(
                "src.handlers.prompt_sender.UserStateRepository",
                return_value=mock_user_repo,
            ),
            patch(
                "src.handlers.prompt_sender.MeetingsRepository",
                return_value=mock_meetings_repo,
            ),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "no_meetings"

    def test_retry_already_executed(self, mock_env: dict[str, str]) -> None:
        """Should return 200 if retry already executed."""
        from src.handlers.prompt_sender import handler

        event = {
            "user_id": "user-001",
            "date": "2024-01-15",
            "is_retry": True,
            "retry_number": 1,
        }

        mock_retry_dedup = MagicMock()
        mock_retry_dedup.try_acquire.return_value = False

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.CallRetryDedup", return_value=mock_retry_dedup),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "retry_already_executed"

    def test_call_already_successful_skips_retry(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should skip retry if call already successful."""
        from src.handlers.prompt_sender import handler

        sample_user_state.call_successful = True
        event = {
            "user_id": "user-001",
            "date": "2024-01-15",
            "is_retry": True,
            "retry_number": 1,
        }

        mock_retry_dedup = MagicMock()
        mock_retry_dedup.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.CallRetryDedup", return_value=mock_retry_dedup),
            patch(
                "src.handlers.prompt_sender.UserStateRepository",
                return_value=mock_user_repo,
            ),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "call_already_successful"

    def test_max_retries_reached(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should skip retry if max retries reached."""
        from src.handlers.prompt_sender import handler

        sample_user_state.retries_today = 3
        event = {
            "user_id": "user-001",
            "date": "2024-01-15",
            "is_retry": True,
            "retry_number": 4,
        }

        mock_retry_dedup = MagicMock()
        mock_retry_dedup.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.prompt_sender.CallRetryDedup", return_value=mock_retry_dedup),
            patch(
                "src.handlers.prompt_sender.UserStateRepository",
                return_value=mock_user_repo,
            ),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "max_retries_reached"


class TestBuildMultiMeetingPrompt:
    """Tests for build_multi_meeting_prompt function."""

    @pytest.fixture
    def sample_meetings(self) -> list[Meeting]:
        """Create sample meetings."""
        now = datetime.now(UTC)
        return [
            Meeting(
                user_id="user-001",
                meeting_id="meeting-1",
                title="Team Standup",
                start_time=now - timedelta(hours=3),
                end_time=now - timedelta(hours=2, minutes=30),
                attendees=["Alice", "Bob", "Charlie"],
                status="pending",
                created_at=now,
            ),
            Meeting(
                user_id="user-001",
                meeting_id="meeting-2",
                title="Client Call",
                start_time=now - timedelta(hours=2),
                end_time=now - timedelta(hours=1),
                attendees=["Client"],
                status="pending",
                created_at=now,
            ),
        ]

    def test_includes_meeting_titles(self, sample_meetings: list[Meeting]) -> None:
        """Should include meeting titles in prompt."""
        from src.handlers.prompt_sender import build_multi_meeting_prompt

        prompt = build_multi_meeting_prompt(sample_meetings)

        assert "Team Standup" in prompt
        assert "Client Call" in prompt

    def test_includes_attendees(self, sample_meetings: list[Meeting]) -> None:
        """Should include attendee names."""
        from src.handlers.prompt_sender import build_multi_meeting_prompt

        prompt = build_multi_meeting_prompt(sample_meetings)

        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "Client" in prompt

    def test_includes_meeting_count(self, sample_meetings: list[Meeting]) -> None:
        """Should include total meeting count."""
        from src.handlers.prompt_sender import build_multi_meeting_prompt

        prompt = build_multi_meeting_prompt(sample_meetings)

        assert "2 meeting" in prompt

    def test_includes_duration(self, sample_meetings: list[Meeting]) -> None:
        """Should include meeting durations."""
        from src.handlers.prompt_sender import build_multi_meeting_prompt

        prompt = build_multi_meeting_prompt(sample_meetings)

        assert "30 min" in prompt
        assert "60 min" in prompt


class TestCollectUniqueAttendees:
    """Tests for _collect_unique_attendees function."""

    def test_collects_unique_names(self) -> None:
        """Should collect unique attendee names."""
        from src.handlers.prompt_sender import _collect_unique_attendees

        now = datetime.now(UTC)
        meetings = [
            Meeting(
                user_id="user-001",
                meeting_id="m1",
                title="M1",
                start_time=now,
                end_time=now + timedelta(hours=1),
                attendees=["Alice", "Bob"],
                status="pending",
                created_at=now,
            ),
            Meeting(
                user_id="user-001",
                meeting_id="m2",
                title="M2",
                start_time=now,
                end_time=now + timedelta(hours=1),
                attendees=["Bob", "Charlie"],
                status="pending",
                created_at=now,
            ),
        ]

        result = _collect_unique_attendees(meetings)

        assert result == ["Alice", "Bob", "Charlie"]

    def test_respects_limit(self) -> None:
        """Should respect the limit parameter."""
        from src.handlers.prompt_sender import _collect_unique_attendees

        now = datetime.now(UTC)
        meetings = [
            Meeting(
                user_id="user-001",
                meeting_id="m1",
                title="M1",
                start_time=now,
                end_time=now + timedelta(hours=1),
                attendees=["A", "B", "C", "D", "E"],
                status="pending",
                created_at=now,
            ),
        ]

        result = _collect_unique_attendees(meetings, limit=3)

        assert len(result) == 3
        assert result == ["A", "B", "C"]
